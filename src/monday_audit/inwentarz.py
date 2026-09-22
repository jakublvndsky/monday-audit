"""Inwentarz konta — sześć kafelków z punktu 2 wytycznych (plan, faza 2a).

To jest **tani** przekrój: workspace'y, tablice, użytkownicy i licencja.
Bez itemów, bez modelu, bez logów aktywności. Odpowiada pierwszemu kliknięciu
z user story — „analizuj moje środowisko" — które ma trwać sekundy i nie zużywać
zauważalnie limitu klienta.

## Dlaczego własne zapytanie o użytkowników, skoro jest `osoby.py`

Bo tamto pobiera `name` i `email`, czyli **dane osobowe**, i dlatego wymaga soli
oraz całej ścieżki pseudonimizacji. Do policzenia, ilu jest gości i ilu agentów,
te pola nie są potrzebne — a najtańszym sposobem na niewyciekanie danych
osobowych jest **nie pobrać ich wcale**. Ta sama zasada, co `konto.py`
ze świadomym brakiem `me { name }`.

## Czego ten moduł NIE liczy

Nie liczy itemów (to faza 3) ani niczego, co wymaga logów aktywności (faza 2b).
„Liczba tablic" to obiekty **aktywne i typu `board`** — podelementy, dokumenty
i obiekty własne są policzone osobno w `tablic_po_typie`, ale do kafelka nie
wchodzą. Rozbicie „tablice wg rodzaju" należy do punktu 4 i do fazy 2b.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from monday_audit.klient import MondayClient
from monday_audit.konto import Konto
from monday_audit.osoby import (
    RODZAJ_ADMIN,
    RODZAJ_CZLONEK,
    RODZAJ_GOSC,
    RODZAJ_PODGLAD,
    RODZAJE_AGENTOW,
    ZNANE_RODZAJE,
)
from monday_audit.podglad_zakresu import RejestrPodgladu, WorkspaceDoWyboru, pobierz_workspace

logger = logging.getLogger(__name__)

# `state: all` i pole `state` przy każdej tablicy: jedno przejście daje
# i aktywne, i zarchiwizowane, i kosz. Liczenie samych aktywnych wyglądałoby
# tak samo jak konto, w którym ktoś zarchiwizował połowę pracy.
# W repo są CZTERY zapytania o `boards` i każde po co innego — mapa, żeby nikt
# nie dokładał piątego w ciemno:
#
#   `tablice.py:_SZKIELET`            — collector, pełne pola, do snapshotu
#   `podglad_zakresu.py:_PYTANIE_TABLIC` — tablice jednego workspace'u, do wyboru zakresu
#   tutaj                             — sam licznik: trzy pola skalarne, strona 100
#   `przeglad_tablic.py:_PYTANIE_TABLIC_Z_LUDZMI` — agregaty, ciągnie subskrybentów
#
# Nazwy są różne CELOWO: do 2026-09-22 dwie z nich nazywały się identycznie
# w dwóch modułach i przy czytaniu diffu nie dało się powiedzieć, o które chodzi.
_PYTANIE_LICZNIK_TABLIC = """
query ($limit: Int!, $p: Int!) {
  boards (limit: $limit, page: $p, state: all) {
    id
    state
    type
  }
}
"""

# Obiekty, które `boards` zwraca, a których nikt nie nazwałby tablicą.
# ZMIERZONE na CXLABS 2026-09-22: z 2017 aktywnych obiektów tylko 1315 to
# `board`, a 484 to kontenery podelementów, 112 dokumenty i 106 obiekty własne.
# Kafelek „liczba tablic" pokazujący 2017 byłby prawdziwy i mylący — tak samo
# jak liczący kosz.
TYP_TABLICY = "board"

# ŚWIADOMIE BEZ `name` i `email`. Do liczenia po rodzaju są zbędne, a ich brak
# znaczy, że dane osobowe nie wchodzą do tego procesu w ogóle — nie ma czego
# pseudonimizować ani redagować.
_PYTANIE_UZYTKOWNIKOW = """
query ($limit: Int!, $p: Int!) {
  users (limit: $limit, page: $p) {
    id
    kind
    status
    is_deleted
  }
}
"""

LIMIT_TABLIC = 100
LIMIT_UZYTKOWNIKOW = 200


@dataclass(frozen=True, slots=True)
class Inwentarz:
    """Sześć kafelków. Każda liczba ma powiedzieć, czego dotyczy — stąd rozbicie
    użytkowników na cztery rodzaje zamiast jednej sumy."""

    konto_nazwa: str
    # Licencja: `tier` bywa `null` w `plan`, a niezerowy w `account` — kolejność
    # źródeł rozstrzyga `konto.py`, my bierzemy gotowy wynik.
    licencja_tier: str | None
    licencja_period: str | None
    licencja_max_users: int | None

    workspacow: int
    workspace_y: tuple[WorkspaceDoWyboru, ...]
    # `crm` → 4, `service` → 2, `None` → 1 (workspace bez przypisania do produktu)
    po_produktach: dict[str, int]

    # Kafelek pokazuje AKTYWNE, nie sumę wszystkich stanów. ZMIERZONE na CXLABS
    # 2026-09-22: 3268 obiektów, z czego 1206 w koszu i 46 w archiwum. Liczba
    # 3268 jest prawdziwa i bezużyteczna — nikt nie ma 3268 tablic, tylko 2016
    # i pełny kosz. Suma zostaje obok, żeby nie znikła.
    tablic_aktywnych: int
    tablic_razem: int
    tablic_po_stanie: dict[str, int]
    tablic_po_typie: dict[str, int]

    # Rozbicie po `kind`. „Użytkownicy" to admini i członkowie, czyli ci, którzy
    # zajmują płatne miejsca (O7) — goście, podgląd i agenty liczą się osobno,
    # bo mieszanie ich w jedną liczbę zawyżało `ZOMBIE_ACCOUNT` czterokrotnie.
    uzytkownikow: int
    gosci: int
    agentow_ai: int
    podgladajacych: int
    po_rodzajach: dict[str, int]
    # Rodzaje spoza znanej piątki. API nie deklaruje zamkniętej listy (O17),
    # więc nowy rodzaj ma być widoczny, a nie wpadać po cichu do „innych".
    nieznane_rodzaje: tuple[str, ...] = ()

    wywolan: int = 0
    zastrzezenia: tuple[str, ...] = field(default_factory=tuple)

    def do_json(self) -> dict[str, Any]:
        return {
            "konto_nazwa": self.konto_nazwa,
            "licencja": {
                "tier": self.licencja_tier,
                "period": self.licencja_period,
                "max_users": self.licencja_max_users,
            },
            "workspacow": self.workspacow,
            "po_produktach": dict(self.po_produktach),
            "tablic_aktywnych": self.tablic_aktywnych,
            "tablic_razem": self.tablic_razem,
            "tablic_po_stanie": dict(self.tablic_po_stanie),
            "tablic_po_typie": dict(self.tablic_po_typie),
            "uzytkownikow": self.uzytkownikow,
            "gosci": self.gosci,
            "agentow_ai": self.agentow_ai,
            "podgladajacych": self.podgladajacych,
            "po_rodzajach": dict(self.po_rodzajach),
            "nieznane_rodzaje": list(self.nieznane_rodzaje),
            "wywolan": self.wywolan,
            "zastrzezenia": list(self.zastrzezenia),
        }


def policz_produkty(workspace_y: tuple[WorkspaceDoWyboru, ...]) -> dict[str, int]:
    """Workspace'y po rodzaju produktu. Brak przypisania jest osobną kategorią.

    `bez_produktu` NIE jest tym samym co „nie umieliśmy odczytać" — pole
    `account_product` przychodzi puste dla workspace'ów spoza któregokolwiek
    produktu i to jest normalny stan konta, nie błąd odczytu.
    """
    licznik: Counter[str] = Counter()
    for w in workspace_y:
        licznik[w.produkt_kind or "bez_produktu"] += 1
    return dict(sorted(licznik.items()))


def policz_rodzaje(surowi: list[dict[str, Any]]) -> tuple[dict[str, int], tuple[str, ...]]:
    """Liczy użytkowników po `kind`, pomijając usuniętych.

    Usunięci odpadają, bo kafelek ma mówić o koncie, jakim jest dzisiaj.
    Zwraca też rodzaje spoza znanej piątki — nienazwany rodzaj musi być widoczny.
    """
    licznik: Counter[str] = Counter()
    nieznane: set[str] = set()

    for osoba in surowi:
        if not isinstance(osoba, dict) or osoba.get("is_deleted"):
            continue
        rodzaj = osoba.get("kind")
        klucz = str(rodzaj) if rodzaj else "nieznany"
        licznik[klucz] += 1
        if klucz not in ZNANE_RODZAJE:
            nieznane.add(klucz)

    return dict(sorted(licznik.items())), tuple(sorted(nieznane))


async def _policz_tablice(klient: MondayClient) -> tuple[int, dict[str, int], dict[str, int]]:
    """Obiekty `boards` konta. Zwraca `(aktywnych tablic, po stanie, po typie)`.

    „Aktywnych tablic" znaczy `state: active` **oraz** `type: board` — reszta to
    kontenery podelementów, dokumenty i obiekty własne, których nikt nie nazywa
    tablicą.
    """
    po_stanie: Counter[str] = Counter()
    po_typie: Counter[str] = Counter()
    aktywnych_tablic = 0
    strona = 1
    while True:
        odpowiedz = await klient.query(
            _PYTANIE_LICZNIK_TABLIC,
            {"limit": LIMIT_TABLIC, "p": strona},
            etykieta="inwentarz_tablice",
        )
        surowe = odpowiedz.get("boards") or []
        for tablica in surowe:
            if not isinstance(tablica, dict) or not tablica.get("id"):
                continue
            stan = str(tablica.get("state") or "nieznany")
            typ = str(tablica.get("type") or "nieznany")
            po_stanie[stan] += 1
            if stan != "active":
                # Rozbicie po typie dotyczy AKTYWNYCH. Liczone po wszystkich
                # stanach dawało 777 podelementów obok 484 w przeglądzie —
                # dwie liczby o tym samym, w jednym bloku wyjścia.
                continue
            po_typie[typ] += 1
            if typ == TYP_TABLICY:
                aktywnych_tablic += 1
        if len(surowe) < LIMIT_TABLIC:
            break
        strona += 1
    return aktywnych_tablic, dict(sorted(po_stanie.items())), dict(sorted(po_typie.items()))


async def pobierz_uzytkownikow(klient: MondayClient) -> list[dict[str, Any]]:
    """Wszyscy użytkownicy konta, bez pól z danymi osobowymi. Paginuje do końca.

    Publiczna, bo `przeglad_tablic` potrzebuje tego samego: mapy `id → kind`,
    żeby policzyć gości na tablicy. Druga kopia tego zapytania byłaby drugim
    miejscem, w którym ktoś kiedyś dopisze `name` i wpuści dane osobowe.
    """
    zebrani: list[dict[str, Any]] = []
    strona = 1
    while True:
        odpowiedz = await klient.query(
            _PYTANIE_UZYTKOWNIKOW,
            {"limit": LIMIT_UZYTKOWNIKOW, "p": strona},
            etykieta="inwentarz_uzytkownicy",
        )
        surowi = odpowiedz.get("users") or []
        zebrani.extend(u for u in surowi if isinstance(u, dict))
        if len(surowi) < LIMIT_UZYTKOWNIKOW:
            return zebrani
        strona += 1


async def zbuduj_inwentarz(
    klient: MondayClient, konto: Konto, rejestr: RejestrPodgladu
) -> Inwentarz:
    """Sześć kafelków dla całego konta.

    `konto` przychodzi z zewnątrz, bo `rozpoznaj_konto` robi przy okazji rzecz,
    której nie wolno tu powielać: sprawdza, czy token ma admina, i przerywa, gdy
    pyta o całe konto bez niego. Inwentarz liczony tokenem bez admina byłby cicho
    niepełny — a to najgorszy rodzaj liczby w raporcie.

    `rejestr` jest ten sam, który dostał klient — stąd wiemy, ile wywołań kosztował
    sam inwentarz, bez zgadywania po liczbie stron.
    """
    przed = rejestr.wywolan

    workspace_y = await pobierz_workspace(klient)
    aktywnych_tablic, po_stanie, po_typie = await _policz_tablice(klient)
    surowi = await pobierz_uzytkownikow(klient)
    po_rodzajach, nieznane = policz_rodzaje(surowi)

    zastrzezenia = list(konto.zastrzezenia)
    if nieznane:
        zastrzezenia.append(
            f"nieznane rodzaje kont: {', '.join(nieznane)} — nie wliczono ich do żadnego kafelka"
        )

    inwentarz = Inwentarz(
        konto_nazwa=konto.nazwa,
        licencja_tier=konto.tier,
        licencja_period=konto.period,
        licencja_max_users=konto.max_users,
        workspacow=len(workspace_y),
        workspace_y=workspace_y,
        po_produktach=policz_produkty(workspace_y),
        tablic_aktywnych=aktywnych_tablic,
        tablic_razem=sum(po_stanie.values()),
        tablic_po_stanie=po_stanie,
        tablic_po_typie=po_typie,
        uzytkownikow=po_rodzajach.get(RODZAJ_ADMIN, 0) + po_rodzajach.get(RODZAJ_CZLONEK, 0),
        gosci=po_rodzajach.get(RODZAJ_GOSC, 0),
        # Wszystkie rodzaje agentowe razem, nie sam `personal_agent_member`:
        # na CXLABS agentów zewnętrznych są cztery i pominięcie ich zaniżałoby
        # kafelek (decyzja Kuby 2026-09-22).
        agentow_ai=sum(po_rodzajach.get(rodzaj, 0) for rodzaj in RODZAJE_AGENTOW),
        podgladajacych=po_rodzajach.get(RODZAJ_PODGLAD, 0),
        po_rodzajach=po_rodzajach,
        nieznane_rodzaje=nieznane,
        wywolan=rejestr.wywolan - przed,
        zastrzezenia=tuple(zastrzezenia),
    )
    logger.info(
        "inwentarz: %d workspace'ów, %d tablic, %d użytkowników (%d wywołań)",
        inwentarz.workspacow,
        inwentarz.tablic_aktywnych,
        inwentarz.uzytkownikow,
        inwentarz.wywolan,
    )
    return inwentarz
