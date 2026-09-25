"""Funkcje wejściowe pakietu dla portalu (plan, faza 6, wariant A).

Decyzja Kuby z 2026-09-23: kod audytu trafia do repo portalu jako importowany
pakiet. Ekrany, sesje, konta i magazyn klucza pisze portal — ten moduł jest
JEDYNYM miejscem, które portal ma wołać. Wszystko inne w `monday_audit` to
szczegół implementacji, który może się zmienić bez ostrzeżenia.

Dwie funkcje, dwa kroki z user story:

1. `przeglad_konta` — „Analizuj moje środowisko": sześć kafelków (krok 6-1),
2. `analiza_konta` — „Chcę wykonać analizę": pełny skan, model, uwagi (6-2).

## Umowa, której portal może się trzymać

* **Klucz monday przychodzi parametrem** i żyje tylko w pamięci wywołania —
  nie trafia do logów, argv ani na dysk (D12).
* **Nic o osobie nie jest zapisywane.** `przeglad_konta` nie dotyka żadnej
  bazy. `analiza_konta` zbiera snapshot i mapowanie osób do SQLite W PAMIĘCI
  (5c), a do bazy wołającego zapisuje wyłącznie to, co przeszło przez
  `przechowanie.py`: metadane runu, koszt, zamaskowane uwagi, liczby.
* **Raport z nazwiskami wraca w pamięci** (`WynikAnalizy.raport_html`) i nie
  ma kopii na dysku — jak go oddać człowiekowi, decyduje portal (5c, wariant A).
* **Sól pseudonimizacji, `client_id` i klucze przychodzą parametrami**, a nie
  z `.env` tego pakietu — dostarcza je portal.
* **Wynik ma `do_json()`** z polami, które są kontraktem. Typy dla portalu
  generujemy z dataclass (krok 6-4), nie piszemy ręcznie.
* **Błędy po stronie klienta konta** (token bez admina, zły klucz) wracają
  jako `UslugaError` z komunikatem, który można pokazać człowiekowi — bez
  treści odpowiedzi API.
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from monday_audit.analiza import (
    BUDZET_NARZEDZI,
    SCIEZKA_PROMPTU_ANALIZY,
    OdpowiedzBezJsonaError,
    PozaSufitem,
    przytnij_do_sufitu,
    rozdziel_hipotezy,
    zbadaj_konto,
    zbuduj_zadanie,
)
from monday_audit.baza import MapowanieOsob, polacz, zastosuj_migracje
from monday_audit.detektory import uruchom_detektory
from monday_audit.dowod import KontraktError
from monday_audit.inwentarz import Inwentarz, zbuduj_inwentarz
from monday_audit.klient import LimitDziennyError, MondayClient, MondayError
from monday_audit.konto import LIMITY_DZIENNE, Zakres, ZakresError, rozpoznaj_konto
from monday_audit.koszt import Szacunek, historia_analiz, oszacuj, zapisz_zuzycie_analizy
from monday_audit.logi import DOBRANYCH_BEZ_WLASCICIELA, MAKS_BEZ_OKNA, TOP_PO_ITEMACH, Z_OGONA
from monday_audit.narzedzia import Narzedzia
from monday_audit.obserwowalnosc import (
    Wysylka,
    hasz_obrazu,
    wyslij_bezpiecznie,
    zbuduj_trace_analizy,
)
from monday_audit.osoby import RODZAJE_AGENTOW, MaPII, zredaguj_pii
from monday_audit.podglad_zakresu import RejestrPodgladu
from monday_audit.przebieg import wykonaj_run, zapisz_zuzycie
from monday_audit.przechowanie import (
    PrzechowanieError,
    uwaga_do_zapisu,
    zapisz_statystyki,
    zapisz_uwagi,
)
from monday_audit.raport_uwag import wyrenderuj_uwagi, zbuduj_raport_uwag
from monday_audit.rubryka import Rubryka, wczytaj_rubryke
from monday_audit.sdk import MODEL, hash_promptu
from monday_audit.uwagi import WynikUwag, waliduj_uwagi

logger = logging.getLogger(__name__)


class UslugaError(RuntimeError):
    """Błąd, który portal pokazuje człowiekowi. Komunikat bez danych z API."""


class AnalizaError(RuntimeError):
    """Sesja modelu oddała odpowiedź bez struktury. Za sesję już zapłacono.

    `odpowiedz_modelu` niesie surową odpowiedź W PAMIĘCI, żeby wołający mógł ją
    pokazać albo przejrzeć — pakiet nie zapisuje jej nigdzie, bo niesie
    pseudonimy (5c).
    """

    def __init__(self, komunikat: str, odpowiedz_modelu: dict[str, Any]) -> None:
        super().__init__(komunikat)
        self.odpowiedz_modelu = odpowiedz_modelu


@dataclass(frozen=True, slots=True)
class Kafelek:
    """Jeden kafelek z punktu 2 wytycznych.

    `klucz` jest stały i służy portalowi do wyboru ikony i trasy — w fazie 7
    kliknięcie w kafelek przenosi do pogłębionego raportu tej kategorii.
    `szczegoly` to rozbicie, które kafelek może pokazać pod liczbą.
    """

    klucz: str
    etykieta: str
    wartosc: int | str | None
    szczegoly: dict[str, Any] = field(default_factory=dict)

    def do_json(self) -> dict[str, Any]:
        return {
            "klucz": self.klucz,
            "etykieta": self.etykieta,
            "wartosc": self.wartosc,
            "szczegoly": dict(self.szczegoly),
        }


@dataclass(frozen=True, slots=True)
class PrzegladKonta:
    """Wynik kroku 1. Zero modelu, zero itemów, zero zapisu."""

    konto_nazwa: str
    kafelki: tuple[Kafelek, ...]
    # Ile wywołań z DZIENNEGO limitu konta klienta to kosztowało — portal
    # może to pokazać obok przycisku kroku 2, który kosztuje więcej.
    wywolan: int
    # Czego te liczby nie obejmują. Pokazywać razem z kafelkami, nie w przypisie.
    zastrzezenia: tuple[str, ...] = ()

    def do_json(self) -> dict[str, Any]:
        return {
            "konto_nazwa": self.konto_nazwa,
            "kafelki": [k.do_json() for k in self.kafelki],
            "wywolan": self.wywolan,
            "zastrzezenia": list(self.zastrzezenia),
        }


# Kolejność jak w punkcie 2 wytycznych. Stała, bo portal buduje na niej układ.
KAFELKI = ("workspace", "tablice", "uzytkownicy", "goscie", "agenci_ai", "licencja")


def kafelki_z_inwentarza(inwentarz: Inwentarz) -> tuple[Kafelek, ...]:
    """`Inwentarz` → sześć kafelków. Czysta funkcja, testowalna bez sieci."""
    return (
        Kafelek(
            "workspace",
            "Workspace'y",
            inwentarz.workspacow,
            {"po_produktach": dict(inwentarz.po_produktach)},
        ),
        Kafelek(
            "tablice",
            "Tablice",
            inwentarz.tablic_aktywnych,
            {
                # Kafelek liczy AKTYWNE tablice typu `board`; reszta obok, żeby
                # „3268 obiektów, w tym 1206 w koszu" nie znikło (faza 2a).
                "razem_obiektow": inwentarz.tablic_razem,
                "po_stanie": dict(inwentarz.tablic_po_stanie),
                "po_typie": dict(inwentarz.tablic_po_typie),
            },
        ),
        Kafelek(
            "uzytkownicy",
            "Użytkownicy",
            inwentarz.uzytkownikow,
            {
                "podgladajacych": inwentarz.podgladajacych,
                "po_rodzajach": dict(inwentarz.po_rodzajach),
            },
        ),
        Kafelek("goscie", "Goście", inwentarz.gosci),
        Kafelek("agenci_ai", "Agenci AI", inwentarz.agentow_ai),
        Kafelek(
            "licencja",
            "Licencja",
            inwentarz.licencja_tier,
            {"okres": inwentarz.licencja_period, "max_uzytkownikow": inwentarz.licencja_max_users},
        ),
    )


async def przeglad_konta(klucz_monday: str) -> PrzegladKonta:
    """Krok 1 — „Analizuj moje środowisko". ~36 wywołań na CXLABS, 0 USD.

    Wymaga tokena z uprawnieniami admina: inwentarz liczony bez admina byłby
    cicho niepełny, więc `rozpoznaj_konto` przerywa, a tu wraca to jako
    `UslugaError` z komunikatem dla człowieka.
    """
    if not klucz_monday or not klucz_monday.strip():
        raise UslugaError("brak klucza API monday")

    rejestr = RejestrPodgladu()
    try:
        async with MondayClient(klucz_monday.strip(), rejestr) as klient:
            konto = await rozpoznaj_konto(klient, Zakres(typ="cale_konto"))
            inwentarz = await zbuduj_inwentarz(klient, konto, rejestr)
    except ZakresError:
        raise UslugaError(
            "Ten klucz nie ma uprawnień administratora konta monday — przegląd całego "
            "konta byłby niepełny. Użyj klucza administratora."
        ) from None
    except LimitDziennyError:
        raise UslugaError(
            "Dzienny limit wywołań API tego konta monday jest wyczerpany. Spróbuj jutro."
        ) from None
    except (MondayError, httpx.HTTPError) as blad:
        # Treść błędu z API zostaje w logu po NASZEJ stronie, nie w komunikacie —
        # bywa fragmentem odpowiedzi.
        logger.warning("przegląd konta nie wyszedł: %s", type(blad).__name__)
        raise UslugaError(
            "Nie udało się odczytać konta tym kluczem. Sprawdź, czy klucz jest poprawny."
        ) from None

    return PrzegladKonta(
        konto_nazwa=inwentarz.konto_nazwa,
        kafelki=kafelki_z_inwentarza(inwentarz),
        wywolan=inwentarz.wywolan,
        zastrzezenia=inwentarz.zastrzezenia,
    )


# ── szacunek kroku 2 z danych kroku 1 (6-3) ──────────────────────────────
#
# Zamiast progu „pięciu workspace'ów" z user story: koszt kroku 2 nie zależy od
# liczby workspace'ów, tylko od liczby OBIEKTÓW i próbki logów (faza 3: konto
# z tysiącem małych tablic kosztuje więcej niż konto z jedną wielką). Każda
# część collectora ma policzalny koszt — stałe niżej są jego stronicowaniem.

STRONA_UZYTKOWNIKOW = 500  # `osoby.zbierz_osoby(limit=500)`
STRONA_TABLIC = 25  # `tablice.LIMIT_STRONY`, stany: wszystkie (także kosz)
# `logi.TOP_PO_ITEMACH` + `logi.Z_OGONA` + dobrane bez właściciela (górna granica:
# ile ich jest, krok 1 nie wie).
PROBKA_LOGOW = TOP_PO_ITEMACH + Z_OGONA + DOBRANYCH_BEZ_WLASCICIELA
MAKS_STRON_LOGOW = 10  # `logi.MAKS_STRON_LOGOW`
# Średnio stron logu na tablicę próbki. ZMIERZONE 2026-09-23 na pełnym CXLABS:
# szacunek z jedną stroną dał 283, collector zużył 334 — stąd 1,5.
STRON_LOGOW_TYPOWO = 1.5
STALE_WYWOLANIA = 1 + 4 + 5  # konto + statystyki automatyzacji + sonda agentów
SOND_TABLIC = 10  # `automatyzacje.MAKS_SOND`
MAKS_PRZEBIEGOW_WYWOLAN = 60  # `automatyzacje.MAKS_PRZEBIEGOW` × 2
BEZ_OKNA_TYPOWO = 65  # tablic bez aktywnego właściciela na pełnym CXLABS, 2026-09-25
NARZEDZI_TYPOWO = 30  # wywołania narzędzi na żywo w sesji — zmierzone 2026-09-24
# Przerwanie przy połowie dziennego limitu konta klienta (skill monday-graphql).
PROG_LIMITU = 0.5


@dataclass(frozen=True, slots=True)
class SzacunekAnalizy:
    """Ile kosztuje krok 2 — do pokazania OBOK przycisku, przed kliknięciem.

    Wywołania monday liczone są z danych kroku 1, więc da się je podać przed
    zebraniem czegokolwiek. Koszt modelu — już nie: zależy od liczby hipotez,
    a te powstają dopiero z zebranego snapshotu. Dlatego `usd_od` to dolna
    granica, a dokładna kwota przychodzi przez `analiza_konta(przed_sesja=…)`,
    zanim ruszy model.
    """

    wywolan_typowo: int
    wywolan_maks: int
    limit_dzienny: int | None
    usd_od: float
    zastrzezenia: tuple[str, ...] = ()

    @property
    def udzial_maks(self) -> float | None:
        return round(self.wywolan_maks / self.limit_dzienny, 4) if self.limit_dzienny else None

    @property
    def przekracza_prog(self) -> bool:
        """Najgorszy przypadek zjada ponad połowę dnia konta klienta."""
        return bool(self.udzial_maks is not None and self.udzial_maks > PROG_LIMITU)

    def do_json(self) -> dict[str, Any]:
        return {
            "wywolan_typowo": self.wywolan_typowo,
            "wywolan_maks": self.wywolan_maks,
            "limit_dzienny": self.limit_dzienny,
            "udzial_maks": self.udzial_maks,
            "przekracza_prog": self.przekracza_prog,
            "usd_od": self.usd_od,
            "zastrzezenia": list(self.zastrzezenia),
        }


def _kafelek(przeglad: PrzegladKonta, klucz: str) -> Kafelek:
    return next(k for k in przeglad.kafelki if k.klucz == klucz)


def szacuj_analize(
    przeglad: PrzegladKonta, *, trwala: sqlite3.Connection | None = None
) -> SzacunekAnalizy:
    """Koszt kroku 2 z kafelków kroku 1. Zero wywołań, zero modelu.

    `trwala` opcjonalnie — z historią analiz dolna granica USD liczy się z niej
    (`koszt.oszacuj`), bez niej z pomiaru startowego.
    """
    tablice = _kafelek(przeglad, "tablice")
    obiektow = int(tablice.szczegoly.get("razem_obiektow") or tablice.wartosc or 0)
    aktywnych = int(tablice.wartosc or 0)
    kont = sum(
        int(v)
        for v in (_kafelek(przeglad, "uzytkownicy").szczegoly.get("po_rodzajach") or {}).values()
    )
    tier = _kafelek(przeglad, "licencja").wartosc

    probka = min(aktywnych, PROBKA_LOGOW)
    stale = (
        STALE_WYWOLANIA
        + -(-max(kont, 1) // STRONA_UZYTKOWNIKOW)
        + -(-max(obiektow, 1) // STRONA_TABLIC)
        + min(aktywnych, SOND_TABLIC)
    )
    # Narzędzia AI na żywo (próbka kolumn, log tablicy) też idą z limitu klienta:
    # maks = cały budżet sesji, typowo = tyle, ile zużyła sesja na pełnym CXLABS
    # 2026-09-24 (29 przy budżecie 30).
    typowo = (
        stale + round(probka * STRON_LOGOW_TYPOWO) + MAKS_PRZEBIEGOW_WYWOLAN // 2 + NARZEDZI_TYPOWO
    )
    maks = stale + probka * MAKS_STRON_LOGOW + MAKS_PRZEBIEGOW_WYWOLAN + BUDZET_NARZEDZI
    # Log bez okna dla tablic bez właściciela: jedna strona na tablicę, do
    # `MAKS_BEZ_OKNA`. Ile ich jest, krok 1 nie wie — typowo liczba z pełnego
    # CXLABS (65), maks = limit.
    typowo += min(aktywnych, BEZ_OKNA_TYPOWO)
    maks += min(aktywnych, MAKS_BEZ_OKNA)

    historia = historia_analiz(trwala) if trwala is not None else None
    usd_od = oszacuj(1, historia).koszt_usd

    zastrzezenia = [
        "koszt modelu to dolna granica — dokładna kwota jest znana dopiero po zebraniu "
        "danych, przed uruchomieniem modelu",
    ]
    limit = LIMITY_DZIENNE.get(str(tier)) if tier else None
    if limit is None:
        zastrzezenia.append(f"nieznany plan `{tier}` — nie da się podać udziału w limicie dziennym")
    return SzacunekAnalizy(
        wywolan_typowo=typowo,
        wywolan_maks=maks,
        limit_dzienny=limit,
        usd_od=usd_od,
        zastrzezenia=tuple(zastrzezenia),
    )


# ── krok 2: analiza konta ────────────────────────────────────────────────


@dataclass(frozen=True)
class WynikAnalizy:
    """Wynik kroku 2.

    Dwie wersje uwag, i to jest sedno 5c:

    * `uwagi` — ZAMASKOWANE, w postaci z `uwagi_zapisane` (osoba → `[OSOBA]`,
      daty → dni przed runem). Te wolno pokazać, przechować, porównać,
    * `raport_html` — raport Z NAZWISKAMI, jedyny raz. Nie wchodzi do `do_json`,
      bo portal ma go oddać człowiekowi, a nie przepuścić przez swoje API i logi.

    `blad_zapisu` zamiast wyjątku: zapis minimalny, który padł, nie może zabrać
    wyniku, za który zapłacono — biblioteczna wersja zasady „najpierw wynik,
    potem zapis" z CLI.
    """

    run_id: str | None
    hipotez: int
    do_modelu: int
    z_szablonu: int
    szacunek: Szacunek
    uwagi: tuple[dict[str, Any], ...] = ()
    pominietych: int = 0
    odrzuconych: int = 0
    zuzycie: dict[str, Any] = field(default_factory=dict)
    sekund: float = 0.0
    wywolan_monday: int | None = None
    bez_obrazu_konta: bool = True
    zapisanych_uwag: int = 0
    blad_zapisu: str | None = None
    raport_html: str | None = None
    # Tylko w pamięci: dla operatora CLI (`--wyjscie`, tabelka). Niesie
    # pseudonimy, więc NIE wchodzi do `do_json`.
    odpowiedz_modelu: dict[str, Any] | None = None
    walidacja: WynikUwag | None = None
    # Klasy przycięte sufitem (`analiza.SUFIT_NA_KLASE`) — liczby, bez obiektów.
    poza_sufitem: tuple[PozaSufitem, ...] = ()

    def do_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "hipotez": self.hipotez,
            "do_modelu": self.do_modelu,
            "z_szablonu": self.z_szablonu,
            "szacunek_usd": self.szacunek.koszt_usd,
            "koszt_usd": self.zuzycie.get("koszt_usd"),
            "uwagi": [dict(u) for u in self.uwagi],
            "pominietych": self.pominietych,
            "odrzuconych": self.odrzuconych,
            "sekund": self.sekund,
            "wywolan_monday": self.wywolan_monday,
            "bez_obrazu_konta": self.bez_obrazu_konta,
            "zapisanych_uwag": self.zapisanych_uwag,
            "blad_zapisu": self.blad_zapisu,
            "ma_raport": self.raport_html is not None,
            "poza_sufitem": {
                p.klasa_id: {"zbadanych": p.zbadanych, "wszystkich": p.wszystkich}
                for p in self.poza_sufitem
            },
        }


def _teraz() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def analiza_konta(
    klucz_monday: str,
    *,
    klucz_anthropic: str,
    client_id: str,
    sol: bytes,
    trwala: sqlite3.Connection,
    zakres: Zakres | None = None,
    wejscie: dict[str, Any] | None = None,
    slad: Wysylka | None = None,
    run_id: str | None = None,
    tylko_szacunek: bool = False,
    przed_sesja: Callable[[WynikAnalizy], None] | None = None,
) -> WynikAnalizy:
    """Krok 2 — „Chcę wykonać analizę". Collector W PAMIĘCI, model, uwagi.

    `trwala` to baza wołającego z migracjami tego pakietu; trafia do niej
    wyłącznie zapis minimalny (5c). `klucz_anthropic` pusty = rozliczenie
    subskrypcją (`konfiguracja.klucz_anthropic`). `slad` to odbiorca trace'ów
    albo `None`; zamyka go wołający. `przed_sesja` dostaje hipotezy i szacunek,
    ZANIM ruszy model — dla ekranu, który chce pokazać koszt przed wydaniem.
    """
    if not klucz_monday or not klucz_monday.strip():
        raise UslugaError("brak klucza API monday")

    zrodlo = polacz(":memory:")
    try:
        zastosuj_migracje(zrodlo)
        zastosuj_migracje(trwala)
        raport_runu = await wykonaj_run(
            token=klucz_monday.strip(),
            con=zrodlo,
            client_id=client_id,
            zakres=zakres or Zakres(typ="cale_konto"),
            sol=sol,
        )
        # Klient monday dla narzędzi NA ŻYWO (`probka_kolumn`, `log_tablicy`).
        # ZMIERZONE 2026-09-24: bez niego model odrzucił 20 z 20 BOARD_OVERCOMPLEX
        # („narzędzie niedostępne"), bo martwych kolumn nie da się ustalić bez
        # próbki itemów. Ten sam `MondayClient` co collector: `przygotuj_zapytanie`
        # odrzuca `mutation` i `subscription`, więc zapisu nie ma jak wysłać.
        # Rejestr w pamięci — wiersz `runy` jest w `trwala`, a nie w `zrodlo`.
        rejestr_narzedzi = RejestrPodgladu()
        async with MondayClient(klucz_monday.strip(), rejestr_narzedzi) as klient:
            wynik = await analizuj_snapshot(
                zrodlo=zrodlo,
                snapshot_id=raport_runu.snapshot_id,
                trwala=trwala,
                klucz_anthropic=klucz_anthropic,
                client_id=client_id,
                sol=sol,
                wejscie=wejscie,
                slad=slad,
                run_id=run_id,
                tylko_szacunek=tylko_szacunek,
                wywolan_monday=raport_runu.wywolan,
                przed_sesja=przed_sesja,
                klient=klient,
            )
        # Wywołania narzędzi też idą z limitu dziennego klienta — liczymy je razem.
        return dataclasses.replace(
            wynik, wywolan_monday=raport_runu.wywolan + rejestr_narzedzi.wywolan
        )
    finally:
        # Baza w pamięci znika razem z tym zamknięciem — to jest cały mechanizm 5c.
        zrodlo.close()


async def analizuj_snapshot(
    *,
    zrodlo: sqlite3.Connection,
    snapshot_id: int,
    trwala: sqlite3.Connection,
    klucz_anthropic: str,
    client_id: str,
    sol: bytes,
    wejscie: dict[str, Any] | None = None,
    slad: Wysylka | None = None,
    run_id: str | None = None,
    tylko_szacunek: bool = False,
    wywolan_monday: int | None = None,
    rubryka: Rubryka | None = None,
    przed_sesja: Callable[[WynikAnalizy], None] | None = None,
    klient: MondayClient | None = None,
) -> WynikAnalizy:
    """Analiza gotowego snapshotu. `zrodlo` to baza, w której on leży.

    `klient` to otwarty `MondayClient` dla narzędzi na żywo; `None` = model
    widzi sam snapshot (i klasy, które potrzebują próbki itemów, nie domkną się).

    Wydzielone z `analiza_konta`, bo CLI umie też analizować snapshot zebrany
    przed fazą 5c (`--snapshot N`) — wtedy `zrodlo` jest bazą trwałą.
    """
    w_pamieci = zrodlo is not trwala
    rubryka = rubryka or wczytaj_rubryke()
    # Znane osoby — z `zrodlo`, bo tam jest tabela mapowania; w trybie pamięci
    # znika razem z nim. Służą redakcji obrazu konta i drugiej siatce trace'u.
    wpisy = tuple(MapowanieOsob(zrodlo, client_id).wczytaj())
    nie_ludzie = _nie_ludzie(zrodlo, snapshot_id)
    wejscie = _zredagowane_wejscie(wejscie or {}, wpisy, nie_ludzie)
    hipotezy, _ = uruchom_detektory(zrodlo, snapshot_id, rubryka)
    # Szablony PRZED modelem — wiedza starej ścieżki (`analiza.rozdziel_hipotezy`).
    do_modelu, z_szablonow = rozdziel_hipotezy(hipotezy, rubryka)
    # Sufit PO szablonach: szablon kosztuje zero, więc nie ma powodu go przycinać.
    do_modelu, poza_sufitem = przytnij_do_sufitu(do_modelu)
    # Szacunek WYŁĄCZNIE dla tego, co pójdzie do modelu; szablon kosztuje zero.
    szacunek = oszacuj(len(do_modelu), historia_analiz(trwala))
    podstawa = WynikAnalizy(
        run_id=None,
        hipotez=len(hipotezy),
        do_modelu=len(do_modelu),
        z_szablonu=len(z_szablonow),
        szacunek=szacunek,
        wywolan_monday=wywolan_monday,
        bez_obrazu_konta=not wejscie,
        poza_sufitem=tuple(poza_sufitem),
    )
    if przed_sesja is not None:
        # Szacunek PRZED wydaniem pieniędzy — CLI wypisuje go, zanim ruszy model.
        przed_sesja(podstawa)
    if not hipotezy or tylko_szacunek:
        return podstawa

    run_id = run_id or f"analiza-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    prompt_hash = hash_promptu(SCIEZKA_PROMPTU_ANALIZY)
    # Wiersz w `runy` PRZED sesją — `zuzycie_hipotez.run_id` ma klucz obcy do
    # `runy`. `snapshot_id` tylko przy snapshocie z bazy trwałej: ten z pamięci
    # w niej nie istnieje i klucz obcy by go odrzucił.
    trwala.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
        "rubric_ver, prompt_hash) VALUES (?, ?, ?, 'w_toku', ?, ?, ?, ?)",
        (
            run_id,
            client_id,
            None if w_pamieci else snapshot_id,
            _teraz(),
            MODEL,
            rubryka.wersja,
            prompt_hash,
        ),
    )
    trwala.commit()

    obraz_hash = hasz_obrazu(wejscie)
    wspolne: dict[str, Any] = {
        "wpisy": wpisy if slad is not None else (),
        "nie_ludzie": nie_ludzie,
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "model": MODEL,
        "prompt_hash": prompt_hash,
        "obraz_hash": obraz_hash,
        # Zadanie w postaci, którą dostał model, z obrazem konta jako haszem —
        # żeby trace dało się przeczytać i odtworzyć (zgłoszone 2026-09-24).
        "zadanie": zbuduj_zadanie(
            do_modelu,
            wejscie,
            rubryka,
            obraz_zastepczy=f"[obraz konta — tylko hasz {obraz_hash}]",
        )
        if do_modelu
        else None,
        "hipotezy": [h.do_zapisu() for h in do_modelu],
        "z_szablonu": len(z_szablonow),
        "szacunek_usd": szacunek.koszt_usd,
    }

    try:
        zaczeto = time.monotonic()
        if do_modelu:
            zestaw = Narzedzia(
                con=zrodlo, snapshot_id=snapshot_id, client_id=client_id, sol=sol, klient=klient
            )
            try:
                odpowiedz = await zbadaj_konto(
                    do_modelu,
                    zestaw=zestaw,
                    wejscie=wejscie,
                    klucz_api=klucz_anthropic,
                    rubryka=rubryka,
                )
            except OdpowiedzBezJsonaError as blad:
                # Sesja opłacona — koszt do bazy, surowy tekst do wołającego
                # (w pamięci). Bez tego po 24 min nie zostało nic (2026-09-24).
                zapisz_zuzycie(trwala, run_id, blad.zuzycie)
                raise AnalizaError(
                    str(blad),
                    {
                        "surowy_tekst": blad.tekst,
                        "zuzycie": blad.zuzycie,
                        "przebieg_narzedzi": blad.przebieg,
                    },
                ) from None
        else:
            odpowiedz = {"uwagi": [], "pominiete": [], "zuzycie": {}}
        sekund = round(time.monotonic() - zaczeto, 3)

        # Kopia PRZED doklejeniem szablonów: trace generacji opisuje wywołanie
        # modelu, a uwaga z szablonu kazałaby przypisać mu coś, czego nie napisał.
        odpowiedz_modelu = dict(odpowiedz)
        # Struktura odpowiedzi MODELU sprawdzana PRZED doklejeniem szablonów.
        # Wcześniej (także w CLI przed 6-2) szablony trafiały do `uwagi` przed
        # walidacją, więc odpowiedź bez tego pola dostawała je „w prezencie"
        # i sesja bez struktury wyglądała jak „model nic nie znalazł" —
        # dokładnie to, czego `uwagi.waliduj_uwagi` ma nie dopuszczać.
        if do_modelu:
            _sprawdz_strukture(odpowiedz_modelu)
        odpowiedz["uwagi"] = z_szablonow + list(odpowiedz.get("uwagi") or [])
        try:
            walidacja = waliduj_uwagi(odpowiedz, rubryka)
        except KontraktError as blad:
            raise AnalizaError(str(blad), odpowiedz_modelu) from blad
        zuzycie = odpowiedz.get("zuzycie") or {}

        # Raport Z NAZWISKAMI — teraz albo nigdy: mapowanie żyje w `zrodlo`.
        raport_html = _raport_z_nazwiskami(
            walidacja,
            zrodlo=zrodlo,
            client_id=client_id,
            run_id=run_id,
            rubryka=rubryka,
            # Sufit idzie STRUKTURĄ (procent w bloku „Czego ten raport nie
            # sprawdził"), a nie tekstem — tekstem zostają zastrzeżenia obrazu.
            zastrzezenia=tuple(wejscie.get("zastrzezenia") or ()),
            snapshot_id=snapshot_id,
            poza_sufitem=poza_sufitem,
        )

        zapisanych, blad_zapisu = _zapis_minimalny(
            trwala,
            run_id,
            walidacja,
            zuzycie=zuzycie,
            wejscie=wejscie,
            ile_do_modelu=len(do_modelu),
            wywolan_narzedzi=len(odpowiedz.get("wywolania_narzedzi") or []),
            sekund=sekund,
            hipotez=len(hipotezy),
            wywolan_monday=wywolan_monday,
        )

        # Trace PO zapisie — padnięty eksport nie może zabrać wyniku.
        if do_modelu:
            wyslij_bezpiecznie(
                slad,
                lambda: zbuduj_trace_analizy(
                    **wspolne,
                    odpowiedz=odpowiedz_modelu,
                    przyjetych=len(walidacja.przyjete),
                    odrzucone_reguly=[o.regula for o in walidacja.odrzucone],
                ),
                opis=f"analiza {run_id}",
            )
    except BaseException as awaria:
        # Run, który padł, jest NAJCIEKAWSZY w trace'ach. Tylko przy `Exception`:
        # przy Ctrl-C człowiek chce przerwać, a nie czekać.
        if isinstance(awaria, Exception) and do_modelu:
            # Tekst liczony TU, nie w lambdzie: nazwa z `except ... as` znika po
            # wyjściu z bloku, a lambda odwołuje się do nazw leniwie.
            opis_awarii = f"{type(awaria).__name__}: {awaria}"[:500]
            # Przy odpowiedzi bez JSON-a trace niesie surowy tekst i narzędzia —
            # przez tę samą bramkę maskowania. To jedyne miejsce, w którym
            # da się potem zobaczyć, co model faktycznie oddał.
            czesciowa = awaria.odpowiedz_modelu if isinstance(awaria, AnalizaError) else None
            wyslij_bezpiecznie(
                slad,
                lambda: zbuduj_trace_analizy(**wspolne, odpowiedz=czesciowa, blad=opis_awarii),
                opis=f"analiza {run_id} (awaria)",
            )
        # `przerwany`, nie zostawiony w `w_toku` — wiersz wiszący w toku na
        # zawsze wygląda jak run, który wciąż trwa.
        trwala.execute(
            "UPDATE runy SET status = 'przerwany', finished_at = ? WHERE run_id = ?",
            (_teraz(), run_id),
        )
        trwala.commit()
        raise

    return dataclasses.replace(
        podstawa,
        run_id=run_id,
        uwagi=tuple(_zamaskowane(walidacja.przyjete)),
        pominietych=len(walidacja.pominiete),
        odrzuconych=len(walidacja.odrzucone),
        zuzycie=dict(zuzycie),
        sekund=sekund,
        zapisanych_uwag=zapisanych,
        blad_zapisu=blad_zapisu,
        raport_html=raport_html,
        odpowiedz_modelu=odpowiedz_modelu,
        walidacja=walidacja,
    )


def _nie_ludzie(con: sqlite3.Connection, snapshot_id: int) -> frozenset[str]:
    """Pseudonimy agentów AI ze snapshotu — ich nazwy nie są osobą."""
    wiersze = con.execute(
        "SELECT json_extract(o.value, '$.user_hash') AS h, json_extract(o.value, '$.kind') AS k "
        "FROM snapshots, json_each(snapshots.payload, '$.uzytkownicy.uzytkownicy') AS o "
        "WHERE snapshots.id = ?",
        (snapshot_id,),
    )
    return frozenset(w["h"] for w in wiersze if w["k"] in RODZAJE_AGENTOW and w["h"])


def _zredagowane_wejscie(
    wejscie: dict[str, Any], wpisy: Sequence[MaPII], nie_ludzie: frozenset[str]
) -> dict[str, Any]:
    """Obraz konta po redakcji osób — PRZED modelem.

    ## ZMIERZONE 2026-09-24: bramka `wejscie_analizy` nie zna nazwisk

    Obraz konta składa `cli_inwentarz` (albo portal), który świadomie nie
    pobiera `name` (faza 2a), więc jego bramka maskuje tylko wzorcem. Nazwa
    workspace'u „Radek Leady…" przeszła do modelu. Tutaj, w kroku 2, mapowanie
    osób JEST — collector właśnie je zebrał do pamięci — więc to jest miejsce,
    w którym obraz dostaje tę samą redakcję co snapshot.
    """
    if not wejscie:
        return {}
    zredagowane, sciezki = zredaguj_pii(wejscie, wpisy, nie_ludzie=nie_ludzie)
    if sciezki:
        logger.warning(
            "obraz konta: zredagowano osoby w %d miejscach przed modelem: %s",
            len(sciezki),
            ", ".join(sorted(sciezki)[:10]),
        )
    return dict(zredagowane)


def _sprawdz_strukture(odpowiedz_modelu: dict[str, Any]) -> None:
    if not isinstance(odpowiedz_modelu.get("uwagi"), list):
        raise AnalizaError("odpowiedź modelu nie ma listy `uwagi`", odpowiedz_modelu)


def _zamaskowane(przyjete: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Uwagi w postaci, którą wolno pokazać i przechować — jak `uwagi_zapisane`.

    Uwaga, której bramka nie przepuści, wypada z listy zamiast psuć całość —
    i tak nie trafiłaby do bazy, a raport z nazwiskami ma ją w całości.
    """
    wynik = []
    for uwaga in przyjete:
        try:
            wynik.append(uwaga_do_zapisu(uwaga))
        except PrzechowanieError:
            logger.exception("uwaga %s nie przeszła bramki przechowania", uwaga.get("klasa_id"))
    return wynik


