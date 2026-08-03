"""Testy wejścia do pętli agenta (etap 3.11), warstwa 1.

Zero wywołań modelu i zero wywołań monday. Sprawdzamy argumenty i JEDNĄ
rzecz, która ma znaczenie pieniężne: czy brak klucza Anthropic przerywa,
ZANIM wydamy wywołanie monday.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from monday_audit.agent import MODEL
from monday_audit.cli_agent import zbuduj_parser


def test_snapshot_i_klient_sa_wymagane() -> None:
    """Bez snapshotu nie ma czego analizować, bez klienta nie ma soli."""
    with pytest.raises(SystemExit):
        zbuduj_parser().parse_args(["--snapshot", "5"])
    with pytest.raises(SystemExit):
        zbuduj_parser().parse_args(["--klient", "cxlabs"])


def test_model_domyslnie_przypiety() -> None:
    argumenty = zbuduj_parser().parse_args(["--klient", "cxlabs", "--snapshot", "5"])

    assert argumenty.model == MODEL
    assert "latest" not in argumenty.model


def test_zawezenie_do_klas_i_limitu() -> None:
    """Tanie próby przed pełnym runem — bo pełny run kosztuje pieniądze."""
    argumenty = zbuduj_parser().parse_args(
        [
            "--klient",
            "cxlabs",
            "--snapshot",
            "5",
            "--klasy",
            "ZOMBIE_ACCOUNT",
            "--klasy",
            "AUTOMATION_DEAD",
            "--limit",
            "3",
        ]
    )

    assert argumenty.klasy == ["ZOMBIE_ACCOUNT", "AUTOMATION_DEAD"]
    assert argumenty.limit == 3


def test_sufit_wywolan_monday_ma_wartosc_domyslna() -> None:
    """Agent nie może zjeść dziennego limitu klienta, nawet gdy zbłądzi w pętli."""
    argumenty = zbuduj_parser().parse_args(["--klient", "cxlabs", "--snapshot", "5"])

    assert argumenty.budzet_monday > 0


def test_bez_zawezenia_domyslnie_wszystkie_hipotezy() -> None:
    argumenty = zbuduj_parser().parse_args(["--klient", "cxlabs", "--snapshot", "5"])

    assert argumenty.klasy == []
    assert argumenty.limit is None


async def test_brak_klucza_przerywa_przed_wywolaniem_monday(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """NAJWAŻNIEJSZY test tego pliku, i jest o pieniądzach.

    Gdyby kolejność była odwrotna, run bez klucza zdążyłby wydać wywołania
    monday z dziennego limitu klienta i przewrócić się dopiero przy modelu.
    Tych wywołań nie da się odzyskać.
    """
    from monday_audit.cli_agent import uruchom
    from monday_audit.konfiguracja import ZMIENNA_PLIKU, KonfiguracjaError

    monkeypatch.setenv(ZMIENNA_PLIKU, str(tmp_path / "nie-ma-mnie.env"))
    monkeypatch.setenv("MONDAY_TOKEN", "atrapa")
    monkeypatch.setenv("SOL_PSEUDONIMIZACJI", "x" * 16)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    argumenty = zbuduj_parser().parse_args(
        ["--klient", "cxlabs", "--snapshot", "1", "--baza", str(tmp_path / "b.db")]
    )

    with pytest.raises(KonfiguracjaError, match="ANTHROPIC_API_KEY"):
        await uruchom(argumenty)

    # I nie powstała nawet baza — czyli nie doszliśmy do żadnego zapisu.
    assert not (tmp_path / "b.db").exists()


def test_modul_nie_miesza_warstw() -> None:
    """Collector i agent to dwie warstwy, które CLAUDE.md rozdziela świadomie.

    Wejście agenta nie może zbierać danych: zlanie tego w jedną komendę
    znaczyłoby, że nie da się powtórzyć analizy bez ponownego uderzania
    w monday, a etap 4 wymaga dokładnie tego — tego samego snapshotu
    przepuszczonego przez nowy prompt.
    """
    from monday_audit import cli_agent

    zrodlo = Path(cli_agent.__file__).read_text(encoding="utf-8")

    assert "wykonaj_run" not in zrodlo, "to jest zbieranie danych, nie analiza"
    assert "zapisz_snapshot" not in zrodlo
