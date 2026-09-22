"""Trace jednej hipotezy: co wychodzi poza serwer (plan, faza 4).

Testy pilnują trzech rzeczy, w tej kolejności ważności:

1. **czego w trace NIE ma** — prompt systemowy niesie inwentarz klienta,
2. **że PII nie przechodzi** i że trafienie jest policzone, a nie przemilczane,
3. że kształt zgadza się z tym, czego oczekuje Langfuse.

Atrapa zamiast `WynikHipotezy` jest celowa: `agent.py` ciągnie Agent SDK,
a trace ma się dać zbudować bez podprocesu modelu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from monday_audit.obserwowalnosc import RODZAJ_GENERACJA, zbuduj_trace
from monday_audit.osoby import WpisPII

INWENTARZ = "tablica Zdzisławy, workspace Sprzedaż, kolumna lead_status"


@dataclass
class AtrapaHipotezy:
    klasa_id: str = "BOARD_OVERCOMPLEX"
    obiekt_id: str = "9445123456"
    zapis: dict[str, Any] = field(default_factory=dict)

    def do_zapisu(self) -> dict[str, Any]:
        return self.zapis or {"klasa_id": self.klasa_id, "obiekt_id": self.obiekt_id}


@dataclass
class AtrapaWyniku:
    hipoteza: AtrapaHipotezy = field(default_factory=AtrapaHipotezy)
    finding: dict[str, Any] | None = None
    odrzucona: dict[str, Any] | None = None
    wywolania_narzedzi: list[str] = field(default_factory=list)
    zuzycie: dict[str, float] = field(default_factory=dict)
    blad: str | None = None
    blokow_tekstu: int = 1
    znakow_finalnych: int = 400
    znakow_wyrzuconych: int = 0


def _zbuduj(wynik: AtrapaWyniku, wpisy: list[WpisPII] | None = None) -> Any:
    return zbuduj_trace(
        wynik,
        run_id="run-1",
        snapshot_id=7,
        model="claude-opus-5",
        prompt_hash="abc123",
        wpisy=wpisy or [],
    )


# ── czego w trace NIE ma ─────────────────────────────────────────────────


def test_prompt_systemowy_nie_wychodzi_tylko_jego_hasz() -> None:
    """Prompt systemowy niesie INWENTARZ: nazwy tablic, workspace'ów i kolumn
    klienta. Hasz daje to, po co trace'owi prompt — wiedzę, że dwa runy szły
    tym samym. Treść nie daje nic ponadto, a kosztuje wysłaniem inwentarza."""
    trace = _zbuduj(AtrapaWyniku(finding={"opis": "za dużo kolumn"}))

    assert trace.metadane["prompt_hash"] == "abc123"
    assert INWENTARZ not in str(trace)
    assert "prompt" not in str(trace.obserwacje).lower().replace("prompt_hash", "")


# ── PII nie przechodzi, a trafienie jest policzone ───────────────────────


def test_mail_w_findingu_zostaje_zamaskowany() -> None:
    wynik = AtrapaWyniku(finding={"dowod": "zgłoszenie od lead@obcy.test"})

    trace = _zbuduj(wynik)

    assert trace.obserwacje[0].wyjscie["dowod"] == "zgłoszenie od [E-MAIL]"
    assert trace.trafienia_maskowania["email"] == 1
    assert not trace.czysty


def test_trafienie_widac_w_metadanych_trace_u() -> None:
    """Liczba idzie do metadanych, żeby było ją widać W LANGFUSE, a nie tylko
    w naszym logu. Człowiek patrzący na trace ma zobaczyć, że coś przeciekło."""
    trace = _zbuduj(AtrapaWyniku(finding={"dowod": "x@y.test"}))

    assert trace.metadane["trafien_maskowania"] == 1
    assert trace.metadane["pola_z_trafieniami"] == ["wyjscie.dowod"]


def test_trafienie_krzyczy_do_logu_jako_ostrzezenie(caplog: Any) -> None:
    """Trafienie to NIE sukces maskowania. Pierwszą linią jest zasada, że PII
    nie wchodzi do kontekstu modelu — `[E-MAIL]` znaczy, że puściła."""
    with caplog.at_level(logging.WARNING):
        _zbuduj(AtrapaWyniku(finding={"dowod": "x@y.test"}))

    assert "PIERWSZA linia" in caplog.text
    assert "x@y.test" not in caplog.text


def test_czysty_trace_nie_ostrzega(caplog: Any) -> None:
    with caplog.at_level(logging.WARNING):
        trace = _zbuduj(AtrapaWyniku(finding={"dowod": "tablica ma 41 kolumn"}))

    assert trace.czysty
    assert not caplog.text


def test_uzytkownik_konta_dostaje_pseudonim_a_nie_zamiennik() -> None:
    """Ta sama reguła, co w `maskowanie`: znanego człowieka pseudonimizujemy,
    żeby w trace było widać, że w dwóch miejscach chodzi o TĘ SAMĄ osobę."""
    wpisy = [WpisPII("a1b2c3", "Zdzisława Wąchockańska", "zdzislawa@klient.test")]
    wynik = AtrapaWyniku(finding={"dowod": "właściciel: Zdzisława Wąchockańska"})

    trace = _zbuduj(wynik, wpisy)

    assert trace.obserwacje[0].wyjscie["dowod"] == "właściciel: [OSOBA:a1b2c3]"
    assert trace.czysty


def test_blad_api_tez_idzie_przez_maskowanie() -> None:
    """Treść błędu z API bywa fragmentem odpowiedzi — nie jest „tylko
    komunikatem" i nie ma powodu, żeby omijała warstwę."""
    trace = _zbuduj(AtrapaWyniku(blad="błąd API: odrzucono dla admin@klient.test"))

    assert "[E-MAIL]" in trace.obserwacje[0].wyjscie["blad"]
    assert trace.metadane["rozstrzygniecie"] == "blad"


