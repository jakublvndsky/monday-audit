"""Collector tablic przeciwko prawdziwemu API — warstwa 2 z 04-test.md.

**WYŁĄCZNIE konto CXLABS. Nigdy konto klienta.**

    uv run pytest -m integracyjny

ZAKRES TEGO TESTU TO JEDNA TABLICA, wskazana przez Kubę: 5097387646.
Nie workspace, nie konto — jedna tablica. Test sprawdza między innymi to,
że run faktycznie nie wyszedł poza nią, bo przy audycie cudzego konta
„dotknęliśmy dokładnie tego, co wskazałeś" musi być weryfikowalne,
a nie deklarowane.

Zapytanie jest odczytem: `przygotuj_zapytanie()` odrzuca mutacje na wejściu
do jedynej ścieżki, którą kod gada z API (D6).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.klient import MondayClient
from monday_audit.konto import Zakres
from monday_audit.osoby import DLUGOSC_HASHA
from monday_audit.tablice import zbierz_tablice

pytestmark = [
    pytest.mark.integracyjny,
    pytest.mark.skipif(
        not os.environ.get("MONDAY_TOKEN"),
        reason="brak MONDAY_TOKEN — warstwa 2 wymaga tokena konta CXLABS",
    ),
]

# Tablica wskazana przez właściciela konta. Rozpoznana 2026-07-30:
# „Lista pomysłów Agentów AI", workspace 6576039, active, public.
TABLICA = "5097387646"
WORKSPACE = "6576039"

SOL_TESTOWA = b"sol-tylko-do-testu-integracyjnego-nie-produkcyjna"
KLIENT = "cxlabs-test"


@pytest.fixture
def rejestr(tmp_path: Path) -> Iterator[RejestrWywolan]:
    con = polacz(tmp_path / "integracyjny.db")
    zastosuj_migracje(con)
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) "
        "VALUES ('test-tablice', 'cxlabs', 'w_toku', '2026-07-30T10:00:00+00:00')"
    )
    con.commit()
    yield RejestrWywolan(con, "test-tablice")
    con.close()


async def test_zakres_jednej_tablicy_nie_wychodzi_poza_nia(rejestr: RejestrWywolan) -> None:
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=6) as klient:
        wynik = await zbierz_tablice(
            klient, Zakres.tablice(TABLICA), client_id=KLIENT, sol=SOL_TESTOWA
        )

    # To jest sedno: dokładnie jedna tablica i dokładnie ta wskazana.
    assert [t.board_id for t in wynik.tablice] == [TABLICA]
    assert wynik.tablice[0].workspace_id == WORKSPACE

    tablica = wynik.tablice[0]
    assert tablica.nazwa, "tablica ma nazwę"
    assert tablica.state == "active"
    assert tablica.kolumny, "kolumny są potrzebne detektorom z 3.9"
    assert all(set(k) == {"id", "title", "type"} for k in tablica.kolumny)


async def test_granica_d5_zaden_item_nie_wchodzi(rejestr: RejestrWywolan) -> None:
    """`items_count` to granica. W payloadzie nie ma treści itemów ani kolumn."""
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=6) as klient:
        wynik = await zbierz_tablice(
            klient, Zakres.tablice(TABLICA), client_id=KLIENT, sol=SOL_TESTOWA
        )

    tablica = wynik.tablice[0]
    assert tablica.items_count is not None, "bez items_count nie ocenimy objętości (O4)"
    assert wynik.discovery["items_count_dostepne"] is True

    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)
    for zabronione in ('"items"', '"column_values"', '"updates"', '"items_page"'):
        assert zabronione not in payload, f"{zabronione} to zejście na itemy — D5 tego zabrania"


async def test_owners_i_subscribers_sa_pseudonimami(rejestr: RejestrWywolan) -> None:
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=6) as klient:
        wynik = await zbierz_tablice(
            klient, Zakres.tablice(TABLICA), client_id=KLIENT, sol=SOL_TESTOWA
        )

    tablica = wynik.tablice[0]
    wzorzec = re.compile(rf"^[0-9a-f]{{{DLUGOSC_HASHA}}}$")

    for pseudonim in (*tablica.owners, *tablica.subscribers):
        assert wzorzec.match(pseudonim), "owners i subscribers muszą być hashami, nie id"

    # Surowe identyfikatory monday są liczbami — żadna z nich nie może
    # zostać w payloadzie.
    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)
    assert re.search(r'"owners": \["\d+"', payload) is None
    assert re.search(r'"subscribers": \["\d+"', payload) is None


async def test_wywolania_trafiaja_do_tabeli(rejestr: RejestrWywolan) -> None:
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=6) as klient:
        await zbierz_tablice(klient, Zakres.tablice(TABLICA), client_id=KLIENT, sol=SOL_TESTOWA)
        wykonanych = klient.liczba_wywolan

    wiersze = rejestr._con.execute(
        "SELECT complexity FROM wywolania WHERE narzedzie = 'graphql:boards' ORDER BY id"
    ).fetchall()
    koszty = [w["complexity"] for w in wiersze]

    assert len(koszty) == wykonanych
    # Każde udane wywołanie zostawia complexity. Konwencja z 3.2: NULL znaczy
    # próbę nieudaną, więc brak wartości byłby tu błędem.
    assert all(k is not None for k in koszty)
    # Pierwsza strona ma realny koszt. Kolejna, pusta strona domykająca
    # paginację może kosztować 0 — zmierzone na CXLABS, dlatego nie żądamy
    # dodatniego kosztu od wszystkich wywołań.
    assert koszty[0] > 0
