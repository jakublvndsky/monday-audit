"""Rozpoznanie konta przeciwko prawdziwemu API — warstwa 2 z 04-test.md.

**WYŁĄCZNIE konto CXLABS. Nigdy konto klienta.**

    uv run pytest -m integracyjny

Kryterium z 03-build.md 3.3: „metadane konta w snapshocie, walidacja admina
działa". Test nie zakłada, czy token ma admina — sprawdza, że walidacja
zachowuje się zgodnie z tym, co token faktycznie ma. Dzięki temu przeżyje
nadanie Kubie uprawnień admina i nie trzeba go będzie wtedy poprawiać.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.klient import MondayClient
from monday_audit.konto import Zakres, ZakresError, rozpoznaj_konto

# Sekrety wstawia fixture `zrodlo_sekretow` z conftest.py — przez
# `konfiguracja.wczytaj()`, czyli tą samą drogą co program (D12).
# Brak sekretów pomija te testy, nie wywraca ich.
pytestmark = pytest.mark.integracyjny

PIERWSZY_WORKSPACE = "query { workspaces (limit: 1) { id } }"


@pytest.fixture
def rejestr(tmp_path: Path) -> Iterator[RejestrWywolan]:
    con = polacz(tmp_path / "integracyjny.db")
    zastosuj_migracje(con)
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) "
        "VALUES ('test-konto', 'cxlabs', 'w_toku', '2026-07-30T10:00:00+00:00')"
    )
    con.commit()
    yield RejestrWywolan(con, "test-konto")
    con.close()


def _polaczenie(rejestr: RejestrWywolan) -> sqlite3.Connection:
    return rejestr._con


async def test_rozpoznanie_konta_na_cxlabs(rejestr: RejestrWywolan) -> None:
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=6) as klient:
        workspace = (await klient.query(PIERWSZY_WORKSPACE, etykieta="ws"))["workspaces"][0]["id"]
        konto = await rozpoznaj_konto(klient, Zakres.workspace(workspace), dostosuj_budzet=False)

    assert konto.account_id
    assert konto.slug
    assert konto.zakres.workspace_ids == (str(workspace),)
    assert konto.pokrycie_pelne is False, "zakres zawężony nigdy nie jest pełnym pokryciem"

    # Zawężenie zakresu MUSI być widoczne w snapshocie — inaczej powstaje
    # audyt niepełny udający pełny.
    assert any("zawężony" in z for z in konto.zastrzezenia)

    payload = json.dumps(konto.do_snapshotu(), ensure_ascii=False)
    assert "@" not in payload, "żadnych adresów e-mail w snapshocie"

    wiersz = (
        _polaczenie(rejestr)
        .execute("SELECT complexity FROM wywolania WHERE narzedzie = 'graphql:konto'")
        .fetchone()
    )
    assert wiersz["complexity"] > 0, "rozpoznanie konta musi zostawić ślad w `wywolania`"


async def test_walidacja_zakresu_zgadza_sie_z_uprawnieniami(rejestr: RejestrWywolan) -> None:
    """Całe konto przechodzi wtedy i tylko wtedy, gdy token ma admina."""
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, rejestr, budzet_wywolan=6) as klient:
        workspace = (await klient.query(PIERWSZY_WORKSPACE, etykieta="ws"))["workspaces"][0]["id"]
        rozpoznane = await rozpoznaj_konto(
            klient, Zakres.workspace(workspace), dostosuj_budzet=False
        )

        if rozpoznane.is_admin:
            pelne = await rozpoznaj_konto(klient, Zakres.cale_konto(), dostosuj_budzet=False)
            assert pelne.pokrycie_pelne is True
        else:
            with pytest.raises(ZakresError, match="nie ma uprawnień admina"):
                await rozpoznaj_konto(klient, Zakres.cale_konto(), dostosuj_budzet=False)
