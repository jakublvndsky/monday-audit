"""Testy ręcznego uruchomienia (etap 3.8), warstwa 1 z 04-test.md.

Najważniejsze: brak sekretu w środowisku ma dać jasny komunikat, a nie
sięgnięcie po plik `.env`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.cli import eksportuj, zbuduj_parser, zbuduj_zakres
from monday_audit.logi import MAKS_STRON_LOGOW, TOP_PO_ITEMACH, Z_OGONA
from monday_audit.przebieg import RaportRunu, zapisz_snapshot


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


# ── zakres z argumentów ──────────────────────────────────────────────────


def test_cale_konto_bez_identyfikatorow() -> None:
    assert zbuduj_zakres("cale_konto", []).typ == "cale_konto"


def test_cale_konto_z_id_jest_bledem() -> None:
    """Zakres całego konta nie przyjmuje listy — to dwa różne tryby."""
    with pytest.raises(SystemExit, match="nie przyjmuje --id"):
        zbuduj_zakres("cale_konto", ["6576039"])


@pytest.mark.parametrize("nazwa", ["workspace", "tablice"])
def test_zawezony_zakres_wymaga_id(nazwa: str) -> None:
    with pytest.raises(SystemExit, match="wymaga co najmniej jednego --id"):
        zbuduj_zakres(nazwa, [])


def test_workspace_i_tablice_ida_do_wlasciwych_pol() -> None:
    ws = zbuduj_zakres("workspace", ["6576039"])
    tab = zbuduj_zakres("tablice", ["5097387646", "5099672900"])

    assert ws.workspace_ids == ("6576039",) and ws.board_ids == ()
    assert tab.board_ids == ("5097387646", "5099672900") and tab.workspace_ids == ()


def test_parser_zbiera_wiele_id() -> None:
    argumenty = zbuduj_parser().parse_args(
        ["--klient", "cxlabs", "--zakres", "tablice", "--id", "1", "--id", "2"]
    )

    assert argumenty.id == ["1", "2"]
    assert argumenty.klient == "cxlabs"


def test_parser_ma_sufity_z_wartosciami_domyslnymi() -> None:
    argumenty = zbuduj_parser().parse_args(["--klient", "x", "--zakres", "cale_konto"])

    assert argumenty.maks_sond == 10
    assert argumenty.top_logow == TOP_PO_ITEMACH == 30
    assert argumenty.z_ogona == Z_OGONA == 20
    assert argumenty.maks_stron_logow == MAKS_STRON_LOGOW
    assert argumenty.dni_okna == 90


def test_parser_odrzuca_nieznany_zakres() -> None:
    with pytest.raises(SystemExit):
        zbuduj_parser().parse_args(["--klient", "x", "--zakres", "wymyslony"])


# ── sekrety wyłącznie ze środowiska ──────────────────────────────────────


async def test_brak_tokena_daje_jasny_komunikat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bez tokena program tłumaczy, co zrobić — nie szuka pliku .env."""
    from monday_audit.cli import uruchom

    monkeypatch.delenv("MONDAY_TOKEN", raising=False)
    argumenty = zbuduj_parser().parse_args(
        ["--klient", "cxlabs", "--zakres", "cale_konto", "--baza", str(tmp_path / "b.db")]
    )

    with pytest.raises(SystemExit, match=r"nie czyta pliku \.env"):
        await uruchom(argumenty)


async def test_brak_soli_przerywa_po_tokenie(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from monday_audit.cli import uruchom
    from monday_audit.osoby import PseudonimizacjaError

    monkeypatch.setenv("MONDAY_TOKEN", "atrapa-tokena")
    monkeypatch.delenv("SOL_PSEUDONIMIZACJI", raising=False)
    argumenty = zbuduj_parser().parse_args(
        ["--klient", "cxlabs", "--zakres", "cale_konto", "--baza", str(tmp_path / "b.db")]
    )

    with pytest.raises(PseudonimizacjaError, match="brak SOL_PSEUDONIMIZACJI"):
        await uruchom(argumenty)


def test_modul_nie_otwiera_pliku_env() -> None:
    """Gwarancja mechaniczna, nie obietnica.

    Sekret ze środowiska procesu jest w porządku (`os.environ`), sięganie
    po PLIK `.env` nie jest. Test sprawdza to drugie: brak nazwy pliku jako
    literału i brak bibliotek, które go czytają.
    """
    from monday_audit import cli

    zrodlo = Path(cli.__file__).read_text(encoding="utf-8")

    assert '".env"' not in zrodlo, "nazwa pliku .env jako literał"
    assert "'.env'" not in zrodlo, "nazwa pliku .env jako literał"
    assert "env_file" not in zrodlo, "env_file czyta .env (np. pydantic-settings)"
    assert "dotenv" not in zrodlo, "python-dotenv czyta .env"
    # A sekret ze środowiska ma tu być — to jest właśnie dozwolona droga.
    assert "os.environ" in zrodlo


# ── eksport do przeglądu ─────────────────────────────────────────────────


def test_eksport_zapisuje_czytelny_json(con: sqlite3.Connection, tmp_path: Path) -> None:
    snapshot_id = zapisz_snapshot(
        con,
        client_id="cxlabs",
        payload={"meta": {"a": 1}, "tablice": {"tablice": []}},
        run_at="2026-07-31T09:00:00+00:00",
    )
    con.close()
    raport = RaportRunu(
        run_id="r1",
        snapshot_id=snapshot_id,
        client_id="cxlabs",
        zakres="1 workspace'ów",
        wywolan=37,
        complexity=591_741,
        sekund=23.5,
        bajtow_payloadu=100,
        zastrzezenia=(),
        discovery={},
    )

    cel = eksportuj(raport, baza=tmp_path / "test.db", katalog=tmp_path / "snapshoty")

    assert cel.name == f"snapshot_{snapshot_id}_cxlabs.json"
    assert json.loads(cel.read_text(encoding="utf-8"))["meta"] == {"a": 1}
    # Wcięcia, bo ten plik czyta człowiek przy BRAMIE.
    assert "\n  " in cel.read_text(encoding="utf-8")
