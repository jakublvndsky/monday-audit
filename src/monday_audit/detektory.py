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
from datetime import datetime, timedelta
from typing import Any

from monday_audit.osoby import RODZAJ_AGENT
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


def _prog(meta: dict[str, Any], dni: int) -> str:
    """Znacznik `run_at` minus N dni, obcięty do sekundy.

    Kilka klas ma progi inne niż okno collectora: 180 dni dla nieaktywnych
    gości, 60 dni dla zamilknięcia tablicy, 7 i 30 dni dla spadku
    zaangażowania. Wszystkie liczą się od `run_at` ZE SNAPSHOTU, więc
    zamrożony snapshot daje ten sam wynik niezależnie od dnia uruchomienia.

    Obcięcie do 19 znaków, bo po tym samym obcięciu porównuje się w SQL —
    monday oddaje `...52Z`, a collector `...36.382683+00:00`.
    """
    run_at = str(meta.get("run_at") or "")
    try:
        kiedy = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
    except ValueError:
        raise DetektorError(f"`meta.run_at` nie jest datą ISO: {run_at!r}") from None
    return (kiedy - timedelta(days=dni)).isoformat()[:19]


def _konto_id(con: sqlite3.Connection, snapshot_id: int) -> str:
    """Identyfikator konta — `obiekt_id` dla klas, które nie dotyczą obiektu.

    ZOMBIE_ACCOUNT dotyczy osoby, BOARD_GHOST tablicy, ale GUEST_SPRAWL
    i AUTOMATION_ABSENT mówią o koncie jako całości. Puste `obiekt_id`
    zostawiłoby hipotezę bez adresu w tabeli `hipotezy_odrzucone`.
    """
    wiersz = con.execute(
        "SELECT json_extract(payload, '$.konto.konto.id') AS id FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    return str(wiersz["id"]) if wiersz and wiersz["id"] else "konto"


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


# ── wspólne wejście dla klas tablicowych ─────────────────────────────────
#
# `typ = 'board'` w każdej z nich (O14). `boards` zwraca też tablice
# podelementów i DOKUMENTY — zmierzone na workspace 6576039: board 97,
# document 5, sub_items_board 3. Dokument nie ma itemów ani kolumn w sensie
# tablicy, więc w BOARD_GHOST wyglądałby na porzucony, a w BOARD_OVERCOMPLEX
# na pusty. Filtrowanie po NAZWIE byłoby błędem: nazwa jest lokalizowana.
_TABLICE = """
tablice AS (
    SELECT
        json_extract(t.value, '$.board_id')        AS board_id,
        json_extract(t.value, '$.nazwa')           AS nazwa,
        json_extract(t.value, '$.typ')             AS typ,
        json_extract(t.value, '$.state')           AS state,
        json_extract(t.value, '$.items_count')     AS items_count,
        json_extract(t.value, '$.created_at')      AS created_at,
        json_extract(t.value, '$.updated_at')      AS updated_at,
        json_extract(t.value, '$.workspace_id')    AS workspace_id,
        json_array_length(COALESCE(json_extract(t.value, '$.kolumny'), '[]')) AS kolumn,
        json_extract(t.value, '$.owners')          AS owners,
        t.value                                     AS surowa
    FROM snap, json_each(snap.payload, '$.tablice.tablice') AS t
)
"""

# Sygnały aktywności per tablica. `LEFT JOIN` do tego jest OBOWIĄZKOWY:
# tablica nieobecna tutaj nie znaczy „bez aktywności", tylko „nie była
# próbkowana". Zlanie tych dwóch przypadków dałoby BOARD_GHOST na tablicach,
# których nikt nie sprawdził.
_AKTYWNOSC = """
aktywnosc AS (
    SELECT
        json_extract(a.value, '$.board_id')       AS board_id,
        json_extract(a.value, '$.wpisow')         AS wpisow,
        json_extract(a.value, '$.najnowszy_at')   AS najnowszy_at,
        json_extract(a.value, '$.po_klasie')      AS po_klasie,
        json_extract(a.value, '$.kubelki_dni')    AS kubelki_dni,
        json_extract(a.value, '$.udzial_autorow') AS udzial_autorow
    FROM snap, json_each(snap.payload, '$.aktywnosc.aktywnosc_tablic') AS a
)
"""


# ── BOARD_GHOST ──────────────────────────────────────────────────────────

_BOARD_GHOST = f"""
WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
{_TABLICE},
{_AKTYWNOSC}
SELECT
    tablice.board_id, tablice.nazwa, tablice.typ, tablice.items_count,
    tablice.created_at, tablice.updated_at,
    aktywnosc.wpisow, aktywnosc.najnowszy_at,
    aktywnosc.po_klasie, aktywnosc.kubelki_dni
FROM tablice
JOIN aktywnosc USING (board_id)          -- JOIN, nie LEFT: bez próbki nie orzekamy
WHERE tablice.typ = 'board'
  AND tablice.state = 'active'
  AND COALESCE(tablice.items_count, 0) > 0
  AND substr(tablice.created_at, 1, 19) < substr(:okno_od, 1, 19)
  AND (
        aktywnosc.wpisow = 0
     OR aktywnosc.najnowszy_at IS NULL
     OR substr(aktywnosc.najnowszy_at, 1, 19) < substr(:okno_od, 1, 19)
      )
ORDER BY tablice.board_id
"""


def board_ghost(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Aktywna tablica z itemami, na której nikt nic nie zmienił w oknie.

    Sygnał stoi na `najnowszy_at` z activity logu, NIE na `updated_at` (O18).
    Zmierzone na 105 tablicach: log był nowszy niż `updated_at` w 94
    przypadkach, zgodny w 11, starszy w ZERO, przy rozbieżności do 40,6 dnia.
    `updated_at` śledzi metadane tablicy, nie pracę na itemach, więc sygnał
    na nim uznawałby za martwe tablice, na których pracowano trzy tygodnie temu.
    """
    meta = _meta(con, snapshot_id)
    parametry = {"snapshot_id": snapshot_id, "okno_od": meta["okno_od"]}

    hipotezy = [
        Hipoteza(
            klasa_id="BOARD_GHOST",
            obiekt_id=str(w["board_id"]),
            fakty={
                "board_id": str(w["board_id"]),
                "nazwa": w["nazwa"],
                "typ": w["typ"],
                "najnowszy_at": w["najnowszy_at"],
                "items_count": w["items_count"],
                "po_klasie": json.loads(w["po_klasie"] or "{}"),
                "kubelki_dni": json.loads(w["kubelki_dni"] or "{}"),
                # Dwa dodatkowe fakty dla agenta: `updated_at` do porównania
                # z logiem (jeśli się rozjeżdżają, ktoś ruszał ustawienia,
                # a nie pracował) i wiek tablicy.
                "updated_at": w["updated_at"],
                "created_at": w["created_at"],
                "wpisow_w_oknie": w["wpisow"],
                "okno_od": meta["okno_od"],
            },
            budzet_wywolan=budzet,
        )
        for w in con.execute(_BOARD_GHOST, parametry)
    ]

    # „Nie próbkowano" i „brak aktywności" to dwie różne rzeczy. Liczba idzie
    # do logu, bo raport nie może wyglądać na pełne pokrycie, gdy nie jest.
    nieprobkowane = con.execute(
        f"""
        WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
        {_TABLICE}, {_AKTYWNOSC}
        SELECT COUNT(*) AS n FROM tablice
        LEFT JOIN aktywnosc USING (board_id)
        WHERE tablice.typ = 'board' AND tablice.state = 'active'
          AND aktywnosc.board_id IS NULL
        """,
        {"snapshot_id": snapshot_id},
    ).fetchone()["n"]
    if nieprobkowane:
        logger.warning(
            "BOARD_GHOST: %d aktywnych tablic bez próbki logu — o nich NIE orzekamy "
            "(uruchom z --wszystkie-logi, żeby domknąć pokrycie)",
            nieprobkowane,
        )
    return hipotezy


# ── BOARD_NO_OWNER ───────────────────────────────────────────────────────

_BOARD_NO_OWNER = f"""
WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
{_TABLICE},
{_AKTYWNOSC},
osoby AS (
    SELECT json_extract(o.value, '$.user_hash') AS user_hash,
           json_extract(o.value, '$.status')    AS status,
           json_extract(o.value, '$.kind')      AS kind
    FROM snap, json_each(snap.payload, '$.uzytkownicy.uzytkownicy') AS o
),
-- Właściciele w rozbiciu na tych, którzy są jeszcze aktywni, i pozostałych.
wlasciciele AS (
    SELECT
        tablice.board_id,
        COUNT(*)                                                  AS wszystkich,
        SUM(CASE WHEN osoby.status = 'ACTIVE' THEN 1 ELSE 0 END)   AS aktywnych
    FROM tablice, json_each(tablice.owners) AS wl
    LEFT JOIN osoby ON osoby.user_hash = wl.value
    GROUP BY tablice.board_id
)
SELECT
    tablice.board_id, tablice.nazwa, tablice.owners, tablice.updated_at,
    aktywnosc.udzial_autorow,
    COALESCE(wlasciciele.wszystkich, 0) AS wlascicieli,
    COALESCE(wlasciciele.aktywnych, 0)  AS wlascicieli_aktywnych
FROM tablice
LEFT JOIN wlasciciele USING (board_id)
LEFT JOIN aktywnosc   USING (board_id)
WHERE tablice.typ = 'board'
  AND tablice.state = 'active'
  AND COALESCE(wlasciciele.aktywnych, 0) = 0
ORDER BY tablice.board_id
"""


def board_no_owner(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Aktywna tablica bez właściciela albo z samymi nieaktywnymi właścicielami."""
    hipotezy: list[Hipoteza] = []
    for w in con.execute(_BOARD_NO_OWNER, {"snapshot_id": snapshot_id}):
        udzialy: dict[str, int] = json.loads(w["udzial_autorow"] or "{}")
        # Najaktywniejszy autor to kandydat na właściciela — i to jest cała
        # wartość tej klasy: nie „brakuje pola", a „nikt formalnie nie odpowiada
        # za tablicę, na której ktoś realnie pracuje".
        top = max(udzialy.items(), key=lambda p: (p[1], p[0]))[0] if udzialy else None
        hipotezy.append(
            Hipoteza(
                klasa_id="BOARD_NO_OWNER",
                obiekt_id=str(w["board_id"]),
                fakty={
                    "board_id": str(w["board_id"]),
                    "nazwa": w["nazwa"],
                    "owners": json.loads(w["owners"] or "[]"),
                    "updated_at": w["updated_at"],
                    "top_kontrybutor_hash": top,
                    "podstawa": (
                        "brak właścicieli"
                        if w["wlascicieli"] == 0
                        else f"{w['wlascicieli']} właścicieli, żaden ze statusem ACTIVE"
                    ),
                },
                budzet_wywolan=budzet,
            )
        )
    return hipotezy


# ── BOARD_OVERCOMPLEX ────────────────────────────────────────────────────

# Rubryka: `liczba_kolumn > 15`.
PROG_KOLUMN = 15

_BOARD_OVERCOMPLEX = f"""
WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
{_TABLICE}
SELECT
    board_id, nazwa, kolumn, items_count,
    (SELECT json_group_array(json_extract(k.value, '$.type'))
     FROM json_each(json_extract(surowa, '$.kolumny')) AS k) AS typy_kolumn
FROM tablice
WHERE typ = 'board'
  AND state = 'active'
  AND kolumn > :prog
ORDER BY kolumn DESC, board_id
"""


def board_overcomplex(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Tablica z liczbą kolumn powyżej progu.

    `kolumny_martwe[]` i `rozmiar_probki` z `dowod` NIE są tu wypełniane —
    wymagają próbki itemów, czyli jedynego świadomego wyjątku od D5, i to
    jest robota agenta (budżet 8). Detektor daje mu punkt wejścia i rozkład
    typów kolumn, bo kolumny formuł i lustra to inna historia niż 20 pól
    tekstowych wypełnianych ręcznie.
    """
    hipotezy: list[Hipoteza] = []
    for w in con.execute(_BOARD_OVERCOMPLEX, {"snapshot_id": snapshot_id, "prog": PROG_KOLUMN}):
        typy: list[str] = json.loads(w["typy_kolumn"] or "[]")
        rozklad: dict[str, int] = {}
        for typ in typy:
            rozklad[typ] = rozklad.get(typ, 0) + 1
        hipotezy.append(
            Hipoteza(
                klasa_id="BOARD_OVERCOMPLEX",
                obiekt_id=str(w["board_id"]),
                fakty={
                    "board_id": str(w["board_id"]),
                    "liczba_kolumn": w["kolumn"],
                    "prog_kolumn": PROG_KOLUMN,
                    "items_count": w["items_count"],
                    "nazwa": w["nazwa"],
                    "typy_kolumn": dict(sorted(rozklad.items())),
                    # Jawnie, żeby agent nie myślał, że detektor to policzył.
                    "kolumny_martwe": None,
                    "rozmiar_probki": None,
                    "do_zbadania_przez_agenta": "kolumny_martwe[] wymagają próbki itemów (D5)",
                },
                budzet_wywolan=budzet,
            )
        )
    return hipotezy


# ── GUEST_SPRAWL ─────────────────────────────────────────────────────────

# Rubryka: `liczba_guest > 0.25 * liczba_members OR goście z last_activity > 180 dni`.
PROG_UDZIALU_GOSCI = 0.25
DNI_NIEAKTYWNEGO_GOSCIA = 180

_GOSCIE = """
WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
osoby AS (
    SELECT json_extract(o.value, '$.user_hash')     AS user_hash,
           json_extract(o.value, '$.kind')          AS kind,
           json_extract(o.value, '$.status')        AS status,
           json_extract(o.value, '$.last_activity') AS last_activity
    FROM snap, json_each(snap.payload, '$.uzytkownicy.uzytkownicy') AS o
),
-- Do których tablic gość ma wlot. Subskrypcja jest jedynym śladem dostępu,
-- jaki mamy — `owners` i `subscribers` to pseudonimy z 3.5.
dostepy AS (
    SELECT sub.value AS user_hash,
           json_group_array(json_extract(t.value, '$.board_id')) AS tablice
    FROM snap,
         json_each(snap.payload, '$.tablice.tablice') AS t,
         json_each(json_extract(t.value, '$.subscribers')) AS sub
    GROUP BY sub.value
)
SELECT
    osoby.user_hash, osoby.status, osoby.last_activity,
    COALESCE(dostepy.tablice, '[]') AS tablice_dostepne,
    (osoby.last_activity IS NULL
     OR substr(osoby.last_activity, 1, 19) < :prog_180) AS nieaktywny
FROM osoby
LEFT JOIN dostepy USING (user_hash)
WHERE osoby.kind = 'guest'
ORDER BY osoby.user_hash
"""


def guest_sprawl(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Za dużo gości wobec członków albo goście bez aktywności od pół roku.

    Jedna hipoteza na KONTO, nie na gościa — rubryka wymienia w `dowod`
    zarówno liczby zbiorcze, jak i listę pseudonimów, a warunek odrzucenia
    („model współpracy oparty na gościach") dotyczy całego konta. Rozbicie na
    hipotezę per gość kazałoby agentowi ten sam warunek rozstrzygać N razy.
    """
    meta = _meta(con, snapshot_id)
    parametry = {
        "snapshot_id": snapshot_id,
        "prog_180": _prog(meta, DNI_NIEAKTYWNEGO_GOSCIA),
    }
    goscie = list(con.execute(_GOSCIE, parametry))

    czlonkowie = con.execute(
        """
        SELECT COUNT(*) AS n
        FROM snapshots, json_each(payload, '$.uzytkownicy.uzytkownicy') AS o
        WHERE snapshots.id = ? AND json_extract(o.value, '$.kind') = 'member'
        """,
        (snapshot_id,),
    ).fetchone()["n"]

    nieaktywni = [g for g in goscie if g["nieaktywny"]]
    udzial = round(len(goscie) / czlonkowie, 4) if czlonkowie else None
    powyzej_progu = udzial is not None and udzial > PROG_UDZIALU_GOSCI

    if not goscie or not (powyzej_progu or nieaktywni):
        return []

    return [
        Hipoteza(
            klasa_id="GUEST_SPRAWL",
            obiekt_id=_konto_id(con, snapshot_id),
            fakty={
                "liczba_guest": len(goscie),
                "liczba_members": czlonkowie,
                "udzial": udzial,
                "prog_udzialu": PROG_UDZIALU_GOSCI,
                "powyzej_progu_udzialu": powyzej_progu,
                "guest_hash": [g["user_hash"] for g in goscie],
                "goscie_nieaktywni": [
                    {
                        "user_hash": g["user_hash"],
                        "last_activity": g["last_activity"],
                        "tablice_dostepne": json.loads(g["tablice_dostepne"]),
                    }
                    for g in nieaktywni
                ],
                "dni_nieaktywnosci": DNI_NIEAKTYWNEGO_GOSCIA,
                # Rubryka: przy modelu opartym na gościach raportuj TYLKO
                # nieaktywnych, nie samą liczbę. Agent musi wiedzieć, która
                # przesłanka zapaliła się sama.
                "podstawa": (
                    "udział gości i nieaktywni"
                    if powyzej_progu and nieaktywni
                    else "udział gości"
                    if powyzej_progu
                    else "goście nieaktywni"
                ),
            },
            budzet_wywolan=budzet,
        )
    ]


# ── PLAN_MISMATCH ────────────────────────────────────────────────────────

PROG_NADWYZKI_MIEJSC = 0.3
DNI_AKTYWNOSCI_PLANU = 30

_AKTYWNI_30 = """
SELECT COUNT(*) AS n
FROM snapshots, json_each(payload, '$.uzytkownicy.uzytkownicy') AS o
WHERE snapshots.id = :snapshot_id
  AND json_extract(o.value, '$.kind') IN ('admin', 'member')
  AND json_extract(o.value, '$.status') = 'ACTIVE'
  AND json_extract(o.value, '$.last_activity') IS NOT NULL
  AND substr(json_extract(o.value, '$.last_activity'), 1, 19) >= :prog_30
"""

_MIEJSCA_ZAJETE = """
SELECT COUNT(*) AS n
FROM snapshots, json_each(payload, '$.uzytkownicy.uzytkownicy') AS o
WHERE snapshots.id = :snapshot_id
  AND json_extract(o.value, '$.kind') IN ('admin', 'member')
"""


def plan_mismatch(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Nadwyżka miejsc wobec realnie aktywnych ludzi.

    Mianownik ma dwa źródła i to NIE jest to samo. `plan.max_users` to miejsca
    **kupione**, ale na koncie CXLABS `account.plan` zwraca null przy tokenie
    bez admina (O2). Wtedy schodzimy na liczbę rekordów `kind IN (admin,
    member)`, czyli miejsca **zajęte** — a to zawsze zaniża nadwyżkę, bo
    niewykorzystanych miejsc kupionych po prostu nie widać. Detektor zapisuje,
    którego źródła użył, i rubryka każe agentowi to powiedzieć w findingu.
    """
    meta = _meta(con, snapshot_id)
    wiersz = con.execute(
        """
        SELECT json_extract(payload, '$.konto.plan.tier')      AS tier,
               json_extract(payload, '$.konto.plan.max_users') AS max_users
        FROM snapshots WHERE id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    if wiersz is None:
        raise DetektorError(f"snapshot {snapshot_id} nie istnieje")

    parametry = {"snapshot_id": snapshot_id, "prog_30": _prog(meta, DNI_AKTYWNOSCI_PLANU)}
    aktywni = con.execute(_AKTYWNI_30, parametry).fetchone()["n"]

    if wiersz["max_users"]:
        miejsca, podstawa = int(wiersz["max_users"]), "max_users z planu (miejsca kupione)"
    else:
        miejsca = con.execute(_MIEJSCA_ZAJETE, {"snapshot_id": snapshot_id}).fetchone()["n"]
        podstawa = "kind IN (admin, member) — miejsca ZAJĘTE, nie kupione (O2)"

    if not miejsca:
        # Rubryka: brak mianownika = nie zgaduj. Bez tego dzielilibyśmy przez zero
        # albo — gorzej — wypisali klientowi nadwyżkę policzoną z niczego.
        logger.warning("PLAN_MISMATCH: brak liczby miejsc, hipoteza nie powstaje")
        return []

    nadwyzka = (miejsca - aktywni) / miejsca
    if nadwyzka <= PROG_NADWYZKI_MIEJSC:
        return []

    return [
        Hipoteza(
            klasa_id="PLAN_MISMATCH",
            obiekt_id=_konto_id(con, snapshot_id),
            fakty={
                "plan_tier": wiersz["tier"],
                "podstawa_miejsc": podstawa,
                "liczba_miejsc": miejsca,
                "aktywni_30d": aktywni,
                "nadwyzka_miejsc": miejsca - aktywni,
                "udzial_nadwyzki": round(nadwyzka, 4),
                "prog": PROG_NADWYZKI_MIEJSC,
                "daty_utworzenia_kont": _daty_utworzenia(con, snapshot_id),
            },
            budzet_wywolan=budzet,
        )
    ]


def _daty_utworzenia(con: sqlite3.Connection, snapshot_id: int) -> dict[str, str | None]:
    """Najstarsze i najnowsze konto. Rubryka: „jeśli nikt nowy nie doszedł
    od pół roku, to nie zapas, to nadpłata"."""
    wiersz = con.execute(
        """
        SELECT MIN(json_extract(o.value, '$.created_at')) AS najstarsze,
               MAX(json_extract(o.value, '$.created_at')) AS najnowsze
        FROM snapshots, json_each(payload, '$.uzytkownicy.uzytkownicy') AS o
        WHERE snapshots.id = ?
          AND json_extract(o.value, '$.kind') IN ('admin', 'member')
        """,
        (snapshot_id,),
    ).fetchone()
    return {"najstarsze": wiersz["najstarsze"], "najnowsze": wiersz["najnowsze"]}


# ── AUTOMATION_ABSENT ────────────────────────────────────────────────────

PROG_TABLIC_BEZ_AUTOMATYZACJI = 0.8

# Wzorzec z rubryki: „tablica z kolumną statusu, właścicielem i datą" to gotowy
# kandydat na automatyzację „zmiana statusu → powiadom właściciela → ustaw
# termin". Detektor zawęża pulę, wybór trzech należy do agenta.
_KANDYDACI = f"""
WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
{_TABLICE}
SELECT board_id, nazwa, items_count, kolumn
FROM tablice
WHERE typ = 'board' AND state = 'active' AND COALESCE(items_count, 0) > 0
  AND json_array_length(owners) > 0
  AND EXISTS (SELECT 1 FROM json_each(json_extract(surowa, '$.kolumny')) AS k
              WHERE json_extract(k.value, '$.type') = 'status')
  AND EXISTS (SELECT 1 FROM json_each(json_extract(surowa, '$.kolumny')) AS k
              WHERE json_extract(k.value, '$.type') IN ('date', 'timeline'))
ORDER BY items_count DESC, board_id
LIMIT 10
"""


def automation_absent(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Konto bez automatyzacji albo z automatyzacjami na garstce tablic.

    Sygnał NIE mówi „ile automatyzacji klient ma" — tego API nie oddaje
    (O1, O12). Mówi, na ilu sondowanych tablicach nie było ANI JEDNEGO
    zdarzenia automatyzacji. Zmierzone na CXLABS: 104 tablice ze 105, przy
    1061 uruchomieniach na koncie — czyli automatyzacje siedzą praktycznie
    na jednej tablicy.
    """
    wiersz = con.execute(
        """
        SELECT
            json_extract(payload, '$.automatyzacje.uruchomienia.razem') AS razem,
            json_extract(payload, '$.automatyzacje.podsumowanie.tablic_sondowanych')
                AS sondowanych,
            json_extract(payload, '$.automatyzacje.podsumowanie.tablic_bez_zdarzen')
                AS bez_zdarzen,
            json_extract(payload, '$.automatyzacje.podsumowanie.tablic_pominietych')
                AS pominietych
        FROM snapshots WHERE id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    if wiersz is None:
        raise DetektorError(f"snapshot {snapshot_id} nie istnieje")

    sondowanych = wiersz["sondowanych"] or 0
    bez_zdarzen = wiersz["bez_zdarzen"] or 0
    udzial = round(bez_zdarzen / sondowanych, 4) if sondowanych else None
    brak_uruchomien = (wiersz["razem"] or 0) == 0

    if not (brak_uruchomien or (udzial is not None and udzial > PROG_TABLIC_BEZ_AUTOMATYZACJI)):
        return []

    kandydaci = [
        {"board_id": str(k["board_id"]), "nazwa": k["nazwa"], "items_count": k["items_count"]}
        for k in con.execute(_KANDYDACI, {"snapshot_id": snapshot_id})
    ]
    return [
        Hipoteza(
            klasa_id="AUTOMATION_ABSENT",
            obiekt_id=_konto_id(con, snapshot_id),
            fakty={
                "uruchomienia_konta": wiersz["razem"],
                "tablic_sondowanych": sondowanych,
                "tablic_bez_zdarzen": bez_zdarzen,
                "udzial": udzial,
                "prog": PROG_TABLIC_BEZ_AUTOMATYZACJI,
                # Rubryka ma warunek odrzucenia „udział policzony na wycinku".
                # Bez tej liczby agent nie wie, czy patrzy na całość.
                "tablic_pominietych_w_sondowaniu": wiersz["pominietych"] or 0,
                "kandydaci": kandydaci,
                "kandydaci_wzorzec": "kolumna statusu + właściciel + kolumna daty",
            },
            budzet_wywolan=budzet,
        )
    ]


# ── ENGAGEMENT_DROP ──────────────────────────────────────────────────────

PROG_AKTYWNYCH_7D = 0.4
PROG_AKTYWNYCH_90D = 0.7

# Grupą jest ZESPÓŁ. Wariant per workspace jest niewykonalny: API nie przypisuje
# użytkowników do workspace'ów, a „udział aktywnych" wymaga znanego mianownika.
# Kubełek `0-7` z 3.7 jest per TABLICA, więc mówi o aktywności tablic, nie ludzi
# — i dlatego wchodzi tu tylko jako fakt pomocniczy, nie jako sygnał.
_ENGAGEMENT = """
WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
czlonkowie AS (
    SELECT z.value                                   AS zespol,
           json_extract(o.value, '$.user_hash')      AS user_hash,
           json_extract(o.value, '$.last_activity')  AS last_activity
    FROM snap,
         json_each(snap.payload, '$.uzytkownicy.uzytkownicy') AS o,
         json_each(json_extract(o.value, '$.zespoly')) AS z
    WHERE json_extract(o.value, '$.kind') IN ('admin', 'member')
      AND json_extract(o.value, '$.status') = 'ACTIVE'
)
SELECT
    zespol,
    COUNT(*) AS osob,
    SUM(CASE WHEN last_activity IS NOT NULL
              AND substr(last_activity, 1, 19) >= :prog_7  THEN 1 ELSE 0 END) AS aktywni_7d,
    SUM(CASE WHEN last_activity IS NOT NULL
              AND substr(last_activity, 1, 19) >= :prog_90 THEN 1 ELSE 0 END) AS aktywni_90d
FROM czlonkowie
GROUP BY zespol
HAVING osob >= :min_osob
ORDER BY zespol
"""

# Poniżej tej liczby udziały są bez znaczenia: w zespole dwuosobowym jedna
# osoba na urlopie daje 50% spadku.
MIN_OSOB_W_GRUPIE = 3


def engagement_drop(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Zespół, który używał konta i przestał."""
    meta = _meta(con, snapshot_id)
    parametry = {
        "snapshot_id": snapshot_id,
        "prog_7": _prog(meta, 7),
        "prog_90": _prog(meta, 90),
        "min_osob": MIN_OSOB_W_GRUPIE,
    }

    hipotezy: list[Hipoteza] = []
    for w in con.execute(_ENGAGEMENT, parametry):
        udzial_7 = w["aktywni_7d"] / w["osob"]
        udzial_90 = w["aktywni_90d"] / w["osob"]
        if not (udzial_7 < PROG_AKTYWNYCH_7D and udzial_90 > PROG_AKTYWNYCH_90D):
            continue
        hipotezy.append(
            Hipoteza(
                klasa_id="ENGAGEMENT_DROP",
                obiekt_id=str(w["zespol"]),
                fakty={
                    "grupa": w["zespol"],
                    "grupa_typ": "zespol",
                    "osob_w_grupie": w["osob"],
                    "aktywni_7d": w["aktywni_7d"],
                    "aktywni_90d": w["aktywni_90d"],
                    "udzial_7d": round(udzial_7, 4),
                    "udzial_90d": round(udzial_90, 4),
                    "progi": {"7d": PROG_AKTYWNYCH_7D, "90d": PROG_AKTYWNYCH_90D},
                    # Oba pola z `dowod` należą do agenta — detektor widzi
                    # spadek, ale nie zna jego przyczyny ani dnia zwrotu.
                    "data_zwrotu": None,
                    "zdarzenie_towarzyszace": None,
                    "do_zbadania_przez_agenta": "kiedy nastąpił zwrot i co się wtedy stało",
                },
                budzet_wywolan=budzet,
            )
        )
    return hipotezy


# ── DUPLICATE_STRUCTURE i PROCESS_BYPASS ─────────────────────────────────
#
# Obie klasy porównują tablice PARAMI, w obrębie jednego workspace. Wspólny
# fundament: zbiór kolumn tablicy jako `tytuł:typ` i miara nakładania.
#
# Nakładanie liczymy jako Jaccard, czyli część wspólna przez sumę. Alternatywą
# było dzielenie przez mniejszy zbiór, ale wtedy tablica pięciokolumnowa
# zawarta w trzydziestokolumnowej dawałaby 100% i klasa mówiłaby „duplikat"
# o czymś, co jest wycinkiem, nie kopią.

_PARY_TABLIC = f"""
WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
{_TABLICE},
{_AKTYWNOSC},
kolumny AS (
    SELECT tablice.board_id, tablice.workspace_id,
           json_extract(k.value, '$.title') || ':' || json_extract(k.value, '$.type') AS kol
    FROM tablice, json_each(json_extract(tablice.surowa, '$.kolumny')) AS k
    WHERE tablice.typ = 'board'
),
rozmiary AS (SELECT board_id, COUNT(DISTINCT kol) AS n FROM kolumny GROUP BY board_id),
subskrypcje AS (
    SELECT tablice.board_id, sub.value AS user_hash
    FROM tablice, json_each(json_extract(tablice.surowa, '$.subscribers')) AS sub
),
pary AS (
    SELECT
        a.board_id AS a_id, b.board_id AS b_id, a.workspace_id AS workspace_id,
        COUNT(DISTINCT a.kol) AS wspolnych
    FROM kolumny a
    JOIN kolumny b
      ON a.workspace_id IS b.workspace_id
     AND a.kol = b.kol
     AND a.board_id < b.board_id           -- każda para raz, deterministycznie
    GROUP BY a.board_id, b.board_id
)
SELECT
    pary.a_id, pary.b_id, pary.workspace_id, pary.wspolnych,
    ra.n AS a_kolumn, rb.n AS b_kolumn,
    ta.nazwa AS a_nazwa, tb.nazwa AS b_nazwa,
    ta.created_at AS a_created, tb.created_at AS b_created,
    ta.state AS a_state, tb.state AS b_state,
    (SELECT COUNT(*) FROM subskrypcje sa
      JOIN subskrypcje sb ON sa.user_hash = sb.user_hash
     WHERE sa.board_id = pary.a_id AND sb.board_id = pary.b_id) AS wspolnych_subskrybentow,
    (SELECT COUNT(DISTINCT user_hash) FROM subskrypcje
      WHERE board_id IN (pary.a_id, pary.b_id)) AS subskrybentow_razem,
    -- AKTYWNOŚĆ OBU STRON PARY. `LEFT JOIN`, nie `JOIN`: tablica bez próbki logu
    -- ma `NULL`, a to znaczy „nie wiem", nie „zero wpisów". Zlanie tych dwóch
    -- dałoby fakt „obie martwe" dla pary, której w ogóle nie próbkowaliśmy.
    --
    -- POWÓD DODANIA (2026-08-17): agent przy `effort=medium` gubił ten fakt
    -- w 3 z 6 findingów — niesystematycznie, bo nikt go nie wymagał. Rubryka nie
    -- miała aktywności w polach dowodu, więc walidacja tego nie pilnowała, a fakt
    -- „czy któraś ze stron w ogóle żyje" ROZSTRZYGA klasę: rubryka opisuje
    -- rozjazd jako „jedna aktywna, reszta cichnie".
    aa.wpisow AS a_wpisow, ab.wpisow AS b_wpisow,
    aa.najnowszy_at AS a_najnowszy, ab.najnowszy_at AS b_najnowszy
FROM pary
JOIN rozmiary ra ON ra.board_id = pary.a_id
JOIN rozmiary rb ON rb.board_id = pary.b_id
JOIN tablice   ta ON ta.board_id = pary.a_id
JOIN tablice   tb ON tb.board_id = pary.b_id
LEFT JOIN aktywnosc aa ON aa.board_id = pary.a_id
LEFT JOIN aktywnosc ab ON ab.board_id = pary.b_id
ORDER BY pary.a_id, pary.b_id
"""

PROG_NAKLADANIA_DUPLIKATU = 0.7
PROG_NAKLADANIA_BYPASSU = 0.5


def _nakladanie(wspolnych: int, a: int, b: int) -> float:
    suma = a + b - wspolnych
    return round(wspolnych / suma, 4) if suma else 0.0


def duplicate_structure(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Dwie tablice w tym samym workspace o niemal identycznym zestawie kolumn."""
    hipotezy: list[Hipoteza] = []
    for w in con.execute(_PARY_TABLIC, {"snapshot_id": snapshot_id}):
        nakladanie = _nakladanie(w["wspolnych"], w["a_kolumn"], w["b_kolumn"])
        if nakladanie < PROG_NAKLADANIA_DUPLIKATU:
            continue
        hipotezy.append(
            Hipoteza(
                klasa_id="DUPLICATE_STRUCTURE",
                obiekt_id=f"{w['a_id']}+{w['b_id']}",
                fakty={
                    "board_ids": [str(w["a_id"]), str(w["b_id"])],
                    "nazwy": [w["a_nazwa"], w["b_nazwa"]],
                    "nakladanie_kolumn": nakladanie,
                    "prog": PROG_NAKLADANIA_DUPLIKATU,
                    "kolumn": {str(w["a_id"]): w["a_kolumn"], str(w["b_id"]): w["b_kolumn"]},
                    "kolumn_wspolnych": w["wspolnych"],
                    "nakladanie_subskrybentow": _nakladanie(
                        w["wspolnych_subskrybentow"],
                        w["subskrybentow_razem"],
                        w["wspolnych_subskrybentow"],
                    )
                    if w["subskrybentow_razem"]
                    else 0.0,
                    "subskrybentow_wspolnych": w["wspolnych_subskrybentow"],
                    # Aktywność KAŻDEJ ze stron osobno — to ona rozstrzyga, czy
                    # to rozjazd („jedna aktywna, reszta cichnie", jak mówi
                    # rubryka), czy obie tablice są martwe od powstania.
                    #
                    # `None` zamiast zera, gdy tablica nie weszła do próbki logów:
                    # „nie wiem" i „zero wpisów" to dwie różne rzeczy, a agent ma
                    # prawo je odróżnić. Kontrakt dopuszcza `None` w tym polu
                    # przez ten sam wyjątek, co `kubelki_dni` (POLA_ROZKLADU).
                    "aktywnosc_stron": {
                        str(w["a_id"]): w["a_wpisow"],
                        str(w["b_id"]): w["b_wpisow"],
                    },
                    "daty_utworzenia": {
                        str(w["a_id"]): w["a_created"],
                        str(w["b_id"]): w["b_created"],
                    },
                    "workspace_id": w["workspace_id"],
                },
                budzet_wywolan=budzet,
            )
        )
    return hipotezy


DNI_ZAMILKNIECIA = 60
DNI_OKNA_POWSTANIA = 30
MIN_NOWYCH_TABLIC = 2


def process_bypass(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Tablica ucichła, a obok powstały nowe o podobnej strukturze.

    Sygnał złożony, jedyna klasa o wadze krytycznej. Koniunkcja z rubryki:
    tablica milczy dłużej niż 60 dni ORAZ w oknie ±30 dni od zamilknięcia
    powstały co najmniej dwie nowe tablice w tym samym workspace o nakładaniu
    kolumn >= 50%.

    Odrzucenia z rubryki (podział wg klienta, rozłączni subskrybenci, świadoma
    archiwizacja) rozstrzyga agent — detektor podaje mu nakładanie
    subskrybentów, bo to właśnie ono odróżnia ucieczkę od nowego zespołu.
    """
    meta = _meta(con, snapshot_id)
    prog_zamilkniecia = _prog(meta, DNI_ZAMILKNIECIA)

    # Kiedy każda tablica ucichła: `najnowszy_at` z logu (O18 — NIE `updated_at`).
    ucichle = {
        str(w["board_id"]): w["najnowszy_at"]
        for w in con.execute(
            f"""
            WITH snap AS (SELECT payload FROM snapshots WHERE id = :snapshot_id),
            {_TABLICE}, {_AKTYWNOSC}
            SELECT aktywnosc.board_id, aktywnosc.najnowszy_at
            FROM tablice JOIN aktywnosc USING (board_id)
            WHERE tablice.typ = 'board'
              AND tablice.state = 'active'
              AND aktywnosc.najnowszy_at IS NOT NULL
              AND substr(aktywnosc.najnowszy_at, 1, 19) < :prog
            """,
            {"snapshot_id": snapshot_id, "prog": prog_zamilkniecia},
        )
    }
    if not ucichle:
        return []

    # Pary z nakładaniem >= 50%; grupujemy nowe tablice pod starą.
    kandydaci: dict[str, list[dict[str, Any]]] = {}
    for w in con.execute(_PARY_TABLIC, {"snapshot_id": snapshot_id}):
        nakladanie = _nakladanie(w["wspolnych"], w["a_kolumn"], w["b_kolumn"])
        if nakladanie < PROG_NAKLADANIA_BYPASSU:
            continue
        for stary, nowy, nowy_nazwa, nowy_created in (
            (str(w["a_id"]), str(w["b_id"]), w["b_nazwa"], w["b_created"]),
            (str(w["b_id"]), str(w["a_id"]), w["a_nazwa"], w["a_created"]),
        ):
            zamilkla = ucichle.get(stary)
            if not zamilkla or not nowy_created:
                continue
            if not _w_oknie(nowy_created, zamilkla, DNI_OKNA_POWSTANIA):
                continue
            kandydaci.setdefault(stary, []).append(
                {
                    "board_id": nowy,
                    "nazwa": nowy_nazwa,
                    "created_at": nowy_created,
                    "nakladanie_kolumn": nakladanie,
                    "subskrybentow_wspolnych": w["wspolnych_subskrybentow"],
                }
            )

    hipotezy: list[Hipoteza] = []
    for stary in sorted(kandydaci):
        nowe = sorted(kandydaci[stary], key=lambda n: str(n["board_id"]))
        if len(nowe) < MIN_NOWYCH_TABLIC:
            continue
        hipotezy.append(
            Hipoteza(
                klasa_id="PROCESS_BYPASS",
                obiekt_id=stary,
                fakty={
                    "board_stary": stary,
                    "boardy_nowe": [n["board_id"] for n in nowe],
                    "data_zamilkniecia": ucichle[stary],
                    "daty_utworzenia_nowych": {n["board_id"]: n["created_at"] for n in nowe},
                    "nakladanie_kolumn": {n["board_id"]: n["nakladanie_kolumn"] for n in nowe},
                    "nakladanie_subskrybentow": {
                        n["board_id"]: n["subskrybentow_wspolnych"] for n in nowe
                    },
                    "okno_powstania_dni": DNI_OKNA_POWSTANIA,
                    "dni_zamilkniecia": DNI_ZAMILKNIECIA,
                    "hipoteza_przyczyny": None,
                    "do_zbadania_przez_agenta": (
                        "czy to obejście procesu, czy podział wg klienta/okresu; "
                        "rozłączni subskrybenci znaczą nowy zespół, nie ucieczkę"
                    ),
                },
                budzet_wywolan=budzet,
            )
        )
    return hipotezy


# ── UZYTKOWNIK_WYGASZONY ─────────────────────────────────────────────────
#
# Osoba WIDOCZNA w logach, której akcje leżą tylko w starych kubełkach.
# To dopełnienie `ZOMBIE_ACCOUNT`: tamten bierze osoby NIEOBECNE w logach
# (`autorzy.user_hash IS NULL`), więc zbiory są rozłączne z konstrukcji —
# zmierzone na snapshocie #7: 8 osób w logach, 7 kont zombie, 0 wspólnych.
#
# Czyta gotową sekcję `aktywnosc.per_uzytkownik`, więc zero nowych wywołań monday.

# Ile akcji musi być, żeby mówić o „pracy". Poniżej tego jedno kliknięcie sprzed
# dwóch miesięcy dawałoby finding — a to szum, nie proces.
MIN_AKCJI_WYGASZONEGO = 5

# Jaki udział świeżej aktywności (0-30 dni) w starej (31-90) jeszcze uznajemy za
# wygaszenie. ZMIERZONE na #7: przy 0,15 kandydatami zostają dwie osoby, a osoba
# z 29 akcjami świeżymi wobec 48 starych (0,60) słusznie odpada — ona nadal pracuje,
# tylko mniej.
PROG_SWIEZEJ_AKTYWNOSCI = 0.15

# Rodzaje kont, które nie są ludźmi. `personal_agent_member` to konto agenta AI —
# ZMIERZONE: 3 z 8 osób w logach snapshotu #7. Bez tego filtra klasa zgłaszałaby
# „agent Quotation przestał pracować".
RODZAJE_NIE_LUDZIE = frozenset({RODZAJ_AGENT})


def uzytkownik_wygaszony(con: sqlite3.Connection, snapshot_id: int, budzet: int) -> list[Hipoteza]:
    """Osoba, która pracowała w logach i przestała — z tym, CO robiła.

    Różnica wobec `ZOMBIE_ACCOUNT` jest jakościowa: tam konto milczy i nie wiadomo,
    co robiło. Tutaj `boardy[]`, `po_event` i `kubelki_dni` mówią na czym pracowała,
    co dokładnie robiła i w którym przedziale czasu — więc finding może odpowiedzieć
    na „co, gdzie, kiedy przestał", a nie tylko „ile dni ciszy".

    Warunki odrzucenia z rubryki, które da się sprawdzić deterministycznie, są
    sprawdzone TUTAJ, nie zostawione agentowi: konta agentów AI i autorzy nieobecni
    na liście kont. Zostawienie ich modelowi kosztowałoby sesję za sesją to samo
    rozumowanie, a wynik byłby ten sam (D1).
    """
    wiersz = con.execute(
        "SELECT payload FROM snapshots WHERE id = :snapshot_id",
        {"snapshot_id": snapshot_id},
    ).fetchone()
    if wiersz is None:
        return []
    payload = json.loads(wiersz["payload"])
    per_uzytkownik = (payload.get("aktywnosc") or {}).get("per_uzytkownik") or []
    lista_osob = (payload.get("uzytkownicy") or {}).get("uzytkownicy") or []
    konta = {str(o.get("user_hash")): o for o in lista_osob}

    hipotezy: list[Hipoteza] = []
    for wpis in per_uzytkownik:
        haszyk = str(wpis.get("user_hash"))
        if int(wpis.get("akcji") or 0) < MIN_AKCJI_WYGASZONEGO:
            continue
        konto = konta.get(haszyk)
        # Autor w logach, którego nie ma na liście kont: usunięty albo spoza
        # zakresu. Nie ma o kim orzekać — i to jest ODRZUCENIE detektora, nie
        # pytanie do agenta.
        if konto is None or konto.get("kind") in RODZAJE_NIE_LUDZIE:
            continue

        kubelki = wpis.get("kubelki_dni") or {}
        swieze = int(kubelki.get("0-7") or 0) + int(kubelki.get("8-30") or 0)
        stare = int(kubelki.get("31-60") or 0) + int(kubelki.get("61-90") or 0)
        if not stare:
            continue
        if swieze / stare >= PROG_SWIEZEJ_AKTYWNOSCI:
            continue

        hipotezy.append(
            Hipoteza(
                klasa_id="UZYTKOWNIK_WYGASZONY",
                obiekt_id=haszyk,
                fakty={
                    "user_hash": haszyk,
                    "kind": konto.get("kind"),
                    "kubelki_dni": kubelki,
                    "boardy": list(wpis.get("boardy") or []),
                    "po_event": dict(wpis.get("po_event") or {}),
                    "akcji": int(wpis.get("akcji") or 0),
                    "tablic": int(wpis.get("tablic") or 0),
                    # Jawnie, żeby agent nie musiał liczyć tego z kubełków.
                    "akcji_swiezych_0_30": swieze,
                    "akcji_starych_31_90": stare,
                    "udzial_swiezych": round(swieze / stare, 4),
                    "prog_swiezych": PROG_SWIEZEJ_AKTYWNOSCI,
                    "aktywny_ostatnie_7d": bool(wpis.get("aktywny_ostatnie_7d")),
                    "do_zbadania_przez_agenta": (
                        "czy te same tablice żyją dalej z innym autorem — wtedy to "
                        "przekazanie obowiązków, nie porzucenie procesu"
                    ),
                },
                budzet_wywolan=budzet,
            )
        )
    return hipotezy


def _w_oknie(znacznik: str, srodek: str, dni: int) -> bool:
    """Czy `znacznik` wpada w ±`dni` od `srodek`. Oba w UTC ze snapshotu."""
    try:
        a = datetime.fromisoformat(str(znacznik).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(srodek).replace("Z", "+00:00"))
    except ValueError:
        return False
    return abs((a - b).days) <= dni


# Rejestr. Klasy bez wpisu nie mają jeszcze detektora — `uruchom_detektory`
# mówi o tym wprost, zamiast po cichu zwracać pustą listę.
DETEKTORY: dict[str, Detektor] = {
    "ZOMBIE_ACCOUNT": zombie_account,
    "UZYTKOWNIK_WYGASZONY": uzytkownik_wygaszony,
    "PLAN_MISMATCH": plan_mismatch,
    "GUEST_SPRAWL": guest_sprawl,
    "ENGAGEMENT_DROP": engagement_drop,
    "BOARD_GHOST": board_ghost,
    "BOARD_NO_OWNER": board_no_owner,
    "BOARD_OVERCOMPLEX": board_overcomplex,
    "DUPLICATE_STRUCTURE": duplicate_structure,
    "PROCESS_BYPASS": process_bypass,
    "AUTOMATION_ABSENT": automation_absent,
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
