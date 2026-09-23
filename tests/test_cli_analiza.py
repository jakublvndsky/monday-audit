"""Wpięcie trace'ów w `cli_analiza` — test przez całą komendę (faza 5b).

W tym repo zdarzyły się już dwie usterki klasy „przeszło testy, a nie było
podpięte" (`can_use_tool` i klucz API), a trzecia była blisko: do fazy 5b nowa
ścieżka nie wysyłała trace'ów wcale, bo tracing z fazy 4 siedział w innej
funkcji. Testy samego `zbuduj_trace_analizy` tego by nie wykryły — dlatego ten
plik przejeżdża `uruchom` w całości, z atrapą tylko tam, gdzie zaczyna się
świat zewnętrzny: model, Langfuse i konfiguracja.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from monday_audit import cli_analiza
from monday_audit.agent import AgentError
from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.detektory import Hipoteza
from monday_audit.przebieg import zapisz_snapshot


class AtrapaSladu:
    def __init__(self) -> None:
        self.wyslane: list[Any] = []
        self.zamkniety = False

    def wyslij(self, trace: Any) -> None:
        self.wyslane.append(trace)

    def zamknij(self) -> None:
        self.zamkniety = True


@pytest.fixture
def srodowisko(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    baza = tmp_path / "test.db"
    con = polacz(baza)
    zastosuj_migracje(con)
    snapshot_id = zapisz_snapshot(
        con, client_id="cxlabs", payload={"meta": {}}, run_at="2026-09-23T00:00:00Z"
    )
    con.close()

    slad = AtrapaSladu()
    hipoteza = Hipoteza(
        klasa_id="BOARD_GHOST",
        obiekt_id="b1",
        fakty={"wpisow": 0, "nazwa": "tablica 1"},
        budzet_wywolan=2,
    )
    monkeypatch.setattr(cli_analiza, "wczytaj", lambda: SimpleNamespace(monday_audit_db=baza))
    monkeypatch.setattr(cli_analiza, "sol_z_ustawien", lambda _: b"s" * 16)
    monkeypatch.setattr(cli_analiza, "klucz_anthropic", lambda _: "klucz-testowy")
    monkeypatch.setattr(cli_analiza, "uruchom_detektory", lambda *_: ([hipoteza], {}))
    monkeypatch.setattr(cli_analiza, "wysylka_z_ustawien", lambda _: slad)
    monkeypatch.setattr(cli_analiza, "KATALOG_WYNIKOW", tmp_path / "raporty")
    return {"baza": baza, "snapshot_id": snapshot_id, "slad": slad}


def _argumenty(srodowisko: dict[str, Any], run_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        klient="cxlabs",
        snapshot=srodowisko["snapshot_id"],
        baza=None,
        wejscie=None,
        tylko_szacunek=False,
        run_id=run_id,
        json=False,
    )


def _status(srodowisko: dict[str, Any], run_id: str) -> str:
    con = polacz(srodowisko["baza"])
    try:
        return str(con.execute("SELECT status FROM runy WHERE run_id = ?", (run_id,)).fetchone()[0])
    finally:
        con.close()


async def test_udana_analiza_wysyla_trace(
    srodowisko: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def atrapa_sesji(*_: Any, **__: Any) -> dict[str, Any]:
        return {
            "uwagi": [],
            "pominiete": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "b1", "powod": "świeża"}],
            "zuzycie": {"tokens_out": 100, "koszt_usd": 0.12},
            "wywolania_narzedzi": ["pobierz_inwentarz:konto"],
        }

    monkeypatch.setattr(cli_analiza, "zbadaj_konto", atrapa_sesji)

    assert await cli_analiza.uruchom(_argumenty(srodowisko, "t-ok")) == 0

    slad = srodowisko["slad"]
    assert len(slad.wyslane) == 1, "trace analizy nie wyszedł — tracing niepodpięty"
    trace = slad.wyslane[0]
    assert trace.nazwa == "analiza:konto"
    assert trace.metadane["run_id"] == "t-ok"
    assert trace.metadane["rozstrzygniecie"] == "zakonczona"
    # Bufor dosłany — inaczej krótki proces CLI kończy się przed eksportem.
    assert slad.zamkniety
    assert _status(srodowisko, "t-ok") == "zakonczony"


async def test_padnieta_analiza_tez_wysyla_trace(
    srodowisko: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run, który padł, jest NAJCIEKAWSZY w trace'ach. Pierwszy prawdziwy run
    nowej ścieżki padł po opłaconej sesji i nie zostawił po sobie nic —
    ani wyniku, ani śladu."""

    async def sesja_padajaca(*_: Any, **__: Any) -> dict[str, Any]:
        raise AgentError("sesja analizy padła: błąd API")

    monkeypatch.setattr(cli_analiza, "zbadaj_konto", sesja_padajaca)

    with pytest.raises(AgentError):
        await cli_analiza.uruchom(_argumenty(srodowisko, "t-awaria"))

    slad = srodowisko["slad"]
    assert len(slad.wyslane) == 1
    trace = slad.wyslane[0]
    assert trace.metadane["rozstrzygniecie"] == "blad"
    assert "błąd API" in trace.obserwacje[0].wyjscie["blad"]
    assert slad.zamkniety
    assert _status(srodowisko, "t-awaria") == "przerwany"
