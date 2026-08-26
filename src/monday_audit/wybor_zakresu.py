"""Wybór zakresu audytu przed odpaleniem agenta — flagi, filtr, widełki kosztu.

Klient płaci za audyt własnym kluczem Anthropic. Skoro rachunek jest jego,
musi widzieć **za co** płaci i móc to zawęzić — do wybranych workspace'ów,
a w środku do konkretnych tablic.

## Oszczędność jest w modelu, nie w monday

ZMIERZONE na snapshocie #7: pełny run to 53 hipotezy i ~4,10 USD. Wybór
jednej tablicy zostawia 3 hipotezy o tablicach plus 13 o koncie — 1,12 USD,
czyli **−73%**. Ale collector zbiera całe konto tak samo, bo snapshot musi
zostać kompletny: panel pokazuje całe konto, a `DUPLICATE_STRUCTURE` nie ma
z czym porównywać tablicy, jeśli nie zebraliśmy pozostałych.

Podłoga to **0,87 USD** — 13 hipotez o koncie (ludzie, goście, plan,
automatyzacje). Żaden wybór tablic ich nie dotyczy, bo nie są związane
z żadną tablicą. Ekran musi to mówić wprost, inaczej klient odznaczy
wszystko i zdziwi się rachunkiem.

## Flagi są ETYKIETAMI, nie rekomendacjami

Decyzja świadoma: nie piszemy „proponujemy pominąć". Etykieta mówi, co
widzimy w danych; ocena należy do klienta, bo to on wie, która tablica jest
dla niego ważna. Rekomendacja przenosiłaby na nas odpowiedzialność za to,
czego klient nie zobaczy w raporcie.

Dwie flagi wyglądają podobnie i znaczą przeciwne rzeczy:

- `nieuzywana_od_startu` — szablon, którego nikt nie ruszył. Hałas.
- `cisza_90_dni` — ktoś zaczął proces i **porzucił**. To jest znalezisko,
  i to jedno z ciekawszych na koncie.

Dlatego ekran nie ma jednej etykiety „śmieć" i nie odznacza flagowanych
domyślnie — jest przycisk, który to robi na żądanie.

## „Mnóstwo pustych kolumn" jest NIEWYLICZALNE i to nie jest brak

Snapshot zna kolumnę jako `{id, title, type}` — bez wypełnienia, bo D5
zabrania schodzenia na poziom itemów. ZMIERZONE: wszystkie 16 hipotez
`BOARD_OVERCOMPLEX` ma pustą listę `kolumny_martwe`; wypełnia ją agent,
próbkując itemy w jedynym jawnym wyjątku od D5.

Zamiast tego `raportowa` — udział kolumn automatycznych. To fakt z danych,
nie domysł o danych, których nie mamy.

## `niepróbkowana` to trzecia wartość, nie brak

Collector loguje najwyżej 100 tablic. Brak tablicy w `aktywnosc_tablic`
znaczy „nie wiemy", **nie** „nie było aktywności" — ta sama pułapka, o której
mówi komentarz nad `_AKTYWNOSC` w `detektory.py` (stąd wymóg `LEFT JOIN`).
Cisza pokazana jako brak flagi czytałaby się jak „tablica żywa".
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from monday_audit.detektory import Hipoteza
from monday_audit.rubryka import Rubryka

logger = logging.getLogger(__name__)


# Tylko `board` trafia na listę wyboru. ZMIERZONE na #7: ze 124 obiektów
# w `tablice.tablice` prawdziwymi tablicami jest 59 — reszta to 41
# `sub_items_board`, 22 `custom_object` i 2 `document`. Ekran ze 124
# wierszami byłby ekranem z 65 pozycjami, których klient nie zakłada
# ani nie wybiera. Ten sam filtr stosuje `_PARY_TABLIC` w `detektory.py`.
TYP_TABLICY = "board"

# Kolumny wyliczane przez monday, nie wypełniane przez człowieka. Wysoki
# udział znaczy „tablica raportowa" — czyta z innych, nie prowadzi procesu.
# Zastępnik nieosiągalnej flagi o pustych kolumnach (patrz docstring modułu).
TYPY_AUTOMATYCZNE = frozenset(
    {
        "formula",
        "mirror",
        "lookup",
        "dependency",
        "progress",
        "auto_number",
        "creation_log",
        "last_updated",
        "item_id",
    }
)

# Od tego udziału kolumn automatycznych tablica dostaje flagę `raportowa`.
PROG_RAPORTOWEJ = 0.5

# Poniżej tej różnicy `updated_at - created_at` tablica nie została ruszona
# po założeniu. Ta sama stała i to samo znaczenie co `SEKUND_NIERUSZONEJ`
# w `detektory.py`, gdzie filtruje `BOARD_GHOST` przed hipotezą.
SEKUND_NIERUSZONEJ = 86_400

FLAGA_NIEUZYWANA = "nieuzywana_od_startu"
FLAGA_CISZA = "cisza_90_dni"
FLAGA_RAPORTOWA = "raportowa"
FLAGA_NIEPROBKOWANA = "nieprobkowana"

# Rozdzielnik pary w `obiekt_id` klas porównujących tablice parami
# (`DUPLICATE_STRUCTURE`, `PROCESS_BYPASS`). ZMIERZONE na #7:
# `5093364928+5093573344`.
ROZDZIELNIK_PARY = "+"

# Powód wpisywany do `hipotezy_odrzucone` dla hipotez odsianych wyborem.
# Tabela i widok „czego nie widać" już istnieją — nie budujemy drugiego
# śladu na to samo.
POWOD_POZA_ZAKRESEM = "poza wybranym zakresem"

# Gdy klasa nie ma ani jednego wpisu w `zuzycie_hipotez`, nie zgadujemy zera:
# cicha zerówka zaniżyłaby widełki, a klient zobaczyłby rachunek wyższy niż
# zgoda. Wartość z realnej średniej na hipotezę z etapu 4 (0,0599 USD).
KOSZT_ZAPASOWY_USD = 0.06


class WyborError(RuntimeError):
    """Żądany wybór zakresu nie da się pogodzić ze snapshotem."""


@dataclass(frozen=True, slots=True)
class PozycjaTablicy:
    """Jedna tablica na ekranie wyboru. Liczby, flagi, zero ocen."""

    board_id: str
    nazwa: str
    workspace_id: str
    workspace_nazwa: str
    kolumn: int
    kolumn_automatycznych: int
    items_count: int
    wpisow: int | None
    flagi: tuple[str, ...]
    hipotez: int

    @property
    def oflagowana(self) -> bool:
        return bool(self.flagi)


@dataclass(frozen=True, slots=True)
class PozycjaWorkspace:
    """Workspace jako grupa na ekranie. Powstaje z pól tablic.

    Konto nie ma zapytania listującego workspace'y — znamy je wyłącznie
    z pola `workspace { id name }` przy tablicach. Dlatego lista workspace'ów
    może istnieć tylko PO zebraniu tablic, i to wymusza kolejność ekranów.
    """

    workspace_id: str
    nazwa: str
    tablic: int
    hipotez: int


@dataclass(frozen=True, slots=True)
class Widelki:
    """Szacunek kosztu runu: dolna granica, punkt środkowy, górna.

    Punkt środkowy jest na wyraźne życzenie — same widełki bez niego zmuszają
    czytającego do zgadywania, czego się spodziewać.
    """

    dolna_usd: float
    srodek_usd: float
    gorna_usd: float
    podloga_usd: float
    hipotez: int
    hipotez_o_koncie: int
    klasy_bez_historii: tuple[str, ...]

    @property
    def oszacowane_z_zapasu(self) -> bool:
        return bool(self.klasy_bez_historii)


@dataclass(frozen=True, slots=True)
class WyborZakresu:
    """Cały ekran wyboru: workspace'y, tablice, widełki, zastrzeżenia."""

    workspace_y: tuple[PozycjaWorkspace, ...]
    tablice: tuple[PozycjaTablicy, ...]
    widelki: Widelki
    pominietych_pomocniczych: int
    tablic_bez_logow: int
    uwagi_o_zakresie: tuple[str, ...]


