"""Activity logs przeciwko prawdziwemu API — warstwa 2 z 04-test.md.

**WYŁĄCZNIE konto CXLABS, wyłącznie workspace 6576039.**

    uv run pytest -m integracyjny

Policzone: lista czterech wskazanych tablic (1 wywołanie) + próbka top 2
i 1 z ogona (3 wywołania). Razem ~4 wywołania i ani jedno zapytanie poza
wskazanym workspace'em.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.klient import MondayClient
from monday_audit.konto import Zakres
from monday_audit.logi import na_iso, zbierz_logi
from monday_audit.osoby import DLUGOSC_HASHA
from monday_audit.tablice import zbierz_tablice

# Sekrety wstawia fixture `zrodlo_sekretow` z conftest.py — przez
# `konfiguracja.wczytaj()`, czyli tą samą drogą co program (D12).
# Brak sekretów pomija te testy, nie wywraca ich.
pytestmark = pytest.mark.integracyjny

WORKSPACE = "6576039"
# Tablice z workspace 6576039, rozpoznane 2026-07-30. Podane jawnie zamiast
# przelotu po 105 tablicach — to jest ten wolumen, którego nie bierzemy.
TABLICE = ("5097387646", "5099672900", "5099260890", "5099260855")

SOL_TESTOWA = b"sol-tylko-do-testu-integracyjnego-nie-produkcyjna"
KLIENT = "cxlabs-test"


@pytest.fixture
def rejestr(tmp_path: Path) -> Iterator[RejestrWywolan]:
    con = polacz(tmp_path / "integracyjny.db")
    zastosuj_migracje(con)
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) "
        "VALUES ('test-logi', 'cxlabs', 'w_toku', '2026-07-30T10:00:00+00:00')"
    )
    con.commit()
    yield RejestrWywolan(con, "test-logi")
    con.close()


async def test_sygnaly_aktywnosci_bez_tresci(rejestr: RejestrWywolan) -> None:
    """Kryterium 3.7: sampling działa, snapshot ma sygnały, zero treści."""
    token = os.environ["MONDAY_TOKEN"]
    dzis = datetime.now(tz=UTC)
    od = (dzis - timedelta(days=90)).isoformat()

    async with MondayClient(token, rejestr, budzet_wywolan=10) as klient:
        tablice = (
            await zbierz_tablice(
                klient, Zakres.tablice(*TABLICE), client_id=KLIENT, sol=SOL_TESTOWA
            )
        ).tablice
        assert tablice, "wskazane tablice muszą być widoczne dla tokena"

        wynik = await zbierz_logi(
            klient,
            tablice,
            client_id=KLIENT,
            sol=SOL_TESTOWA,
            znane_hashe=set(),
            od=od,
            do=dzis.isoformat(),
            top=2,
            z_ogona=1,
        )

    assert len(wynik.sygnaly) == 3, "top 2 + 1 z ogona"
    assert wynik.pominietych_tablic == len(tablice) - 3

    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)

    # Zero treści: pola z `data` nie mogą się pojawić pod żadną postacią.
    for zabronione in ("previous_value", "pulse_name", "column_title", '"data"', '"value"'):
        assert zabronione not in payload, f"{zabronione} to treść klienta — D5 tego zabrania"
    assert "@" not in payload


async def test_created_at_konwertuje_sie_na_sensowna_date(rejestr: RejestrWywolan) -> None:
    """Log zwraca liczbę jednostek 100 ns, nie datę ISO (O13).

    Bez tej konwersji okno 90 dni w ENGAGEMENT_DROP liczyłoby śmieci.
    """
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=8) as klient:
        tablice = (
            await zbierz_tablice(
                klient, Zakres.tablice("5099672900"), client_id=KLIENT, sol=SOL_TESTOWA
            )
        ).tablice

        wynik = await zbierz_logi(
            klient, tablice, client_id=KLIENT, sol=SOL_TESTOWA, top=1, z_ogona=0
        )

    sygnal = wynik.sygnaly[0]
    if sygnal.wpisow == 0:
        pytest.skip("tablica nie ma wpisów w logu — nie ma czego konwertować")

    najnowszy = sygnal.najnowszy_at
    najstarszy = sygnal.najstarszy_at
    assert najnowszy is not None
    assert najstarszy is not None

    for znacznik in (najnowszy, najstarszy):
        # Prawdziwa data, nie 17-cyfrowa liczba przepuszczona bez zmian.
        assert re.match(r"^20\d\d-\d\d-\d\dT", znacznik), znacznik
        rok = datetime.fromisoformat(znacznik).year
        assert 2020 <= rok <= 2030, f"rok {rok} spoza sensownego zakresu"

    assert najstarszy <= najnowszy


async def test_autorzy_sa_pseudonimami(rejestr: RejestrWywolan) -> None:
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=8) as klient:
        tablice = (
            await zbierz_tablice(
                klient, Zakres.tablice("5099672900"), client_id=KLIENT, sol=SOL_TESTOWA
            )
        ).tablice

        wynik = await zbierz_logi(
            klient, tablice, client_id=KLIENT, sol=SOL_TESTOWA, top=1, z_ogona=0
        )

    wzorzec = re.compile(rf"^[0-9a-f]{{{DLUGOSC_HASHA}}}$")
    for pseudonim in wynik.sygnaly[0].autorzy:
        assert wzorzec.match(pseudonim), "autor wpisu musi być hashem, nie user_id"


def test_konwersja_zgadza_sie_ze_zmierzonym_znacznikiem() -> None:
    """Wartość zmierzona na koncie CXLABS 2026-07-30, obok updated_at tablicy."""
    assert na_iso(17830789794688296) is not None
    assert str(na_iso(17830789794688296)).startswith("2026-07-03")