# ── kształt dla Langfuse ─────────────────────────────────────────────────


def test_zuzycie_przetlumaczone_na_nazwy_langfuse() -> None:
    """Mapowanie nazw siedzi tutaj, nie w `agent.py` — tam zużycie odpowiada
    przed D8, a nie przed cudzym systemem."""
    wynik = AtrapaWyniku(
        finding={"ok": True},
        zuzycie={
            "tokens_in": 10,
            "tokens_out": 20,
            "tokens_cache_read": 3000,
            "tokens_cache_write": 40,
            "koszt_usd": 0.42,
        },
    )

    generacja = _zbuduj(wynik).obserwacje[0]

    assert generacja.rodzaj == RODZAJ_GENERACJA
    assert generacja.zuzycie == {
        "input": 10,
        "output": 20,
        "cache_read_input_tokens": 3000,
        "cache_creation_input_tokens": 40,
    }
    assert generacja.koszt_usd == 0.42


def test_narzedzia_sa_osobnymi_wezlami() -> None:
    wynik = AtrapaWyniku(finding={"ok": True}, wywolania_narzedzi=["snapshot", "probka"])

    nazwy = [o.nazwa for o in _zbuduj(wynik).obserwacje]

    assert nazwy == ["hipoteza:BOARD_OVERCOMPLEX", "narzedzie:snapshot", "narzedzie:probka"]


def test_rozstrzygniecie_odrzucona_ma_swoje_wyjscie() -> None:
    trace = _zbuduj(AtrapaWyniku(odrzucona={"powod": "kolumny są używane"}))

    assert trace.metadane["rozstrzygniecie"] == "odrzucona"
    assert trace.obserwacje[0].wyjscie == {"powod": "kolumny są używane"}


def test_brak_rozstrzygniecia_nie_wywraca_budowania() -> None:
    """Hipoteza, która padła przed odpowiedzią, ma mieć trace — to właśnie
    ona jest najciekawsza przy szukaniu przyczyny."""
    trace = _zbuduj(AtrapaWyniku())

    assert trace.metadane["rozstrzygniecie"] == "brak"
    assert trace.obserwacje[0].wyjscie is None
