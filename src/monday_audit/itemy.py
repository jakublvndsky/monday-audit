"""Itemy i agregaty — punkt 3 wytycznych (plan, faza 3).

Tu zdejmujemy D5 („`items_count` to granica"). Świadomie i z zapisanymi
warunkami, bo bez itemów nie ma leadów, ticketów ani przyrostu dziennego.

## Granica danych osobowych — warunek, nie zalecenie

Item niesie imię, nazwisko, telefon i mail **klienta naszego klienta**.
Dzisiejsza pseudonimizacja chroni użytkowników konta, nie treść itemów.
Dlatego z itemu wolno brać WYŁĄCZNIE:

    id, created_at, updated_at, group { id title }
    column_values (ids: [...])   — tylko kolumny typu `status`, wskazane po id

**Nigdy `name` itemu** (nazwa leada to zwykle osoba albo firma) i **nigdy
`column_values` bez `ids`**. Zmierzone 2026-09-22 (O46): wybiórcze pobranie
zwraca `{"lead_status": "Qualified"}` i nic poza tym.

## Czego NIE trzeba pobierać

**Liczba leadów i ticketów to suma `items_count`**, którą collector ma już za
darmo przy każdej tablicy. Itemy pobieramy WYŁĄCZNIE po rozkłady: etapy lejka
i przyrost dzienny. To zmienia rząd kosztu — z „476 wywołań na konto" na „tyle,
ile tablic z rozpoznanym lejkiem".

## Rozpoznanie lejka — trzy stopnie (O46)

1. kolumna `lead_status` albo `deal_stage` — lejek **znany**, etapy wprost;
2. same grupy — raportujemy je jako **grupy**, nie jako etapy. Czy grupy są
   lejkiem, rozstrzyga faza 5;
3. ani jedno, ani drugie — **„nie rozpoznano lejka"**, bez zgadywania po nazwach.

Goły `status` NIE jest stopniem pierwszym: to domyślny identyfikator pierwszej
kolumny statusu na dowolnej tablicy, więc robiłby lejek z czegokolwiek.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from monday_audit.klient import MondayClient
from monday_audit.przeglad_tablic import na_datetime

logger = logging.getLogger(__name__)

# Kanoniczne identyfikatory kolumn zakładanych przez SZABLONY monday.
#
# ZMIERZONE 2026-09-22 (O46): `Leads e-commerce` ma czternaście kolumn typu
# `status`, a tylko `lead_status` niesie lejek. `Deals` ma `deal_stage`.
#
# `status95` doszło po pełnym przebiegu (O51), który pokazał, że dla produktu
# `service` nie rozpoznawaliśmy lejka W OGÓLE — 64 tablice, zero z lejkiem.
# ZMIERZONE na tablicy `Tickets`:
#
#     status95 „Status": New, New reply, Awaiting customer, Reopen,
#                        Self resolved, Resolved
#     done_colors: [11, 1] → Resolved, Self resolved
#
# To cykl życia ZGŁOSZENIA, czyli dokładny odpowiednik `lead_status` po stronie
# obsługi. I — w odróżnieniu od odrzuconego pomysłu „kolumna z `done_colors`
# to lejek" — ten identyfikator ROZRÓŻNIA: nie ma go wśród ośmiu najczęstszych
# id kolumn statusu na koncie, podczas gdy `done_colors` ma 76% tablic.
#
# Czego to świadomie NIE obejmuje: tablic serwisowych, na których klient zbudował
# własną kolumnę (`color_*`). Tam nie da się wskazać cyklu życia bez zgadywania,
# więc zostają stopniem 2 albo 3 — i to jest właściwa odpowiedź, nie brak.
KOLUMNY_LEJKA = ("lead_status", "deal_stage", "status95")

# ZMIERZONE (O43): 100 itemów = 1 wywołanie, complexity 2020, ~1 s.
LIMIT_ITEMOW = 100

# Okno przyrostu. Ta sama liczba, co w przeglądzie tablic i w `cli.py`.
OKNO_DNI = 90

_PYTANIE_ITEMOW = """
query ($id: [ID!], $limit: Int!, $cursor: String, $kolumny: [String!]) {
  boards (ids: $id) {
    items_page (limit: $limit, cursor: $cursor) {
      cursor
      items {
        id
        created_at
        updated_at
        group { id title }
        column_values (ids: $kolumny) { id text }
      }
    }
  }
}
"""


@dataclass(frozen=True, slots=True)
class Lejek:
    """Jak rozpoznaliśmy etapy na tablicy — i czy w ogóle."""

    stopien: int  # 1 = kolumna kanoniczna, 2 = grupy, 3 = nierozpoznany
    kolumna: str | None
    opis: str
    # Etykiety, które KLIENT oznaczył jako kończące proces (O48). Nie nasza
    # heurystyka, tylko odczyt `done_colors` z ustawień kolumny — czyli
    # deklaracja, a nie zgadywanie. Puste, gdy kolumna nie niesie ustawień
    # albo lejek jest rozpoznany po grupach.
    etapy_koncowe: frozenset[str] = frozenset()


def etapy_zadeklarowane(kolumna: dict[str, Any]) -> frozenset[str]:
    """`settings_str` kolumny statusu → etykiety oznaczone jako końcowe (O48).

    ZMIERZONE 2026-09-22 na tablicy 5095638019:

        labels:      {"0": "Eligible", ..., "4": "Branding Completed"}
        done_colors: [4]

    `Branding Completed` jest końcowy **dlatego, że klient tak ustawił kolumnę**,
    a nie dlatego, że pasuje do słownika słów. To źródło mocniejsze od każdej
    naszej reguły i darmowe: `settings_str` idzie w tym samym zapytaniu, co
    reszta kolumn.

    Zwraca pusty zbiór przy czymkolwiek nieoczekiwanym. Ustawienia kolumny są
    treścią pisaną przez klienta — wywracanie audytu na cudzym JSON-ie byłoby
    oddaniem mu kontroli nad naszym przebiegiem.
    """
    surowe = kolumna.get("settings_str")
    if not isinstance(surowe, str) or not surowe.strip():
        return frozenset()
    try:
        ustawienia = json.loads(surowe)
    except (ValueError, TypeError):
        return frozenset()
    if not isinstance(ustawienia, dict):
        return frozenset()
    etykiety = ustawienia.get("labels")
    konczace = ustawienia.get("done_colors")
    if not isinstance(etykiety, dict) or not isinstance(konczace, list):
        return frozenset()
    return frozenset(
        tekst
        for indeks in konczace
        if isinstance(tekst := etykiety.get(str(indeks)), str) and tekst.strip()
    )


def rozpoznaj_lejek(kolumny: list[dict[str, Any]], grup: int) -> Lejek:
    """Reguła trójstopniowa z O46. Nie zgaduje po nazwach — to faza 5."""
    po_id = {str(k.get("id")): k for k in kolumny if isinstance(k, dict)}
    for kanoniczna in KOLUMNY_LEJKA:
        if kanoniczna in po_id:
            return Lejek(
                stopien=1,
                kolumna=kanoniczna,
                opis=f"kolumna `{kanoniczna}` z szablonu monday CRM",
                etapy_koncowe=etapy_zadeklarowane(po_id[kanoniczna]),
            )
    if grup > 1:
        return Lejek(
            stopien=2,
            kolumna=None,
            opis=f"brak kolumny kanonicznej; {grup} grup — raportowane jako GRUPY, nie etapy",
        )
    return Lejek(stopien=3, kolumna=None, opis="nie rozpoznano lejka")


@dataclass(frozen=True, slots=True)
class AgregatTablicy:
    """Rozkłady dla jednej tablicy. Bez ani jednego pola z treścią itemu."""

    board_id: str
    nazwa: str | None
    produkt: str | None
    itemow: int
    lejek: Lejek
    # Etapy (stopień 1) albo grupy (stopień 2). Klucz to etykieta, nie id —
    # raport pokazuje „Qualified", nie `color_mks8s5y`.
    rozklad: dict[str, int]
    # Ile itemów powstało w oknie; `przyrost_dzienny` to iloraz, nie osobny pomiar.
    powstalo_w_oknie: int
    okno_dni: int
    pobranych: int
    urwane: bool = False

    @property
    def przyrost_dzienny(self) -> float:
        return round(self.powstalo_w_oknie / self.okno_dni, 2) if self.okno_dni else 0.0


@dataclass(frozen=True, slots=True)
class PlanPobrania:
    """Co pobieramy, czego nie i ile to kosztuje — POLICZONE PRZED pobieraniem.

    `items_count` mamy przy każdej tablicy za darmo, więc koszt całego zejścia
    na itemy da się wyliczyć, zanim wydamy pierwsze wywołanie (O43). Sampling
    jest tu decyzją arytmetyczną, a nie strojeniem po fakcie.
    """

    do_pobrania: tuple[tuple[str, int], ...]
    pominiete: tuple[tuple[str, int], ...]
    wywolan_szacunek: int
    budzet: int

    @property
    def mimo_budzetu_pominieto(self) -> int:
        return len(self.pominiete)


def zaplanuj_pobranie(kandydaci: list[tuple[str, int]], *, budzet: int) -> PlanPobrania:
    """Dzieli tablice na pobierane i pomijane tak, żeby zmieścić się w budżecie.

    Kolejność: **od najmniejszych**. Przy ciasnym budżecie dwadzieścia małych
    tablic niesie więcej informacji o koncie niż jedna wielka — a wielka i tak
    ma policzoną liczbę itemów z `items_count`, więc tracimy tylko jej rozkład.
    """
    posortowane = sorted(kandydaci, key=lambda p: p[1])
    do_pobrania: list[tuple[str, int]] = []
    pominiete: list[tuple[str, int]] = []
    wydane = 0

    for board_id, itemow in posortowane:
        koszt = max(1, -(-itemow // LIMIT_ITEMOW))
        if wydane + koszt <= budzet:
            do_pobrania.append((board_id, itemow))
            wydane += koszt
        else:
            pominiete.append((board_id, itemow))

    return PlanPobrania(
        do_pobrania=tuple(do_pobrania),
        pominiete=tuple(pominiete),
        wywolan_szacunek=wydane,
        budzet=budzet,
    )


def policz_rozklad(
    itemy: list[dict[str, Any]], lejek: Lejek, *, teraz: datetime | None = None, okno_dni: int
) -> tuple[dict[str, int], int]:
    """Rozkład etapów albo grup plus liczba itemów powstałych w oknie."""
    rozklad: Counter[str] = Counter()
    granica = (teraz or datetime.now(tz=UTC)) - timedelta(days=okno_dni)
    w_oknie = 0

    for item in itemy:
        if not isinstance(item, dict):
            continue

        if lejek.stopien == 1:
            etykieta = ""
            for wartosc in item.get("column_values") or []:
                if isinstance(wartosc, dict) and wartosc.get("id") == lejek.kolumna:
                    etykieta = str(wartosc.get("text") or "")
                    break
            # Pusty status to informacja, nie brak danych: lead bez etapu leży
            # poza lejkiem i w raporcie ma być widoczny osobno.
            rozklad[etykieta or "(bez etapu)"] += 1
        else:
            grupa = item.get("group") or {}
            rozklad[str(grupa.get("title") or "(bez grupy)")] += 1

        powstal = na_datetime(item.get("created_at"))
        if powstal is not None and powstal >= granica:
            w_oknie += 1

    return dict(sorted(rozklad.items(), key=lambda p: -p[1])), w_oknie


async def pobierz_itemy(
    klient: MondayClient, board_id: str, *, kolumny: list[str], maks_stron: int = 200
) -> tuple[list[dict[str, Any]], bool]:
    """Itemy jednej tablicy przez kursor. Zwraca `(itemy, czy_urwano)`.

    `maks_stron` to bezpiecznik, nie strojenie: kursor bez sufitu przy usterce
    API kręciłby się w nieskończoność na cudzym limicie wywołań.
    """
    zebrane: list[dict[str, Any]] = []
    kursor: str | None = None
    for _ in range(maks_stron):
        odpowiedz = await klient.query(
            _PYTANIE_ITEMOW,
            {"id": [board_id], "limit": LIMIT_ITEMOW, "cursor": kursor, "kolumny": kolumny},
            etykieta="itemy",
        )
        tablice = odpowiedz.get("boards") or []
        strona = (tablice[0].get("items_page") if tablice else None) or {}
        zebrane.extend(i for i in (strona.get("items") or []) if isinstance(i, dict))
        kursor = strona.get("cursor")
        if not kursor:
            return zebrane, False
    return zebrane, True


@dataclass(frozen=True, slots=True)
class WynikItemow:
    """Punkt 3 wytycznych dla całego konta."""

    itemow_razem: int
    itemow_per_produkt: dict[str, int]
    tablice: tuple[AgregatTablicy, ...]
    plan: PlanPobrania
    wywolan: int
    zastrzezenia: tuple[str, ...] = field(default_factory=tuple)

    def do_json(self) -> dict[str, Any]:
        return {
            "itemow_razem": self.itemow_razem,
            "itemow_per_produkt": dict(self.itemow_per_produkt),
            "tablice": [
                {
                    "board_id": t.board_id,
                    "nazwa": t.nazwa,
                    "produkt": t.produkt,
                    "itemow": t.itemow,
                    "lejek_stopien": t.lejek.stopien,
                    "lejek_opis": t.lejek.opis,
                    "rozklad": dict(t.rozklad),
                    "powstalo_w_oknie": t.powstalo_w_oknie,
                    "przyrost_dzienny": t.przyrost_dzienny,
                    "pobranych": t.pobranych,
                    "urwane": t.urwane,
                }
                for t in self.tablice
            ],
            "plan": {
                "do_pobrania": len(self.plan.do_pobrania),
                "pominiete": len(self.plan.pominiete),
                "wywolan_szacunek": self.plan.wywolan_szacunek,
                "budzet": self.plan.budzet,
            },
            "wywolan": self.wywolan,
            "zastrzezenia": list(self.zastrzezenia),
        }


async def zbuduj_itemy(
    klient: MondayClient,
    rejestr: Any,
    tablice: list[dict[str, Any]],
    *,
    produkty: dict[str, str | None] | None = None,
    budzet: int,
    okno_dni: int = OKNO_DNI,
    teraz: datetime | None = None,
) -> WynikItemow:
    """Punkt 3 wytycznych dla konta. `tablice` przychodzą z zewnątrz.

    Tablic NIE pobieramy tutaj i to jest decyzja: w repo były już cztery
    zapytania o `boards`, a piąte oznaczałoby piąte miejsce do aktualizacji przy
    każdej zmianie pól. `przeglad_tablic.pobierz_tablice` oddaje wszystko, czego
    ten moduł potrzebuje — łącznie z `items_count`, `groups` i `columns`.
    """
    przed = rejestr.wywolan

    # Liczba leadów i ticketów NIE wymaga ani jednego wywołania: `items_count`
    # przyszło razem z tablicami.
    itemow_razem = 0
    per_produkt: Counter[str] = Counter()
    kandydaci: list[tuple[str, int]] = []
    po_id: dict[str, dict[str, Any]] = {}

    for tablica in tablice:
        board_id = str(tablica.get("id") or "")
        if not board_id:
            continue
        ile = int(tablica.get("items_count") or 0)
        itemow_razem += ile
        po_id[board_id] = tablica

        # Per produkt liczymy TUTAJ, ze wszystkich tablic — tak jak sumę. Pierwsza
        # wersja liczyła to w pętli po pobranych i gubiła tablice odcięte
        # budżetem oraz te bez lejka: model dostawał `razem: 7350` obok
        # `crm: 50` bez słowa wyjaśnienia (review 2026-09-23). `items_count`
        # jest za darmo, więc nie ma powodu, żeby podział był uboższy od sumy.
        #
        # Produkt bierzemy z mapy workspace'ów, którą inwentarz już ma — zamiast
        # dokładać `account_product` do zapytania o tablice.
        workspace_id = str((tablica.get("workspace") or {}).get("id") or "")
        if produkt_tablicy := (produkty or {}).get(workspace_id):
            per_produkt[produkt_tablicy] += ile

        lejek = rozpoznaj_lejek(tablica.get("columns") or [], len(tablica.get("groups") or []))
        # Rozkład pobieramy TYLKO tam, gdzie jest co rozkładać. Tablica bez
        # rozpoznanego lejka i bez grup nie powie nic, czego nie mówi `items_count`.
        if lejek.stopien <= 2:
            kandydaci.append((board_id, ile))

    plan = zaplanuj_pobranie(kandydaci, budzet=budzet)

    agregaty: list[AgregatTablicy] = []
    for board_id, ile in plan.do_pobrania:
        tablica = po_id[board_id]
        lejek = rozpoznaj_lejek(tablica.get("columns") or [], len(tablica.get("groups") or []))
        kolumny = [lejek.kolumna] if lejek.kolumna else []
        itemy, urwane = await pobierz_itemy(klient, board_id, kolumny=kolumny)
        rozklad, w_oknie = policz_rozklad(itemy, lejek, teraz=teraz, okno_dni=okno_dni)

        workspace_id = str((tablica.get("workspace") or {}).get("id") or "")
        produkt = (produkty or {}).get(workspace_id)
        agregaty.append(
            AgregatTablicy(
                board_id=board_id,
                nazwa=tablica.get("name"),
                produkt=produkt,
                itemow=ile,
                lejek=lejek,
                rozklad=rozklad,
                powstalo_w_oknie=w_oknie,
                okno_dni=okno_dni,
                pobranych=len(itemy),
                urwane=urwane,
            )
        )

    agregaty.sort(key=lambda a: a.itemow, reverse=True)

    zastrzezenia = [
        "z itemów czytamy WYŁĄCZNIE id, daty, grupę i wskazaną kolumnę etapu — "
        "nazwy itemów i pozostałe kolumny nie wchodzą do procesu (O46)",
    ]
    if plan.pominiete:
        najwieksza = max(i for _, i in plan.pominiete)
        zastrzezenia.append(
            f"{len(plan.pominiete)} tablic pominiętych przez budżet {plan.budzet} wywołań "
            f"(największa ma {najwieksza} itemów) — ich LICZBA itemów jest znana, rozkład nie"
        )
    # `items_count` NIE jest obietnicą, że da się te itemy pobrać. ZMIERZONE
    # 2026-09-22 (O47): `👤 Leads` ma `items_count: 7076`, jest `active`, typu
    # `board`, ma 28 grup — i oddaje ZERO itemów, zarówno przez `items_page`
    # tablicy, jak i przez `items_page` każdej grupy. Bez błędu, po prostu pusto.
    #
    # Raport, który pokazuje „7076 itemów" obok pustego rozkładu, kłamie ciszej,
    # niż gdyby się wywalił.
    puste = [a for a in agregaty if a.itemow > 0 and a.pobranych == 0]
    if puste:
        zastrzezenia.append(
            f"{len(puste)} tablic deklaruje itemy, ale nie oddaje ani jednego "
            f"(np. {puste[0].nazwa or puste[0].board_id} — {puste[0].itemow} wg `items_count`) "
            "— rozkład dla nich jest PUSTY, a nie zerowy (O47)"
        )
    niepelne = [a for a in agregaty if a.pobranych and a.pobranych < a.itemow]
    if niepelne:
        zastrzezenia.append(
            f"{len(niepelne)} tablic oddało mniej itemów, niż deklaruje `items_count` "
            "— rozkład liczony z tego, co przyszło"
        )
    urwanych = [a for a in agregaty if a.urwane]
    if urwanych:
        zastrzezenia.append(f"{len(urwanych)} tablic urwanych sufitem stron — rozkład niepełny")

    wynik = WynikItemow(
        itemow_razem=itemow_razem,
        itemow_per_produkt=dict(sorted(per_produkt.items())),
        tablice=tuple(agregaty),
        plan=plan,
        wywolan=rejestr.wywolan - przed,
        zastrzezenia=tuple(zastrzezenia),
    )
    logger.info(
        "itemy: %d razem, rozkład dla %d tablic (%d wywołań)",
        itemow_razem,
        len(agregaty),
        wynik.wywolan,
    )
    return wynik
