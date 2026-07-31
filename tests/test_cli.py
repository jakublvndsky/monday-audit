"""Testy ręcznego uruchomienia (etap 3.8), warstwa 1 z 04-test.md.

Najważniejsze: brak sekretu ma dać jasny komunikat mówiący, czego brakuje
i gdzie to wpisać. Same źródła sekretów i ich precedencja są sprawdzane
w `test_konfiguracja.py` — tutaj tylko styk z CLI.

Hermetyczność tych testów stoi na fixture `odetnij_env` z `conftest.py`:
bez niej znajdowałyby prawdziwy `.env` w roocie repo i przechodziły, nie
sprawdzając niczego.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.cli import eksportuj, ustal_baze, zbuduj_parser, zbuduj_zakres
from monday_audit.konfiguracja import Ustawienia
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
    # Sufity trzymają się stałych z `logi`, żeby CLI i biblioteka nie rozjechały
    # się po podniesieniu próbki (2026-07-31: 30+20 → 60+40, 5 → 10 stron).
    assert argumenty.top_logow == TOP_PO_ITEMACH == 60
    assert argumenty.z_ogona == Z_OGONA == 40
    assert argumenty.maks_stron_logow == MAKS_STRON_LOGOW == 10
    assert argumenty.dni_okna == 90
    assert argumenty.wszystkie_logi is False
    # Trzy `None`, bo źródłem jest konfiguracja albo plan konta, nie argparse.
    # Flaga z wartością domyślną przebijałaby `.env`, a przy budżecie
    # udawałaby hamulec, którego nikt nie zaciągnął.
    assert argumenty.baza is None
    assert argumenty.plik_env is None
    assert argumenty.budzet_wywolan is None


def test_wszystkie_logi_przekladaja_sie_na_brak_sufitu() -> None:
    """Flaga ma unieważnić sufity, a nie dodać się do nich.

    Samo zachowanie `wybierz_probke(top=None)` sprawdza `test_logi.py` —
    tutaj tylko tłumaczenie flagi na `None`, bo to robi CLI.
    """
    argumenty = zbuduj_parser().parse_args(
        ["--klient", "x", "--zakres", "cale_konto", "--wszystkie-logi", "--top-logow", "5"]
    )

    assert argumenty.wszystkie_logi is True
    assert (None if argumenty.wszystkie_logi else argumenty.top_logow) is None


def test_parser_odrzuca_nieznany_zakres() -> None:
    with pytest.raises(SystemExit):
        zbuduj_parser().parse_args(["--klient", "x", "--zakres", "wymyslony"])


# ── styk z konfiguracją ──────────────────────────────────────────────────


async def test_brak_sekretow_daje_jasny_komunikat(tmp_path: Path) -> None:
    """Bez sekretów program mówi, czego brakuje i gdzie to wpisać."""
    from monday_audit.cli import uruchom
    from monday_audit.konfiguracja import KonfiguracjaError

    argumenty = zbuduj_parser().parse_args(
        ["--klient", "cxlabs", "--zakres", "cale_konto", "--baza", str(tmp_path / "b.db")]
    )

    with pytest.raises(KonfiguracjaError) as blad:
        await uruchom(argumenty)

    assert "MONDAY_TOKEN: brak" in str(blad.value)
    assert "SOL_PSEUDONIMIZACJI: brak" in str(blad.value)


async def test_brak_soli_przerywa_mimo_tokena(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Token bez soli nie wystarcza — hashowanie bez soli jest pozorne."""
    from monday_audit.cli import uruchom
    from monday_audit.konfiguracja import KonfiguracjaError

    monkeypatch.setenv("MONDAY_TOKEN", "atrapa-tokena")
    argumenty = zbuduj_parser().parse_args(
        ["--klient", "cxlabs", "--zakres", "cale_konto", "--baza", str(tmp_path / "b.db")]
    )

    with pytest.raises(KonfiguracjaError, match="SOL_PSEUDONIMIZACJI: brak"):
        await uruchom(argumenty)


def test_token_opuszcza_secretstr_dokladnie_raz() -> None:
    """Gwarancja mechaniczna, nie obietnica.

    Token jest w `SecretStr` właśnie po to, żeby wyjęcie wartości było jawnym,
    policzalnym zdarzeniem. Jedno wystąpienie `get_secret_value()` w tym module
    i to na liście argumentów `wykonaj_run` — każde `print(token)`, każde
    wstawienie tokena do argv (widocznego w `ps`) albo do komunikatu błędu
    przewróci ten test.
    """
    from monday_audit import cli

    wiersze = Path(cli.__file__).read_text(encoding="utf-8").splitlines()
    uzycia = [w.strip() for w in wiersze if "get_secret_value()" in w]

    assert uzycia == ["token=ustawienia.monday_token.get_secret_value(),"]


# ── precedencja ścieżki bazy ─────────────────────────────────────────────


def _ustawienia(baza: Path) -> Ustawienia:
    return Ustawienia.model_construct(monday_audit_db=baza)


def test_flaga_bazy_bije_konfiguracje(tmp_path: Path) -> None:
    argumenty = zbuduj_parser().parse_args(
        ["--klient", "x", "--zakres", "cale_konto", "--baza", str(tmp_path / "z-flagi.db")]
    )

    assert ustal_baze(argumenty, _ustawienia(Path("z-configu.db"))) == tmp_path / "z-flagi.db"


def test_bez_flagi_baza_z_konfiguracji() -> None:
    """`MONDAY_AUDIT_DB` na serwerze wskazuje bazę poza repo — bez żadnej flagi."""
    argumenty = zbuduj_parser().parse_args(["--klient", "x", "--zakres", "cale_konto"])

    assert argumenty.baza is None
    assert ustal_baze(argumenty, _ustawienia(Path("/var/lib/audyt.db"))) == Path(
        "/var/lib/audyt.db"
    )


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
