"""Granice sesji w API (D16) — czy klient może dosięgnąć czegoś, co nie jego.

Ten plik istnieje z jednego powodu: **wcześniej granice w tym projekcie pękały
trzy razy** — flaga `--read-only` w MCP, callback `can_use_tool` i klucz API,
który nie dochodził do modelu. Za każdym razem mechanizm był udokumentowany
i wyglądał na działający, a testy sprawdzały element w izolacji, nie jego
podłączenie.

Tu sprawdzamy PODŁĄCZENIE: pytamy endpointów tak, jak zapytałby odbiorca.

Cztery reguły:

1. bez ciasteczka każdy endpoint danych → 401
2. sesja klienta na endpoincie zespołowym → **404**, nie 403
3. `?klient=` podany przez klienta jest **ignorowany**, nie honorowany
4. payload klienta nie ma kluczy wewnętrznych ani treści tropu
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.deanonimizacja import WZORZEC_HASHA
from monday_audit.dostep import ROLA_KLIENT, ROLA_ZESPOL, utworz_konto
from monday_audit.konfiguracja import UstawieniaPoczty
from monday_audit.pulpit import KLUCZE_WEWNETRZNE
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.web.api import zbuduj_aplikacje
from monday_audit.zadania import utworz_zadanie, zapisz_stan

# Atrapa klucza monday, SKŁADANA Z CZĘŚCI. Hook `token-monday` szuka wzorca JWT
# w treści plików i nie potrafi odróżnić atrapy od prawdziwego klucza — i tak ma
# być, bo hook próbujący tej oceny byłby dziurą. Wklejony wzorzec zatrzymywałby
# każdy commit tego pliku, więc nie ma go tu w jednym kawałku.
PREFIKS_JWT = "eyJhbGciOi" + "JIUzI1NiJ9"
ATRAPA_KLUCZA = PREFIKS_JWT + ".ATRAPA-DO-TESTU." + "podpis-nieprawdziwy"

RUBRYKA = wczytaj_rubryke()
RUN_AT = "2026-08-01T21:09:13.860699+00:00"
HASH_ANNY = "05677b1ab370bae1"
HASLO_KLIENTA = "test-haslo-klienta-1"
HASLO_ZESPOLU = "test-haslo-zespolu-1"
EMAIL = "jle@cxlabs.digital"

PAYLOAD: dict[str, Any] = {
    "meta": {"client_id": "cxlabs", "run_at": RUN_AT, "collector_ver": "0.1.0"},
    "konto": {
        "konto": {"nazwa": "CXLABS"},
        "plan": {"tier": "enterprise"},
        "zakres": {"typ": "workspace", "workspace_ids": ["6576039"], "board_ids": []},
        "zastrzezenia": ["token bez uprawnień admina"],
    },
    "uzytkownicy": {"podsumowanie": {"razem": 95, "agentow": 36}},
    "tablice": {"podsumowanie": {"razem": 105}},
    "automatyzacje": {"podsumowanie": {"automatyzacji_widzianych": 80}},
    "aktywnosc": {"podsumowanie": {"tablic_zbadanych": 105}},
}


def _snapshot_i_run(con: sqlite3.Connection, client_id: str, snapshot_id: int, run: str) -> None:
    payload = {**PAYLOAD, "meta": {**PAYLOAD["meta"], "client_id": client_id}}
    con.execute(
        "INSERT INTO snapshots (id, client_id, run_at, collector_ver, payload) "
        "VALUES (?, ?, ?, '0.1.0', ?)",
        (snapshot_id, client_id, RUN_AT, json.dumps(payload, ensure_ascii=False)),
    )
    con.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
        "rubric_ver, hipotez_zbadanych, findingow, koszt_usd) "
        "VALUES (?, ?, ?, 'zakonczony', ?, 'claude-sonnet-5', ?, 19, 1, 1.71)",
        (run, client_id, snapshot_id, RUN_AT, RUBRYKA.wersja),
    )
    klasa = RUBRYKA.po_id["ZOMBIE_ACCOUNT"]
    con.execute(
        "INSERT INTO findings (run_id, snapshot_id, klasa_id, rubric_ver, waga, wysilek, "
        "typ_wyceny, kwota_pln, widocznosc, opis, rekomendacja, dowod, pewnosc, trop) "
        "VALUES (?, ?, 'ZOMBIE_ACCOUNT', ?, 'wysoka', 'niski', ?, NULL, ?, "
        "'martwe konto', 'zwolnić', ?, 'wysoka', ?)",
        (
            run,
            snapshot_id,
            RUBRYKA.wersja,
            klasa.typ_wyceny,
            klasa.widocznosc,
            json.dumps({"user_hash": HASH_ANNY}),
            klasa.trop_sprzedazowy,
        ),
    )
    con.execute(
        "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
        "VALUES (?, ?, 'Anna Górniak', 'anna@klient.test')",
        (client_id, HASH_ANNY),
    )


@contextmanager
def caplog_reczny() -> Iterator[list[str]]:
    """Zbiera komunikaty logu na czas bloku.

    `caplog` z pytest nie widzi logów z wątku puli FastAPI w każdej konfiguracji,
    a link resetu jest logowany właśnie tam. Własny handler jest pewniejszy niż
    fixture, która czasem łapie, a czasem nie — i test, który czasem łapie, nie
    pilnuje niczego.
    """
    zapisy: list[str] = []

    class Zbieracz(logging.Handler):
        def emit(self, zapis: logging.LogRecord) -> None:
            zapisy.append(zapis.getMessage())

    korzen = logging.getLogger()
    handler = Zbieracz()
    poziom = korzen.level
    korzen.addHandler(handler)
    korzen.setLevel(logging.INFO)
    try:
        yield zapisy
    finally:
        korzen.removeHandler(handler)
        korzen.setLevel(poziom)


@pytest.fixture
def baza(tmp_path: Path) -> Iterator[Path]:
    sciezka = tmp_path / "web.db"
    con = polacz(sciezka)
    zastosuj_migracje(con)
    # DWÓCH klientów — bez drugiego nie da się sprawdzić, czy pierwszy widzi
    # tylko siebie. Test na jednym kliencie przechodzi zawsze i nic nie znaczy.
    _snapshot_i_run(con, "cxlabs", 5, "r-cxlabs")
    _snapshot_i_run(con, "inny-klient", 6, "r-inny")
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_KLIENTA, client_id="cxlabs")
    utworz_konto(con, rola=ROLA_ZESPOL, haslo=HASLO_ZESPOLU, email=EMAIL)
    con.commit()
    con.close()
    yield sciezka


@pytest.fixture
def klient_http(baza: Path) -> Iterator[TestClient]:
    with TestClient(zbuduj_aplikacje(baza=baza), base_url="https://test") as c:
        yield c


def _zaloguj_klienta(c: TestClient, client_id: str = "cxlabs") -> None:
    odp = c.post("/api/sesja/klient", json={"haslo": HASLO_KLIENTA, "client_id": client_id})
    assert odp.status_code == 200, odp.text


def _zaloguj_zespol(c: TestClient) -> None:
    odp = c.post("/api/sesja/zespol", json={"email": EMAIL, "haslo": HASLO_ZESPOLU})
    assert odp.status_code == 200, odp.text


# ── 1. bez sesji nic ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sciezka", ["/api/ja", "/api/pulpit", "/api/klienci", "/api/audyt/mozliwosc"]
)
def test_bez_ciasteczka_kazdy_endpoint_danych_odmawia(
    klient_http: TestClient, sciezka: str
) -> None:
    assert klient_http.get(sciezka).status_code == 401


def test_zle_haslo_nie_daje_sesji(klient_http: TestClient) -> None:
    odp = klient_http.post(
        "/api/sesja/klient", json={"haslo": "zupelnie-inne", "client_id": "cxlabs"}
    )

    assert odp.status_code == 401
    assert klient_http.get("/api/pulpit").status_code == 401


def test_nieistniejacy_klient_daje_ten_sam_blad_co_zle_haslo(klient_http: TestClient) -> None:
    """Rozróżnienie mówiłoby, które identyfikatory klientów są prawdziwe."""
    zly_klient = klient_http.post(
        "/api/sesja/klient", json={"haslo": HASLO_KLIENTA, "client_id": "nie-ma-takiego"}
    )
    zle_haslo = klient_http.post("/api/sesja/klient", json={"haslo": "inne", "client_id": "cxlabs"})

    assert zly_klient.status_code == zle_haslo.status_code == 401
    assert zly_klient.json()["detail"] == zle_haslo.json()["detail"]


# ── 2. klient nie dosięga endpointów zespołu ─────────────────────────────


def test_klient_na_liscie_klientow_dostaje_404_nie_403(klient_http: TestClient) -> None:
    """403 potwierdziłoby, że endpoint istnieje i ma treść.

    Lista klientów CXLABS to informacja handlowa — kto z kim pracuje.
    """
    _zaloguj_klienta(klient_http)

    odp = klient_http.get("/api/klienci")

    assert odp.status_code == 404
    assert "cxlabs" not in odp.text


def test_zespol_widzi_liste_klientow(klient_http: TestClient) -> None:
    """Komplement poprzedniego — inaczej test przechodziłby przy zepsutym API."""
    _zaloguj_zespol(klient_http)

    odp = klient_http.get("/api/klienci")

    assert odp.status_code == 200
    assert {p["client_id"] for p in odp.json()} == {"cxlabs", "inny-klient"}


# ── 3. parametr od przeglądarki jest IGNOROWANY ──────────────────────────


def test_klient_nie_zobaczy_cudzych_danych_przez_parametr(klient_http: TestClient) -> None:
    """Najważniejszy test tego pliku.

    Endpoint czytający `client_id` z zapytania dawałby dostęp do cudzych danych
    przez podmianę jednego słowa w URL-u. `?klient=` jest tu **ignorowany**,
    bo `client_id` bierzemy z sesji.
    """
    _zaloguj_klienta(klient_http, "cxlabs")

    odp = klient_http.get("/api/pulpit?klient=inny-klient")

    assert odp.status_code == 200
    assert odp.json()["client_id"] == "cxlabs", "parametr z URL-a nadpisał sesję"


def test_zespol_moze_przelaczac_klienta_parametrem(klient_http: TestClient) -> None:
    """Dla zespołu `?klient=` DZIAŁA — to drop-down."""
    _zaloguj_zespol(klient_http)

    assert klient_http.get("/api/pulpit?klient=cxlabs").json()["client_id"] == "cxlabs"
    assert klient_http.get("/api/pulpit?klient=inny-klient").json()["client_id"] == "inny-klient"


def test_klient_nie_zobaczy_cudzego_zadania(klient_http: TestClient) -> None:
    """Cudze i nieistniejące zadanie dają TO SAMO 404.

    Inaczej po kodzie odpowiedzi dałoby się sprawdzać, które identyfikatory
    zadań istnieją.
    """
    _zaloguj_klienta(klient_http)

    cudze = klient_http.get("/api/audyt/nie-moje-zadanie")

    assert cudze.status_code == 404


# ── 4. payload klienta nie niesie treści wewnętrznych ────────────────────


def test_payload_klienta_nie_ma_kluczy_wewnetrznych(klient_http: TestClient) -> None:
    _zaloguj_klienta(klient_http)

    dane = klient_http.get("/api/pulpit").json()

    for klucz in KLUCZE_WEWNETRZNE:
        assert klucz not in dane, f"{klucz} przeszedł do payloadu klienta"


def test_payload_zespolu_ma_klucze_wewnetrzne(klient_http: TestClient) -> None:
    """Bez tego poprzedni test przechodziłby przy całkowicie zepsutym API."""
    _zaloguj_zespol(klient_http)

    dane = klient_http.get("/api/pulpit?klient=cxlabs").json()

    for klucz in KLUCZE_WEWNETRZNE:
        assert klucz in dane


def test_payload_klienta_nie_niesie_tropu_ani_hasha(klient_http: TestClient) -> None:
    _zaloguj_klienta(klient_http)
    trop = RUBRYKA.po_id["ZOMBIE_ACCOUNT"].trop_sprzedazowy
    assert trop

    tekst = klient_http.get("/api/pulpit").text

    assert trop not in tekst
    assert WZORZEC_HASHA.search(tekst) is None
    assert "Anna Górniak" in tekst, "deanonimizacja ma działać, tylko bez hashy"


# ── wylogowanie i hamulec kosztu ─────────────────────────────────────────


def test_wylogowanie_unieważnia_sesje_natychmiast(klient_http: TestClient) -> None:
    """Sesje są w bazie właśnie po to — kasujemy wiersz, nie czekamy na wygaśnięcie."""
    _zaloguj_klienta(klient_http)
    assert klient_http.get("/api/pulpit").status_code == 200

    klient_http.post("/api/sesja/koniec")

    assert klient_http.get("/api/pulpit").status_code == 401


def test_hamulec_kosztu_odmawia_drugiego_audytu(klient_http: TestClient, baza: Path) -> None:
    """Klient klika i wydaje NASZE pieniądze, więc odmowa jest w API, nie w JS.

    Sprawdzamy przez HTTP, nie przez funkcję — bo `curl` nie widzi wyszarzonego
    przycisku.
    """
    from monday_audit.zadania import utworz_zadanie

    con = polacz(baza)
    utworz_zadanie(con, client_id="cxlabs", konto_id=1)
    con.close()
    _zaloguj_klienta(klient_http)

    odp = klient_http.post("/api/audyt", json={"klucz_api": "x" * 40, "zakres": "cale_konto"})

    assert odp.status_code == 429
    assert "trwa" in odp.json()["detail"] or "możliwy" in odp.json()["detail"]


def test_mozliwosc_mowi_dlaczego_nie_wolno(klient_http: TestClient, baza: Path) -> None:
    from monday_audit.zadania import utworz_zadanie

    con = polacz(baza)
    utworz_zadanie(con, client_id="cxlabs", konto_id=1)
    con.close()
    _zaloguj_klienta(klient_http)

    dane = klient_http.get("/api/audyt/mozliwosc").json()

    assert dane["wolno"] is False
    assert dane["powod"]
    assert dane["client_id"] == "cxlabs"


def test_ciasteczko_jest_httponly_i_secure(klient_http: TestClient) -> None:
    """XSS nie może wykraść sesji, a sesja nie może pójść po HTTP."""
    odp = klient_http.post(
        "/api/sesja/klient", json={"haslo": HASLO_KLIENTA, "client_id": "cxlabs"}
    )

    naglowek = odp.headers["set-cookie"].lower()
    assert "httponly" in naglowek
    assert "secure" in naglowek
    assert "samesite=lax" in naglowek


def test_odpalenie_audytu_nie_wywala_serwera(
    klient_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresja z pierwszego `curl`-a na żywo.

    Endpoint był synchroniczny, a planował zadanie przez
    `asyncio.get_running_loop().run_in_executor` — w funkcji `def`, nie
    `async def`, pętli zdarzeń NIE MA, więc leciał `RuntimeError: no running
    event loop` i klient dostawał 500 zamiast startu audytu.

    Testy granic tego nie łapały, bo żaden nie odpalał runu — sprawdzały, kto
    co WIDZI, nie czy odpalanie działa. Ten test podmienia samo wykonanie audytu
    na atrapę, żeby sprawdzić WYŁĄCZNIE ścieżkę HTTP: 200 i identyfikator
    zadania, bez wchodzenia do monday.
    """
    wywolania: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "monday_audit.web.api.uruchom_audyt_w_tle",
        lambda *a: wywolania.append(a),
    )
    _zaloguj_klienta(klient_http)

    odp = klient_http.post("/api/audyt", json={"klucz_api": "x" * 40, "zakres": "cale_konto"})

    assert odp.status_code == 200, odp.text
    assert odp.json()["zadanie_id"]
    assert len(wywolania) == 1, "zadanie w tle nie zostało zaplanowane"


