"""Przegląd tablic — punkt 4 wytycznych (plan, faza 2b).

Punkt 4 wygląda jak specyfikacja tabeli, ale jest listą **metryk**: tablice wg
rodzaju, średnia userów na tablicę, goście na tablicy, ostatnia aktywność,
automatyzacje. Tabela z dwoma tysiącami wierszy nie odpowiada na żadne z tych
pytań, więc ten moduł produkuje **agregaty po wszystkich tablicach plus krótką
listę tablic z żywymi automatyzacjami**.

## Dlaczego automatyzacje są tylko przy kilku tablicach

Bo API nie oddaje listy automatyzacji ani ich przypisania do tablic (O41, O42).
Jedyna droga to zdarzenia uruchomień. ZMIERZONE 2026-09-22 na CXLABS:
`trigger_events` z filtrem `hostType: "board"` zwraca zdarzenia, **z których
każde ma `hostInstanceId`**, czyli identyfikator tablicy — 53 zdarzenia i siedem
tablic w JEDNYM wywołaniu. Bez tego filtru przypisanie ma 3% zdarzeń, a wariant
per tablica kosztowałby 2016 zapytań.

## Czego ten przegląd NIE pokazuje, i raport musi to napisać

- automatyzacji, które **istnieją, ale nigdy nie odpaliły** — w tym API są
  niewidoczne, a to często te najciekawsze w audycie,
- automatyzacji o `hostType` innym niż `board` (`account_level`,
  `app_feature_object`),
- czegokolwiek spoza okna `OKNO_DNI`.

Liczymy **uruchomienia**, nie automatyzacje: `TriggerEvent` nie niesie
identyfikatora automatyzacji, więc „ile ich jest na tej tablicy" pozostaje poza
zasięgiem. Kolumna nazywa się `uruchomien` i ma się tak nazywać.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from monday_audit.obraz.inwentarz import TYP_TABLICY, pobierz_uzytkownikow
from monday_audit.zbieranie.klient import MondayClient
from monday_audit.zbieranie.osoby import RODZAJ_GOSC
from monday_audit.zbieranie.podglad_zakresu import RejestrPodgladu, WorkspaceDoWyboru

logger = logging.getLogger(__name__)

# Okno zdarzeń. Ta sama wartość, co `--dni-okna` w `cli.py` — dwa różne okna
# w jednym raporcie znaczyłyby dwie różne odpowiedzi na „ostatnio".
OKNO_DNI = 90

# Strona 25, nie 100: to zapytanie ciągnie `subscribers` i `owners`, a one dawały
# większość complexity w collectorze. Skill `monday-graphql` zaleca ~25 przy
# zagnieżdżeniach i nie ma powodu tego obchodzić.
LIMIT_TABLIC = 25
LIMIT_ZDARZEN = 200

_PYTANIE_TABLIC_Z_LUDZMI = """
query ($limit: Int!, $p: Int!) {
  boards (limit: $limit, page: $p, state: active, order_by: created_at) {
    id
    name
    type
    board_kind
    updated_at
    items_count
    workspace { id name }
    owners { id }
    subscribers { id }
    groups { id }
    # `settings_str` niesie `done_colors` — deklarację klienta, które etapy
    # kończą proces (O48). ZERO dodatkowych wywołań: kolumny i tak pobieramy,
    # więc to samo zapytanie, tylko szersze. Rośnie complexity, ale wiążący
    # jest limit dzienny, nie complexity.
    columns { id type settings_str }
  }
}
"""

# `hostType: "board"` jest tu całym mechanizmem, nie zawężeniem dla oszczędności:
# bez niego 97% zdarzeń nie mówi, gdzie się wykonało.
_PYTANIE_ZDARZEN = """
query ($f: TriggerEventsFiltersInput, $off: Int) {
  trigger_events (filters: $f, nextPageOffset: $off) {
    triggerEvents {
      triggerUuid
      hostInstanceId
      eventState
      triggerStartedAt
    }
  }
}
"""


@dataclass(frozen=True, slots=True)
class TablicaZywa:
    """Tablica, na której automatyzacje faktycznie się uruchamiały."""

    board_id: str
    nazwa: str | None
    workspace_nazwa: str | None
    produkt: str | None
    uruchomien: int
    bledow: int
    ostatnie_uruchomienie: str | None


@dataclass(frozen=True, slots=True)
class PrzegladTablic:
    """Punkt 4 wytycznych: agregaty po tablicach plus lista tych z automatyzacjami."""

    tablic: int
    po_rodzaju: dict[str, int]
    po_typie: dict[str, int]

    # Średnia i mediana obok siebie, bo przy tablicach rozjeżdżają się mocno:
    # kilka tablic ogólnofirmowych z setką subskrybentów zawyża średnią i sugeruje
    # zaangażowanie, którego nie ma na pozostałych.
    userow_srednio: float | None
    userow_mediana: float | None
    gosci_na_tablicach: int
    tablic_z_goscmi: int

    # Kubełki po `updated_at`: ile tablic ruszało się w 30, 90, 365 dniach i ile
    # nie ruszało się dłużej. `board.updated_at` zaniża wiek (O18), więc to jest
    # sygnał, a nie wyrok.
    aktywnosc: dict[str, int]

    zywe_automatyzacje: tuple[TablicaZywa, ...]
    uruchomien_razem: int
    okno_dni: int

    wywolan: int = 0
    zastrzezenia: tuple[str, ...] = field(default_factory=tuple)

    def do_json(self) -> dict[str, Any]:
        return {
            "tablic": self.tablic,
            "po_rodzaju": dict(self.po_rodzaju),
            "po_typie": dict(self.po_typie),
            "userow_srednio": self.userow_srednio,
            "userow_mediana": self.userow_mediana,
            "gosci_na_tablicach": self.gosci_na_tablicach,
            "tablic_z_goscmi": self.tablic_z_goscmi,
            "aktywnosc": dict(self.aktywnosc),
            "zywe_automatyzacje": [
                {
                    "board_id": t.board_id,
                    "nazwa": t.nazwa,
                    "workspace_nazwa": t.workspace_nazwa,
                    "produkt": t.produkt,
                    "uruchomien": t.uruchomien,
                    "bledow": t.bledow,
                    "ostatnie_uruchomienie": t.ostatnie_uruchomienie,
                }
                for t in self.zywe_automatyzacje
            ],
            "uruchomien_razem": self.uruchomien_razem,
            "okno_dni": self.okno_dni,
            "wywolan": self.wywolan,
            "zastrzezenia": list(self.zastrzezenia),
        }


def na_datetime(znacznik: str | None) -> datetime | None:
    """Znacznik z API → `datetime`. `None`, gdy pusty albo nie do sparsowania.

    Znaczniki **przechowujemy jako napisy** — tak robi całe repo (`updated_at`,
    `last_activity`, `run_at`), bo idą przez `do_json()` do frontu, a generator
    typów nie zna `datetime` i wystawiłby je jako `unknown`.

    Ale **porównujemy po sparsowaniu**. Porównanie napisów ISO działa tylko
    dopóki format jest identyczny co do znaku: `2026-09-22T08:41:25.933Z`
    i `2026-09-22T10:41:25+02:00` to ten sam moment, a leksykograficznie
    wygrywa drugi. To dokładnie ta klasa cichego błędu, która nie rzuca wyjątku
    i daje zły wynik.
    """
    if not znacznik:
        return None
    try:
        return datetime.fromisoformat(znacznik.replace("Z", "+00:00"))
    except ValueError:
        return None


def kubelek_aktywnosci(updated_at: str | None, *, teraz: datetime | None = None) -> str:
    """`30d` / `90d` / `365d` / `starsze` / `nieznane`.

    Kubełek, nie data, bo w raporcie liczy się rząd wielkości: „nie ruszała się
    od roku" niesie decyzję, a „ostatnia zmiana 2025-08-14" wymaga liczenia
    w pamięci czytającego.
    """
    kiedy = na_datetime(updated_at)
    if kiedy is None:
        return "nieznane"
    dni = ((teraz or datetime.now(tz=UTC)) - kiedy).days
    if dni <= 30:
        return "30d"
    if dni <= 90:
        return "90d"
    if dni <= 365:
        return "365d"
    return "starsze"


@dataclass(frozen=True, slots=True)
class Agregaty:
    """Wynik `policz_agregaty`. Dataclassa, nie słownik: do 2026-09-22 te siedem
    pól jeździło jako `dict[str, Any]`, więc literówka w kluczu przechodziła
    mypy i wywalała się dopiero w locie."""

    tablic: int
    po_rodzaju: dict[str, int]
    po_typie: dict[str, int]
    userow_srednio: float | None
    userow_mediana: float | None
    gosci_na_tablicach: int
    tablic_z_goscmi: int
    aktywnosc: dict[str, int]


def policz_agregaty(
    tablice: list[dict[str, Any]], goscie: set[str], *, teraz: datetime | None = None
) -> Agregaty:
    """Agregaty po tablicach. `goscie` to identyfikatory kont o rodzaju `guest`.

    **Liczone są wyłącznie obiekty `type: board`.** Rozbicie po typie obejmuje
    wszystko, co zwróciło API, ale średnia userów, goście i aktywność biorą tylko
    prawdziwe tablice. ZMIERZONE na CXLABS: z 2017 aktywnych obiektów 484 to
    kontenery podelementów, a te mają subskrybentów odziedziczonych po rodzicu —
    liczone razem zaniżają średnią i opisują coś, czego nikt nie nazywa tablicą.
    """
    po_rodzaju: Counter[str] = Counter()
    po_typie: Counter[str] = Counter()
    aktywnosc: Counter[str] = Counter()
    userow: list[int] = []
    gosci_razem = 0
    z_goscmi = 0
    tablic_wlasciwych = 0

    for tablica in tablice:
        po_typie[str(tablica.get("type") or "nieznany")] += 1
        if str(tablica.get("type") or "") != TYP_TABLICY:
            continue

        tablic_wlasciwych += 1
        po_rodzaju[str(tablica.get("board_kind") or "nieznany")] += 1
        aktywnosc[kubelek_aktywnosci(tablica.get("updated_at"), teraz=teraz)] += 1

        subskrybenci = {
            str(s["id"])
            for s in (tablica.get("subscribers") or [])
            if isinstance(s, dict) and s.get("id")
        }
        userow.append(len(subskrybenci))
        ilu_gosci = len(subskrybenci & goscie)
        gosci_razem += ilu_gosci
        if ilu_gosci:
            z_goscmi += 1

    return Agregaty(
        tablic=tablic_wlasciwych,
        po_rodzaju=dict(sorted(po_rodzaju.items())),
        po_typie=dict(sorted(po_typie.items())),
        aktywnosc=dict(sorted(aktywnosc.items())),
        userow_srednio=round(statistics.fmean(userow), 1) if userow else None,
        userow_mediana=float(statistics.median(userow)) if userow else None,
        gosci_na_tablicach=gosci_razem,
        tablic_z_goscmi=z_goscmi,
    )


def zbierz_uruchomienia(zdarzenia: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`board_id` → `{uruchomien, bledow, ostatnie}`. Zdarzenia bez tablicy odpadają."""
    wynik: dict[str, dict[str, Any]] = {}
    # Sparsowane znaczniki trzymamy OBOK wyniku, nie w nim — wynik ma mieć
    # dokładnie te pola, które deklaruje docstring.
    najnowsze: dict[str, datetime] = {}
    for zdarzenie in zdarzenia:
        board_id = zdarzenie.get("hostInstanceId")
        if not board_id:
            continue
        wpis = wynik.setdefault(str(board_id), {"uruchomien": 0, "bledow": 0, "ostatnie": None})
        wpis["uruchomien"] += 1
        if str(zdarzenie.get("eventState") or "").lower() not in ("success", ""):
            wpis["bledow"] += 1

        # Największy znacznik wybieramy po SPARSOWANIU, a przechowujemy napis
        # taki, jaki przyszedł z API — patrz `na_datetime`. Znacznik, którego nie
        # da się sparsować, nie wygrywa: wolimy pokazać starszą datę, o której coś
        # wiemy, niż nowszą, o której nie wiemy nic.
        kiedy = zdarzenie.get("triggerStartedAt")
        kiedy_dt = na_datetime(str(kiedy) if kiedy else None)
        if kiedy_dt is not None:
            poprzednie = najnowsze.get(str(board_id))
            if poprzednie is None or kiedy_dt > poprzednie:
                najnowsze[str(board_id)] = kiedy_dt
                wpis["ostatnie"] = str(kiedy)

    return wynik


