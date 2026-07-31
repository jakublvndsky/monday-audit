"""Collector — activity logs z samplingiem (etap 3.7).

To jest miejsce, w którym najłatwiej spalić dzienny limit klienta: nie ma
logu na poziomie konta, więc każda tablica to osobne wywołanie. Dlatego
sampling jest tu warunkiem wykonalności, nie optymalizacją.

**Z logów bierzemy sygnały, nie treść.** Pole `data` zawiera realne wartości
kolumn i nazwy itemów (`value`, `previous_value`, `pulse_name`) — czyli
dokładnie to, czego zabrania D5 i granica PII. Nie trafia do snapshotu ani
w całości, ani we fragmentach.

Trzy pułapki zmierzone na koncie CXLABS (`OTWARTE.md` O13):

1. **`created_at` nie jest datą ISO.** To liczba w jednostkach 100 ns od epoki,
   np. `17830789794688296`. Naiwne `fromisoformat` albo porównanie stringów
   dają śmieci, a na tym polu stoi całe okno czasowe.
2. **API nie ma znacznika „to zrobiła automatyzacja".** `ActivityLogType` ma
   siedem pól i żadne nie mówi, czy autorem był człowiek. Rozstrzygamy przez
   porównanie pseudonimu autora z listą użytkowników konta z 3.4. To heurystyka
   i jest oznaczona jako heurystyka.
3. **Sto wpisów na stronę to nie „tyle jest".** Ruchliwa tablica ma ich więcej,
   więc paginujemy — z zabezpieczeniem na wypadek, gdyby `page` był ignorowany
   (w tym API zdarzył się już zepsuty filtr, O12).

**Dlaczego sygnałów jest tyle:** pierwsza wersja agregowała trzy osie osobno
(kto, co, kiedy) i wyrzucała powiązania. Do health score'u to za cienko —
„jedna osoba zrobiła 90% zmian trzy miesiące temu" to zupełnie inny stan konta
niż „pięć osób zmienia coś co tydzień", a obie sytuacje dawały identyczny
zestaw liczb. Doszły więc: znacznik ostatniej zmiany OD CZŁOWIEKA, udział
autorów, kubełki czasowe i podział zdarzeń na strukturalne, operacyjne
i uprawnieniowe. Żaden nie kosztuje dodatkowego wywołania — te dane były
już w odpowiedzi, tylko je gubiłem.
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
query ($ids: [ID!], $limit: Int!, $p: Int!, $od: ISO8601DateTime, $do: ISO8601DateTime) {
  boards (ids: $ids) {
    id
    activity_logs (limit: $limit, page: $p, from: $od, to: $do) {
      id event entity created_at user_id
    }
  }
}
"""

# `created_at` w logach to liczba jednostek 100 ns od epoki Unixa.
JEDNOSTEK_NA_SEKUNDE = 10_000_000

# Sufity samplingu. Wracamy do liczb ze specyfikacji 3.7 („top 30 + 20
# z ogona"), bo 10 + 5 dawało health score liczony na 14% tablic.
TOP_PO_ITEMACH = 30
Z_OGONA = 20
LIMIT_WPISOW = 100
MAKS_STRON_LOGOW = 5

# Ile ostatnich wpisów decyduje o odpowiedzi „żywa czy pozornie żywa".
OKNO_OSTATNICH = 5

# Kubełki czasowe. Bez rozkładu w czasie nie ma `ENGAGEMENT_DROP` — spadek
# zaangażowania widać w kształcie, nie w sumie.
KUBELKI = ((30, "0-30"), (60, "31-60"), (90, "61-90"))
KUBELEK_STARSZE = "starsze"