def _sekundy_miedzy(od: str | None, do: str | None) -> float | None:
    """Różnica dwóch znaczników ISO albo `None`, gdy któregoś brak.

    Zegar procesu nie bierze w tym udziału: liczymy różnicę dwóch pól
    snapshotu, więc wynik jest taki sam dziś i za rok — wymóg powtarzalności.
    """
    if not od or not do:
        return None
    try:
        a = datetime.fromisoformat(str(od).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(do).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("nieparsowalne znaczniki czasu tablicy: %r, %r", od, do)
        return None
    return (b - a).total_seconds()


def _flagi_tablicy(
    tablica: dict[str, Any],
    aktywnosc: dict[str, Any] | None,
) -> tuple[tuple[str, ...], int]:
    """Etykiety tablicy i liczba kolumn automatycznych.

    `aktywnosc is None` znaczy „poza próbką logów" i daje
    `nieprobkowana` — nie ciszę. Odwrotnie: obecna w próbce z zerem wpisów
    to cisza potwierdzona, czyli kandydat na znalezisko.
    """
    flagi: list[str] = []
    kolumny = tablica.get("kolumny") or []
    automatycznych = sum(1 for k in kolumny if k.get("type") in TYPY_AUTOMATYCZNE)

    odstep = _sekundy_miedzy(tablica.get("created_at"), tablica.get("updated_at"))
    nietknieta = odstep is not None and odstep < SEKUND_NIERUSZONEJ
    if nietknieta:
        flagi.append(FLAGA_NIEUZYWANA)

    if aktywnosc is None:
        flagi.append(FLAGA_NIEPROBKOWANA)
    elif not nietknieta:
        # Cisza tylko na tablicy, która KIEDYŚ żyła. ZMIERZONE na #7: bez
        # tego warunku ciszę dostaje 30 tablic zamiast 3, bo 27 nietkniętych
        # szablonów oczywiście też nie ma wpisów. Zlanie tych dwóch flag
        # zamazywałoby jedyną, która jest znaleziskiem: ktoś zaczął proces
        # i go porzucił.
        kubelki = aktywnosc.get("kubelki_dni") or {}
        if not sum(int(v or 0) for v in kubelki.values()):
            flagi.append(FLAGA_CISZA)

    if kolumny and automatycznych / len(kolumny) >= PROG_RAPORTOWEJ:
        flagi.append(FLAGA_RAPORTOWA)

    return tuple(flagi), automatycznych


def _o_tablicy(hipoteza: Hipoteza, znane_tablice: frozenset[str]) -> bool:
    """Czy `obiekt_id` hipotezy wskazuje tablicę z tego snapshotu.

    Kryterium jest tym, czym hipoteza JEST, a nie tym, o czym wspomina.
    Dwie ślepe uliczki, obie sprawdzone na #7:

    - `"board_id" in klasa.dowod` gubiło `DUPLICATE_STRUCTURE` (`board_ids[]`),
      czyli najdroższą klasę — 21 hipotez z 53 przechodziłoby filtr
      niezależnie od wyboru klienta.
    - wzorzec po nazwie pola wciągał `UZYTKOWNIK_WYGASZONY`, bo jego dowód
      ma `boardy[]` (osoba pracowała na tablicach). Ta hipoteza jest
      o CZŁOWIEKU, a jej `obiekt_id` to hash osoby — odsianie jej przy
      wyborze tablic obniżało podłogę z 13 hipotez do 4 i klient stracił
      znaleziska, których wybór tablic nie dotyczy.

    Rozstrzyga sam identyfikator, porównany ze snapshotem: hash osoby,
    `account_id` ani `automation_id` nigdy nie są tablicą.
    """
    czlony = _tablice_hipotezy(hipoteza)
    return bool(czlony) and all(czlon in znane_tablice for czlon in czlony)


def identyfikatory_tablic(payload: dict[str, Any]) -> frozenset[str]:
    """Wszystkie `board_id` ze snapshotu — także obiekty pomocnicze.

    Świadomie bez filtra po `typ`: hipoteza może dotyczyć `sub_items_board`,
    a wtedy nadal jest hipotezą o tablicy, tylko taką, której nie stawiamy
    na ekranie wyboru.
    """
    return frozenset(
        str(t.get("board_id"))
        for t in ((payload.get("tablice") or {}).get("tablice") or [])
        if t.get("board_id") is not None
    )


def _tablice_hipotezy(hipoteza: Hipoteza) -> tuple[str, ...]:
    """Tablice, o których mówi hipoteza. Pusta krotka = hipoteza nie o tablicy.

    Źródłem jest `obiekt_id`, bo tylko on jest wspólny dla wszystkich klas.
    Klasy porównujące parami trzymają tam dwa identyfikatory zlepione `+`
    i obie strony liczą się jednakowo: para bez jednej strony nie jest parą.
    """
    if not hipoteza.obiekt_id:
        return ()
    return tuple(hipoteza.obiekt_id.split(ROZDZIELNIK_PARY))


def odsiej_hipotezy(
    hipotezy: list[Hipoteza],
    *,
    board_ids: frozenset[str] | None,
    znane_tablice: frozenset[str],
) -> tuple[list[Hipoteza], list[Hipoteza]]:
    """Dzieli hipotezy na badane i pominięte. `None` = bez zawężenia.

    Filtr stoi MIĘDZY detektorami a agentem i to jest cała jego rola.
    `uruchom_detektory` nie dostaje parametru zawężającego, a
    `zbadaj_hipotezy` przyjmuje gotową listę — więc żadna z tych warstw
    nie musi wiedzieć, że wybór zakresu istnieje.

    Domyślnie hipoteza ZOSTAJE. Odsiewamy tylko wtedy, gdy wiemy, że mówi
    o tablicy poza wyborem — bo pomyłka w stronę „zbadaj" kosztuje kilka
    centów, a w stronę „pomiń" odbiera klientowi znalezisko, za które
    zapłacił.
    """
    if board_ids is None:
        return list(hipotezy), []

    badane: list[Hipoteza] = []
    pominiete: list[Hipoteza] = []

    for hipoteza in hipotezy:
        if not _o_tablicy(hipoteza, znane_tablice):
            badane.append(hipoteza)
            continue

        # Wszystkie strony muszą być wybrane. Para, której jedna połowa
        # wypadła, nie jest parą — a `DUPLICATE_STRUCTURE` bez drugiej
        # tablicy nie ma czego porównać.
        if all(bid in board_ids for bid in _tablice_hipotezy(hipoteza)):
            badane.append(hipoteza)
        else:
            pominiete.append(hipoteza)

    return badane, pominiete


def _sredni_koszt_klas(con: sqlite3.Connection) -> dict[str, tuple[float, float, float]]:
    """Klasa → (p25, mediana, p75) kosztu jednej hipotezy, z realnych runów.

    Wyłącznie runy zakończone i wyłącznie hipotezy, które faktycznie kosztowały:
    klasy szablonowe (`rola_agenta: brak`) zapisują 0 USD, bo nie wołają modelu,
    i wciągnięcie ich do rozkładu zaniżyłoby percentyle dla klas modelowych.
    """
    wiersze = con.execute(
        "SELECT z.klasa_id, z.koszt_usd FROM zuzycie_hipotez z "
        "JOIN runy r ON r.run_id = z.run_id "
        "WHERE r.status = 'zakonczony' AND z.koszt_usd IS NOT NULL AND z.koszt_usd > 0"
    ).fetchall()

    zebrane: dict[str, list[float]] = {}
    for wiersz in wiersze:
        zebrane.setdefault(str(wiersz["klasa_id"]), []).append(float(wiersz["koszt_usd"]))

    wynik: dict[str, tuple[float, float, float]] = {}
    for klasa_id, koszty in zebrane.items():
        koszty.sort()
        mediana = statistics.median(koszty)
        if len(koszty) >= 4:
            dolna, gorna = _percentyle(koszty)
        else:
            # Za mało pomiarów na percentyle. Rozrzut z min/max jest szerszy,
            # a szersze widełki są uczciwsze niż wąskie i zmyślone.
            dolna, gorna = koszty[0], koszty[-1]
        wynik[klasa_id] = (dolna, mediana, gorna)
    return wynik


def _percentyle(posortowane: list[float]) -> tuple[float, float]:
    """p25 i p75 metodą najbliższej rangi. Bez numpy — to jedna linijka logiki."""
    n = len(posortowane)
    return (
        posortowane[max(0, int(0.25 * n) - 1)],
        posortowane[min(n - 1, int(0.75 * n))],
    )


def oszacuj_koszt(
    hipotezy: list[Hipoteza],
    con: sqlite3.Connection,
    *,
    rubryka: Rubryka,
    znane_tablice: frozenset[str],
) -> Widelki:
    """Widełki kosztu runu agenta dla podanego zbioru hipotez.

    Pierwsze szacowanie PRZED runem w tym projekcie — `ewaluacja.py` liczy
    koszt po fakcie i mówi wprost „wszystko z bazy, nic z szacunku". Tu też
    nic nie jest wyssane: liczby biorą się z `zuzycie_hipotez`, a klasa bez
    historii jest wymieniona z nazwy, nie doliczona po cichu.

    Klasy szablonowe (`rola_agenta: brak`) wnoszą 0,00 USD, bo nie wołają
    modelu — `ZOMBIE_ACCOUNT` z etapu 4 jest tego dowodem.
    """
    rozklad = _sredni_koszt_klas(con)

    dolna = srodek = gorna = 0.0
    podloga = 0.0
    o_koncie = 0
    bez_historii: set[str] = set()

    for hipoteza in hipotezy:
        klasa = rubryka.po_id.get(hipoteza.klasa_id)
        if klasa is not None and klasa.rola_agenta == "brak":
            # Szablon, zero wywołań modelu. Nie doliczamy nic.
            continue

        widelki_klasy = rozklad.get(hipoteza.klasa_id)
        if widelki_klasy is None:
            bez_historii.add(hipoteza.klasa_id)
            widelki_klasy = (KOSZT_ZAPASOWY_USD, KOSZT_ZAPASOWY_USD, KOSZT_ZAPASOWY_USD)

        dolna += widelki_klasy[0]
        srodek += widelki_klasy[1]
        gorna += widelki_klasy[2]

        # Podłoga to hipotezy, których żaden wybór tablic nie usunie: ludzie,
        # goście, plan, automatyzacje. Klient musi ją widzieć, zanim odznaczy
        # wszystko i zdziwi się rachunkiem.
        #
        # ZMIERZONA USTERKA (snapshot #8): podłoga liczyła się z MEDIANY
        # (`widelki_klasy[1]`), a dolna granica z p25 — więc przy 15 z 24
        # hipotez po stronie konta wychodziło `dolna=0,99` przy `podloga=2,07`.
        # Kwota niższa od własnej podłogi jest po prostu nieprawdą.
        #
        # Podłoga MUSI iść z tego samego percentyla co dolna granica: to ta sama
        # suma, tylko po podzbiorze hipotez.
        if not _o_tablicy(hipoteza, znane_tablice):
            o_koncie += 1
            podloga += widelki_klasy[0]

    return Widelki(
        dolna_usd=round(dolna, 2),
        srodek_usd=round(srodek, 2),
        gorna_usd=round(gorna, 2),
        podloga_usd=round(podloga, 2),
        hipotez=len(hipotezy),
        hipotez_o_koncie=o_koncie,
        klasy_bez_historii=tuple(sorted(bez_historii)),
    )


def zbuduj_wybor(
    payload: dict[str, Any],
    hipotezy: list[Hipoteza],
    con: sqlite3.Connection,
    *,
    rubryka: Rubryka,
) -> WyborZakresu:
    """Składa ekran wyboru ze snapshotu. Zero wywołań API, zero kosztu modelu.

    Wszystko jest już w snapshocie: metadane tablic, próbka logów i liczba
    hipotez z detektorów. Ekran, który pokazuje klientowi rachunek, sam nie
    generuje rachunku.
    """
    from monday_audit.pulpit import _nazwy_tablic

    sekcja = payload.get("tablice") or {}
    wszystkie = sekcja.get("tablice") or []
    nazwy = _nazwy_tablic(payload)

    aktywnosc_po_tablicy = {
        str(wpis.get("board_id")): wpis
        for wpis in ((payload.get("aktywnosc") or {}).get("aktywnosc_tablic") or [])
    }

    znane = identyfikatory_tablic(payload)
    hipotez_po_tablicy: dict[str, int] = {}
    for hipoteza in hipotezy:
        if not _o_tablicy(hipoteza, znane):
            continue
        for bid in _tablice_hipotezy(hipoteza):
            hipotez_po_tablicy[bid] = hipotez_po_tablicy.get(bid, 0) + 1

    pozycje: list[PozycjaTablicy] = []
    pominietych = 0
    bez_logow = 0

    for tablica in wszystkie:
        if tablica.get("typ") != TYP_TABLICY:
            pominietych += 1
            continue

        bid = str(tablica.get("board_id"))
        aktywnosc = aktywnosc_po_tablicy.get(bid)
        flagi, automatycznych = _flagi_tablicy(tablica, aktywnosc)
        if aktywnosc is None:
            bez_logow += 1

        kubelki = (aktywnosc or {}).get("kubelki_dni") or {}
        pozycje.append(
            PozycjaTablicy(
                board_id=bid,
                nazwa=nazwy.get(bid, bid),
                workspace_id=str(tablica.get("workspace_id") or ""),
                workspace_nazwa=str(tablica.get("workspace_nazwa") or ""),
                kolumn=len(tablica.get("kolumny") or []),
                kolumn_automatycznych=automatycznych,
                items_count=int(tablica.get("items_count") or 0),
                wpisow=(
                    sum(int(v or 0) for v in kubelki.values()) if aktywnosc is not None else None
                ),
                flagi=flagi,
                hipotez=hipotez_po_tablicy.get(bid, 0),
            )
        )

    return WyborZakresu(
        workspace_y=_zgrupuj_workspace(pozycje),
        tablice=tuple(pozycje),
        widelki=oszacuj_koszt(hipotezy, con, rubryka=rubryka, znane_tablice=znane),
        pominietych_pomocniczych=pominietych,
        tablic_bez_logow=bez_logow,
        uwagi_o_zakresie=tuple(
            str(u) for u in ((payload.get("meta") or {}).get("uwagi_o_zakresie") or [])
        ),
    )


def _zgrupuj_workspace(pozycje: list[PozycjaTablicy]) -> tuple[PozycjaWorkspace, ...]:
    """Workspace'y wyliczone z tablic, posortowane malejąco po liczbie tablic."""
    zebrane: dict[str, dict[str, Any]] = {}
    for pozycja in pozycje:
        wpis = zebrane.setdefault(
            pozycja.workspace_id,
            {"nazwa": pozycja.workspace_nazwa, "tablic": 0, "hipotez": 0},
        )
        wpis["tablic"] += 1
        wpis["hipotez"] += pozycja.hipotez

    return tuple(
        PozycjaWorkspace(
            workspace_id=wid,
            nazwa=dane["nazwa"] or wid,
            tablic=dane["tablic"],
            hipotez=dane["hipotez"],
        )
        for wid, dane in sorted(zebrane.items(), key=lambda kv: (-kv[1]["tablic"], kv[1]["nazwa"]))
    )


def sprawdz_wybor(payload: dict[str, Any], board_ids: list[str]) -> frozenset[str]:
    """Waliduje wybór wobec snapshotu. Obce identyfikatory to błąd, nie ostrzeżenie.

    Pusta lista znaczy „całe konto" i zwraca `frozenset()` — wołający
    rozstrzyga, czy to znaczy bez zawężenia (patrz `odsiej_hipotezy`,
    gdzie brakiem zawężenia jest `None`).

    Cicha tolerancja obcego `board_id` pozwoliłaby zapłacić za audyt tablicy,
    której w tym snapshocie nie ma — i nikt by tego nie zauważył.
    """
    dostepne = {
        str(t.get("board_id"))
        for t in ((payload.get("tablice") or {}).get("tablice") or [])
        if t.get("typ") == TYP_TABLICY
    }
    obce = sorted(set(map(str, board_ids)) - dostepne)
    if obce:
        raise WyborError(
            f"identyfikatory poza tym snapshotem: {', '.join(obce[:5])}"
            + (f" (i {len(obce) - 5} więcej)" if len(obce) > 5 else "")
        )
    return frozenset(map(str, board_ids))


def zapisz_pominiete(con: sqlite3.Connection, *, run_id: str, pominiete: list[Hipoteza]) -> None:
    """Ślad po hipotezach odsianych wyborem — w tabeli, która już istnieje.

    Panel pokazuje `hipotezy_odrzucone` jako „czego nie widać", więc pominięte
    wyborem trafiają dokładnie tam, gdzie klient ich szuka. Osobna tabela
    byłaby drugim śladem na to samo.
    """
    if not pominiete:
        return
    con.executemany(
        "INSERT INTO hipotezy_odrzucone (run_id, klasa_id, obiekt_id, powod) VALUES (?, ?, ?, ?)",
        [(run_id, h.klasa_id, h.obiekt_id, POWOD_POZA_ZAKRESEM) for h in pominiete],
    )


def opis_zawezenia(*, wybranych: int, wszystkich: int, o_koncie: int) -> str:
    """Adnotacja do raportu: ile tablic objął audyt.

    Bez niej raport z jedną tablicą wygląda jak pełny audyt konta, w którym
    prawie nic nie znaleziono. To dwie bardzo różne wiadomości.
    """
    if wybranych >= wszystkich:
        return ""
    return (
        f"Audyt objął {wybranych} z {wszystkich} tablic wskazanych przy zamówieniu. "
        f"Klasy dotyczące całego konta ({o_koncie} zbadanych) nie zależą od tego "
        "wyboru — sprawdziliśmy je w pełni."
    )


def opis_milczenia_par(klasy: list[str], rubryka: Rubryka) -> str:
    """Adnotacja o klasach, które porównują tablice parami.

    `DUPLICATE_STRUCTURE` łączy tablice w pary w obrębie workspace'u. Przy
    jednej wybranej tablicy nie ma czego z czym porównać, więc klasa milczy —
    a milczenie czyta się jak „duplikatów nie ma". To nie to samo.
    """
    if not klasy:
        return ""
    nazwy = [(rubryka.po_id[k].nazwa if k in rubryka.po_id else k) for k in sorted(set(klasy))]
    return (
        f"{', '.join(nazwy)} — te znaleziska powstają z porównania tablic między "
        "sobą. Przy zawężonym wyborze część par wypadła z audytu, więc brak "
        "takiego znaleziska nie znaczy, że problemu nie ma."
    )


def klasy_milczace(pominiete: list[Hipoteza], rubryka: Rubryka) -> list[str]:
    """Klasy porównujące parami, których hipotezy odsiał wybór.

    Kryterium mechaniczne: `obiekt_id` zawiera rozdzielnik pary. Nie lista
    nazw w kodzie — nowa klasa porównująca parami ma być objęta adnotacją
    bez zmiany tego pliku.
    """
    return sorted({h.klasa_id for h in pominiete if ROZDZIELNIK_PARY in (h.obiekt_id or "")})


def wybor_do_json(wybor: WyborZakresu) -> dict[str, Any]:
    """Payload dla frontu. Nazwy pól zgodne z generatorem typów."""
    return {
        "workspace_y": [
            {
                "workspace_id": w.workspace_id,
                "nazwa": w.nazwa,
                "tablic": w.tablic,
                "hipotez": w.hipotez,
            }
            for w in wybor.workspace_y
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
                "wpisow": t.wpisow,
                "flagi": list(t.flagi),
                "hipotez": t.hipotez,
                "oflagowana": t.oflagowana,
            }
            for t in wybor.tablice
        ],
        "widelki": {
            "dolna_usd": wybor.widelki.dolna_usd,
            "srodek_usd": wybor.widelki.srodek_usd,
            "gorna_usd": wybor.widelki.gorna_usd,
            "podloga_usd": wybor.widelki.podloga_usd,
            "hipotez": wybor.widelki.hipotez,
            "hipotez_o_koncie": wybor.widelki.hipotez_o_koncie,
            "klasy_bez_historii": list(wybor.widelki.klasy_bez_historii),
            "oszacowane_z_zapasu": wybor.widelki.oszacowane_z_zapasu,
        },
        "pominietych_pomocniczych": wybor.pominietych_pomocniczych,
        "tablic_bez_logow": wybor.tablic_bez_logow,
        "uwagi_o_zakresie": list(wybor.uwagi_o_zakresie),
    }


def wczytaj_payload(con: sqlite3.Connection, snapshot_id: int) -> dict[str, Any]:
    """Payload snapshotu jako dict. Brak snapshotu to błąd wołającego."""
    wiersz = con.execute("SELECT payload FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if wiersz is None:
        raise WyborError(f"snapshot {snapshot_id} nie istnieje")
    return json.loads(wiersz["payload"])
