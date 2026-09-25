"""Wstrzymanie nowych audytów w panelu (decyzja Kuby 2026-09-23, faza 5c).

Panel chodzi starą ścieżką i każdy audyt zapisuje na dysku snapshot oraz
tabelę mapowania z nazwiskami. Do fazy 6 nowe audyty są wstrzymane —
**w API, nie w JS**, bo `curl` nie widzi wyszarzonego przycisku.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.stary_panel.dostep import ROLA_KLIENT, utworz_konto
from monday_audit.stary_panel.web.api import POWOD_WSTRZYMANIA, zbuduj_aplikacje

HASLO = "test-haslo-klienta-1"


@pytest.fixture
def klient_http(tmp_path: Path) -> Iterator[TestClient]:
    sciezka = tmp_path / "web.db"
    con = polacz(sciezka)
    zastosuj_migracje(con)
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO, client_id="cxlabs")
    con.commit()
    con.close()
    with TestClient(zbuduj_aplikacje(baza=sciezka), base_url="https://test") as c:
        odp = c.post("/api/sesja/klient", json={"haslo": HASLO, "client_id": "cxlabs"})
        assert odp.status_code == 200, odp.text
        yield c


def test_przycisk_dostaje_powod_wstrzymania(klient_http: TestClient) -> None:
    dane = klient_http.get("/api/audyt/mozliwosc").json()

    assert dane["wolno"] is False
    assert dane["powod"] == POWOD_WSTRZYMANIA


def test_start_audytu_odmawia_503(klient_http: TestClient) -> None:
    odp = klient_http.post("/api/audyt", json={"klucz_api": "x" * 40, "zakres": "cale_konto"})

    assert odp.status_code == 503
    assert odp.json()["detail"] == POWOD_WSTRZYMANIA


def test_zgoda_na_czekajace_zadanie_tez_odmawia(klient_http: TestClient) -> None:
    """Zadanie zebrane przed wstrzymaniem nie może przejść do analizy
    tylnymi drzwiami — druga faza też zapisuje findingi przy snapshocie."""
    odp = klient_http.post(
        "/api/audyt/cokolwiek/zgoda",
        json={"klucz_api": "x" * 40, "workspace_ids": [], "board_ids": []},
    )

    assert odp.status_code == 503


def test_nic_nie_trafia_do_bazy(klient_http: TestClient, tmp_path: Path) -> None:
    klient_http.post("/api/audyt", json={"klucz_api": "x" * 40, "zakres": "cale_konto"})

    con = polacz(tmp_path / "web.db")
    try:
        assert con.execute("SELECT COUNT(*) FROM zadania").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
    finally:
        con.close()
