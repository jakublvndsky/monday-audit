"""Collector — automatyzacje (etap 3.6).

**Specyfikacja 3.6 opisuje API, którego nie ma.** Rozpoznanie z 2026-07-30
(introspekcja schematu, `OTWARTE.md` O12) pokazało:

- `Board` **nie ma** pola `automations` — w 39 polach typu nie ma nic
  o automatyzacjach ani workflow
- `account { usage }` **nie istnieje**; korzeniowe `usage` to `CampaignsUsage`,
  czyli zużycie marketingowe, nie automatyzacje
- automatyzacje nazywają się w API **triggerami** i mają własne zapytania

Co z tego wynika dla kosztu: 3.6 ostrzega przed krokiem „liniowym per tablica,
~200 wywołań". Poziom konta załatwiają **trzy wywołania**, niezależnie od
wielkości konta. Sonda per tablica jest opcjonalna i twardo ograniczona.

Trzy zapytania, które faktycznie działają:

1. `account_trigger_statistics` — `success`, `failure`, `total` uruchomień.
   **To odpowiedź na O1.** Działa tylko BEZ filtra: `filters.board_id` jest
   typu `Int` (32 bity), a identyfikatory tablic na koncie CXLABS mają
   10 cyfr i przekraczają zakres. Filtr jest więc bezużyteczny — to
   ograniczenie API monday, nie nasze.
2. `account_triggers_statistics_by_entity_id(run_status:)` — mapa
   `automation_id → {total, powód: liczba}`. Kluczem jest **automatyzacja**,
   nie tablica, więc atrybucji do tablicy z tego nie ma.
3. `trigger_events(filters: {boardId: String})` — jedyna ścieżka per tablica,
   bo tu `boardId` jest Stringiem i przyjmuje pełny identyfikator. Zwraca
   pojedyncze zdarzenia, więc kosztuje wywołanie na tablicę i ma wolumen.

**Czego nie ma i nie będzie z tego API:** listy automatyzacji per tablica.
Nie da się powiedzieć „ta tablica ma 4 automatyzacje, z czego 2 martwe" —
tylko „ta tablica miała 0 zdarzeń automatyzacji w okresie". Dlatego
`AUTOMATION_DEAD` dostaje zwężony sygnał i musi to wiedzieć.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from monday_audit.klient import MondayClient
from monday_audit.osoby import waliduj_brak_pii

logger = logging.getLogger(__name__)

STATYSTYKI_KONTA = """
query { account_trigger_statistics { id success failure total } }
"""

PER_AUTOMATYZACJA = """
query ($status: TriggerEventState!) {
  account_triggers_statistics_by_entity_id (run_status: $status) {
    id automation_statistics workflow_statistics
  }
}
"""

ZDARZENIA_TABLICY = """
query ($f: TriggerEventsFiltersInput) {
  trigger_events (filters: $f) {
    triggerEvents { eventKind eventState triggerStartedAt entityKind is_test_run }
  }
}
"""

# Zmierzone: `trigger_events` zwraca 200 zdarzeń na stronę. Pełna strona znaczy
# „jest więcej", więc sonda musi to odnotować, zamiast udawać, że policzyła.
ROZMIAR_STRONY = 200

# Stany uruchomień z enuma `TriggerEventState`.
STANY_URUCHOMIEN = ("success", "failure", "exhausted")

# Domyślny sufit sondowania. Workspace 6576039 ma 105 aktywnych tablic;
# sondowanie wszystkich to 105 wywołań, czyli dokładnie ten wolumen,
# którego przy audycie nie chcemy brać bez decyzji.
MAKS_SOND = 10


@dataclass(frozen=True, slots=True)
class SondaTablicy:
    """Wynik jednego zapytania `trigger_events` zawężonego do tablicy."""

    board_id: str
    zdarzen: int
    po_stanie: dict[str, int]
    testowych: int
    najnowsze: str | None
    strona_pelna: bool

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "board_id": self.board_id,
            "zdarzen": self.zdarzen,
            "po_stanie": dict(self.po_stanie),
            "testowych": self.testowych,
            "najnowsze": self.najnowsze,
            "strona_pelna": self.strona_pelna,
        }


@dataclass(frozen=True, slots=True)
class WynikAutomatyzacji:
    uruchomien_sukces: int | None
    uruchomien_bledow: int | None
    uruchomien_razem: int | None
    automatyzacje_z_bledami: tuple[dict[str, Any], ...]
    sondy: tuple[SondaTablicy, ...]
    pominietych_tablic: int
    discovery: dict[str, Any]

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "uruchomienia": {
                "sukces": self.uruchomien_sukces,
                "bledow": self.uruchomien_bledow,
                "razem": self.uruchomien_razem,
            },
            "automatyzacje_z_bledami": [dict(a) for a in self.automatyzacje_z_bledami],
            "sondy_tablic": [s.do_snapshotu() for s in self.sondy],
            "podsumowanie": self.podsumowanie(),
            "discovery": dict(self.discovery),
        }

    def podsumowanie(self) -> dict[str, Any]:
        return {
            "automatyzacji_z_bledami": len(self.automatyzacje_z_bledami),
            "tablic_sondowanych": len(self.sondy),
            "tablic_pominietych": self.pominietych_tablic,
            "tablic_bez_zdarzen": sum(1 for s in self.sondy if s.zdarzen == 0),
            "tablic_z_urwana_strona": sum(1 for s in self.sondy if s.strona_pelna),
        }


def _rozbij_statystyki(surowe: Any) -> tuple[dict[str, Any], ...]:
    """Zamienia mapę `automation_id → {total, powód: n}` na listę rekordów.

    monday zwraca to jako JSON (czasem jako string), a klucze to identyfikatory
    automatyzacji, nie tablic. Powody błędów to teksty od monday, nie treść
    klienta — mimo to przechodzą przez walidację PII razem z resztą payloadu.
    """
    if isinstance(surowe, str):
        try:
            surowe = json.loads(surowe)
        except ValueError:
            return ()
    if not isinstance(surowe, dict):
        return ()

    rekordy: list[dict[str, Any]] = []
    for automation_id, wartosci in surowe.items():
        if not isinstance(wartosci, dict):
            continue
        powody = {k: v for k, v in wartosci.items() if k != "total"}
        rekordy.append(
            {
                "automation_id": str(automation_id),
                "total": wartosci.get("total"),
                "powody": powody,
            }
        )
    return tuple(sorted(rekordy, key=lambda r: str(r["automation_id"])))


async def statystyki_konta(klient: MondayClient) -> tuple[dict[str, int | None], dict[str, Any]]:
    """Trzy liczby uruchomień dla całego konta. Jedno wywołanie.

    Nie da się tego zawęzić do workspace ani tablicy — filtr `board_id`
    jest zepsuty (Int32). Zapytanie zwraca wyłącznie trzy licznikowe liczby,
    nie wylicza ani nie ujawnia żadnych tablic.
    """
    dane = await klient.query(STATYSTYKI_KONTA, etykieta="triggery")
    wpis = dane.get("account_trigger_statistics") or {}
    liczby = {
        "sukces": wpis.get("success"),
        "bledow": wpis.get("failure"),
        "razem": wpis.get("total"),
    }
    dostepne = liczby["razem"] is not None
    logger.info(
        "[DISCOVERY] %s account_trigger_statistics: %s",
        "✅" if dostepne else "❌",
        liczby,
    )
    return liczby, {"uruchomienia_dostepne": dostepne}


async def sonduj_tablice(
    klient: MondayClient,
    board_ids: Sequence[str],
    *,
    od: str | None = None,
    do: str | None = None,
    maks_sond: int = MAKS_SOND,
) -> tuple[tuple[SondaTablicy, ...], int]:
    """Po jednym wywołaniu na tablicę, z twardym sufitem.

    Zwraca też liczbę tablic POMINIĘTYCH przez sufit. Cichy limit wyglądałby
    w raporcie jak „sprawdziliśmy wszystko", a to jest dokładnie ten rodzaj
    milczenia, którego ten projekt zabrania.
    """
    do_sondowania = list(board_ids[:maks_sond])
    pominietych = max(0, len(board_ids) - len(do_sondowania))
    if pominietych:
        logger.warning(
            "sonduję %d z %d tablic — sufit maks_sond=%d; %d tablic POMINIĘTYCH "
            "i odnotowanych w snapshocie",
            len(do_sondowania),
            len(board_ids),
            maks_sond,
            pominietych,
        )

    sondy: list[SondaTablicy] = []
    for board_id in do_sondowania:
        filtry: dict[str, Any] = {"boardId": str(board_id)}
        if od or do:
            filtry["dateRange"] = {"startDate": od, "endDate": do}

        dane = await klient.query(ZDARZENIA_TABLICY, {"f": filtry}, etykieta="triggery")
        zdarzenia = (dane.get("trigger_events") or {}).get("triggerEvents") or []
        znaczniki = sorted(
            str(z["triggerStartedAt"]) for z in zdarzenia if z.get("triggerStartedAt")
        )
        sondy.append(
            SondaTablicy(
                board_id=str(board_id),
                zdarzen=len(zdarzenia),
                po_stanie=dict(Counter(str(z.get("eventState")) for z in zdarzenia)),
                testowych=sum(1 for z in zdarzenia if z.get("is_test_run")),
                najnowsze=znaczniki[-1] if znaczniki else None,
                strona_pelna=len(zdarzenia) >= ROZMIAR_STRONY,
            )
        )

    return tuple(sondy), pominietych


async def zbierz_automatyzacje(
    klient: MondayClient,
    *,
    board_ids: Sequence[str] = (),
    od: str | None = None,
    do: str | None = None,
    maks_sond: int = MAKS_SOND,
) -> WynikAutomatyzacji:
    """Zbiera to, co API faktycznie daje: statystyki konta plus opcjonalne sondy.

    Bez `board_ids` kosztuje **trzy wywołania** niezależnie od wielkości konta.
    """
    liczby, discovery = await statystyki_konta(klient)

    z_bledami: tuple[dict[str, Any], ...] = ()
    for status in ("failure", "success"):
        dane = await klient.query(PER_AUTOMATYZACJA, {"status": status}, etykieta="triggery")
        wpis = dane.get("account_triggers_statistics_by_entity_id") or {}
        rekordy = _rozbij_statystyki(wpis.get("automation_statistics"))
        if status == "failure":
            z_bledami = rekordy
        discovery[f"automatyzacji_ze_stanem_{status}"] = len(rekordy)

    sondy, pominietych = await sonduj_tablice(klient, board_ids, od=od, do=do, maks_sond=maks_sond)

    discovery.update(
        {
            # Ograniczenia API, nie nasze — detektory muszą je znać.
            "lista_automatyzacji_dostepna": False,
            "atrybucja_per_tablica": bool(sondy),
            "filtr_board_id_zepsuty_int32": True,
            "okno_czasowe": {"od": od, "do": do} if (od or do) else None,
            "stany_uruchomien": list(STANY_URUCHOMIEN),
        }
    )

    wynik = WynikAutomatyzacji(
        uruchomien_sukces=liczby["sukces"],
        uruchomien_bledow=liczby["bledow"],
        uruchomien_razem=liczby["razem"],
        automatyzacje_z_bledami=z_bledami,
        sondy=sondy,
        pominietych_tablic=pominietych,
        discovery=discovery,
    )

    waliduj_brak_pii(json.dumps(wynik.do_snapshotu(), ensure_ascii=False), [])

    logger.info(
        "automatyzacje: %s uruchomień razem, %d automatyzacji z błędami, "
        "%d tablic sondowanych, %d pominiętych",
        liczby["razem"],
        len(z_bledami),
        len(sondy),
        pominietych,
    )
    if not discovery["lista_automatyzacji_dostepna"]:
        # Wymóg 3.6: brak danych obsłużony jawnie, nie ciszą.
        logger.warning(
            "API nie udostępnia listy automatyzacji per tablica — AUTOMATION_DEAD "
            "dostaje zwężony sygnał: zero zdarzeń w okresie, a nie zero uruchomień "
            "konkretnej automatyzacji"
        )
    return wynik
