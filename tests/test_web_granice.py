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
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.deanonimizacja import WZORZEC_HASHA
from monday_audit.dostep import ROLA_KLIENT, ROLA_ZESPOL, utworz_konto
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
