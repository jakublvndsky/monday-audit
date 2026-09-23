"""Jedna sesja na całe konto (plan, faza 5b-2).

Testy dzielą się na dwie grupy i pierwsza jest ważniejsza:

- **treść zadania** — czy model dostaje zastrzeżenia RAZEM z liczbami, a nie
  po nich. Liczba bez swojego ograniczenia to najgroźniejszy rodzaj danych
  w tym projekcie, bo wygląda dokładnie jak liczba pewna,
- **kompletność rozstrzygnięć** — czy pominięta hipoteza jest słyszalna.

Sesji nie odpalamy: `ClaudeSDKClient` uruchamia podproces, a to mierzyłoby
SDK, nie nasz kod.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from monday_audit.analiza import SCIEZKA_PROMPTU_ANALIZY, zbuduj_zadanie
from monday_audit.detektory import Hipoteza

WEJSCIE: dict[str, Any] = {
    "konto": {"workspacow": 136, "tablic_aktywnych": 1315},
    "zestawienia": [{"produkt": "crm", "w_toku": 1290, "pokrycie": 0.614}],
    "zastrzezenia": [
        "[zestawienia] crm: 7113 itemów nie weszło do żadnego kubełka (O47). Pokrycie 61.4%",
        "[tablice] nieuruchomionych automatyzacji nie widać (O41)",
    ],
}


def _hipotezy(ile: int = 2) -> list[Hipoteza]:
    return [
        Hipoteza(
            klasa_id="BOARD_GHOST",
            obiekt_id=f"b{i}",
            fakty={"wpisow": 0, "nazwa": f"tablica {i}"},
            budzet_wywolan=2,
        )
        for i in range(ile)
    ]


# ── treść zadania ────────────────────────────────────────────────────────


def test_zastrzezenia_ida_przed_liczbami() -> None:
    """Kolejność jest treścią, nie formatowaniem. Model czytający liczby bez
    ich ograniczeń napisze uwagę opartą na liczbie, o której nie wie, że jest
    niepełna — a tego błędu nie widać w wyniku, dopóki nie zobaczy go klient."""
    zadanie = zbuduj_zadanie(_hipotezy(), WEJSCIE)

    assert zadanie.index("CZEGO TE LICZBY NIE OBEJMUJĄ") < zadanie.index("OBRAZ KONTA")
    assert "Pokrycie 61.4%" in zadanie


def test_zastrzezenia_nie_dublują_się_w_obrazie() -> None:
    """Raz jako ostrzeżenie, nie drugi raz jako pole JSON-a — powtórzenie
    tej samej treści w dwóch miejscach uczy model ją pomijać."""
    zadanie = zbuduj_zadanie(_hipotezy(), WEJSCIE)
    obraz = zadanie.split("OBRAZ KONTA")[1]

    assert "zastrzezenia" not in obraz


def test_wszystkie_hipotezy_sa_w_zadaniu() -> None:
    zadanie = zbuduj_zadanie(_hipotezy(3), WEJSCIE)

    assert "HIPOTEZY DO ROZSTRZYGNIĘCIA (3)" in zadanie
    for i in range(3):
        assert f"b{i}" in zadanie


def test_zadanie_mowi_wprost_ile_ma_byc_rozstrzygniec() -> None:
    """Bez tego zdania model rozstrzyga „te ciekawsze". Suma jest jedynym
    mechanicznym sprawdzianem kompletności, jaki mamy."""
    zadanie = zbuduj_zadanie(_hipotezy(7), WEJSCIE)

    assert "musi wynosić 7" in zadanie


def test_obraz_konta_jest_poprawnym_jsonem() -> None:
    zadanie = zbuduj_zadanie(_hipotezy(), WEJSCIE)
    fragment = zadanie.split("## OBRAZ KONTA")[1].split("## HIPOTEZY")[0].strip()

    assert json.loads(fragment)["konto"]["workspacow"] == 136


def test_pusta_lista_zastrzezen_nie_wywraca_zadania() -> None:
    zadanie = zbuduj_zadanie(_hipotezy(), {"konto": {"workspacow": 1}})

    assert "OBRAZ KONTA" in zadanie


# ── prompt ───────────────────────────────────────────────────────────────


def test_prompt_analizy_istnieje_i_ma_blok() -> None:
    """Prompt jest runtime'em, nie dokumentacją — brak bloku to błąd wdrożenia,
    a nie literówka w pliku markdown."""
    from monday_audit.agent import _tekst_promptu

    tresc = _tekst_promptu(SCIEZKA_PROMPTU_ANALIZY)

    assert '"uwagi"' in tresc
    assert '"pominiete"' in tresc


def test_prompt_zabrania_wyceny_i_stopniowania() -> None:
    """Dwie rzeczy, które wypadły z zakresu decyzją Kuby. Gdyby prompt o nich
    milczał, model dopisałby wagi i kwoty z własnej inicjatywy — robi tak,
    bo tak wygląda większość audytów, które widział."""
    from monday_audit.agent import _tekst_promptu

    tresc = _tekst_promptu(SCIEZKA_PROMPTU_ANALIZY)

    assert "NIE WYCENIASZ" in tresc
    assert "NIE STOPNIUJESZ" in tresc


def test_prompt_wymaga_dowodu() -> None:
    from monday_audit.agent import _tekst_promptu

    assert "DOWÓD" in _tekst_promptu(SCIEZKA_PROMPTU_ANALIZY)


# ── budżet i sufity ──────────────────────────────────────────────────────


def test_budzet_jest_na_sesje_a_nie_na_hipoteze() -> None:
    """Sedno 5b-2: budżety z rubryki zastępuje JEDEN sufit na cały run."""
    from monday_audit.agent import MAKS_OBROTOW
    from monday_audit.analiza import BUDZET_NARZEDZI, MAKS_OBROTOW_ANALIZY

    assert BUDZET_NARZEDZI > 0
    # Więcej obrotów niż w sesji per hipoteza — jest do rozstrzygnięcia
    # kilkadziesiąt hipotez, a nie jedna.
    assert MAKS_OBROTOW_ANALIZY > MAKS_OBROTOW


async def test_brak_hipotez_przerywa_zamiast_placic_za_pusta_sesje() -> None:
    from monday_audit.agent import AgentError
    from monday_audit.analiza import zbadaj_konto

    with pytest.raises(AgentError, match="brak hipotez"):
        await zbadaj_konto([], zestaw=None, wejscie={}, klucz_api="")  # type: ignore[arg-type]
