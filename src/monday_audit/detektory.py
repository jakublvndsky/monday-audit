"""Detektory deterministyczne — sygnały wzbudzające hipotezy (etap 3.9).

**Zero AI.** Czysty SQL po JSON-ie zapisanym w `snapshots.payload`.

Dlaczego SQL, a nie Python po sparsowanym słowniku: snapshot jest niemutowalny
i jest jedynym źródłem prawdy (D7). Zapytanie idzie prosto do tego źródła, więc
nie ma warstwy, w której dane dałoby się po drodze „poprawić". Do tego SQLite
z JSON1 daje deterministyczną kolejność przez `ORDER BY`, a powtarzalność jest
warunkiem odbioru 3.9: **ten sam snapshot musi dać tę samą listę hipotez.**

Detektor NIE stwierdza znaleziska. Wzbudza hipotezę — czyli mówi „tu coś nie
pasuje, sprawdź". Rozstrzygnięcie należy do agenta (3.11), a ten dostaje na to
budżet wywołań z rubryki. Rozdział jest celowy: wzbudzenie musi być tanie
i wyczerpujące, rozstrzyganie jest drogie i wybiórcze.

**Progi czasowe pochodzą ze snapshotu, nie z zegara.** Każdy detektor porównuje
się z `meta.okno_od`, które collector zapisał w momencie runu. Gdyby brały
`now()`, ten sam snapshot przepuszczony przez detektory pół roku później dałby
inną listę — a etap 4 wymaga porównywania starych snapshotów z nową rubryką.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from monday_audit.rubryka import Rubryka, wczytaj_rubryke

logger = logging.getLogger(__name__)


class DetektorError(RuntimeError):
    """Nie da się wzbudzić hipotez — brak snapshotu albo niespójny payload."""


@dataclass(frozen=True, slots=True)
class Hipoteza:
    """Sygnał do zbadania. Kształt narzucony przez 03-build.md 3.9.

    `fakty` to dokładnie te pola, które rubryka wymienia w `dowod` danej klasy.
    Nie jest to swobodny worek na kontekst: finding bez `dowod` odpada na
    walidacji kontraktu (zakaz twardy), więc hipoteza, która nie niesie faktów,
    jest hipotezą, której agent nie ma z czego domknąć.
    """

    klasa_id: str
    obiekt_id: str
    fakty: dict[str, Any] = field(default_factory=dict)
    budzet_wywolan: int = 0

    def do_zapisu(self) -> dict[str, Any]:
        return {
            "klasa_id": self.klasa_id,
            "obiekt_id": self.obiekt_id,
            "fakty": dict(self.fakty),
            "budzet_wywolan": self.budzet_wywolan,
        }


# Detektor: (połączenie, snapshot_id, budżet klasy) → hipotezy.
Detektor = Callable[[sqlite3.Connection, int, int], list[Hipoteza]]


def _meta(con: sqlite3.Connection, snapshot_id: int) -> dict[str, Any]:
    """Metadane snapshotu. Stąd biorą się progi czasowe, nie z zegara."""
    wiersz = con.execute(
        "SELECT json_extract(payload, '$.meta') AS meta FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if wiersz is None or wiersz["meta"] is None:
        raise DetektorError(f"snapshot {snapshot_id} nie istnieje albo nie ma sekcji `meta`")
    return json.loads(wiersz["meta"])


# ── ZOMBIE_ACCOUNT ───────────────────────────────────────────────────────
#
# Rubryka 0.2:
#   kind IN (admin, member) AND status = ACTIVE
#   AND (last_activity > 90 dni
#        OR (last_activity = null AND autor nieobecny w activity logach okna))
#
# Trzy rzeczy, na które trzeba tu uważać, wszystkie zmierzone:
#
# 1. `kind IN (admin, member)` NIE jest kosmetyką. Na koncie CXLABS z 95
#    rekordów tylko 19 zajmuje płatne miejsce; pozostałe 76 to goście, konta
#    podglądowe i 36 kont agentów AI. Bez tego filtra klasa wystawiłaby klientowi
#    rachunek za konta, które nie są ludźmi (O17).
# 2. `last_activity = null` NIE znaczy „martwy". Pole jest puste u 37 z 95 osób
#    i znaczy „nie wiem". Dlatego dla tych kont rozstrzyga druga przesłanka:
#    obecność pseudonimu autora w activity logach okna (3.7). Nieobecny w logach
#    ORAZ bez `last_activity` to mocny kandydat; sam brak pola to nic.
# 3. `became_active_at` młodsze niż okno = konto świeże, nie martwe. Świeżo
#    dodany człowiek nie ma jeszcze historii i nie jest zombie.
#
# Porównanie czasu przez `substr(..., 1, 19)`, czyli po `YYYY-MM-DDTHH:MM:SS`.
# Bez tego porównywalibyśmy leksykograficznie napisy o RÓŻNYCH ogonach: monday
# zwraca `2026-01-21T02:41:52Z`, a collector zapisuje okno jako
# `2026-05-03T18:00:36.382683+00:00`. Na tych danych wynik i tak wychodzi
# poprawny, ale wyłącznie przez zbieg okoliczności — `Z` wypada w ASCII po
# kropce. Obcięcie do 19 znaków daje ten sam format po obu stronach.
#
# Warunek poprawności: oba znaczniki są w UTC. Są — monday oddaje `Z`,
# a collector `+00:00`. Gdyby API zaczęło zwracać strefę lokalną, ten SQL
# byłby cicho zły, więc `test_znaczniki_z_roznymi_ogonami_porownuja_sie`
# pilnuje przypadku granicznego.

_ZOMBIE = """
WITH
snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
-- Wszyscy autorzy widziani w activity logach okna. To druga przesłanka:
-- brak w logach jest sygnałem tylko wtedy, gdy log w ogóle był zbierany.
autorzy AS (
    SELECT DISTINCT autor.value AS user_hash
    FROM snap,
         json_each(snap.payload, '$.aktywnosc.aktywnosc_tablic') AS tablica,
         json_each(tablica.value, '$.autorzy') AS autor
),
osoby AS (
    SELECT
        json_extract(o.value, '$.user_hash')        AS user_hash,
        json_extract(o.value, '$.kind')             AS kind,
        json_extract(o.value, '$.status')           AS status,
        json_extract(o.value, '$.last_activity')    AS last_activity,
        json_extract(o.value, '$.became_active_at') AS became_active_at
    FROM snap, json_each(snap.payload, '$.uzytkownicy.uzytkownicy') AS o
)
SELECT
    osoby.user_hash,
    osoby.kind,
    osoby.status,
    osoby.last_activity,
    osoby.became_active_at,
    (autorzy.user_hash IS NOT NULL) AS obecnosc_w_logach