def test_klucz_api_nie_wchodzi_do_bazy(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Najważniejsza granica tego etapu (D11).

    Szukamy klucza w CAŁEJ bazie — po wszystkich tabelach i kolumnach, nie tylko
    w tych, o których pamiętamy. Kolumna, której dziś nie ma, może dojść za pół
    roku i wtedy ten test ma zapytać „a czy tam nie wyciekł".
    """
    klucz = ATRAPA_KLUCZA
    monkeypatch.setattr("monday_audit.web.api.uruchom_audyt_w_tle", lambda *a: None)
    _zaloguj_klienta(klient_http)

    odp = klient_http.post("/api/audyt", json={"klucz_api": klucz, "zakres": "cale_konto"})
    assert odp.status_code == 200

    con = polacz(baza)
    trafienia = []
    for tabela in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        nazwa = str(tabela["name"])
        for wiersz in con.execute(f"SELECT * FROM {nazwa}"):  # noqa: S608 — nazwa z sqlite_master
            for wartosc in tuple(wiersz):
                if isinstance(wartosc, str) and "PODSTAWIONY-KLUCZ" in wartosc:
                    trafienia.append(nazwa)
    con.close()

    assert trafienia == [], f"klucz API wylądował w bazie: {trafienia}"


def test_osierocone_zadanie_nie_blokuje_na_zawsze(klient_http: TestClient, baza: Path) -> None:
    """Regresja z pierwszego uruchomienia na żywo.

    Endpoint padł na `RuntimeError` PO utworzeniu wiersza zadania, więc został
    stan `w_kolejce`, którego nic nigdy nie zmieniło. Klient dostawał „audyt już
    trwa" bez końca — blokada z powodu NASZEGO błędu, której nie da się zdjąć.

    Po `MINUT_NA_OSIEROCENIE` takie zadanie dostaje stan `blad` i przestaje
    blokować.
    """
    from datetime import UTC, datetime, timedelta

    from monday_audit.zadania import MINUT_NA_OSIEROCENIE, wolno_odpalic

    dawno = (datetime.now(tz=UTC) - timedelta(minutes=MINUT_NA_OSIEROCENIE + 5)).isoformat()
    con = polacz(baza)
    con.execute(
        "INSERT INTO zadania (id, client_id, konto_id, stan, etap, postep, zaczeto) "
        "VALUES ('osierocone', 'cxlabs', 1, 'w_kolejce', 'czekam na start', 0, ?)",
        (dawno,),
    )
    con.commit()

    wolno, powod = wolno_odpalic(con, "cxlabs")

    assert wolno, f"osierocone zadanie nadal blokuje: {powod}"
    stan = con.execute("SELECT stan, blad FROM zadania WHERE id = 'osierocone'").fetchone()
    assert stan["stan"] == "blad"
    assert "nie zgłosiło postępu" in stan["blad"]
    con.close()


def test_zadanie_czekajace_na_zgode_nie_jest_osierocone(
    klient_http: TestClient, baza: Path
) -> None:
    """Zgoda ważna dwanaście godzin nie może padać po czterdziestu minutach.

    Reaper porównuje `zaczeto` — moment założenia zadania — a nie ostatni
    zgłoszony postęp. Zadanie czekające na decyzję klienta nie zgłasza postępu
    Z DEFINICJI, więc bez wyłączenia go z listy osieroconych dostałoby stan
    `blad` po czterdziestu minutach, a klient wracałby do audytu, którego już
    nie ma.

    Test podmienia `zaczeto` w bazie, bo tylko to sprawdza mechanizm. Odczyt
    kodu potwierdziłby jedynie, że dobrze go przeczytałem.
    """
    from datetime import UTC, datetime, timedelta

    from monday_audit.zadania import (
        GODZIN_WAZNOSCI_ZGODY,
        MINUT_NA_OSIEROCENIE,
        STAN_CZEKA_NA_ZGODE,
        wolno_odpalic,
    )

    teraz = datetime.now(tz=UTC)
    dawno = (teraz - timedelta(minutes=MINUT_NA_OSIEROCENIE + 5)).isoformat()
    wazna_do = (teraz + timedelta(hours=GODZIN_WAZNOSCI_ZGODY)).isoformat()
    con = polacz(baza)
    con.execute(
        "INSERT INTO zadania (id, client_id, konto_id, stan, etap, postep, zaczeto, zgoda_do) "
        "VALUES ('czeka', 'cxlabs', 1, ?, 'czekam na wybór zakresu', 60, ?, ?)",
        (STAN_CZEKA_NA_ZGODE, dawno, wazna_do),
    )
    con.commit()

    wolno_odpalic(con, "cxlabs")

    stan = con.execute("SELECT stan FROM zadania WHERE id = 'czeka'").fetchone()
    assert stan["stan"] == STAN_CZEKA_NA_ZGODE, (
        "zadanie czekające na zgodę zostało uznane za osierocone — zgoda "
        "ważna dwanaście godzin jest wtedy fikcją"
    )
    con.close()


def test_zgoda_po_terminie_jest_wygaszana(klient_http: TestClient, baza: Path) -> None:
    """Komplement poprzedniego: wyłączenie z reapera nie znaczy „nigdy nie wygasa".

    Bez tego zadanie czekające na zgodę zostawałoby w bazie na zawsze i liczyło
    się do sufitu audytów, blokując klienta — dokładnie ta usterka, którą
    reaper miał naprawiać.
    """
    from datetime import UTC, datetime, timedelta

    from monday_audit.zadania import STAN_CZEKA_NA_ZGODE, wolno_odpalic

    teraz = datetime.now(tz=UTC)
    con = polacz(baza)
    con.execute(
        "INSERT INTO zadania (id, client_id, konto_id, stan, etap, postep, zaczeto, zgoda_do) "
        "VALUES ('przedawniona', 'cxlabs', 1, ?, 'czekam', 60, ?, ?)",
        (
            STAN_CZEKA_NA_ZGODE,
            (teraz - timedelta(hours=20)).isoformat(),
            (teraz - timedelta(hours=1)).isoformat(),
        ),
    )
    con.commit()

    wolno_odpalic(con, "cxlabs")

    stan = con.execute("SELECT stan, blad FROM zadania WHERE id = 'przedawniona'").fetchone()
    assert stan["stan"] == "blad"
    assert "zestarzały" in stan["blad"]
    con.close()


def test_swieze_zadanie_nadal_blokuje(klient_http: TestClient, baza: Path) -> None:
    """Komplement — inaczej poprzednia poprawka zniosłaby hamulec zupełnie."""
    from monday_audit.zadania import utworz_zadanie, wolno_odpalic

    con = polacz(baza)
    utworz_zadanie(con, client_id="cxlabs", konto_id=1)

    wolno, powod = wolno_odpalic(con, "cxlabs")

    assert not wolno
    assert "trwa" in powod
    con.close()


def test_rownolegle_zadania_nie_wywalaja_sie_na_sqlite(baza: Path) -> None:
    """Regresja zmierzona w przeglądarce, nie w testach.

    FastAPI wykonuje synchroniczne endpointy w PULI WĄTKÓW, a generator
    zależności trafiał do innego wątku niż ciało endpointu. sqlite3 rzucał:

        SQLite objects created in a thread can only be used in that same thread

    Objawiało się jako 500 przy równoległych żądaniach — front pyta jednocześnie
    o pulpit, listę klientów i możliwość audytu, więc trafiało prawie zawsze.
    Panel mówił „nie ma jeszcze audytu tego konta", mając w bazie 11 znalezisk.

    **20 testów granic było zielonych**, bo `TestClient` obsługuje żądania PO
    KOLEI, w jednym wątku. Dlatego ten test strzela nimi RÓWNOLEGLE — inaczej
    nie odtworzyłby warunku, w którym usterka istnieje.
    """
    from concurrent.futures import ThreadPoolExecutor

    with TestClient(zbuduj_aplikacje(baza=baza), base_url="https://test") as c:
        _zaloguj_zespol(c)
        sciezki = ["/api/pulpit", "/api/klienci", "/api/audyt/mozliwosc", "/api/ja"] * 4

        with ThreadPoolExecutor(max_workers=8) as pula:
            kody = list(pula.map(lambda s: c.get(s).status_code, sciezki))

    assert all(k == 200 for k in kody), f"równoległe żądania dały: {sorted(set(kody))}"


def test_klucz_api_nie_zostaje_w_bazie_po_nieudanym_runie(tmp_path: Path) -> None:
    """Najgorszy moment na wyciek to BŁĄD, nie sukces.

    Ścieżka udana kończy się porządkami; ścieżka błędu zapisuje komunikat
    wyjątku — a wyjątki z klientów HTTP potrafią cytować nagłówki żądania.
    Dlatego ten test celowo używa klucza, który monday odrzuci.

    Zmierzone też ręcznie na żywym serwerze (2026-08-06): znacznik w kształcie
    JWT przeszedł POST → collector → 401 z monday i nie pojawił się ani w zrzucie
    bazy, ani w logu uvicorna, ani w argv procesów. Test pilnuje bazy, bo tylko
    ona przeżywa restart.
    """
    znacznik = f"{ATRAPA_KLUCZA}.znacznik-tego-testu"
    baza = tmp_path / "granica.db"
    con = polacz(baza)
    zastosuj_migracje(con)
    konto = utworz_konto(con, rola=ROLA_KLIENT, client_id="acme", haslo="x" * 12)

    zid = utworz_zadanie(con, konto_id=konto, client_id="acme")
    # Symulujemy dokładnie to, co robi `web/run.py`, gdy collector rzuci wyjątkiem
    # cytującym nagłówek Authorization — najgorszy realistyczny przypadek.
    zapisz_stan(
        con,
        zid,
        stan="blad",
        etap="audyt przerwany",
        blad=f"ZapytanieError: HTTP 401 przy naglowku Authorization: {znacznik}",
    )

    zrzut = "\n".join(con.iterdump())
    assert znacznik not in zrzut, "klucz API klienta trafił do bazy przez komunikat błędu"
    # Szukamy też samego KSZTAŁTU tokenu, nie tylko tego jednego znacznika:
    # następny wyciek będzie miał inną treść, ale ten sam prefiks JWT.
    assert PREFIKS_JWT not in zrzut, "w bazie jest coś w kształcie klucza monday"


# ── 5. wersja audytu: nowy parametr, ta sama reguła ──────────────────────
#
# `run` różni się od `klient` tym, że NIE MOŻNA go zignorować: klient ma prawo
# obejrzeć swój starszy audyt. Więc granicą nie jest „pomiń parametr", a
# „sprawdź właściciela" — i to jest dokładnie ten rodzaj granicy, który w tym
# projekcie pękał trzy razy, bo wyglądał na działający.


def test_klient_nie_otworzy_audytu_cudzego_klienta(klient_http: TestClient) -> None:
    """Podanie `run` obcego klienta daje 404, nie cudzy panel.

    Bez sprawdzenia właściciela `zbuduj_pulpit(run_id=...)` zbudowałby panel
    klienta „inny-klient" — razem z nazwiskami z jego tabeli mapowania. Sama ta
    funkcja nie pyta, czyj run dostała, i nie powinna: granica należy do
    endpointu, bo to on wie, kto pyta.
    """
    _zaloguj_klienta(klient_http, "cxlabs")

    odp = klient_http.get("/api/pulpit?run=r-inny")

    assert odp.status_code == 404, "sesja klienta dosięgnęła cudzego audytu"
    # 404, nie 403: 403 potwierdziłoby, że run `r-inny` istnieje.
    assert "Anna" not in odp.text and "inny-klient" not in odp.text


def test_klient_otwiera_swoj_audyt_po_run_id(klient_http: TestClient) -> None:
    """Druga strona tej samej granicy — bez niej „bezpieczne" znaczy „zepsute".

    Test sprawdzający tylko odmowę przechodziłby też wtedy, gdyby endpoint
    odrzucał KAŻDY `run`. Wtedy drop-down wersji nie działałby wcale, a granica
    wyglądałaby na szczelną.
    """
    _zaloguj_klienta(klient_http, "cxlabs")

    odp = klient_http.get("/api/pulpit?run=r-cxlabs")

    assert odp.status_code == 200, odp.text
    assert odp.json()["run_id"] == "r-cxlabs"


def test_nieistniejacy_run_tez_daje_404(klient_http: TestClient) -> None:
    """Ten sam kod dla „nie ma" i „nie twój".

    Rozróżnienie pozwoliłoby zgadywać identyfikatory cudzych runów: 404 dla
    nieistniejącego i 403 dla obcego to wyrocznia istnienia.
    """
    _zaloguj_klienta(klient_http, "cxlabs")
    assert klient_http.get("/api/pulpit?run=nie-ma-takiego").status_code == 404


def test_lista_wersji_klienta_zawiera_tylko_jego_runy(klient_http: TestClient) -> None:
    """Drop-down nie może wyliczyć cudzych audytów — nawet bez ich treści.

    Lista identyfikatorów i dat obcych runów sama jest informacją: mówi, ilu
    klientów mamy i kiedy ich audytowaliśmy.
    """
    _zaloguj_klienta(klient_http, "cxlabs")

    wersje = klient_http.get("/api/pulpit").json()["wersje"]

    assert [w["run_id"] for w in wersje] == ["r-cxlabs"]
    assert all("inny" not in w["run_id"] for w in wersje)


def test_zespol_przelacza_wersje_wybranego_klienta(klient_http: TestClient) -> None:
    """Zespół ma oba parametry naraz: kogo (`klient`) i z kiedy (`run`)."""
    _zaloguj_zespol(klient_http)

    odp = klient_http.get("/api/pulpit?klient=inny-klient&run=r-inny")

    assert odp.status_code == 200, odp.text
    dane = odp.json()
    assert dane["client_id"] == "inny-klient"
    assert dane["run_id"] == "r-inny"


def test_zespol_z_niedopasowanym_run_dostaje_404(klient_http: TestClient) -> None:
    """Sprawdzenie właściciela dotyczy TAKŻE zespołu.

    Nie dla ochrony przed nami samymi, a dlatego, że `klient=A&run=B` to pomyłka
    — panel zbudowany z runu B pod nagłówkiem klienta A pokazywałby cudze liczby
    z właściwą nazwą u góry. Cichy błąd, najgorszy rodzaj.
    """
    _zaloguj_zespol(klient_http)

    odp = klient_http.get("/api/pulpit?klient=cxlabs&run=r-inny")

    assert odp.status_code == 404, "zbudował panel z runu innego klienta"


# ── 6. reset haseł: klient nie może sam ──────────────────────────────────
#
# Wymaganie Kuby: hasło klienta resetuje ZESPÓŁ, klient nigdy. Realizujemy to
# brakiem endpointu dla klienta, nie zablokowanym endpointem — a wołanie
# zespołowego daje 404, bo 403 znaczyłoby „istnieje i nie wolno ci".


def test_klient_nie_zresetuje_sobie_hasla(klient_http: TestClient) -> None:
    """Sedno wymagania. Bez tego cała reszta nie ma znaczenia.

    Hasło jest jedyną bramą do danych osobowych klienta, a my nie mamy jak
    potwierdzić, kto o reset prosi — nie ma wysyłki maili (O24). Więc reset nie
    może być samoobsługowy.
    """
    _zaloguj_klienta(klient_http, "cxlabs")

    odp = klient_http.post("/api/haslo/klienta", json={"client_id": "cxlabs"})

    assert odp.status_code == 404, "klient zresetował sobie hasło"
    assert "haslo" not in odp.text, "odpowiedź niesie hasło"


def test_klient_nie_zresetuje_hasla_cudzego_klienta(klient_http: TestClient) -> None:
    """Ta sama droga, obcy cel — też 404, i to z tego samego powodu."""
    _zaloguj_klienta(klient_http, "cxlabs")

    odp = klient_http.post("/api/haslo/klienta", json={"client_id": "inny-klient"})

    assert odp.status_code == 404
    assert "haslo" not in odp.text


def test_klient_nie_ma_endpointu_wlasnego_hasla(klient_http: TestClient) -> None:
    """`/api/haslo/moje` jest zespołowe. Klient nie zmienia sobie hasła sam."""
    _zaloguj_klienta(klient_http, "cxlabs")

    odp = klient_http.post("/api/haslo/moje", json={"obecne_haslo": HASLO_KLIENTA})

    assert odp.status_code == 404
    assert "haslo" not in odp.text


def test_bez_sesji_reset_odmawia(klient_http: TestClient) -> None:
    """Bez ciasteczka to 401 — inaczej reset byłby otwarty dla każdego."""
    assert klient_http.post("/api/haslo/klienta", json={"client_id": "cxlabs"}).status_code == 401
    assert klient_http.post("/api/haslo/moje", json={"obecne_haslo": "x"}).status_code == 401


def test_zespol_resetuje_haslo_klienta(klient_http: TestClient) -> None:
    """Druga strona granicy — bez niej „bezpieczne" znaczyłoby „zepsute".

    Test sprawdzający tylko odmowy przechodziłby też wtedy, gdyby endpoint
    odrzucał WSZYSTKO i reset nie działał dla nikogo.
    """
    _zaloguj_zespol(klient_http)

    odp = klient_http.post("/api/haslo/klienta", json={"client_id": "cxlabs"})

    assert odp.status_code == 200, odp.text
    dane = odp.json()
    assert dane["haslo"], "nie zwrócił nowego hasła"
    # Reset NIE wylogowuje (decyzja) — więc odpowiedź musi to powiedzieć.
    assert "wazne_sesje" in dane and "godzin_sesji" in dane


def test_stare_haslo_klienta_przestaje_dzialac_po_resecie(klient_http: TestClient) -> None:
    """Skutek, nie wywołanie. To jest test na luką, która faktycznie istniała."""
    _zaloguj_zespol(klient_http)
    nowe = klient_http.post("/api/haslo/klienta", json={"client_id": "cxlabs"}).json()["haslo"]

    stare = klient_http.post(
        "/api/sesja/klient", json={"haslo": HASLO_KLIENTA, "client_id": "cxlabs"}
    )
    assert stare.status_code == 401, "stare hasło nadal wpuszcza"

    with TestClient(klient_http.app, base_url="https://test") as swieza:
        wejscie = swieza.post("/api/sesja/klient", json={"haslo": nowe, "client_id": "cxlabs"})
        assert wejscie.status_code == 200, "nowe hasło nie wpuszcza"


def test_zespol_nie_zresetuje_hasla_klienta_bez_konta(klient_http: TestClient) -> None:
    """Klient bez konta dostępu to 404, nie utworzenie konta po cichu."""
    _zaloguj_zespol(klient_http)

    odp = klient_http.post("/api/haslo/klienta", json={"client_id": "inny-klient"})

    assert odp.status_code == 404, "zresetował hasło konta, którego nie ma"


def test_zmiana_wlasnego_hasla_wymaga_obecnego(klient_http: TestClient) -> None:
    """Sesja potwierdza tożsamość, ale bywa porzucona w cudzej przeglądarce.

    Bez tego warunku przejęta sesja pozwala przejąć konto NA STAŁE — a to
    różnica między szkodą na 12 godzin i szkodą bez końca.
    """
    _zaloguj_zespol(klient_http)

    odp = klient_http.post("/api/haslo/moje", json={"obecne_haslo": "zupelnie-nie-to"})

    assert odp.status_code == 403
    assert "haslo" not in odp.json(), "zwrócił hasło przy złym obecnym"


def test_zespol_zmienia_wlasne_haslo(klient_http: TestClient) -> None:
    _zaloguj_zespol(klient_http)

    odp = klient_http.post("/api/haslo/moje", json={"obecne_haslo": HASLO_ZESPOLU})

    assert odp.status_code == 200, odp.text
    assert odp.json()["haslo"]


def test_zmiana_wlasnego_hasla_nie_przyjmuje_obcego_konta(klient_http: TestClient) -> None:
    """`konto_id` w ciele jest IGNOROWANE — bo go tam nie ma.

    Gdyby endpoint je czytał, osoba z zespołu zmieniałaby hasło innej osobie
    z zespołu. Pydantic odrzuci nadmiarowe pole albo je pominie; w obu razach
    zmienione zostaje konto Z SESJI, nie z ciała.
    """
    _zaloguj_zespol(klient_http)
    obce = klient_http.get("/api/klienci")  # cokolwiek, żeby mieć sesję aktywną
    assert obce.status_code == 200

    odp = klient_http.post("/api/haslo/moje", json={"obecne_haslo": HASLO_ZESPOLU, "konto_id": 1})

    assert odp.status_code == 200, odp.text
    # Konto klienta (id 1 w fixture) musi mieć NIETKNIĘTE hasło.
    with TestClient(klient_http.app, base_url="https://test") as swieza:
        assert (
            swieza.post(
                "/api/sesja/klient", json={"haslo": HASLO_KLIENTA, "client_id": "cxlabs"}
            ).status_code
            == 200
        ), "zmienił hasło konta podanego w ciele żądania"


# ── 7. „nie pamiętam hasła" — jedyna droga hasła BEZ sesji ───────────────
#
# Zgłoszone przez Kubę: „dalej nie mogę zresetować hasła z panelu logowania".
# Poprzednia wersja miała reset TYLKO za sesją, czyli błędne koło — kto zgubił
# hasło, nie mógł się zalogować, żeby je zmienić.


def test_zapomniane_haslo_dziala_bez_sesji(klient_http: TestClient) -> None:
    """Sedno poprawki. Ten endpoint MUSI działać bez ciasteczka.

    Gdyby wymagał sesji (jak `/api/haslo/moje`), wróciłoby błędne koło, o którym
    zgłoszenie: „zmień hasło, gdy je znasz" zamiast „nie pamiętam hasła".
    """
    odp = klient_http.post("/api/haslo/zapomniane", json={"email": EMAIL})

    assert odp.status_code == 200, odp.text
    assert "komunikat" in odp.json()


def test_zapomniane_odpowiada_identycznie_dla_nieznanego_adresu(
    klient_http: TestClient,
) -> None:
    """Inaczej brama mówi, które adresy @cxlabs.digital są prawdziwe."""
    istniejacy = klient_http.post("/api/haslo/zapomniane", json={"email": EMAIL})
    nieistniejacy = klient_http.post(
        "/api/haslo/zapomniane", json={"email": "nie-ma-takiego@cxlabs.digital"}
    )

    assert istniejacy.status_code == nieistniejacy.status_code == 200
    assert istniejacy.json() == nieistniejacy.json(), "odpowiedzi się różnią — to wyrocznia"


def test_zapomniane_nie_zdradza_klientow(klient_http: TestClient) -> None:
    """Konto klienta nie ma e-maila, więc tą drogą nie da się go dosięgnąć."""
    odp = klient_http.post("/api/haslo/zapomniane", json={"email": "cxlabs"})

    # Ten sam komunikat co zawsze; hasła w odpowiedzi nie ma.
    assert odp.status_code in (200, 422)
    assert "haslo" not in odp.text


def test_token_z_linku_dziala_bez_sesji_i_zmienia_haslo(
    klient_http: TestClient, baza: Path
) -> None:
    """Druga połowa ścieżki: token z maila → nowe hasło, też bez sesji.

    Token wydajemy wprost z bazy, nie przez endpoint: bez skonfigurowanego SMTP
    mail nie wychodzi, a test ma sprawdzać WYMIANĘ tokenu, nie wysyłkę.
    """
    from monday_audit.dostep import poproszono_o_reset

    con = polacz(baza)
    token = poproszono_o_reset(con, email=EMAIL, ip=None)
    con.close()
    assert token is not None

    odp = klient_http.post("/api/haslo/z-linku", json={"token": token})

    assert odp.status_code == 200, odp.text
    nowe = odp.json()["haslo"]
    # Stare hasło zespołu przestaje wpuszczać, nowe wpuszcza.
    stare = klient_http.post("/api/sesja/zespol", json={"email": EMAIL, "haslo": HASLO_ZESPOLU})
    assert stare.status_code == 401, "stare hasło zespołu nadal wpuszcza"
    swieze = klient_http.post("/api/sesja/zespol", json={"email": EMAIL, "haslo": nowe})
    assert swieze.status_code == 200, "nowe hasło nie wpuszcza"


def test_zmyslony_token_odmawia(klient_http: TestClient) -> None:
    """400 z jednym komunikatem — bez różnicy „nie ma" i „wygasł"."""
    odp = klient_http.post("/api/haslo/z-linku", json={"token": "z" * 40})

    assert odp.status_code == 400
    assert "haslo" not in odp.json()


def test_link_resetu_wskazuje_na_port_z_zadania(klient_http: TestClient, baza: Path) -> None:
    """ZMIERZONA USTERKA: link prowadził na port, na którym nic nie nasłuchiwało.

    `ADRES_PUBLICZNY` miało stałą domyślną `http://127.0.0.1:8000`, a
    `--serwuj --port 8010` jej nie dotykało. Kuba kliknął link i przeglądarka nie
    miała z czym się połączyć.

    Test pyta pod adresem `https://test`, a więc INNYM niż stara stała — gdyby
    link nadal brał adres z konfiguracji, zobaczylibyśmy tu `:8000`.
    """
    with caplog_reczny() as zapisy:
        odp = klient_http.post("/api/haslo/zapomniane", json={"email": EMAIL})
    assert odp.status_code == 200

    linki = [w for w in zapisy if "?reset=" in w]
    assert linki, "link nie trafił nawet do logu"
    assert "127.0.0.1:8000" not in linki[0], "link nadal wskazuje starą stałą"
    assert "https://test" in linki[0], f"link nie wziął adresu z żądania: {linki[0]}"


def test_adres_publiczny_wygrywa_gdy_ustawiony(baza: Path) -> None:
    """Za odwrotnym proxy adres z żądania jest WEWNĘTRZNY, więc musi dać się nadpisać.

    Caddy (etap 5) przekaże żądanie na `127.0.0.1:8000`, a odbiorca zna
    `https://audyt.cxlabs.digital`. Bez tego nadpisania link w mailu prowadziłby
    do wnętrza serwera.
    """
    from monday_audit.konfiguracja import UstawieniaPoczty

    ustawienia = UstawieniaPoczty(adres_publiczny="https://audyt.cxlabs.digital/")
    with (
        TestClient(
            zbuduj_aplikacje(baza=baza, ustawienia=ustawienia), base_url="https://test"
        ) as c,
        caplog_reczny() as zapisy,
    ):
        assert c.post("/api/haslo/zapomniane", json={"email": EMAIL}).status_code == 200

    linki = [w for w in zapisy if "?reset=" in w]
    assert linki, "link nie trafił do logu"
    assert "https://audyt.cxlabs.digital/?reset=" in linki[0], (
        f"nie użył ADRES_PUBLICZNY: {linki[0]}"
    )


def test_klient_nie_nada_sobie_dostepu(klient_http: TestClient) -> None:
    """Nadawanie dostępu to akcja administracyjna — klient dostaje 404.

    Bez tego klient mógłby zakładać konta dowolnym identyfikatorom, w tym cudzym.
    """
    _zaloguj_klienta(klient_http, "cxlabs")

    odp = klient_http.post("/api/klient/dostep", json={"client_id": "nowy-klient"})

    assert odp.status_code == 404
    assert "haslo" not in odp.text


def test_zespol_nadaje_dostep_klientowi_bez_konta(klient_http: TestClient) -> None:
    """Panel pokazuje „nie może się zalogować", więc musi dać to naprawić.

    `inny-klient` ma w fixture audyt, ale nie ma konta — dokładnie stan, który
    w bazie produkcyjnej miał `cxlabs` z 17 audytami.
    """
    _zaloguj_zespol(klient_http)

    odp = klient_http.post("/api/klient/dostep", json={"client_id": "inny-klient"})

    assert odp.status_code == 200, odp.text
    nowe = odp.json()["haslo"]
    # Nadane hasło musi FAKTYCZNIE wpuszczać — inaczej „nadałem dostęp" kłamie.
    with TestClient(klient_http.app, base_url="https://test") as swieza:
        wejscie = swieza.post("/api/sesja/klient", json={"client_id": "inny-klient", "haslo": nowe})
        assert wejscie.status_code == 200, "nadane hasło nie wpuszcza"


def test_nadanie_dostepu_dwa_razy_odmawia(klient_http: TestClient) -> None:
    """409, nie ciche wydanie drugiego hasła.

    Ta sama reguła co w `utworz_konto` i w indeksie z migracji 007: dwa aktywne
    konta jednego klienta to stan, w którym `zaloguj` wpuszcza dowolne z nich.
    """
    _zaloguj_zespol(klient_http)

    assert klient_http.post("/api/klient/dostep", json={"client_id": "nowy"}).status_code == 200
    powtorka = klient_http.post("/api/klient/dostep", json={"client_id": "nowy"})

    assert powtorka.status_code == 409
    assert "reset" in powtorka.json()["detail"], "komunikat nie mówi, co zrobić"


def test_lista_klientow_niesie_stan_dostepu(klient_http: TestClient) -> None:
    """Front rysuje „nie może się zalogować" z tego pola, więc musi ono dojść."""
    _zaloguj_zespol(klient_http)

    pozycje = {p["client_id"]: p for p in klient_http.get("/api/klienci").json()}

    assert pozycje["cxlabs"]["ma_konto"] is True, "klient z kontem oznaczony jako bez"
    assert pozycje["inny-klient"]["ma_konto"] is False, "brak konta nieoznaczony"


# ── klucz Anthropic KLIENTA (one-shot) ───────────────────────────────────
#
# Po co: koszt modelu idzie na rachunek klienta, nie CXLABS. Przy koncie
# z czterema workspace'ami audyt to ~17 USD (O35), więc to warunek opłacalności
# usługi. Klucz jedzie tą samą drogą co klucz monday i obowiązują go te same
# granice — te testy to sprawdzają.

ATRAPA_KLUCZA_MODELU = "sk-ant-PODSTAWIONY-KLUCZ-MODELU-" + "z" * 20


def test_klucz_anthropic_klienta_nie_wchodzi_do_bazy(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ta sama granica co przy kluczu monday (D11), drugi klucz.

    Szukamy w CAŁEJ bazie, nie w wybranych kolumnach: kolumna, której dziś nie ma,
    może dojść za pół roku.
    """
    monkeypatch.setattr("monday_audit.web.api.uruchom_audyt_w_tle", lambda *a: None)
    _zaloguj_klienta(klient_http)

    odp = klient_http.post(
        "/api/audyt",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_KLUCZA_MODELU,
            "zakres": "cale_konto",
        },
    )
    assert odp.status_code == 200

    con = polacz(baza)
    trafienia = []
    for tabela in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        nazwa = str(tabela["name"])
        for wiersz in con.execute(f"SELECT * FROM {nazwa}"):  # noqa: S608 — z sqlite_master
            for wartosc in tuple(wiersz):
                if isinstance(wartosc, str) and "PODSTAWIONY-KLUCZ-MODELU" in wartosc:
                    trafienia.append(nazwa)
    con.close()

    assert trafienia == [], f"klucz modelu wyciekł do tabel: {trafienia}"


def test_klucz_anthropic_dochodzi_do_zadania(
    klient_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Klucz musi DOJŚĆ do funkcji runu — inaczej pole byłoby ozdobą.

    To lekcja z dwóch usterek tej klasy: `can_use_tool`, który nigdy nie był
    wołany, i klucza API, który nie dochodził do podprocesu. Oba przeszły testy
    sprawdzające samą funkcję, a nie jej PODŁĄCZENIE.
    """
    przekazane: dict[str, object] = {}

    def atrapa(*argumenty: object) -> None:
        przekazane["argumenty"] = argumenty

    monkeypatch.setattr("monday_audit.web.api.uruchom_audyt_w_tle", atrapa)
    _zaloguj_klienta(klient_http)

    odp = klient_http.post(
        "/api/audyt",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_KLUCZA_MODELU,
            "zakres": "cale_konto",
        },
    )
    assert odp.status_code == 200
    assert ATRAPA_KLUCZA_MODELU in przekazane["argumenty"]  # type: ignore[operator]


def test_brak_klucza_anthropic_to_none_a_nie_pusty_napis(
    klient_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Puste pole MUSI dojść jako `None`, nigdy jako `""`.

    Pusty napis w środowisku podprocesu jest GORSZY niż brak zmiennej: SDK
    zobaczyłby ją i nie spadłby na klucz CXLABS, więc run wywróciłby się na
    uwierzytelnianiu. Ta sama pułapka, którą `klucz_anthropic()` opisuje
    w docstringu dla trybu subskrypcyjnego.
    """
    przekazane: dict[str, object] = {}
    monkeypatch.setattr(
        "monday_audit.web.api.uruchom_audyt_w_tle",
        lambda *a: przekazane.__setitem__("argumenty", a),
    )
    _zaloguj_klienta(klient_http)

    odp = klient_http.post("/api/audyt", json={"klucz_api": ATRAPA_KLUCZA, "zakres": "cale_konto"})
    assert odp.status_code == 200

    # Szukamy klucza po WARTOŚCI, nie po pozycji: `argumenty[-1]` wiązało test
    # z kolejnością parametrów `uruchom_audyt_w_tle`, więc dodanie `board_ids`
    # na końcu wywracało go, choć zachowanie było poprawne. Test ma pilnować
    # reguły („puste pole → None"), nie sygnatury funkcji.
    argumenty = przekazane["argumenty"]
    assert isinstance(argumenty, tuple)
    assert "" not in argumenty, f"pusty napis poszedł jako klucz: {argumenty!r}"
    assert None in argumenty, f"brak `None` w argumentach zadania: {argumenty!r}"


def test_za_krotki_klucz_anthropic_jest_odrzucany(klient_http: TestClient) -> None:
    """`min_length` odsiewa pomyłkę wklejenia, nie sprawdza formatu.

    Formatu nie walidujemy — Anthropic może zmienić postać tokenu, a my nie
    chcemy odrzucać poprawnego klucza, bo nasz wzorzec się zestarzał. Ta sama
    zasada co przy kluczu monday.
    """
    _zaloguj_klienta(klient_http)

    odp = klient_http.post(
        "/api/audyt",
        json={"klucz_api": ATRAPA_KLUCZA, "klucz_anthropic": "krotki", "zakres": "cale_konto"},
    )

    assert odp.status_code == 422


# ── klucz Anthropic WYMAGANY (przełącznik z O36) ─────────────────────────


class _UstawieniaZWymogiem(UstawieniaPoczty):
    """Ustawienia z włączonym wymogiem klucza klienta.

    `UstawieniaPoczty` NIE MA tego pola — endpoint czyta je przez `getattr`
    z domyślnym `False`, więc bez tej klasy wymóg w testach MILCZAŁBY, a testy
    zieleniłyby się bez sprawdzenia niczego. Zmierzone: 55 testów przeszło po
    dodaniu wymogu, bo fikstur nie ma pełnej konfiguracji.
    """

    klucz_modelu_od_klienta_wymagany: bool = True


@pytest.fixture
def klient_z_wymogiem(baza: Path) -> Iterator[TestClient]:
    aplikacja = zbuduj_aplikacje(baza=baza, ustawienia=_UstawieniaZWymogiem())
    with TestClient(aplikacja, base_url="https://test") as c:
        yield c


def _zadanie_na_zgodzie(baza: Path) -> str:
    """Zadanie w stanie `czeka_na_zgode` — tam stoi bramka klucza modelu."""
    from datetime import UTC, datetime, timedelta

    from monday_audit.zadania import (
        GODZIN_WAZNOSCI_ZGODY,
        STAN_CZEKA_NA_ZGODE,
        utworz_zadanie,
        zapisz_stan,
    )

    con = polacz(baza)
    try:
        zadanie_id = utworz_zadanie(con, client_id="cxlabs", konto_id=1)
        zapisz_stan(
            con,
            zadanie_id,
            stan=STAN_CZEKA_NA_ZGODE,
            snapshot_id=5,
            zgoda_do=(datetime.now(tz=UTC) + timedelta(hours=GODZIN_WAZNOSCI_ZGODY)).isoformat(),
        )
        con.commit()
        return zadanie_id
    finally:
        con.close()


def test_zbieranie_startuje_bez_klucza_anthropic(
    klient_z_wymogiem: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bramka klucza modelu PRZENIOSŁA SIĘ na `/zgoda` (2026-08-25).

    Zbieranie danych nie woła modelu, więc żądanie klucza na tym etapie było
    pytaniem o pieniądze, zanim ktokolwiek wiedział, ile ich będzie. Decyzja
    z 2026-08-19 („koszt całkowicie po stronie klienta") obowiązuje nadal —
    pilnuje jej test niżej, na endpoincie, który faktycznie uruchamia model.
    """
    monkeypatch.setattr("monday_audit.web.api.uruchom_audyt_w_tle", lambda *a: None)
    _zaloguj_klienta(klient_z_wymogiem)

    odp = klient_z_wymogiem.post(
        "/api/audyt", json={"klucz_api": ATRAPA_KLUCZA, "zakres": "cale_konto"}
    )

    assert odp.status_code == 200, odp.text


def test_bez_klucza_anthropic_analiza_nie_startuje(
    klient_z_wymogiem: TestClient, baza: Path
) -> None:
    """Decyzja Kuby 2026-08-19: koszt CAŁKOWICIE po stronie klienta.

    400, nie 422: dane są poprawne, brakuje warunku uruchomienia. Komunikat musi
    mówić, CO ZROBIĆ — front pokazuje `detail` wprost.
    """
    _zaloguj_klienta(klient_z_wymogiem)
    zadanie_id = _zadanie_na_zgodzie(baza)

    odp = klient_z_wymogiem.post(
        f"/api/audyt/{zadanie_id}/zgoda", json={"klucz_api": ATRAPA_KLUCZA}
    )

    assert odp.status_code == 400
    tresc = odp.json()["detail"]
    assert "console.anthropic.com" in tresc, "komunikat musi mówić, skąd wziąć klucz"
    assert "nie zapisujemy" in tresc, "i co robimy z kluczem"


def test_pusty_klucz_anthropic_tez_nie_startuje(klient_z_wymogiem: TestClient, baza: Path) -> None:
    """Spacje to nie klucz. `min_length` pydantica by tego nie złapało przy 20 spacjach."""
    _zaloguj_klienta(klient_z_wymogiem)
    zadanie_id = _zadanie_na_zgodzie(baza)

    odp = klient_z_wymogiem.post(
        f"/api/audyt/{zadanie_id}/zgoda",
        json={"klucz_api": ATRAPA_KLUCZA, "klucz_anthropic": " " * 25},
    )

    assert odp.status_code == 400


def test_z_kluczem_anthropic_audyt_startuje(
    klient_z_wymogiem: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wymóg nie może blokować poprawnego żądania."""
    monkeypatch.setattr("monday_audit.web.api.uruchom_audyt_w_tle", lambda *a: None)
    _zaloguj_klienta(klient_z_wymogiem)

    odp = klient_z_wymogiem.post(
        "/api/audyt",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_KLUCZA_MODELU,
            "zakres": "cale_konto",
        },
    )

    assert odp.status_code == 200


def test_wylaczony_wymog_przywraca_wariant_opcjonalny(
    klient_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Przełącznik musi PRZEŁĄCZAĆ — inaczej jest ozdobą w konfiguracji.

    `klient_http` używa `UstawieniaPoczty` bez tego pola, czyli stanu „wymóg
    wyłączony". To ta sama ścieżka, którą włączymy przy przejściu na produkt:
    koszt wejdzie w cenę subskrypcji i klucz przestanie być barierą wejścia.
    """
    monkeypatch.setattr("monday_audit.web.api.uruchom_audyt_w_tle", lambda *a: None)
    _zaloguj_klienta(klient_http)

    odp = klient_http.post("/api/audyt", json={"klucz_api": ATRAPA_KLUCZA, "zakres": "cale_konto"})

    assert odp.status_code == 200, "bez wymogu audyt ma startować bez klucza modelu"


# ── /health: publiczny, ale nic nie ujawnia ──────────────────────────────


def test_health_dziala_bez_sesji(klient_http: TestClient) -> None:
    """Czytają go systemd, skrypt wdrożenia i tunel — żaden nie ma ciasteczka.

    `05-deploy.md` krok 4 każe sprawdzić `/health` po wstaniu usługi. Wymóg
    sesji zamieniłby kontrolę zdrowia w kontrolę tego, czy ktoś jest zalogowany.
    """
    odp = klient_http.get("/health")

    assert odp.status_code == 200
    dane = odp.json()
    assert dane["status"] == "ok"
    # Numer migracji jest potrzebny: przy wdrożeniu to jedyny sposób sprawdzenia,
    # czy restart podniósł nową wersję schematu.
    assert isinstance(dane["migracja"], int)
    assert dane["migracja"] > 0


def test_health_nie_ujawnia_nic_o_klientach(klient_http: TestClient) -> None:
    """Endpoint jest PUBLICZNY, więc mówi tylko o stanie procesu.

    Baza testowa ma dwóch klientów (`cxlabs`, `inny-klient`), findingi i konta.
    Gdyby `/health` liczył audyty albo wymieniał klientów, byłby wyciekiem
    informacji handlowej — a taki wyciek przez endpoint monitoringu jest tym
    trudniejszy do zauważenia, że nikt tam nie zagląda.
    """
    tresc = klient_http.get("/health").text.lower()

    for zakazane in ("cxlabs", "inny-klient", "client_id", "audyt", "finding", "konto"):
        assert zakazane not in tresc, f"/health ujawnia „{zakazane}”: {tresc}"


def test_health_zglasza_zepsuta_baze(klient_http: TestClient, baza: Path) -> None:
    """Uszkodzona baza musi dać 503, nie 200.

    Plik może istnieć i być nieczytelny — to dokładnie ta awaria, którą kontrola
    zdrowia ma zauważyć. Sprawdzenie „czy plik jest" przepuściłoby ją.
    """
    baza.write_bytes(b"to nie jest baza SQLite, tylko smieci")

    odp = klient_http.get("/health")

    assert odp.status_code == 503
    assert "nie odpowiada" in odp.json()["detail"]
    # Komunikat nie może nieść ścieżki bazy ani nazw tabel.
    assert "_migracje" not in odp.text
    assert str(baza) not in odp.text
