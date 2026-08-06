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