FROM osoby
LEFT JOIN autorzy USING (user_hash)
WHERE osoby.kind IN ('admin', 'member')          -- tylko płatne miejsca (O17)
  AND osoby.status = 'ACTIVE'                     -- PENDING to NEVER_ACTIVATED
  AND autorzy.user_hash IS NULL                   -- nieobecny w logach okna
  AND (
        (osoby.last_activity IS NOT NULL
         AND substr(osoby.last_activity, 1, 19) < substr(:okno_od, 1, 19))
     OR osoby.last_activity IS NULL               -- „nie wiem" + brak w logach
      )
  AND (osoby.became_active_at IS NULL
       OR substr(osoby.became_active_at, 1, 19) < substr(:okno_od, 1, 19))
ORDER BY osoby.user_hash                          -- powtarzalność listy
"""


def zombie_account(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Konto zajmujące płatne miejsce, bez śladu aktywności w oknie."""
    meta = _meta(con, snapshot_id)
    plan_tier = _tier(con, snapshot_id)

    hipotezy: list[Hipoteza] = []
    for w in con.execute(_ZOMBIE, {"snapshot_id": snapshot_id, "okno_od": meta["okno_od"]}):
        hipotezy.append(
            Hipoteza(
                klasa_id="ZOMBIE_ACCOUNT",
                obiekt_id=w["user_hash"],
                # Pola dokładnie z `dowod` klasy w rubryce 0.2.
                fakty={
                    "user_hash": w["user_hash"],
                    "kind": w["kind"],
                    "status": w["status"],
                    "last_activity": w["last_activity"],
                    "obecnosc_w_logach": bool(w["obecnosc_w_logach"]),
                    "plan_tier": plan_tier,
                    # Poza `dowod`, ale agent bez tego nie odróżni „nie wiem"
                    # od „wiem, że dawno": to jedyne dwie różne drogi do tej
                    # hipotezy i prowadzą do różnych rekomendacji.
                    "podstawa": (
                        "last_activity starsze niż okno"
                        if w["last_activity"]
                        else "brak last_activity ORAZ brak autora w logach okna"
                    ),
                    "okno_od": meta["okno_od"],
                },
                budzet_wywolan=budzet,
            )
        )
    return hipotezy