# Podział zdarzeń. `subscribe` i `set_entity_board_role` to NIE używanie
# tablicy — to zmiana dostępu. Na zbadanej tablicy CXLABS stanowiły 32 ze 100
# wpisów, więc wrzucenie ich do „operacyjnych" zawyżałoby sygnał życia
# o jedną trzecią.
STRUKTURALNE = frozenset(
    {
        "create_column",
        "delete_column",
        "update_column_title",
        "move_column",
        "create_group",
        "delete_group",
        "move_group",
        "update_group_title",
        "update_board_name",
        "board_workspace_id_changed",
        "create_board",
        "archive_board",
    }
)
OPERACYJNE = frozenset(
    {
        "create_pulse",
        "delete_pulse",
        "delete_group_pulse",
        "move_pulse",
        "update_column_value",
        "update_name",
        "create_update",
        "delete_update",
    }
)
UPRAWNIENIA = frozenset(
    {
        "subscribe",
        "unsubscribe",
        "set_entity_board_role",
        "remove_entity_board_role",
        "board_permissions_changed",
    }
)


class LogiError(RuntimeError):
    """Nie da się zebrać sygnałów aktywności."""


def na_datetime(surowy: Any) -> datetime | None:
    """`17830789794688296` → data. Dzielimy przez 10^7 (jednostki 100 ns)."""
    if surowy is None:
        return None
    tekst = str(surowy)
    if tekst.isdigit():
        jednostki = int(tekst)
        if jednostki <= 0:
            return None
        return datetime.fromtimestamp(jednostki / JEDNOSTEK_NA_SEKUNDE, tz=UTC)
    try:
        # Gdyby monday zmienił format na ISO — przyjmujemy bez zgadywania.
        return datetime.fromisoformat(tekst.replace("Z", "+00:00"))
    except ValueError:
        return None


def na_iso(surowy: Any) -> str | None:
    """Znacznik z logu jako ISO-8601 w UTC. Normalizuje też wejście już w ISO.

    Trzy przypadki, celowo rozdzielone: liczba dodatnia → data; liczba `0`
    albo `None` → brak znacznika, czyli `None`; tekst nieparsowalny → oryginał,
    bo lepiej oddać to, co przyszło, niż zgadywać.
    """
    if surowy is None:
        return None
    kiedy = na_datetime(surowy)
    if kiedy:
        return kiedy.isoformat()
    return None if str(surowy).isdigit() else str(surowy)


def klasa_zdarzenia(event: str) -> str:
    """Strukturalne, operacyjne, uprawnieniowe albo nieznane.

    Świadomie bez heurystyki po nazwie: nierozpoznane zdarzenie ląduje
    w `inne`, a jego nazwa idzie do discovery, żeby dało się je sklasyfikować
    w następnym runie. Zgadywanie po podłańcuchu dawałoby ciche pomyłki,
    a `po_event` i tak trzyma pełne liczniki.
    """
    if event in STRUKTURALNE:
        return "strukturalne"
    if event in OPERACYJNE:
        return "operacyjne"
    if event in UPRAWNIENIA:
        return "uprawnienia"
    return "inne"


