"""Collector — activity logs z samplingiem (etap 3.7).

To jest miejsce, w którym najłatwiej spalić dzienny limit klienta: nie ma
logu na poziomie konta, więc każda tablica to osobne wywołanie. Dlatego
sampling jest tu warunkiem wykonalności, nie optymalizacją.

**Z logów bierzemy sygnały, nie treść.** Pole `data` zawiera realne wartości
kolumn i nazwy itemów (`value`, `previous_value`, `pulse_name`) — czyli
dokładnie to, czego zabrania D5 i granica PII. Nie trafia do snapshotu ani
w całości, ani we fragmentach; do wyniku idą wyłącznie liczniki, typy zdarzeń
i pseudonimy autorów.

Dwie pułapki zmierzone 2026-07-30 (`OTWARTE.md` O13):

1. **`created_at` nie jest datą ISO.** To liczba w jednostkach 100 ns od epoki,
   np. `17830789794688296`. Naiwne porównanie stringów albo `fromisoformat`
   dają śmieci, a na tym polu stoi okno 90 dni w `ENGAGEMENT_DROP`.
2. **API nie ma znacznika „to zrobiła automatyzacja".** `ActivityLogType` ma
   siedem pól i żadne nie mówi, czy autorem był człowiek. Rozróżnienie robimy
   przez porównanie `user_id` z listą użytkowników konta z 3.4: autor nieznany
   to najpewniej system, bot albo konto usunięte. To heurystyka i jest
   oznaczona jako heurystyka.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from monday_audit.klient import MondayClient
from monday_audit.osoby import policz_hash, waliduj_brak_pii
from monday_audit.tablice import Tablica

logger = logging.getLogger(__name__)

# ŚWIADOMIE BEZ pola `data`. Zawiera `value`, `previous_value`, `pulse_name`
# i `column_title` — treść biznesową klienta. Nie pobieramy jej wcale, żeby
# nie polegać na tym, że ktoś ją potem odfiltruje.
ZAPYTANIE_LOGOW = """
query ($ids: [ID!], $limit: Int!, $od: ISO8601DateTime, $do: ISO8601DateTime) {
  boards (ids: $ids) {
    id
    activity_logs (limit: $limit, from: $od, to: $do) {
      id event entity created_at user_id
    }
  }
}
"""

# `created_at` w logach to liczba jednostek 100 ns od epoki Unixa.
JEDNOSTEK_NA_SEKUNDE = 10_000_000

# Sufity samplingu. Specyfikacja 3.7 mówi „top 30 + 20 losowych z ogona"
# po całym koncie; przy zawężeniu do jednego workspace i wymogu małego
# wolumenu schodzimy do 10 + 5. Zmiana zakresu, nie zasady.
TOP_PO_ITEMACH = 10
Z_OGONA = 5
LIMIT_WPISOW = 100

# Ile ostatnich wpisów decyduje o odpowiedzi „żywa czy pozornie żywa".
OKNO_OSTATNICH = 5


class LogiError(RuntimeError):
    """Nie da się zebrać sygnałów aktywności."""


def na_iso(surowy: Any) -> str | None:
    """Zamienia `created_at` z logu na ISO-8601 w UTC.

    Log zwraca `17830789794688296`, nie datę. Dzielimy przez 10^7, bo to
    jednostki 100 ns od epoki — sprawdzone na koncie CXLABS przez porównanie
    z `board.updated_at`.
    """
    if surowy is None:
        return None
    try:
        jednostki = int(str(surowy))
    except ValueError:
        # Gdyby monday zmienił format na ISO, przepuszczamy bez zmian —
        # lepiej oddać oryginał niż zgadywać.
        return str(surowy)
    if jednostki <= 0:
        return None
    return datetime.fromtimestamp(jednostki / JEDNOSTEK_NA_SEKUNDE, tz=UTC).isoformat()


@dataclass(frozen=True, slots=True)
class SygnalyTablicy:
    """Sygnały aktywności jednej tablicy. Zero treści."""

    board_id: str
    wpisow: int
    po_event: dict[str, int]
    po_entity: dict[str, int]
    autorzy: tuple[str, ...]
    autorow_znanych: int
    autorow_nieznanych: int
    najnowszy_at: str | None
    najstarszy_at: str | None
    ostatnich_od_znanych: int
    ostatnich_zbadanych: int
    strona_pelna: bool

    @property
    def pozornie_zywa(self) -> bool:
        """Ma wpisy, ale żaden z ostatnich nie pochodzi od znanego użytkownika.

        To jest ten sygnał z 3.7, który odróżnia tablicę żywą od takiej,
        którą tylko automatyzacja podtrzymuje.
        """
        return self.wpisow > 0 and self.ostatnich_zbadanych > 0 and self.ostatnich_od_znanych == 0

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "board_id": self.board_id,
            "wpisow": self.wpisow,
            "po_event": dict(self.po_event),
            "po_entity": dict(self.po_entity),
            "autorzy": list(self.autorzy),
            "autorow_znanych": self.autorow_znanych,
            "autorow_nieznanych": self.autorow_nieznanych,
            "najnowszy_at": self.najnowszy_at,
            "najstarszy_at": self.najstarszy_at,
            "ostatnich_od_znanych": self.ostatnich_od_znanych,
            "ostatnich_zbadanych": self.ostatnich_zbadanych,
            "pozornie_zywa": self.pozornie_zywa,
            "strona_pelna": self.strona_pelna,
        }


@dataclass(frozen=True, slots=True)
class WynikLogow:
    sygnaly: tuple[SygnalyTablicy, ...]
    pominietych_tablic: int
    discovery: dict[str, Any]

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "aktywnosc_tablic": [s.do_snapshotu() for s in self.sygnaly],
            "podsumowanie": self.podsumowanie(),
            "discovery": dict(self.discovery),
        }

    def podsumowanie(self) -> dict[str, Any]:
        zdarzenia: Counter[str] = Counter()
        for sygnal in self.sygnaly:
            zdarzenia.update(sygnal.po_event)

        return {
            "tablic_zbadanych": len(self.sygnaly),
            "tablic_pominietych": self.pominietych_tablic,
            "tablic_bez_wpisow": sum(1 for s in self.sygnaly if s.wpisow == 0),
            "tablic_pozornie_zywych": sum(1 for s in self.sygnaly if s.pozornie_zywa),
            "tablic_z_urwana_strona": sum(1 for s in self.sygnaly if s.strona_pelna),
            "wpisow_razem": sum(s.wpisow for s in self.sygnaly),
            "najczestsze_zdarzenia": dict(zdarzenia.most_common(10)),
        }


def wybierz_probke(
    tablice: Sequence[Tablica],
    *,
    top: int = TOP_PO_ITEMACH,
    z_ogona: int = Z_OGONA,
) -> tuple[tuple[Tablica, ...], int]:
    """Top po `items_count` plus ogon. Zwraca próbkę i liczbę pominiętych.

    Specyfikacja mówi „20 **losowych** z ogona". Świadomie deterministycznie:
    bierzemy tablice o NAJMNIEJSZEJ liczbie itemów, bo celem ogona jest
    wychwycenie martwych. Losowanie łamałoby powtarzalność, a 04-test.md
    wymaga, żeby dwa runy na tym samym koncie dały snapshoty różniące się
    tylko znacznikami czasu.
    """
    if top < 0 or z_ogona < 0:
        raise LogiError("sufity próbki nie mogą być ujemne")

    posortowane = sorted(tablice, key=lambda t: (-(t.items_count or 0), t.board_id))
    czolo = posortowane[:top]
    ogon = [t for t in reversed(posortowane) if t not in czolo][:z_ogona]

    probka = tuple(czolo) + tuple(reversed(ogon))
    return probka, max(0, len(tablice) - len(probka))


def _sygnaly(
    board_id: str,
    logi: Sequence[dict[str, Any]],
    *,
    client_id: str,
    sol: bytes,
    znani: Collection[str],
    limit_wpisow: int,
) -> SygnalyTablicy:
    """Buduje sygnały z listy wpisów. Pole `data` w ogóle tu nie dociera."""
    posortowane = sorted(
        logi,
        key=lambda w: (
            int(str(w.get("created_at") or 0)) if str(w.get("created_at") or "0").isdigit() else 0
        ),
        reverse=True,
    )

    hashe: list[str] = []
    znanych = 0
    nieznanych = 0
    for wpis in logi:
        surowy_id = wpis.get("user_id")
        if surowy_id is None:
            continue
        hashe.append(policz_hash(client_id, str(surowy_id), sol))
        if str(surowy_id) in znani:
            znanych += 1
        else:
            nieznanych += 1

    ostatnie = posortowane[:OKNO_OSTATNICH]
    od_znanych = sum(1 for w in ostatnie if str(w.get("user_id")) in znani)
    znaczniki = [na_iso(w.get("created_at")) for w in posortowane]
    czyste = [z for z in znaczniki if z]

    return SygnalyTablicy(
        board_id=board_id,
        wpisow=len(logi),
        po_event=dict(Counter(str(w.get("event")) for w in logi)),
        po_entity=dict(Counter(str(w.get("entity")) for w in logi)),
        autorzy=tuple(sorted(set(hashe))),
        autorow_znanych=znanych,
        autorow_nieznanych=nieznanych,
        najnowszy_at=czyste[0] if czyste else None,
        najstarszy_at=czyste[-1] if czyste else None,
        ostatnich_od_znanych=od_znanych,
        ostatnich_zbadanych=len(ostatnie),
        strona_pelna=len(logi) >= limit_wpisow,
    )


async def zbierz_logi(
    klient: MondayClient,
    tablice: Sequence[Tablica],
    *,
    client_id: str,
    sol: bytes,
    znani_uzytkownicy: Collection[str] = (),
    od: str | None = None,
    do: str | None = None,
    limit_wpisow: int = LIMIT_WPISOW,
    top: int = TOP_PO_ITEMACH,
    z_ogona: int = Z_OGONA,
) -> WynikLogow:
    """Sampluje activity logs i wyciąga z nich sygnały, nie treść.

    `znani_uzytkownicy` to surowe identyfikatory z 3.4 — służą wyłącznie do
    rozstrzygnięcia, czy autor wpisu jest użytkownikiem konta. Do wyniku
    trafiają już tylko pseudonimy.
    """
    probka, pominietych = wybierz_probke(tablice, top=top, z_ogona=z_ogona)
    if pominietych:
        logger.warning(
            "sampluję %d z %d tablic (top %d po items_count + %d z ogona); "
            "%d tablic POMINIĘTYCH i odnotowanych w snapshocie",
            len(probka),
            len(tablice),
            top,
            z_ogona,
            pominietych,
        )

    sygnaly: list[SygnalyTablicy] = []
    znani = {str(u) for u in znani_uzytkownicy}

    for tablica in probka:
        dane = await klient.query(
            ZAPYTANIE_LOGOW,
            {"ids": [tablica.board_id], "limit": limit_wpisow, "od": od, "do": do},
            etykieta="logi",
        )
        wpisy = dane.get("boards") or []
        logi = (wpisy[0].get("activity_logs") or []) if wpisy else []
        sygnaly.append(
            _sygnaly(
                tablica.board_id,
                logi,
                client_id=client_id,
                sol=sol,
                znani=znani,
                limit_wpisow=limit_wpisow,
            )
        )

    bez_autorow = sum(1 for s in sygnaly if s.autorow_nieznanych and not s.autorow_znanych)
    discovery = {
        "logi_dostepne": any(s.wpisow for s in sygnaly),
        # Heurystyka, nie fakt z API — `ActivityLogType` nie ma znacznika bota.
        "rozroznienie_czlowiek_automat": "heurystyka: user_id nieobecny na liście konta",
        "znanych_uzytkownikow_na_wejsciu": len(znani),
        "tablic_tylko_z_nieznanymi_autorami": bez_autorow,
        "okno_czasowe": {"od": od, "do": do} if (od or do) else None,
        "created_at_w_jednostkach_100ns": True,
        "probka": {"top_po_itemach": top, "z_ogona": z_ogona, "limit_wpisow": limit_wpisow},
    }

    wynik = WynikLogow(
        sygnaly=tuple(sygnaly),
        pominietych_tablic=pominietych,
        discovery=discovery,
    )

    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)
    waliduj_brak_pii(payload, [])

    logger.info(
        "logi: %d tablic zbadanych, %d pominiętych, %d wpisów razem, %d tablic pozornie żywych",
        len(sygnaly),
        pominietych,
        sum(s.wpisow for s in sygnaly),
        sum(1 for s in sygnaly if s.pozornie_zywa),
    )
    if not znani:
        logger.warning(
            "brak listy użytkowników konta na wejściu — każdy autor wpisu wyjdzie "
            "jako nieznany, więc sygnał człowiek/automat będzie bezwartościowy"
        )
    return wynik