def _raport_z_nazwiskami(
    walidacja: WynikUwag,
    *,
    zrodlo: sqlite3.Connection,
    client_id: str,
    run_id: str,
    rubryka: Rubryka,
    zastrzezenia: tuple[str, ...],
    snapshot_id: int,
    poza_sufitem: Sequence[PozaSufitem],
) -> str | None:
    """HTML z nazwiskami albo `None` — awaria renderowania nie zabiera wyniku."""
    try:
        raport = zbuduj_raport_uwag(
            walidacja.przyjete,
            con=zrodlo,
            client_id=client_id,
            run_id=run_id,
            run_at=_teraz(),
            rubryka=rubryka,
            pominietych=len(walidacja.pominiete),
            zastrzezenia=zastrzezenia,
            snapshot_id=snapshot_id,
            poza_sufitem=poza_sufitem,
        )
        return wyrenderuj_uwagi(raport)
    except Exception:  # raport nie jest wynikiem — wynik zostaje
        logger.exception("raport z nazwiskami NIE powstał — wynik i zapis idą dalej")
        return None


def _zapis_minimalny(
    trwala: sqlite3.Connection,
    run_id: str,
    walidacja: WynikUwag,
    *,
    zuzycie: dict[str, Any],
    wejscie: dict[str, Any],
    ile_do_modelu: int,
    wywolan_narzedzi: int,
    sekund: float,
    hipotez: int,
    wywolan_monday: int | None,
) -> tuple[int, str | None]:
    """Zapis wyłącznie przez `przechowanie.py`. Zwraca (zapisanych, błąd).

    Błąd zapisu wraca jako napis, nie wyjątek: wynik jest już policzony
    i opłacony, a wołający dostaje go razem z informacją, co nie wyszło.
    """
    blad: str | None = None
    zapisanych = 0
    try:
        zapisz_zuzycie(trwala, run_id, zuzycie)
        zapisz_zuzycie_analizy(
            trwala,
            run_id,
            zuzycie,
            ile_hipotez=ile_do_modelu,
            ile_uwag=len(walidacja.przyjete),
            wywolan_narzedzi=wywolan_narzedzi,
            sekund=sekund,
        )
        # Dwa osobne `try`, bo to dwa osobne zapisy (review 2026-09-23).
        try:
            zapisanych = zapisz_uwagi(trwala, run_id, walidacja.przyjete)
        except PrzechowanieError:
            logger.exception("uwagi NIE zapisane — bramka przechowania zadziałała")
            blad = "uwagi nie zapisane: bramka przechowania"
        if wejscie:
            try:
                zapisz_statystyki(trwala, run_id, wejscie)
            except PrzechowanieError:
                logger.exception("statystyki NIE zapisane — bramka przechowania zadziałała")
                blad = blad or "statystyki nie zapisane: bramka przechowania"
        trwala.execute(
            "UPDATE runy SET status = 'zakonczony', finished_at = ?, findingow = ?, "
            "odrzuconych_walidacja = ?, hipotez_zbadanych = ?, hipotez_odrzuconych = ?, "
            "wywolania_monday = ? WHERE run_id = ?",
            (
                _teraz(),
                zapisanych,
                len(walidacja.odrzucone),
                hipotez,
                len(walidacja.pominiete),
                wywolan_monday,
                run_id,
            ),
        )
        trwala.commit()
    except sqlite3.Error as awaria:
        logger.exception("zapis minimalny padł — wynik wraca mimo to")
        blad = f"zapis minimalny padł: {type(awaria).__name__}"
    return zapisanych, blad


__all__ = [
    "KAFELKI",
    "AnalizaError",
    "Kafelek",
    "PrzegladKonta",
    "SzacunekAnalizy",
    "UslugaError",
    "WynikAnalizy",
    "analiza_konta",
    "analizuj_snapshot",
    "przeglad_konta",
    "szacuj_analize",
]