@dataclass(frozen=True, slots=True)
class SygnalyTablicy:
    """Sygnały aktywności jednej tablicy. Zero treści."""

    board_id: str
    wpisow: int
    po_event: dict[str, int]
    po_entity: dict[str, int]
    po_klasie: dict[str, int]
    autorzy: tuple[str, ...]
    udzial_autorow: dict[str, int]
    autorow_znanych: int
    autorow_nieznanych: int
    najnowszy_at: str | None
    najstarszy_at: str | None
    najnowszy_od_znanego_at: str | None
    kubelki_dni: dict[str, int]
    ostatnich_od_znanych: int
    ostatnich_zbadanych: int
    urwane: bool

    @property
    def pozornie_zywa(self) -> bool:
        """Ma wpisy, ale żaden z ostatnich nie pochodzi od znanego użytkownika.

        Sygnał z 3.7 odróżniający tablicę żywą od takiej, którą podtrzymuje
        tylko automatyzacja.
        """
        return self.wpisow > 0 and self.ostatnich_zbadanych > 0 and self.ostatnich_od_znanych == 0

    @property
    def udzial_najaktywniejszego(self) -> float:
        """0.9 znaczy „jedna osoba zrobiła 90% zmian" — to sygnał, nie ciekawostka."""
        if not self.udzial_autorow:
            return 0.0
        return max(self.udzial_autorow.values()) / sum(self.udzial_autorow.values())

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "board_id": self.board_id,
            "wpisow": self.wpisow,
            "po_event": dict(self.po_event),
            "po_entity": dict(self.po_entity),
            "po_klasie": dict(self.po_klasie),
            "autorzy": list(self.autorzy),
            "udzial_autorow": dict(self.udzial_autorow),
            "udzial_najaktywniejszego": round(self.udzial_najaktywniejszego, 3),
            "autorow_znanych": self.autorow_znanych,
            "autorow_nieznanych": self.autorow_nieznanych,
            "najnowszy_at": self.najnowszy_at,
            "najstarszy_at": self.najstarszy_at,
            "najnowszy_od_znanego_at": self.najnowszy_od_znanego_at,
            "kubelki_dni": dict(self.kubelki_dni),
            "ostatnich_od_znanych": self.ostatnich_od_znanych,
            "ostatnich_zbadanych": self.ostatnich_zbadanych,
            "pozornie_zywa": self.pozornie_zywa,
            "urwane": self.urwane,
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
        klasy: Counter[str] = Counter()
        kubelki: Counter[str] = Counter()
        for sygnal in self.sygnaly:
            zdarzenia.update(sygnal.po_event)
            klasy.update(sygnal.po_klasie)
            kubelki.update(sygnal.kubelki_dni)

        zdominowane = [s for s in self.sygnaly if s.wpisow and s.udzial_najaktywniejszego >= 0.9]
        return {
            "tablic_zbadanych": len(self.sygnaly),
            "tablic_pominietych": self.pominietych_tablic,
            "tablic_bez_wpisow": sum(1 for s in self.sygnaly if s.wpisow == 0),
            "tablic_pozornie_zywych": sum(1 for s in self.sygnaly if s.pozornie_zywa),
            "tablic_bez_zmiany_od_czlowieka": sum(
                1 for s in self.sygnaly if s.wpisow and not s.najnowszy_od_znanego_at
            ),
            "tablic_zdominowanych_jednym_autorem": len(zdominowane),
            "tablic_z_urwanym_logiem": sum(1 for s in self.sygnaly if s.urwane),
            "wpisow_razem": sum(s.wpisow for s in self.sygnaly),
            "po_klasie": dict(klasy),
            "kubelki_dni": dict(kubelki),
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


def _kubelek(kiedy: datetime | None, teraz: datetime) -> str | None:
    if kiedy is None:
        return None
    dni = (teraz - kiedy).days
    for granica, nazwa in KUBELKI:
        if dni <= granica:
            return nazwa
    return KUBELEK_STARSZE


def _sygnaly(
    board_id: str,
    logi: Sequence[dict[str, Any]],
    *,
    client_id: str,
    sol: bytes,
    znane_hashe: Collection[str],
    urwane: bool,
    teraz: datetime,
) -> SygnalyTablicy:
    """Buduje sygnały z listy wpisów. Pole `data` w ogóle tu nie dociera.

    Autorów porównujemy po PSEUDONIMACH, nie po surowych identyfikatorach —
    hash jest deterministyczny, więc porównanie jest równoważne, a surowe id
    osoby nie musi przechodzić przez interfejs tej funkcji.
    """
    wzbogacone = [
        (
            wpis,
            policz_hash(client_id, str(wpis["user_id"]), sol)
            if wpis.get("user_id") is not None
            else None,
        )
        for wpis in logi
    ]
    najstarsza_mozliwa = datetime.min.replace(tzinfo=UTC)
    posortowane = sorted(
        wzbogacone,
        key=lambda para: na_datetime(para[0].get("created_at")) or najstarsza_mozliwa,
        reverse=True,
    )

    udzial: Counter[str] = Counter()
    kubelki: Counter[str] = Counter()
    klasy: Counter[str] = Counter()
    znanych = 0
    najnowszy_znany: datetime | None = None

    for wpis, haszyk in wzbogacone:
        klasy[klasa_zdarzenia(str(wpis.get("event")))] += 1
        kiedy = na_datetime(wpis.get("created_at"))
        kubelek = _kubelek(kiedy, teraz)
        if kubelek:
            kubelki[kubelek] += 1
        if haszyk is None:
            continue
        udzial[haszyk] += 1
        if haszyk in znane_hashe:
            znanych += 1
            if kiedy and (najnowszy_znany is None or kiedy > najnowszy_znany):
                najnowszy_znany = kiedy

    ostatnie = posortowane[:OKNO_OSTATNICH]
    czyste = [z for z in (na_datetime(w.get("created_at")) for w, _ in posortowane) if z]

    return SygnalyTablicy(
        board_id=board_id,
        wpisow=len(logi),
        po_event=dict(Counter(str(w.get("event")) for w in logi)),
        po_entity=dict(Counter(str(w.get("entity")) for w in logi)),
        po_klasie=dict(klasy),
        autorzy=tuple(sorted(udzial)),
        udzial_autorow=dict(udzial.most_common()),
        autorow_znanych=znanych,
        autorow_nieznanych=sum(udzial.values()) - znanych,
        najnowszy_at=czyste[0].isoformat() if czyste else None,
        najstarszy_at=czyste[-1].isoformat() if czyste else None,
        najnowszy_od_znanego_at=najnowszy_znany.isoformat() if najnowszy_znany else None,
        kubelki_dni=dict(kubelki),
        ostatnich_od_znanych=sum(1 for _, haszyk in ostatnie if haszyk and haszyk in znane_hashe),
        ostatnich_zbadanych=len(ostatnie),
        urwane=urwane,
    )


async def _pobierz_logi(
    klient: MondayClient,
    board_id: str,
    *,
    limit: int,
    od: str | None,
    do: str | None,
    maks_stron: int,
) -> tuple[list[dict[str, Any]], bool, bool | None]:
    """Paginuje log jednej tablicy. Zwraca (wpisy, urwane, czy_paginacja_dziala).

    Deduplikacja po `id` wpisu jest zabezpieczeniem, nie optymalizacją: gdyby
    `page` był ignorowany — a w tym API zdarzył się już zepsuty filtr (O12) —
    naiwna paginacja policzyłaby te same zdarzenia po kilka razy i zawyżyła
    każdą metrykę aktywności. Powtórzona strona przerywa pętlę i ląduje
    w discovery jako `paginacja_logow_dziala: false`.
    """
    zebrane: dict[str, dict[str, Any]] = {}
    paginacja_dziala: bool | None = None
    urwane = False

    for numer in range(1, maks_stron + 1):
        dane = await klient.query(
            ZAPYTANIE_LOGOW,
            {"ids": [board_id], "limit": limit, "p": numer, "od": od, "do": do},
            etykieta="logi",
        )
        wpisy = dane.get("boards") or []
        partia = (wpisy[0].get("activity_logs") or []) if wpisy else []
        if not partia:
            break

        nowe = [w for w in partia if str(w.get("id")) not in zebrane]
        if numer > 1:
            paginacja_dziala = bool(nowe)
        for wpis in nowe:
            zebrane[str(wpis.get("id"))] = wpis

        if not nowe or len(partia) < limit:
            break
        if numer == maks_stron:
            urwane = True

    return list(zebrane.values()), urwane, paginacja_dziala


async def zbierz_logi(
    klient: MondayClient,
    tablice: Sequence[Tablica],
    *,
    client_id: str,
    sol: bytes,
    znane_hashe: Collection[str] = (),
    od: str | None = None,
    do: str | None = None,
    limit_wpisow: int = LIMIT_WPISOW,
    top: int = TOP_PO_ITEMACH,
    z_ogona: int = Z_OGONA,
    maks_stron: int = MAKS_STRON_LOGOW,
    teraz: datetime | None = None,
) -> WynikLogow:
    """Sampluje activity logs i wyciąga z nich sygnały, nie treść.

    `znane_hashe` to pseudonimy użytkowników konta z 3.4 (`WynikOsob.hashe`).
    """
    teraz = teraz or datetime.now(tz=UTC)
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
    znane = {str(h) for h in znane_hashe}
    paginacja_dziala: bool | None = None
    nieznane_zdarzenia: set[str] = set()

    for tablica in probka:
        logi, urwane, dziala = await _pobierz_logi(
            klient,
            tablica.board_id,
            limit=limit_wpisow,
            od=od,
            do=do,
            maks_stron=maks_stron,
        )
        if dziala is not None:
            paginacja_dziala = dziala if paginacja_dziala is None else (paginacja_dziala and dziala)
        nieznane_zdarzenia.update(
            str(w.get("event")) for w in logi if klasa_zdarzenia(str(w.get("event"))) == "inne"
        )
        sygnaly.append(
            _sygnaly(
                tablica.board_id,
                logi,
                client_id=client_id,
                sol=sol,
                znane_hashe=znane,
                urwane=urwane,
                teraz=teraz,
            )
        )

    bez_autorow = sum(1 for s in sygnaly if s.autorow_nieznanych and not s.autorow_znanych)
    discovery = {
        "logi_dostepne": any(s.wpisow for s in sygnaly),
        # Heurystyka, nie fakt z API — `ActivityLogType` nie ma znacznika bota.
        "rozroznienie_czlowiek_automat": "heurystyka: pseudonim autora nieobecny na liście konta",
        "znanych_uzytkownikow_na_wejsciu": len(znane),
        "tablic_tylko_z_nieznanymi_autorami": bez_autorow,
        "okno_czasowe": {"od": od, "do": do} if (od or do) else None,
        "created_at_w_jednostkach_100ns": True,
        "paginacja_logow_dziala": paginacja_dziala,
        # Nazwy zdarzeń, których nie umiemy zaklasyfikować — do sklasyfikowania
        # w następnym runie. `po_event` trzyma je tak czy inaczej.
        "nieznane_zdarzenia": sorted(nieznane_zdarzenia),
        "probka": {
            "top_po_itemach": top,
            "z_ogona": z_ogona,
            "limit_wpisow": limit_wpisow,
            "maks_stron": maks_stron,
        },
    }

    wynik = WynikLogow(sygnaly=tuple(sygnaly), pominietych_tablic=pominietych, discovery=discovery)
    waliduj_brak_pii(json.dumps(wynik.do_snapshotu(), ensure_ascii=False), [])

    podsumowanie = wynik.podsumowanie()
    logger.info(
        "logi: %d tablic zbadanych, %d pominiętych, %d wpisów; pozornie żywych %d, "
        "bez zmiany od człowieka %d, zdominowanych jednym autorem %d",
        len(sygnaly),
        pominietych,
        podsumowanie["wpisow_razem"],
        podsumowanie["tablic_pozornie_zywych"],
        podsumowanie["tablic_bez_zmiany_od_czlowieka"],
        podsumowanie["tablic_zdominowanych_jednym_autorem"],
    )
    if not znane:
        logger.warning(
            "brak listy użytkowników konta na wejściu — każdy autor wyjdzie "
            "jako nieznany, więc sygnał człowiek/automat będzie bezwartościowy"
        )
    if paginacja_dziala is False:
        logger.warning(
            "argument `page` w `activity_logs` NIE stronicuje — druga strona "
            "powtórzyła wpisy pierwszej. Liczby są z jednej strony, nie z okna"
        )
    if nieznane_zdarzenia:
        logger.info(
            "[DISCOVERY] %d nieznanych typów zdarzeń do sklasyfikowania: %s",
            len(nieznane_zdarzenia),
            ", ".join(sorted(nieznane_zdarzenia)[:10]),
        )
    return wynik
