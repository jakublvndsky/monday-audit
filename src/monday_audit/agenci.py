"""Collector — sonda rozpoznawcza agentów AI monday.

**To jest sonda, nie zbieranie danych.** Powstała, bo osoba nadzorująca projekt
zapytała, ile kredytów zużywają agenci monday i na co — a odpowiedź zmierzona
2026-08-04 brzmi: **w stabilnym API tych danych nie ma.**

Co zmierzone (zapytaniem, nie samą introspekcją — lekcja z O17):

| Wersja | Co osiągalne |
|---|---|
| `2026-07` (przypięta) | nic; korzeń bez pól o agentach, `usage` to `CampaignsUsage` |
| `2026-10` | nic |
| `2027-01` (release candidate) | `agents` działa — 50 agentów na koncie CXLABS |
| `dev` | `agent_runs`, `agent_run_event`, `agent_skills_catalog` i reszta |

Typy `AgentActivityRun.credits_used` i `VibeQueries.ai_credits_billing_cycle`
**są w schemacie 2026-07, ale nic ich nie zwraca.** A `agent_runs` w `dev`
oddaje dziś `Internal server error` przy każdym zestawie pól.

Dlatego ten moduł nie zbiera danych o kredytach. Sprawdza, **czy już można** —
i zapisuje odpowiedź w snapshocie, żeby pierwszy run po wypuszczeniu tego przez
monday powiedział nam o tym sam, zamiast czekać, aż ktoś zapyta ponownie.

## Zasada, której nie wolno tu złamać

Sonda pyta wersje **nieprzypięte**. Dane z takiego zapytania **nie mają prawa
wejść do findingów**, dopóki `WERSJA_API` nie zostanie podniesiona przez bramę
promocji. Powód: 05-deploy wymaga, żeby audyt sprzed trzech miesięcy dał się
odtworzyć, a wersja `dev` zmienia się bez ostrzeżenia. Wynik sondy ląduje
w `discovery`, czyli tam, gdzie trzymamy ograniczenia API — nie w danych.

Pilnuje tego `WynikAgentow.do_snapshotu()`: sekcja z wersji nieprzypiętej
dostaje flagę `zrodlo_nieprzypiete: true` i pole `NIE_DO_FINDINGOW`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from monday_audit.klient import WERSJA_API, MondayClient, MondayError

logger = logging.getLogger(__name__)

# Wersje sprawdzane po kolei, od przypiętej do najbardziej eksperymentalnej.
# `dev` jest ostatnia i celowo: jeśli cokolwiek stabilniejszego zadziała,
# nie ma powodu tam zaglądać.
WERSJE_SONDY = (WERSJA_API, "2027-01", "dev")

# Sufit agentów w jednym rozpoznaniu. Na koncie CXLABS jest ich 50, ale sonda
# ma odpowiedzieć „czy działa", nie zinwentaryzować konto — to byłoby zbieranie
# danych z nieprzypiętej wersji.
MAKS_AGENTOW = 5

ZAPYTANIE_AGENTOW = "query ($limit: Int!) { agents (limit: $limit) { id } }"

# Minimalny zestaw pól. Pełny (`title`, `summary`, `outputs`) świadomie
# pominięty: to treść pisana przez klienta, czyli i PII, i wektor prompt
# injection. Wejdzie dopiero, gdy będzie po co i przez `waliduj_brak_pii`.
ZAPYTANIE_RUNOW = """
query ($id: ID!, $limit: Int!) {
  agent_runs (agent_id: $id, limit: $limit) { run_id status total_cost }
}
"""


@dataclass
class WynikAgentow:
    """Wynik sondy. Same fakty o DOSTĘPNOŚCI, nie dane o agentach."""

    wersja_odpowiadajaca: str | None = None
    agentow_widocznych: int | None = None
    runy_dostepne: bool = False
    kredyty_dostepne: bool = False
    # Klucze bez znaku `@`: małpa w payloadzie jest wskaźnikiem e-maila
    # w teście antyprzeciekowym z 3.4, a własny separator nie ma prawa
    # osłabiać tego stróża.
    bledy: dict[str, str] = field(default_factory=dict)
    wywolan: int = 0

    @property
    def zrodlo_nieprzypiete(self) -> bool:
        """Czy odpowiedź przyszła z wersji innej niż przypięta."""
        return self.wersja_odpowiadajaca is not None and self.wersja_odpowiadajaca != WERSJA_API

    def do_snapshotu(self) -> dict[str, Any]:
        dane: dict[str, Any] = {
            "wersja_przypieta": WERSJA_API,
            "wersja_odpowiadajaca": self.wersja_odpowiadajaca,
            "agentow_widocznych": self.agentow_widocznych,
            "runy_dostepne": self.runy_dostepne,
            "kredyty_dostepne": self.kredyty_dostepne,
            # Dosłowna treść błędów. Bez niej „nie działa" jest bezużyteczne
            # przy następnym sprawdzeniu — nie wiadomo, czy pola nie ma,
            # czy token nie ma uprawnień, czy resolver padł.
            "bledy": dict(self.bledy),
            "wywolan_sondy": self.wywolan,
        }
        if self.zrodlo_nieprzypiete:
            dane["zrodlo_nieprzypiete"] = True
            dane["NIE_DO_FINDINGOW"] = (
                f"odpowiedź z wersji {self.wersja_odpowiadajaca}, a przypięta jest "
                f"{WERSJA_API} — te dane nie mogą trafić do findingu, bo audyt "
                f"przestałby być odtwarzalny (05-deploy, D4)"
            )
        return dane

    def podsumowanie(self) -> dict[str, Any]:
        return {
            "sledzenie_agentow_dostepne": self.runy_dostepne and not self.zrodlo_nieprzypiete,
            "agentow_widocznych": self.agentow_widocznych or 0,
        }


async def sonduj_agentow(
    klient: MondayClient,
    *,
    wersje: tuple[str, ...] = WERSJE_SONDY,
    maks_agentow: int = MAKS_AGENTOW,
) -> WynikAgentow:
    """Sprawdza, czy powierzchnia agentowa jest osiągalna. Nie zbiera danych.

    Idzie po wersjach od przypiętej w górę i przerywa dopiero, gdy dostanie
    **odpowiedź na pytanie, które faktycznie zadajemy** — czyli gdy uruchomienia
    agentów są osiągalne. Samo `agents` nie wystarcza: zmierzone 2026-08-04,
    `2027-01` oddaje listę agentów, ale `agent_runs` jest dopiero w `dev`.
    Przerwanie na pierwszym `agents` ukryłoby ten fakt — a to on jest
    odpowiedzią dla osoby, która pyta o kredyty.

    Nie przerywa runu przy żadnym błędzie: brak pola to normalny stan
    dzisiaj, a nie awaria. Każdy błąd ląduje w `bledy` z dosłowną treścią.
    """
    wynik = WynikAgentow()

    for wersja in wersje:
        try:
            dane = await klient.query(
                ZAPYTANIE_AGENTOW,
                {"limit": maks_agentow},
                etykieta="sonda_agentow",
                wersja_api=wersja if wersja != WERSJA_API else None,
            )
        except MondayError as blad:
            wynik.wywolan += 1
            wynik.bledy[f"agents/{wersja}"] = str(blad)[:200]
            continue

        wynik.wywolan += 1
        agenci = dane.get("agents") or []
        # Pierwsza wersja z `agents` ustala `wersja_odpowiadajaca`; kolejne
        # nadpisują ją tylko wtedy, gdy dowiozą coś WIĘCEJ (czyli runy).
        if wynik.wersja_odpowiadajaca is None:
            wynik.wersja_odpowiadajaca = wersja
            wynik.agentow_widocznych = len(agenci)
        logger.info(
            "[DISCOVERY] ✅ `agents` działa w wersji %s — widocznych %d", wersja, len(agenci)
        )
        if agenci:
            await _sonduj_runy(klient, wynik, agenci[0], wersja, maks_agentow)
        if wynik.runy_dostepne:
            # To jest odpowiedź na pytanie, które zadajemy. Dalej nie ma po co.
            wynik.wersja_odpowiadajaca = wersja
            wynik.agentow_widocznych = len(agenci)
            break

    if wynik.wersja_odpowiadajaca is None:
        logger.info(
            "[DISCOVERY] ❌ `agents` niedostępne w żadnej ze sprawdzanych wersji: %s",
            ", ".join(wersje),
        )
    elif not wynik.runy_dostepne:
        logger.info(
            "[DISCOVERY] ⚠️ agentów widać (%s), ale uruchomień NIE — czyli kredytów "
            "nie da się dziś policzyć w żadnej sprawdzonej wersji",
            wynik.wersja_odpowiadajaca,
        )

    if wynik.zrodlo_nieprzypiete:
        logger.warning(
            "powierzchnia agentowa działa TYLKO w wersji %s, a przypięta jest %s — "
            "dane rozpoznania NIE wchodzą do findingów (O20)",
            wynik.wersja_odpowiadajaca,
            WERSJA_API,
        )
    return wynik


async def _sonduj_runy(
    klient: MondayClient,
    wynik: WynikAgentow,
    agent: dict[str, Any],
    wersja: str,
    limit: int,
) -> None:
    """Czy da się dosięgnąć uruchomień i kosztu jednego agenta.

    Jeden agent, nie wszyscy: sonda odpowiada „czy działa". Inwentaryzacja
    z nieprzypiętej wersji byłaby zbieraniem danych, którego ten moduł
    świadomie nie robi.
    """
    identyfikator = str(agent.get("id") or "")
    if not identyfikator:
        wynik.bledy[f"agent_runs/{wersja}"] = "agent bez `id`"
        return
    try:
        dane = await klient.query(
            ZAPYTANIE_RUNOW,
            {"id": identyfikator, "limit": limit},
            etykieta="sonda_runow",
            wersja_api=wersja if wersja != WERSJA_API else None,
        )
    except MondayError as blad:
        wynik.wywolan += 1
        wynik.bledy[f"agent_runs/{wersja}"] = str(blad)[:200]
        logger.info("[DISCOVERY] ❌ `agent_runs` w wersji %s: %s", wersja, str(blad)[:110])
        return

    wynik.wywolan += 1
    runy = dane.get("agent_runs") or []
    wynik.runy_dostepne = True
    # `total_cost` bywa `null` przy runach bez rozliczenia — Enterprise ma
    # agentów darmowych (O21), więc zero NIE znaczy „nie działa".
    wynik.kredyty_dostepne = any(r.get("total_cost") is not None for r in runy)
    logger.info(
        "[DISCOVERY] ✅ `agent_runs` w wersji %s — runów %d, koszt dostępny: %s",
        wersja,
        len(runy),
        wynik.kredyty_dostepne,
    )
