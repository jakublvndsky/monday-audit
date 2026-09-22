"""Rollupy produktowe: leady, szanse, tickety, zamknięcia (plan, faza 5a).

Ta warstwa NIE pyta modelu o nic. Zamienia rozkłady per tablica z fazy 3 na
liczby, na które model będzie mógł wskazać w fazie 5b. Kolejność jest celowa:
uwaga krytyczna bez liczby jest opinią.

## Reguła, która jest osądem, a nie odczytem

Faza 3 zebrała rozkłady, ale świadomie nie orzekała, **co liczy się jako
szansa, a co jako zamknięcie** — bo to nie wynika z API, tylko z nazw etapów,
które pisał klient. Reguła stoi tutaj i brzmi tak:

**Rozpoznajemy ETAPY KOŃCOWE, a nie otwarte.** Item, którego etapu nie
rozpoznaliśmy jako końcowego, liczy się jako będący w toku.

Kierunek jest odwrotny do intuicyjnego i ma powód. Słownictwo końca lejka jest
małe i powtarzalne („won", „lost", „unqualified", „odrzucone"), a słownictwo
środka lejka jest dowolne — klient nazywa etapy jak chce („Contacted",
„Do oddzwonienia", „Po demo"). Gdybyśmy rozpoznawali otwarte, prawie wszystko
lądowałoby w „nie wiem" i zestawienie byłoby bezużyteczne.

Cena tego wyboru, nazwana wprost: **nierozpoznany etap końcowy zawyża szanse.**
Dlatego `Zestawienie` niesie `etykiety_w_toku` — pełną listę etykiet
policzonych jako otwarte. Człowiek rzuca na nią okiem i w dziesięć sekund widzi,
czy wśród nich nie siedzi „Archiwum".

## Czego ta warstwa nie ukrywa

Tablica, która deklaruje itemy i nie oddaje żadnego (O47), nie ma rozkładu.
Jej itemy są policzone w `itemow_deklarowanych`, ale **nie wchodzą do żadnego
kubełka** — i zestawienie mówi o tym osobno, zamiast dodać zero i udawać
komplet.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

PRODUKT_CRM = "crm"
PRODUKT_SERVICE = "service"

# ── słownik etapów końcowych ─────────────────────────────────────────────
#
# SŁOWA KLUCZOWE, nie całe etykiety — i to jest poprawka wymuszona przez dane,
# nie upiększenie. Pierwsza wersja porównywała etykietę w całości i przepuściła
# `Brand Duplicate` (123 itemy na `Leads e-commerce`, ZMIERZONE w fazie 3) jako
# szansę otwartą, bo w słowniku stało samo `duplicate`. Etykiety u klientów są
# wielosłowne: `Closed Won`, `Lead Unqualified`, `Brand Duplicate`.
#
# Dopasowanie po GRANICACH SŁÓW, nie po podciągu — ta sama lekcja co
# w `zredaguj_pii`: „won" jako podciąg wchodzi w „Wonderful Leads".
#
# Polskie i angielskie razem, bo konta bywają mieszane: ta sama tablica ma
# „Qualified" i „Do oddzwonienia". Rozdzielanie na języki dawałoby wybór,
# którego nie ma jak dokonać.
WYGRANE = frozenset(
    {
        "won",
        "wygrana",
        "wygrane",
        "podpisana",
        "podpisane",
        "sprzedane",
        "converted",
    }
)

ODPADLO = frozenset(
    {
        "lost",
        "przegrana",
        "przegrane",
        "unqualified",
        "disqualified",
        "niezakwalifikowany",
        "niezakwalifikowane",
        "odrzucony",
        "odrzucone",
        "odrzucona",
        "duplicate",
        "duplikat",
        "spam",
        "rezygnacja",
        "nieaktualne",
        "nieaktualny",
        "archiwum",
        "archived",
        "anulowane",
        "cancelled",
        "canceled",
    }
)

# Zamknięcie bez orzekania o wyniku. „Resolved" nie jest ani wygraną, ani
# przegraną — wrzucenie go do wygranych robiłoby z obsługi zgłoszeń dział
# sprzedaży.
ZAMKNIETE = frozenset(
    {
        "done",
        "closed",
        "resolved",
        "zamkniete",
        "zamknięte",
        "rozwiazane",
        "rozwiązane",
        "zakonczone",
        "zakończone",
        "gotowe",
        "wykonane",
    }
)

# Etap „bez etapu" produkuje `policz_rozklad`, gdy kolumna lejka jest pusta.
# To NIE jest etap końcowy — item bez etapu leży poza lejkiem i ma być widoczny
# jako osobny problem, a nie doliczony do czegokolwiek.
BEZ_ETAPU = "(bez etapu)"

_NIESLOWA = re.compile(r"[^\w]+", re.UNICODE)


def znormalizuj(etykieta: str) -> str:
    """Etykieta etapu do porównania ze słownikiem.

    Małe litery i pojedyncze spacje. `Closed-Won`, `CLOSED WON` i `closed  won`
    to ten sam etap i klient zapisze go na każdy z tych sposobów.
    """
    return _NIESLOWA.sub(" ", etykieta).strip().lower()


def sklasyfikuj(etykieta: str, zadeklarowane_koncowe: frozenset[str] = frozenset()) -> str:
    """Etap → `wygrane` / `odpadlo` / `zamkniete` / `bez_etapu` / `w_toku`.

    ## Dwa źródła, w jasnej hierarchii (O48)

    `zadeklarowane_koncowe` to etykiety, które **klient sam oznaczył** jako
    kończące proces (`done_colors` w ustawieniach kolumny). Deklaracja bije
    słownik: skoro klient powiedział, że `Branding Completed` kończy proces, to
    kończy, niezależnie od tego, czy nasza lista słów go zna.

    **Deklaracja nie zastępuje słownika, tylko go uzupełnia**, i to z konkretnego
    powodu: `done_colors` w monday znaczy „zakończone pomyślnie" — zielony
    znaczek. Etap `Lost` prawie nigdy nie jest tam wpisany, bo przegrana nie
    jest sukcesem. Gdyby deklaracja była jedynym źródłem, cała strona odpadów
    lądowałaby w „w toku" i zestawienie zawyżałoby szanse dokładnie tam, gdzie
    najbardziej boli.

    Dlatego: deklaracja rozstrzyga, ŻE etap jest końcowy, a słownik dopowiada,
    JAKI to koniec. Etap zadeklarowany, którego słownik nie zna, to `zamkniete` —
    neutralne domknięcie, bez orzekania o wyniku.

    Kolejność w samym słowniku też nie jest dowolna: `Closed Won` niesie
    jednocześnie słowo zamknięcia i słowo wygranej, więc wygrana musi iść
    pierwsza. Odwrotna kolejność zamieniłaby każdą wygraną w bezbarwne
    „zamknięte" i zabrała raportowi jedyną liczbę mówiącą o skuteczności.
    """
    czysta = znormalizuj(etykieta)
    if not czysta or czysta == znormalizuj(BEZ_ETAPU):
        return "bez_etapu"

    slowa = set(czysta.split())
    if slowa & WYGRANE:
        return "wygrane"
    if slowa & ODPADLO:
        return "odpadlo"
    if slowa & ZAMKNIETE:
        return "zamkniete"
    # Słownik nie zna tej etykiety. Dopiero teraz pytamy o deklarację —
    # porównanie po znormalizowanej postaci, bo klient zapisuje etykietę
    # w ustawieniach i w itemie tak samo, ale wielkość liter bywa różna.
    if any(znormalizuj(e) == czysta for e in zadeklarowane_koncowe):
        return "zamkniete"
    return "w_toku"


@dataclass(frozen=True)
class Zestawienie:
    """Rollup dla jednego produktu. Liczby plus to, czego nie objęły."""

    produkt: str
    tablic: int
    # Ile itemów deklaruje `items_count` — łącznie, także na tablicach, które
    # nie oddały ani jednego (O47).
    itemow_deklarowanych: int
    # Ile faktycznie weszło do rozkładów. Różnica jest w `itemow_bez_rozkladu`.
    itemow_w_rozkladach: int
    w_toku: int = 0
    wygrane: int = 0
    odpadlo: int = 0
    zamkniete: int = 0
    bez_etapu: int = 0
    # Etykiety policzone jako otwarte — do kontroli wzrokowej, patrz docstring.
    etykiety_w_toku: tuple[str, ...] = ()
    przyrost_dzienny: float = 0.0
    zamkniec_dziennie: float = 0.0

    @property
    def itemow_bez_rozkladu(self) -> int:
        """Itemy zadeklarowane, których nie objął żaden kubełek (O47)."""
        return max(0, self.itemow_deklarowanych - self.itemow_w_rozkladach)

    @property
    def pokrycie(self) -> float:
        """Jaka część zadeklarowanych itemów weszła do rozkładów."""
        if not self.itemow_deklarowanych:
            return 1.0
        return round(self.itemow_w_rozkladach / self.itemow_deklarowanych, 3)

    def do_json(self) -> dict[str, Any]:
        return {
            "produkt": self.produkt,
            "tablic": self.tablic,
            "itemow_deklarowanych": self.itemow_deklarowanych,
            "itemow_w_rozkladach": self.itemow_w_rozkladach,
            "itemow_bez_rozkladu": self.itemow_bez_rozkladu,
            "pokrycie": self.pokrycie,
            "w_toku": self.w_toku,
            "wygrane": self.wygrane,
            "odpadlo": self.odpadlo,
            "zamkniete": self.zamkniete,
            "bez_etapu": self.bez_etapu,
            "etykiety_w_toku": list(self.etykiety_w_toku),
            "przyrost_dzienny": self.przyrost_dzienny,
            "zamkniec_dziennie": self.zamkniec_dziennie,
        }


@dataclass(frozen=True)
class WynikZestawien:
    zestawienia: tuple[Zestawienie, ...] = ()
    zastrzezenia: tuple[str, ...] = ()

    @property
    def po_produkcie(self) -> dict[str, Zestawienie]:
        return {z.produkt: z for z in self.zestawienia}

    def do_json(self) -> dict[str, Any]:
        return {
            "zestawienia": [z.do_json() for z in self.zestawienia],
            "zastrzezenia": list(self.zastrzezenia),
        }


def _udzial_zamkniec(agregat: Any, zamykajace: int, wszystkich_w_rozkladzie: int) -> float:
    """Zamknięcia dziennie = przyrost × udział etapów końcowych.

    To SZACUNEK z ilorazu, nie pomiar, i tak ma być nazwany. Prawdziwy pomiar
    wymagałby dat przejścia między etapami — a `column_values` oddaje `text`
    etapu BIEŻĄCEGO, nie jego historię. Zejście po historię to `activity_logs`
    per tablica, czyli koszt rzędu jednego wywołania na tablicę na stronę.
    """
    if not wszystkich_w_rozkladzie:
        return 0.0
    return round(agregat.przyrost_dzienny * (zamykajace / wszystkich_w_rozkladzie), 2)


def zbuduj_zestawienia(
    agregaty: Sequence[Any],
    *,
    produkty: Iterable[str] = (PRODUKT_CRM, PRODUKT_SERVICE),
) -> WynikZestawien:
    """Agregaty per tablica (faza 3) → rollupy per produkt.

    Domyślnie liczymy CRM i obsługę zgłoszeń, bo tylko tam etapy znaczą coś
    wspólnego. `software` i `core` mają itemy, ale „szansa sprzedaży" na
    tablicy projektowej to pojęcie, którego nikt nie obroni — więc go nie
    produkujemy, zamiast produkować i opatrywać gwiazdką.
    """
    zestawienia: list[Zestawienie] = []
    zastrzezenia: list[str] = []

    for produkt in produkty:
        nasze = [a for a in agregaty if a.produkt == produkt]
        if not nasze:
            continue

        kubelki = {"w_toku": 0, "wygrane": 0, "odpadlo": 0, "zamkniete": 0, "bez_etapu": 0}
        etykiety: dict[str, None] = {}
        deklarowanych = 0
        w_rozkladach = 0
        przyrost = 0.0
        zamkniec = 0.0

        for agregat in nasze:
            deklarowanych += agregat.itemow
            przyrost += agregat.przyrost_dzienny
            w_tablicy = 0
            zamykajacych = 0
            koncowe: frozenset[str] = getattr(agregat.lejek, "etapy_koncowe", frozenset())
            for etykieta, ile in agregat.rozklad.items():
                kubelek = sklasyfikuj(etykieta, koncowe)
                kubelki[kubelek] += ile
                w_tablicy += ile
                if kubelek == "w_toku":
                    etykiety.setdefault(etykieta, None)
                elif kubelek != "bez_etapu":
                    zamykajacych += ile
            w_rozkladach += w_tablicy
            zamkniec += _udzial_zamkniec(agregat, zamykajacych, w_tablicy)

        zestawienie = Zestawienie(
            produkt=produkt,
            tablic=len(nasze),
            itemow_deklarowanych=deklarowanych,
            itemow_w_rozkladach=w_rozkladach,
            etykiety_w_toku=tuple(sorted(etykiety)),
            przyrost_dzienny=round(przyrost, 2),
            zamkniec_dziennie=round(zamkniec, 2),
            **kubelki,
        )
        zestawienia.append(zestawienie)

        if zestawienie.itemow_bez_rozkladu:
            zastrzezenia.append(
                f"{produkt}: {zestawienie.itemow_bez_rozkladu} itemów zadeklarowanych przez "
                f"`items_count` nie weszło do żadnego kubełka — te tablice nie oddały "
                f"rozkładu (O47). Pokrycie {zestawienie.pokrycie:.1%}"
            )
        if zestawienie.bez_etapu:
            zastrzezenia.append(
                f"{produkt}: {zestawienie.bez_etapu} itemów leży POZA lejkiem "
                "(kolumna etapu pusta) — nie są ani w toku, ani zamknięte"
            )

    # Zastrzeżenie o samej regule, zawsze. Czytający ma wiedzieć, na czym stoi
    # liczba „szanse otwarte", nawet gdy wszystko inne wyszło czysto.
    if zestawienia:
        zastrzezenia.append(
            "reguła: rozpoznajemy etapy KOŃCOWE, a item w etapie nierozpoznanym liczy się "
            "jako w toku — więc nierozpoznany etap końcowy ZAWYŻA liczbę otwartych. "
            "Lista etykiet policzonych jako otwarte jest w `etykiety_w_toku`"
        )

    return WynikZestawien(zestawienia=tuple(zestawienia), zastrzezenia=tuple(zastrzezenia))