async def pobierz_tablice(klient: MondayClient) -> list[dict[str, Any]]:
    zebrane: list[dict[str, Any]] = []
    strona = 1
    while True:
        odpowiedz = await klient.query(
            _PYTANIE_TABLIC_Z_LUDZMI,
            {"limit": LIMIT_TABLIC, "p": strona},
            etykieta="przeglad_tablice",
        )
        surowe = odpowiedz.get("boards") or []
        zebrane.extend(t for t in surowe if isinstance(t, dict) and t.get("id"))
        if len(surowe) < LIMIT_TABLIC:
            return zebrane
        strona += 1


async def _jeden_przedzial(
    klient: MondayClient, od: datetime, do: datetime
) -> list[dict[str, Any]]:
    odpowiedz = await klient.query(
        _PYTANIE_ZDARZEN,
        {
            "f": {
                "hostType": "board",
                "dateRange": {
                    "startDate": od.date().isoformat(),
                    "endDate": do.date().isoformat(),
                },
            }
        },
        etykieta="przeglad_zdarzenia",
    )
    partia = (odpowiedz.get("trigger_events") or {}).get("triggerEvents") or []
    return [z for z in partia if isinstance(z, dict)]


async def pobierz_zdarzenia(
    klient: MondayClient, *, okno_dni: int = OKNO_DNI
) -> tuple[list[dict[str, Any]], list[str]]:
    """Zdarzenia automatyzacji przypisane do tablic. Zwraca `(zdarzenia, zastrzeżenia)`.

    ## Dlaczego krojenie okna, a nie stronicowanie

    ZMIERZONE 2026-09-22: `trigger_events` przyjmuje `nextPageOffset`, ale każda
    wartość większa od zera kończy się po stronie monday **błędem serwera**
    (`Internal server error`). Typ `TriggerEventsPage` ma jedno pole,
    `triggerEvents` — nie ma ani kursora, ani licznika wszystkich. Stronicowania
    w praktyce nie ma, a pierwsza strona urywa się na 200 zdarzeniach.

    Więc zamiast stronicować, **zawężamy okno**: tydzień po tygodniu wstecz.
    Tydzień mieści się pod limitem, a gdy nie mieści — dzielimy go na dni.
    Dzień, który dalej daje pełne 200, trafia do zastrzeżeń z datą, zamiast
    urwać się po cichu.
    """
    do = datetime.now(tz=UTC)
    poczatek = do - timedelta(days=okno_dni)
    zastrzezenia: list[str] = []
    po_uuid: dict[str, dict[str, Any]] = {}

    krok = timedelta(days=7)
    kursor = do
    while kursor > poczatek:
        od = max(kursor - krok, poczatek)
        partia = await _jeden_przedzial(klient, od, kursor)

        if len(partia) >= LIMIT_ZDARZEN:
            # Tydzień się nie zmieścił — schodzimy na dni, żeby nie zgubić reszty.
            dzien = od
            while dzien < kursor:
                nastepny = min(dzien + timedelta(days=1), kursor)
                dzienna = await _jeden_przedzial(klient, dzien, nastepny)
                if len(dzienna) >= LIMIT_ZDARZEN:
                    zastrzezenia.append(
                        f"{dzien.date().isoformat()}: zdarzeń było co najmniej {LIMIT_ZDARZEN}, "
                        "czyli tyle, ile wynosi limit odpowiedzi — dzień jest URWANY"
                    )
                for zdarzenie in dzienna:
                    po_uuid[str(zdarzenie.get("triggerUuid") or id(zdarzenie))] = zdarzenie
                dzien = nastepny
        else:
            for zdarzenie in partia:
                po_uuid[str(zdarzenie.get("triggerUuid") or id(zdarzenie))] = zdarzenie

        kursor = od

    return list(po_uuid.values()), zastrzezenia


