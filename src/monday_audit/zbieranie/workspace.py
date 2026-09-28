"""Collector — workspace'y konta i ostatni wpis logu każdej tablicy (faza 9).

Kategoria Workspace w raporcie była „jeszcze nie mierzona", bo snapshot nie
znał workspace'ów wprost: widział je tylko przez tablice, więc workspace bez
ani jednej tablicy nie istniał dla detektorów.

## Dlaczego ostatni wpis logu, a nie `updated_at`

ZMIERZONE 2026-09-28 na pełnym CXLABS: 46 workspace'ów miało wszystkie tablice
z `updated_at` starszym niż 180 dni, a logi potwierdziły ciszę w 44 — w dwóch
ktoś pracował. `updated_at` śledzi metadane tablicy, nie pracę na elementach
(O18, ta sama lekcja co BOARD_GHOST). Log jest źródłem prawdy.

## Koszt

`boards(ids: [...]) { activity_logs(limit: 1) }` oddaje najnowszy wpis dla 50
tablic w JEDNYM wywołaniu: 1319 aktywnych tablic CXLABS to 27 wywołań. Lista
workspace'ów to jedno wywołanie na 100 pozycji.

## Czego ten moduł NIE robi

Nie ocenia, czy workspace jest martwy — to robi detektor, z progiem z rubryki.
Collector spisuje fakty bez progów.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from monday_audit.zbieranie.klient import MondayClient
from monday_audit.zbieranie.konto import Zakres
from monday_audit.zbieranie.logi import na_datetime

logger = logging.getLogger(__name__)

LIMIT_WORKSPACE = 100
# Tablic na jedno zapytanie o ostatni wpis. 50 zmierzone 2026-09-28 na CXLABS
# (6 wywołań na 289 tablic) — bez błędu complexity.
TABLIC_NA_ZAPYTANIE = 50

# `kind` (open / closed / template) i `state` — ZMIERZONE 2026-09-28: API oddaje
# oba w przypiętej wersji. Rodzaj zbieramy od razu, choć żadna klasa go dziś nie
# używa (klasa „otwarty workspace" odłożona) — jedno pole, zero wywołań więcej.
_PYTANIE_WORKSPACE = """
query ($limit: Int!, $p: Int!) {
  workspaces (limit: $limit, page: $p) {
    id
    name
    kind
    state
    account_product { kind }
  }
}
"""

_PYTANIE_OSTATNI_WPIS = """
query ($ids: [ID!]) {
  boards (ids: $ids, limit: 50) {
    id
    activity_logs (limit: 1) { created_at }
  }
}
"""


@dataclass(frozen=True, slots=True)
class Workspace:
    workspace_id: str
    nazwa: str
    rodzaj: str | None
    stan: str | None
    produkt: str | None

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "nazwa": self.nazwa,
            "rodzaj": self.rodzaj,
            "stan": self.stan,
            "produkt": self.produkt,
        }


@dataclass
class WynikWorkspace:
    workspace_y: tuple[Workspace, ...] = ()
    # board_id → ISO najnowszego wpisu logu; `None` = tablica bez wpisów.
    # Tablicy spoza mapy nie odpytano — to „nie wiem", nie „cisza".
    ostatni_wpis: dict[str, str | None] = field(default_factory=dict)
    # Zakres `tablice` nie mówi o workspace'ach nic uczciwego: widzimy wybrane
    # tablice, nie resztę workspace'u. Detektory tej kategorii wtedy milczą.
    objete: bool = True

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "objete": self.objete,
            "workspace_y": [w.do_snapshotu() for w in self.workspace_y],
            "ostatni_wpis_tablic": dict(self.ostatni_wpis),
        }


async def _lista(klient: MondayClient) -> list[Workspace]:
    zebrane: list[Workspace] = []
    strona = 1
    while True:
        dane = await klient.query(
            _PYTANIE_WORKSPACE, {"limit": LIMIT_WORKSPACE, "p": strona}, etykieta="workspaces"
        )
        surowe = dane.get("workspaces") or []
        for w in surowe:
            produkt = w.get("account_product") or {}
            zebrane.append(
                Workspace(
                    workspace_id=str(w.get("id")),
                    nazwa=str(w.get("name") or ""),
                    rodzaj=w.get("kind") or None,
                    stan=w.get("state") or None,
                    produkt=produkt.get("kind") or None,
                )
            )
        if len(surowe) < LIMIT_WORKSPACE:
            return zebrane
        strona += 1


async def ostatnie_wpisy(klient: MondayClient, board_ids: Sequence[str]) -> dict[str, str | None]:
    """Najnowszy wpis logu każdej tablicy, po 50 tablic na wywołanie."""
    wynik: dict[str, str | None] = {}
    for i in range(0, len(board_ids), TABLIC_NA_ZAPYTANIE):
        partia = list(board_ids[i : i + TABLIC_NA_ZAPYTANIE])
        dane = await klient.query(_PYTANIE_OSTATNI_WPIS, {"ids": partia}, etykieta="ostatni_wpis")
        for tablica in dane.get("boards") or []:
            wpisy = tablica.get("activity_logs") or []
            kiedy = na_datetime(wpisy[0].get("created_at")) if wpisy else None
            wynik[str(tablica.get("id"))] = kiedy.isoformat() if kiedy else None
    return wynik


async def zbierz_workspace(
    klient: MondayClient, zakres: Zakres, aktywne_tablice: Sequence[str]
) -> WynikWorkspace:
    """Workspace'y w zakresie i ostatni wpis logu dla `aktywne_tablice`."""
    if zakres.typ == "tablice":
        return WynikWorkspace(objete=False)
    workspace_y = await _lista(klient)
    if zakres.typ == "workspace":
        wybrane = set(zakres.workspace_ids)
        workspace_y = [w for w in workspace_y if w.workspace_id in wybrane]
    wpisy = await ostatnie_wpisy(klient, aktywne_tablice)
    return WynikWorkspace(workspace_y=tuple(workspace_y), ostatni_wpis=wpisy)
