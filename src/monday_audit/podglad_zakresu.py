"""Szybki podgląd konta PRZED zbieraniem danych — workspace'y i tablice w sekundach.

Klient wybiera zakres audytu zaraz po podaniu klucza, a nie po trzech minutach
zbierania. ZGŁOSZONE (Kuba, 2026-08-25): „nie będzie mu się chciało czekać na
wybór workspace'u czy tablicy, to nie ma sensu żadnego".

## Zmierzone na koncie 27690228, nie oszacowane

| co | czas | wywołania | complexity |
|---|---|---|---|
| `workspaces` | **0,84 s** | 1 | 1 000 |
| `boards` jednego workspace'u z kolumnami | **5,66 s** | 2 | 623 724 |
| **razem** | **~6,5 s** | **3** | — |

Pełne zbieranie to 167 s i 130 wywołań, z czego logi 47 s i próbkowanie kolumn
64 s — do wyboru zakresu niepotrzebne. **Koszt modelu: zero**, bo model w tym
w ogóle nie uczestniczy.

## Dlaczego osobny moduł, a nie funkcja w `tablice.py`

To dwie różne rzeczy o tym samym obiekcie. `tablice.py` buduje **snapshot do
audytu**: pełny, pseudonimizowany, niemutowalny (D7), z właścicielami
i subskrybentami. Tu powstaje **podgląd dla człowieka**: ma być szybki i nie
trafia do bazy ani do kontekstu modelu.

Sklejenie ich kusiło, ale ten podgląd musiałby wtedy przechodzić przez
pseudonimizację i walidację PII, których nie potrzebuje — i odwrotnie, snapshot
przejąłby „lekkość", która jest tu zaletą, a tam byłaby utratą danych.

## Czego ten podgląd NIE wie i dlaczego to trzeba powiedzieć

Bez logów (47 s) nie wiemy o **ciszy w oknie 90 dni** ani o tym, **które tablice
są poza próbką**. To dwie z czterech flag ekranu wyboru — w tym ta najciekawsza
(porzucony proces). Wracają w raporcie, po zbieraniu.

Świadomy kompromis: 6 sekund z dwiema flagami bije 3 minuty z czterema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from monday_audit.klient import MondayClient
from monday_audit.wybor_zakresu import (
    FLAGA_NIEUZYWANA,
    FLAGA_RAPORTOWA,
    PROG_RAPORTOWEJ,
    SEKUND_NIERUSZONEJ,
    TYP_TABLICY,
    TYPY_AUTOMATYCZNE,
)

logger = logging.getLogger(__name__)

# 100 to maksimum, które monday przyjmuje na stronę. ZMIERZONE: 124 obiekty
# workspace'u 5610281 zmieściły się w dwóch stronach w 5,66 s. Przy 25
# (jak `LIMIT_STRONY` collectora) byłoby sześć stron i ~15 s — a tu liczy się
# każda sekunda, bo człowiek czeka przed ekranem.
LIMIT_PODGLADU = 100

# Twardy sufit stron. Konto ma 500+ tablic, więc bez tego podgląd JEDNEGO
# workspace'u mógłby zejść w kilkadziesiąt sekund na koncie, którego nie
# zmierzyliśmy. Lepiej pokazać część z jawną adnotacją niż kazać czekać.
MAKS_STRON_PODGLADU = 4

# Ile workspace'ów pobieramy. Konto 27690228 ma ich ponad 100 — ZMIERZONE,
# wcześniej zakładałem „kilka". Lista na froncie ma wyszukiwanie, więc setka
# jest do przejrzenia; bez niego byłaby bezużyteczna.
LIMIT_WORKSPACE = 100

_PYTANIE_WORKSPACE = """
query ($limit: Int!, $p: Int!) {
  workspaces (limit: $limit, page: $p) {
    id
    name
  }
}
"""

# Bez `owners`, `subscribers` i `title` kolumn — one dawały większość
# complexity w zapytaniu collectora. `columns { id type }` kosztuje +1 244
# complexity i +0,46 s, a daje flagę `raportowa`, więc zostaje.
_PYTANIE_TABLIC = """
query ($ws: [ID!], $limit: Int!, $p: Int!) {
  boards (limit: $limit, page: $p, state: active, workspace_ids: $ws, order_by: created_at) {
    id
    name
    type
    state
    items_count
    created_at
    updated_at
    workspace { id name }
    columns { id type }
  }
}
"""


class PodgladError(RuntimeError):
    """Nie da się pokazać podglądu konta tym tokenem."""


class RejestrPodgladu:
    """Rejestr wywołań, który nie zapisuje do bazy — tylko loguje.

    `RejestrWywolan` wymaga otwartego wiersza w `runy`, bo `wywolania.run_id`
    to `NOT NULL REFERENCES`. Podgląd nie jest runem: to trzy zapytania, które
    nie tworzą snapshotu i nie wchodzą do audytu. Zakładanie dla nich wiersza
    w `runy` zaśmiecałoby historię audytów wpisami bez findingów — a to już raz
    zepsuło listę runów w panelu.

    Liczniki zostają w pamięci, żeby endpoint mógł powiedzieć, ile wywołań
    z limitu klienta zużył ten podgląd.
    """

    def __init__(self) -> None:
        self.wywolan = 0
        self.complexity = 0

    def zapisz(
        self,
        *,
        narzedzie: str,
        latency_ms: int | None = None,
        complexity: int | None = None,
        hipoteza_id: str | None = None,
    ) -> None:
        self.wywolan += 1
        self.complexity += complexity or 0
        logger.debug("podgląd: %s %sms complexity=%s", narzedzie, latency_ms, complexity)


@dataclass(frozen=True, slots=True)
class WorkspaceDoWyboru:
    """Jeden workspace na liście wyboru. Bez liczby tablic — ta kosztuje osobne
    zapytanie na każdy workspace, czyli przy setce byłoby to 100 wywołań."""

    workspace_id: str
    nazwa: str


@dataclass(frozen=True, slots=True)
class TablicaDoWyboru:
    """Tablica na ekranie wyboru, z tego, co wiemy w sześć sekund.

    Świadomie NIE ma `wpisow` ani flag o ciszy: te wymagają logów. Pole, które
    zawsze byłoby `None`, sugerowałoby frontowi, że dane kiedyś przyjdą.
    """

    board_id: str
    nazwa: str
    workspace_id: str
    workspace_nazwa: str
    kolumn: int
    kolumn_automatycznych: int
    items_count: int
    flagi: tuple[str, ...]

    @property
    def oflagowana(self) -> bool:
        return bool(self.flagi)


@dataclass(frozen=True, slots=True)
class PodgladKonta:
    """Wynik szybkiego podglądu: co da się wybrać i czego jeszcze nie wiemy."""

    workspace_y: tuple[WorkspaceDoWyboru, ...]
    tablice: tuple[TablicaDoWyboru, ...]
    pominietych_pomocniczych: int
    urwano_na_stronach: bool
    # Zgrubny szacunek kosztu z LICZBY TABLIC, nie z liczby hipotez — tych
    # przed zbieraniem nie znamy. Nazwany „zgrubny" także na ekranie.
    zgrubnie_od_usd: float
    zgrubnie_do_usd: float


# Zgrubny szacunek: ile hipotez na tablicę i ile kosztuje hipoteza.
#
# ZMIERZONE na snapshocie #7: 59 tablic dało 40 hipotez o tablicach, czyli
# ~0,68 na tablicę. Średnia hipoteza modelowa to 0,0741 USD (mediana z
# `zuzycie_hipotez`). Podłoga to hipotezy o koncie — ~0,87 USD niezależnie
# od zakresu.
#
# Rozrzut jest szeroki (dwa runy na tym samym snapshocie: 4,79 i 5,52 USD),
# dlatego mnożniki, nie jedna liczba. To ma być uczciwe „w co wchodzę", nie
# obietnica.
HIPOTEZ_NA_TABLICE = 0.68
KOSZT_HIPOTEZY_USD = 0.0741
PODLOGA_KONTA_USD = 0.87
#
# Mnożniki dobrane tak, żeby przedział OBEJMOWAŁ realny pomiar: 59 tablic dało
# po zbieraniu widełki 2,28–5,00 USD. Przy 0,6/1,4 dolna granica wychodziła
# 2,31, czyli WYŻEJ niż realna — klient zobaczyłby „od 2,31" i mógłby dostać
# rachunek poniżej obiecanego minimum. Wąskie widełki są tu gorsze niż szerokie:
# to szacunek przed zbieraniem, nie wycena.
MNOZNIK_DOLNY = 0.55
MNOZNIK_GORNY = 1.45


def _sekundy_miedzy(od: str | None, do: str | None) -> float | None:
    """Różnica dwóch znaczników ISO albo `None`. Kopia z `wybor_zakresu`?
    Nie — tam liczy na polach snapshotu, tu na surowej odpowiedzi API."""
    if not od or not do:
        return None
    try:
        a = datetime.fromisoformat(str(od).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(do).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("nieparsowalne znaczniki tablicy w podglądzie: %r, %r", od, do)
        return None
    return (b - a).total_seconds()


def _flagi(tablica: dict[str, Any]) -> tuple[tuple[str, ...], int]:
    """Dwie flagi, które da się wyliczyć bez logów, plus liczba kolumn automatycznych.

    `cisza_90_dni` i `nieprobkowana` NIE pojawiają się tutaj i to jest celowe:
    wymagają dziennika (47 s). Ich brak na tym ekranie nie znaczy „tablica
    aktywna" — znaczy „jeszcze nie wiemy", i front musi to napisać.
    """
    flagi: list[str] = []
    kolumny = tablica.get("columns") or []
    automatycznych = sum(1 for k in kolumny if k.get("type") in TYPY_AUTOMATYCZNE)

    odstep = _sekundy_miedzy(tablica.get("created_at"), tablica.get("updated_at"))
    if odstep is not None and odstep < SEKUND_NIERUSZONEJ:
        flagi.append(FLAGA_NIEUZYWANA)
    if kolumny and automatycznych / len(kolumny) >= PROG_RAPORTOWEJ:
        flagi.append(FLAGA_RAPORTOWA)
    return tuple(flagi), automatycznych


async def pobierz_workspace(klient: MondayClient) -> tuple[WorkspaceDoWyboru, ...]:
    """Lista workspace'ów konta. ZMIERZONE: 0,84 s dla 100 pozycji.

    Do 2026-08-25 twierdziłem, że takiego zapytania nie ma — szukałem go
    w naszym kodzie, nie w API monday. To założenie blokowało cały ten ekran.
    """
    odpowiedz = await klient.query(
        _PYTANIE_WORKSPACE,
        {"limit": LIMIT_WORKSPACE, "p": 1},
        etykieta="workspaces",
    )
    surowe = odpowiedz.get("workspaces") or []
    return tuple(
        WorkspaceDoWyboru(workspace_id=str(w["id"]), nazwa=str(w.get("name") or w["id"]))
        for w in surowe
        if isinstance(w, dict) and w.get("id")
    )


async def pobierz_tablice(
    klient: MondayClient, workspace_id: str
) -> tuple[tuple[TablicaDoWyboru, ...], int, bool]:
    """Tablice jednego workspace'u. Zwraca `(tablice, pominiętych, urwano)`.

    ZMIERZONE: 124 obiekty w 5,66 s na dwóch stronach. Z tych 124 tylko 59 jest
    typu `board` — reszta to podelementy, obiekty własne i dokumenty, których
    nikt nie wybiera. Ten sam filtr co w `wybor_zakresu` i w `_PARY_TABLIC`.
    """
    zebrane: list[dict[str, Any]] = []
    strona = 1
    urwano = False
    while True:
        odpowiedz = await klient.query(
            _PYTANIE_TABLIC,
            {"ws": [workspace_id], "limit": LIMIT_PODGLADU, "p": strona},
            etykieta="boards_podglad",
        )
        partia = odpowiedz.get("boards") or []
        zebrane.extend(x for x in partia if isinstance(x, dict))
        if len(partia) < LIMIT_PODGLADU:
            break
        strona += 1
        if strona > MAKS_STRON_PODGLADU:
            # Sufit, nie cichy koniec: front napisze, że lista jest ucięta.
            # Milcząca obcinka czytałaby się jak „to wszystkie tablice".
            urwano = True
            logger.warning(
                "podgląd workspace %s urwany na %d stronach", workspace_id, MAKS_STRON_PODGLADU
            )
            break

    pozycje: list[TablicaDoWyboru] = []
    pominietych = 0
    for tablica in zebrane:
        if tablica.get("type") != TYP_TABLICY:
            pominietych += 1
            continue
        flagi, automatycznych = _flagi(tablica)
        ws = tablica.get("workspace") or {}
        pozycje.append(
            TablicaDoWyboru(
                board_id=str(tablica["id"]),
                nazwa=str(tablica.get("name") or tablica["id"]),
                workspace_id=str(ws.get("id") or workspace_id),
                workspace_nazwa=str(ws.get("name") or ""),
                kolumn=len(tablica.get("columns") or []),
                kolumn_automatycznych=automatycznych,
                items_count=int(tablica.get("items_count") or 0),
                flagi=flagi,
            )
        )
    return tuple(pozycje), pominietych, urwano


def oszacuj_zgrubnie(tablic: int) -> tuple[float, float]:
    """Widełki kosztu z LICZBY TABLIC. Zgrubne i tak nazwane na ekranie.

    Przed zbieraniem nie znamy liczby hipotez — zna ją dopiero detektor na
    snapshocie. Ale klient klikający „Zbierz dane" ma prawo wiedzieć, czy
    wchodzi w rachunek za dolara czy za dziesięć.

    Dokładne widełki (z `wybor_zakresu.oszacuj_koszt`) pokazujemy w drugiej
    bramce, już po zebraniu.
    """
    srodek = PODLOGA_KONTA_USD + tablic * HIPOTEZ_NA_TABLICE * KOSZT_HIPOTEZY_USD
    return round(srodek * MNOZNIK_DOLNY, 2), round(srodek * MNOZNIK_GORNY, 2)


async def zbuduj_podglad(klient: MondayClient, *, workspace_id: str | None = None) -> PodgladKonta:
    """Cały podgląd. Bez `workspace_id` zwraca tylko listę workspace'ów.

    Dwa kroki, nie jeden: lista workspace'ów kosztuje 0,84 s i pokazuje się
    natychmiast, a tablice (5,66 s) dociągamy DOPIERO po wybraniu workspace'u.
    Pobieranie tablic wszystkich workspace'ów naraz to na tym koncie 17 s
    i 2,5 mln complexity — ZMIERZONE.

    Przy podanym `workspace_id` listy workspace'ów NIE pobieramy ponownie: front
    ma ją z pierwszego kroku, a powtórka to zmarnowana sekunda i zbędne
    wywołanie z limitu klienta. Zwracana krotka jest wtedy pusta i to jest
    poprawne — nie „brak workspace'ów", a „nie o to pytano".
    """
    if workspace_id is None:
        return PodgladKonta(
            workspace_y=await pobierz_workspace(klient),
            tablice=(),
            pominietych_pomocniczych=0,
            urwano_na_stronach=False,
            zgrubnie_od_usd=0.0,
            zgrubnie_do_usd=0.0,
        )

    tablice, pominietych, urwano = await pobierz_tablice(klient, workspace_id)
    if not tablice and not pominietych:
        # Pusta odpowiedź ma dwie przyczyny — workspace bez tablic albo
        # niewidoczny tym tokenem — i klient musi wiedzieć którą, bo w drugim
        # przypadku problemem jest klucz, nie konto.
        raise PodgladError(
            f"workspace {workspace_id} nie ma widocznych tablic — sprawdź, "
            "czy token ma do niego dostęp"
        )
    workspace_y = ()
    od, do = oszacuj_zgrubnie(len(tablice))
    return PodgladKonta(
        workspace_y=workspace_y,
        tablice=tablice,
        pominietych_pomocniczych=pominietych,
        urwano_na_stronach=urwano,
        zgrubnie_od_usd=od,
        zgrubnie_do_usd=do,
    )


def podglad_do_json(podglad: PodgladKonta) -> dict[str, Any]:
    """Payload dla frontu. Nazwy pól zgodne z generatorem typów."""
    return {
        "workspace_y": [
            {"workspace_id": w.workspace_id, "nazwa": w.nazwa} for w in podglad.workspace_y
        ],
        "tablice": [
            {
                "board_id": t.board_id,
                "nazwa": t.nazwa,
                "workspace_id": t.workspace_id,
                "workspace_nazwa": t.workspace_nazwa,
                "kolumn": t.kolumn,
                "kolumn_automatycznych": t.kolumn_automatycznych,
                "items_count": t.items_count,
                "flagi": list(t.flagi),
                "oflagowana": t.oflagowana,
            }
            for t in podglad.tablice
        ],
        "pominietych_pomocniczych": podglad.pominietych_pomocniczych,
        "urwano_na_stronach": podglad.urwano_na_stronach,
        "zgrubnie_od_usd": podglad.zgrubnie_od_usd,
        "zgrubnie_do_usd": podglad.zgrubnie_do_usd,
    }