async def zbuduj_przeglad(
    klient: MondayClient,
    rejestr: RejestrPodgladu,
    workspace_y: tuple[WorkspaceDoWyboru, ...] = (),
    *,
    okno_dni: int = OKNO_DNI,
    tablice: list[dict[str, Any]] | None = None,
) -> PrzegladTablic:
    """Agregaty po tablicach plus lista tablic z żywymi automatyzacjami.

    `workspace_y` przychodzi z inwentarza, żeby nie pytać drugi raz o to samo —
    służy wyłącznie do dopisania produktu przy tablicy z automatyzacjami.
    """
    przed = rejestr.wywolan

    uzytkownicy = await pobierz_uzytkownikow(klient)
    goscie = {
        str(u["id"])
        for u in uzytkownicy
        if isinstance(u, dict) and u.get("id") and u.get("kind") == RODZAJ_GOSC
    }

    tablice = tablice if tablice is not None else await pobierz_tablice(klient)
    zdarzenia, urwane = await pobierz_zdarzenia(klient, okno_dni=okno_dni)

    agregaty = policz_agregaty(tablice, goscie)

    # Zero gości na tablicach przy gościach NA KONCIE jest podejrzane, a nie
    # zerowe. ZMIERZONE 2026-09-22: 12 aktywnych gości na CXLABS i ani jedno
    # wystąpienie w `subscribers` 1000 tablic, a `Board` nie ma pola o gościach
    # ani filtra przy `subscribers` (O45). Nie umiem rozstrzygnąć, czy to pole
    # gości nie zwraca, czy siedzą na tablicach spoza sprawdzonych — więc nie
    # pokazuję zera jako faktu.
    podejrzane_zero = agregaty.gosci_na_tablicach == 0 and bool(goscie)
    uruchomienia = zbierz_uruchomienia(zdarzenia)

    produkt_workspace = {w.workspace_id: w.produkt_kind for w in workspace_y}
    po_id = {str(t["id"]): t for t in tablice}

    zywe: list[TablicaZywa] = []
    for board_id, wpis in uruchomienia.items():
        tablica = po_id.get(board_id) or {}
        workspace = tablica.get("workspace") or {}
        zywe.append(
            TablicaZywa(
                board_id=board_id,
                nazwa=tablica.get("name"),
                workspace_nazwa=workspace.get("name"),
                produkt=produkt_workspace.get(str(workspace.get("id"))),
                uruchomien=int(wpis["uruchomien"]),
                bledow=int(wpis["bledow"]),
                ostatnie_uruchomienie=wpis["ostatnie"],
            )
        )
    zywe.sort(key=lambda t: t.uruchomien, reverse=True)

    zastrzezenia = [
        f"automatyzacje policzone jako URUCHOMIENIA w oknie {okno_dni} dni — "
        "te, które istnieją, ale nigdy nie odpaliły, są w tym API niewidoczne",
        "pominięte automatyzacje o zasięgu konta i aplikacji (`hostType` inny niż `board`)",
        *urwane,
    ]
    if podejrzane_zero:
        zastrzezenia.append(
            f"gości na tablicach wyszło ZERO, choć konto ma {len(goscie)} gości — "
            "`Board.subscribers` prawdopodobnie ich nie zwraca (O45). "
            "Tej liczby nie traktuj jako zmierzonej"
        )
    nieznane_tablice = [t.board_id for t in zywe if t.nazwa is None]
    if nieznane_tablice:
        zastrzezenia.append(
            f"{len(nieznane_tablice)} tablic z uruchomieniami nie ma na liście aktywnych "
            "— prawdopodobnie zarchiwizowane albo w koszu"
        )

    przeglad = PrzegladTablic(
        tablic=agregaty.tablic,
        po_rodzaju=agregaty.po_rodzaju,
        po_typie=agregaty.po_typie,
        userow_srednio=agregaty.userow_srednio,
        userow_mediana=agregaty.userow_mediana,
        gosci_na_tablicach=agregaty.gosci_na_tablicach,
        tablic_z_goscmi=agregaty.tablic_z_goscmi,
        aktywnosc=agregaty.aktywnosc,
        zywe_automatyzacje=tuple(zywe),
        uruchomien_razem=sum(t.uruchomien for t in zywe),
        okno_dni=okno_dni,
        wywolan=rejestr.wywolan - przed,
        zastrzezenia=tuple(zastrzezenia),
    )
    logger.info(
        "przegląd: %d tablic, %d z żywymi automatyzacjami (%d wywołań)",
        przeglad.tablic,
        len(przeglad.zywe_automatyzacje),
        przeglad.wywolan,
    )
    return przeglad
