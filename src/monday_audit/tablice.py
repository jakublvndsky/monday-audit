"""Collector — tablice i kolumny (etap 3.5).

Dwie granice, obie twarde:

**D5: nie schodzimy na poziom itemów.** `items_count` to granica. Zapytanie
nie ma pola `items` ani `items_page` i test tego pilnuje. Jedyny wyjątek
przewidziany w architekturze to sampling w `BOARD_OVERCOMPLEX`, i on tu
nie należy.

**PII: `owners` i `subscribers` idą przez `policz_hash`.** Do snapshotu
trafiają wyłącznie te same pseudonimy co w 3.4, nigdy surowe identyfikatory.

Kolumny zostają w całości (id, tytuł, typ), bo bez nich nie da się policzyć
ani `BOARD_OVERCOMPLEX` (które kolumny są martwe), ani `DUPLICATE_STRUCTURE`
(nakładanie kolumn między tablicami) — 3.9.

Stany: zbieramy `state: all` w jednym przelocie, ale do listy w snapshocie
wchodzą `active` i `archived`. Kosz (`deleted`) jest tylko liczony — na koncie
CXLABS to 38% wszystkich tablic i wliczenie go zawyżałoby każdą metrykę
w raporcie klienta (`OTWARTE.md` O10). Parametr `zbieraj_usuniete` odwraca
tę decyzję bez zmiany kodu.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from monday_audit.klient import MondayClient
from monday_audit.konto import Zakres
from monday_audit.osoby import policz_hash, waliduj_brak_pii

logger = logging.getLogger(__name__)

# Pola z 03-build.md 3.5. ŚWIADOMIE BEZ `items` i `items_page` — D5.
# `order_by: created_at` daje stabilną kolejność między runami, więc dwa runy
# na tym samym koncie dają snapshoty różniące się tylko znacznikami czasu
# (wymóg powtarzalności z 04-test.md, warstwa 2).
_SZKIELET = """
query ($p: Int!, $limit: Int!, $state: State!{deklaracje}) {{
  boards (limit: $limit, page: $p, state: $state{filtr}, order_by: created_at) {{
    id name state board_kind items_count created_at updated_at
    workspace {{ id name }}
    owners {{ id }}
    subscribers {{ id }}
    columns {{ id title type }}
  }}
}}
"""

# Zmierzone na CXLABS: strona 25 tablic z tym zestawem pól to ~128 tys.
# complexity. Przy oknie ~9,87 mln (O9) da się iść po 100, ale 25 zostawia
# zapas na hamulec i na resztę runu.
LIMIT_STRONY = 25

# Stany, które wchodzą do listy w snapshocie. `deleted` jest tylko liczony.
STANY_ZBIERANE = ("active", "archived")


@dataclass(frozen=True, slots=True)
class Tablica:
    """Tablica bez itemów i bez surowych identyfikatorów osób."""

    board_id: str
    nazwa: str
    state: str
    board_kind: str
    items_count: int | None
    workspace_id: str | None
    workspace_nazwa: str | None
    owners: tuple[str, ...]
    subscribers: tuple[str, ...]
    kolumny: tuple[dict[str, str], ...]
    created_at: str | None
    updated_at: str | None

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "board_id": self.board_id,
            "nazwa": self.nazwa,
            "state": self.state,
            "board_kind": self.board_kind,
            "items_count": self.items_count,
            "workspace_id": self.workspace_id,
            "workspace_nazwa": self.workspace_nazwa,
            "owners": list(self.owners),
            "subscribers": list(self.subscribers),
            "kolumny": [dict(k) for k in self.kolumny],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class WynikTablic:
    tablice: tuple[Tablica, ...]
    usunietych: int
    discovery: dict[str, Any]

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "tablice": [t.do_snapshotu() for t in self.tablice],
            "podsumowanie": self.podsumowanie(),
            "discovery": dict(self.discovery),
        }

    def podsumowanie(self) -> dict[str, Any]:
        stany = Counter(t.state for t in self.tablice)
        rodzaje = Counter(t.board_kind for t in self.tablice)
        kolumn = [len(t.kolumny) for t in self.tablice]
        itemy = [t.items_count for t in self.tablice if t.items_count is not None]

        return {
            "razem": len(self.tablice),
            "po_state": dict(stany),
            "po_board_kind": dict(rodzaje),
            "usunietych_pominietych": self.usunietych,
            "workspace_ow": len({t.workspace_id for t in self.tablice if t.workspace_id}),
            "itemow_suma": sum(itemy),
            "tablic_bez_itemow": sum(1 for t in self.tablice if not t.items_count),
            "kolumn_suma": sum(kolumn),
            "kolumn_max": max(kolumn, default=0),
            "tablic_bez_wlasciciela": sum(1 for t in self.tablice if not t.owners),
        }


def zbuduj_zapytanie(zakres: Zakres) -> str:
    """Składa zapytanie z filtrem właściwym dla zakresu.

    Filtr wchodzi do treści zapytania, a nie jako `null` w zmiennych: monday
    nie gwarantuje, że jawny `null` znaczy „bez filtra", a cicho zignorowany
    filtr oznaczałby audyt całego konta zamiast wskazanej tablicy. Przy
    zakresie zawężonym to jest różnica między „dotknęliśmy jednej tablicy"
    a „przeszliśmy po całym koncie klienta".
    """
    if zakres.typ == "workspace":
        return _SZKIELET.format(deklaracje=", $ws: [ID!]", filtr=", workspace_ids: $ws")
    if zakres.typ == "tablice":
        return _SZKIELET.format(deklaracje=", $ids: [ID!]", filtr=", ids: $ids")
    return _SZKIELET.format(deklaracje="", filtr="")


def _zmienne(zakres: Zakres, *, limit: int, state: str) -> dict[str, Any]:
    zmienne: dict[str, Any] = {"limit": limit, "state": state}
    if zakres.typ == "workspace":
        zmienne["ws"] = list(zakres.workspace_ids)
    elif zakres.typ == "tablice":
        zmienne["ids"] = list(zakres.board_ids)
    return zmienne


def _hashe(wpisy: Any, client_id: str, sol: bytes) -> tuple[str, ...]:
    """Zamienia listę `{id}` na pseudonimy. Surowe id nie wychodzi z tej funkcji."""
    if not isinstance(wpisy, list):
        return ()
    return tuple(
        policz_hash(client_id, str(w["id"]), sol)
        for w in wpisy
        if isinstance(w, dict) and w.get("id") is not None
    )


def _tablica(surowa: dict[str, Any], *, client_id: str, sol: bytes) -> Tablica:
    """Buduje rekord z listy dozwolonych pól — jak w 3.4."""
    workspace = surowa.get("workspace")
    workspace = workspace if isinstance(workspace, dict) else None
    kolumny = surowa.get("columns") or []

    return Tablica(
        board_id=str(surowa.get("id", "")),
        nazwa=str(surowa.get("name") or ""),
        state=str(surowa.get("state") or ""),
        board_kind=str(surowa.get("board_kind") or ""),
        items_count=surowa.get("items_count"),
        workspace_id=str(workspace["id"]) if workspace and workspace.get("id") else None,
        workspace_nazwa=str(workspace.get("name")) if workspace else None,
        owners=_hashe(surowa.get("owners"), client_id, sol),
        subscribers=_hashe(surowa.get("subscribers"), client_id, sol),
        kolumny=tuple(
            {
                "id": str(k.get("id", "")),
                "title": str(k.get("title") or ""),
                "type": str(k.get("type") or ""),
            }
            for k in kolumny
            if isinstance(k, dict)
        ),
        created_at=surowa.get("created_at") or None,
        updated_at=surowa.get("updated_at") or None,
    )


async def zbierz_tablice(
    klient: MondayClient,
    zakres: Zakres,
    *,
    client_id: str,
    sol: bytes,
    limit: int = LIMIT_STRONY,
    zbieraj_usuniete: bool = False,
    maks_stron: int = 200,
) -> WynikTablic:
    """Zbiera tablice w zadanym zakresie. Nigdy nie dotyka itemów (D5)."""
    zebrane: list[Tablica] = []
    usunietych = 0
    zapytanie = zbuduj_zapytanie(zakres)

    async for surowa in klient.paginate(
        zapytanie,
        "boards",
        _zmienne(zakres, limit=limit, state="all"),
        etykieta="boards",
        maks_stron=maks_stron,
    ):
        tablica = _tablica(surowa, client_id=client_id, sol=sol)
        if tablica.state == "deleted" and not zbieraj_usuniete:
            usunietych += 1
            continue
        zebrane.append(tablica)

    bez_items_count = sum(1 for t in zebrane if t.items_count is None)
    discovery: dict[str, Any] = {
        "items_count_dostepne": bez_items_count == 0,
        "tablic_bez_items_count": bez_items_count,
        "owners_dostepne": any(t.owners for t in zebrane),
        "stany_w_liscie": list(STANY_ZBIERANE) if not zbieraj_usuniete else ["all"],
    }

    wynik = WynikTablic(
        tablice=tuple(zebrane),
        usunietych=usunietych,
        discovery=discovery,
    )

    # Nazwy tablic i tytuły kolumn pisze klient — adres e-mail w nazwie kolumny
    # to wyciek tak samo jak w polu `name` użytkownika. Pustą listę wpisów
    # podajemy świadomie: mapowania osób tu nie mamy, więc sprawdzamy to,
    # co da się sprawdzić bez niego. Pełne skanowanie robi 3.8 na złożonym
    # snapshocie.
    waliduj_brak_pii(json.dumps(wynik.do_snapshotu(), ensure_ascii=False), [])

    logger.info(
        "zebrano %d tablic (zakres: %s), pominiętych z kosza: %d, kolumn łącznie: %d",
        len(zebrane),
        zakres.opis(),
        usunietych,
        wynik.podsumowanie()["kolumn_suma"],
    )
    if bez_items_count:
        # Blokuje ocenę objętości tablicy, czyli O4 i BOARD_GHOST.
        logger.warning(
            "%d tablic bez `items_count` — dla nich objętości nie da się ocenić "
            "bez zejścia na itemy, czego D5 zabrania",
            bez_items_count,
        )
    return wynik
