"""Klient GraphQL przeciwko prawdziwemu API — warstwa 2 z 04-test.md.

**WYŁĄCZNIE konto CXLABS. Nigdy konto klienta.**

Domyślnie pomijane dwa razy: brak `MONDAY_TOKEN` pomija plik, a `make testy`
odcina marker `integracyjny`. Uruchomienie jest świadomą decyzją:

    uv run pytest -m integracyjny

Kryterium z 03-build.md 3.2: „klient przechodzi test na koncie CXLABS,
loguje complexity, a wymuszony limit faktycznie przerywa działanie".

Budżety są tu celowo skąpe — te testy zjadają wywołania z dziennego limitu
konta CXLABS jak każdy inny ruch.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.klient import BudzetWyczerpanyError, MondayClient

pytestmark = [
    pytest.mark.integracyjny,
    pytest.mark.skipif(
        not os.environ.get("MONDAY_TOKEN"),
        reason="brak MONDAY_TOKEN — warstwa 2 wymaga tokena konta CXLABS",
    ),
]

ZAPYTANIE_KONTO = "query { me { id is_admin account { id slug plan { tier } } } }"
ZAPYTANIE_TABLICE = "query ($p: Int!) { boards (limit: 2, page: $p, order_by: created_at) { id } }"


@pytest.fixture
def rejestr(tmp_path: Path) -> Iterator[RejestrWywolan]:
    """Prawdziwy rejestr na tymczasowej bazie — sprawdzamy też ścieżkę zapisu."""
    con = polacz(tmp_path / "integracyjny.db")
    zastosuj_migracje(con)
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) "
        "VALUES ('test-integracyjny', 'cxlabs', 'w_toku', '2026-07-30T10:00:00+00:00')"
    )
    con.commit()
    yield RejestrWywolan(con, "test-integracyjny")
    con.close()


def _polaczenie(rejestr: RejestrWywolan) -> sqlite3.Connection:
    return rejestr._con


async def test_konto_odpowiada_i_complexity_jest_logowane(rejestr: RejestrWywolan) -> None:
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=5) as klient:
        dane = await klient.query(ZAPYTANIE_KONTO, etykieta="konto")

        assert dane["me"]["id"]
        assert klient.complexity_suma > 0

    wiersz = (
        _polaczenie(rejestr)
        .execute("SELECT narzedzie, complexity FROM wywolania WHERE narzedzie = 'graphql:konto'")
        .fetchone()
    )
    assert wiersz["complexity"] > 0


async def test_paginacja_na_kolekcji_wiekszej_niz_strona(rejestr: RejestrWywolan) -> None:
    """Strona po 2 tablice, więc konto CXLABS na pewno da więcej niż jedną."""
    token = os.environ["MONDAY_TOKEN"]
    zebrane: list[str] = []

    async with MondayClient(token, rejestr, budzet_wywolan=5) as klient:
        async for tablica in klient.paginate(ZAPYTANIE_TABLICE, "boards", etykieta="boards"):
            zebrane.append(tablica["id"])
            if len(zebrane) >= 4:
                break

        assert klient.liczba_wywolan >= 2

    assert len(zebrane) == len(set(zebrane)), "paginacja powtórzyła tablice"


async def test_wymuszony_limit_przerywa_dzialanie(rejestr: RejestrWywolan) -> None:
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=1) as klient:
        await klient.query(ZAPYTANIE_KONTO, etykieta="konto")

        with pytest.raises(BudzetWyczerpanyError):
            await klient.query(ZAPYTANIE_KONTO, etykieta="konto")