def _tier(con: sqlite3.Connection, snapshot_id: int) -> str | None:
    """Tier planu z `konto.plan.tier`.

    Na CXLABS `account.plan` zwraca null przy tokenie bez admina, więc collector
    dokłada `account.tier` jako źródło zapasowe i zapisuje w `plan.zrodlo_tieru`,
    skąd wartość wzięła. Tu wystarczy sama wartość — `ZOMBIE_ACCOUNT` używa jej
    tylko do wyceny licencji (O2).
    """
    wiersz = con.execute(
        "SELECT json_extract(payload, '$.konto.plan.tier') AS tier FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    return wiersz["tier"] if wiersz else None


# ── AUTOMATION_DEAD ──────────────────────────────────────────────────────
#
# Rubryka 0.2:
#   failure > 0 OR exhausted > 0 OR (failure / (failure + success)) > 0.05
#
# Klasa pyta o automatyzację, która SIĘ ODPALA i kończy błędem — nie o taką,
# która nigdy nie wystartowała. Tej drugiej nie da się zobaczyć: automatyzacja
# bez uruchomień nie pojawia się w statystykach wcale, a listy automatyzacji
# API nie udostępnia (O1, O12). „Zbudowana i cicho psuje się od miesięcy" jest
# zresztą mocniejszym znaleziskiem, bo dotyczy procesu, na który ktoś liczy.
#
# Czego tu NIE MA i nie wolno dopisać: `board_id`. API nie oddaje przypisania
# automatyzacji do tablicy, więc każde zdanie „ta automatyzacja na tablicy X"
# byłoby zgadywaniem. Rubryka mówi agentowi wprost, żeby tego nie twierdził.

# Poniżej tego progu jeden błąd na tysiąc udanych uruchomień to szum, nie awaria.
PROG_UDZIALU_BLEDOW = 0.05

_AUTOMATION_DEAD = """
WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id)
SELECT
    json_extract(a.value, '$.automation_id')   AS automation_id,
    COALESCE(json_extract(a.value, '$.success'), 0)   AS success,
    COALESCE(json_extract(a.value, '$.failure'), 0)   AS failure,
    COALESCE(json_extract(a.value, '$.exhausted'), 0) AS exhausted,
    json_extract(a.value, '$.powody_bledow')   AS powody_bledow
FROM snap, json_each(snap.payload, '$.automatyzacje.statystyki_automatyzacji') AS a
WHERE COALESCE(json_extract(a.value, '$.failure'), 0) > 0
   OR COALESCE(json_extract(a.value, '$.exhausted'), 0) > 0
ORDER BY automation_id
"""


def automation_dead(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Automatyzacja, która się uruchamia i kończy błędem albo wyczerpaniem."""
    hipotezy: list[Hipoteza] = []
    for w in con.execute(_AUTOMATION_DEAD, {"snapshot_id": snapshot_id}):
        uruchomien = w["failure"] + w["success"]
        udzial = round(w["failure"] / uruchomien, 4) if uruchomien else None
        hipotezy.append(
            Hipoteza(
                klasa_id="AUTOMATION_DEAD",
                obiekt_id=str(w["automation_id"]),
                fakty={
                    "automation_id": str(w["automation_id"]),
                    "failure": w["failure"],
                    "success": w["success"],
                    "exhausted": w["exhausted"],
                    "powody_bledow": json.loads(w["powody_bledow"] or "{}"),
                    "udzial_bledow": udzial,
                    # Warunek odrzucenia z rubryki mówi „pojedynczy błąd przy
                    # tysiącach udanych uruchomień to szum". Detektor tego NIE
                    # odrzuca sam — podaje agentowi gotową ocenę, bo odrzucenie
                    # jest decyzją, którą trzeba uzasadnić w `hipotezy_odrzucone`.
                    "powyzej_progu_udzialu": bool(
                        udzial is not None and udzial > PROG_UDZIALU_BLEDOW
                    ),
                    "prog_udzialu": PROG_UDZIALU_BLEDOW,
                },
                budzet_wywolan=budzet,
            )
        )
    return hipotezy


# Rejestr. Klasy bez wpisu nie mają jeszcze detektora — `uruchom_detektory`
# mówi o tym wprost, zamiast po cichu zwracać pustą listę.
DETEKTORY: dict[str, Detektor] = {
    "ZOMBIE_ACCOUNT": zombie_account,
    "AUTOMATION_DEAD": automation_dead,
}


def uruchom_detektory(
    con: sqlite3.Connection,
    snapshot_id: int,
    rubryka: Rubryka | None = None,
) -> tuple[list[Hipoteza], dict[str, Any]]:
    """Wzbudza hipotezy ze zamrożonego snapshotu. Zwraca hipotezy i raport.

    Raport wymienia klasy BEZ detektora. Cicha pusta lista wyglądałaby jak
    „sprawdzone, nic nie ma", a to dwie różne rzeczy i tylko jedna z nich
    jest prawdą.
    """
    rubryka = rubryka or wczytaj_rubryke()
    do_detekcji = rubryka.do_detekcji()

    hipotezy: list[Hipoteza] = []
    zbudowane: list[str] = []
    bez_detektora: list[str] = []

    for klasa in do_detekcji:
        detektor = DETEKTORY.get(klasa.id)
        if detektor is None:
            bez_detektora.append(klasa.id)
            continue
        wzbudzone = detektor(con, snapshot_id, klasa.budzet_wywolan)
        zbudowane.append(klasa.id)
        hipotezy.extend(wzbudzone)
        logger.info("%s: %d hipotez", klasa.id, len(wzbudzone))

    # Sortowanie na końcu, po (klasa, obiekt) — powtarzalność listy jest
    # warunkiem odbioru 3.9, a kolejność rejestru nie jest gwarancją.
    hipotezy.sort(key=lambda h: (h.klasa_id, h.obiekt_id))

    budzet_zamowiony = sum(h.budzet_wywolan for h in hipotezy)
    raport: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "rubric_version": rubryka.wersja,
        "hipotez": len(hipotezy),
        "po_klasie": {k: sum(1 for h in hipotezy if h.klasa_id == k) for k in sorted(zbudowane)},
        "budzet_zamowiony": budzet_zamowiony,
        "bezpiecznik_globalny": rubryka.maks_wywolan_na_run,
        "klasy_bez_detektora": sorted(bez_detektora),
    }
    if bez_detektora:
        logger.warning(
            '%d klas bez detektora: %s — to NIE znaczy „nic nie znaleziono"',
            len(bez_detektora),
            ", ".join(sorted(bez_detektora)),
        )
    if budzet_zamowiony > rubryka.maks_wywolan_na_run:
        logger.warning(
            "hipotezy zamawiają %d wywołań, bezpiecznik globalny to %d — "
            "agent będzie musiał priorytetyzować (3.11)",
            budzet_zamowiony,
            rubryka.maks_wywolan_na_run,
        )
    return hipotezy, raport
