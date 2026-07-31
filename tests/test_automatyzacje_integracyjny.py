"""Automatyzacje przeciwko prawdziwemu API — warstwa 2 z 04-test.md.

**WYŁĄCZNIE konto CXLABS, wyłącznie workspace 6576039.**

    uv run pytest -m integracyjny

Wolumen jest tu ryzykiem, nie wygodą, więc test jest policzony:
statystyki konta (3 wywołania) + lista tablic zawężona do jednego
workspace (1 wywołanie, limit 5) + najwyżej 3 sondy. Razem ~7 wywołań
i ani jedno zapytanie nie wychodzi poza wskazany workspace.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from monday_audit.automatyzacje import sonduj_tablice, statystyki_konta, zbierz_automatyzacje
from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.klient import MondayClient

# Sekrety wstawia fixture `zrodlo_sekretow` z conftest.py — przez
# `konfiguracja.wczytaj()`, czyli tą samą drogą co program (D12).
# Brak sekretów pomija te testy, nie wywraca ich.
pytestmark = pytest.mark.integracyjny

WORKSPACE = "6576039"
TABLICA = "5097387646"

# Lista tablic zawężona do jednego workspace i do pięciu pozycji.
TABLICE_WORKSPACE = """
query ($p: Int!, $ws: [ID!]) {
  boards (limit: 5, page: $p, state: active, workspace_ids: $ws) { id }
}
"""


@pytest.fixture
def rejestr(tmp_path: Path) -> Iterator[RejestrWywolan]:
    con = polacz(tmp_path / "integracyjny.db")
    zastosuj_migracje(con)
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) "
        "VALUES ('test-automaty', 'cxlabs', 'w_toku', '2026-07-30T10:00:00+00:00')"
    )
    con.commit()
    yield RejestrWywolan(con, "test-automaty")
    con.close()


async def test_o1_liczba_uruchomien_jest_dostepna(rejestr: RejestrWywolan) -> None:
    """O1 pytało, czy API zwraca liczbę uruchomień automatyzacji. Zwraca.

    Jedno wywołanie, trzy liczby, zero danych o tablicach — więc mimo że
    zapytanie jest z natury na poziomie konta, nie wylicza ani nie ujawnia
    niczego z innych workspace'ów.
    """
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=4) as klient:
        liczby, discovery = await statystyki_konta(klient)

        assert klient.liczba_wywolan == 1

    assert discovery["uruchomienia_dostepne"] is True
    assert liczby["razem"] is not None
    assert liczby["razem"] >= 0
    assert liczby["sukces"] is not None
    assert liczby["bledow"] is not None
    assert liczby["razem"] >= liczby["sukces"]


async def test_sonda_wskazanej_tablicy_z_oknem_90_dni(rejestr: RejestrWywolan) -> None:
    """Sprawdza też, czy `dateRange` przyjmuje daty w formacie ISO.

    To jedyna ścieżka per tablica: `trigger_events.filters.boardId` jest
    Stringiem, więc przyjmuje 10-cyfrowy identyfikator — w przeciwieństwie
    do zepsutego Int32 w `account_trigger_statistics`.
    """
    token = os.environ["MONDAY_TOKEN"]
    dzis = datetime.now(tz=UTC).date()
    od = (dzis - timedelta(days=90)).isoformat()

    async with MondayClient(token, rejestr, budzet_wywolan=4) as klient:
        sondy, pominietych = await sonduj_tablice(klient, [TABLICA], od=od, do=dzis.isoformat())

        assert klient.liczba_wywolan == 1

    assert pominietych == 0
    assert sondy[0].board_id == TABLICA
    assert sondy[0].zdarzen >= 0
    # Strona pełna znaczy „jest więcej", więc przy 200 zdarzeniach liczba
    # nie jest kompletna i musi to być widoczne.
    assert sondy[0].strona_pelna == (sondy[0].zdarzen >= 200)


async def test_sufit_sond_dziala_na_zywych_danych(rejestr: RejestrWywolan) -> None:
    """Workspace ma 105 aktywnych tablic. Sufit ma nas przed tym obronić."""
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=8) as klient:
        dane = await klient.query(TABLICE_WORKSPACE, {"p": 1, "ws": [WORKSPACE]}, etykieta="boards")
        board_ids = [t["id"] for t in dane["boards"]]
        assert len(board_ids) == 5, "workspace 6576039 ma więcej niż 5 aktywnych tablic"

        przed = klient.liczba_wywolan
        sondy, pominietych = await sonduj_tablice(klient, board_ids, maks_sond=2)
        po_sondach = klient.liczba_wywolan - przed

    assert len(sondy) == 2
    assert pominietych == 3
    assert po_sondach == 2, "sufit musi ograniczać liczbę WYWOŁAŃ, nie tylko wyników"


async def test_pelny_zbior_zapisuje_ograniczenia_api(rejestr: RejestrWywolan) -> None:
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=8) as klient:
        wynik = await zbierz_automatyzacje(klient, board_ids=[TABLICA], maks_sond=1)

    fragment = wynik.do_snapshotu()
    discovery = fragment["discovery"]

    # Detektory muszą wiedzieć, że sygnał jest zwężony — inaczej AUTOMATION_DEAD
    # zmyśli „martwą automatyzację" z braku danych.
    assert discovery["uruchomienia_dostepne"] is True
    assert discovery["lista_automatyzacji_dostepna"] is False
    assert discovery["filtr_board_id_zepsuty_int32"] is True
    assert discovery["atrybucja_per_tablica"] is True
    assert fragment["podsumowanie"]["tablic_sondowanych"] == 1
