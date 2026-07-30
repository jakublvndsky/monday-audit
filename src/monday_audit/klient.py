"""Klient GraphQL monday.com (etap 3.2).

Fundament collectora. Czysty `httpx`, bez MCP — collector potrzebuje
paginacji, budżetowania, retry i logowania każdego zapytania, a MCP
właśnie tę warstwę abstrahuje (D4).

Pięć własności, których nie wolno stąd usunąć:

1. **Complexity w każdym zapytaniu.** Klient sam wstawia pole
   `complexity { query after reset_in_x_seconds }` do korzenia zapytania.
   To jedyny sposób, żeby wiedzieć, ile faktycznie kosztujemy klienta.
2. **Twardy licznik wywołań.** Przekroczenie budżetu rzuca wyjątek, nie
   loguje ostrzeżenia. Limit dzienny jest limitem KONTA KLIENTA — jego
   wyczerpanie spowalnia integracje klienta w środku dnia roboczego.
3. **Rozdział błędów.** Limit chwilowy → ponawiamy z wykładniczym backoffem
   i pełnym jitterem. Błąd zapytania albo limit dzienny → nie ponawiamy,
   bo powtórzenie da ten sam wynik, a zje kolejne wywołanie z limitu.
4. **Każde wywołanie do tabeli `wywolania`.** Także nieudane — one też
   zjadają limit klienta (D10).
5. **Rozłożenie complexity w czasie.** Zmierzone na koncie CXLABS: strona
   25 tablic z pełnym zestawem pól z 3.5 kosztuje ~128 tys. complexity,
   więc pełny przelot po ~1 900 aktywnych tablicach to ~9,8 mln — dwa razy
   więcej niż limit 5 mln na minutę. Klient czeka na reset okna, zanim
   zapas się wyczerpie, zamiast dostać `ComplexityException` w połowie
   zbierania i zostawić niekompletny snapshot.

Token żyje w nagłówkach tego klienta i nigdzie więcej. Nie trafia do logów,
komunikatów wyjątków ani `repr()` — bo te potrafią dojechać do kontekstu
modelu, a to granica z D6.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

URL_API = "https://api.monday.com/v2"

POLE_COMPLEXITY = "complexity { query after reset_in_x_seconds }"

# Limity chwilowe (minuta, complexity, współbieżność) — ponawiamy.
_WZORCE_PRZEJSCIOWE = (
    "complexity",
    "rate limit exceeded",
    "ratelimitexceeded",
    "minuteratelimitexceeded",
    "concurrency",
    "too many requests",
)

# Limit dzienny — NIE ponawiamy. Reset przychodzi po godzinach, nie po sekundach,
# a każda kolejna próba to kolejne wywołanie zabrane klientowi.
_WZORCE_DZIENNE = (
    "daily_limit_exceeded",
    "dailylimitexceeded",
    "daily limit",
)

_BEZ_KOMENTARZY = re.compile(r"#[^\n]*")


class MondayError(RuntimeError):
    """Baza dla błędów klienta monday."""


class ZapytanieError(MondayError):
    """Błąd zapytania: złe pole, brak uprawnień, zły JSON.

    **Nie ponawiamy.** Powtórzenie da ten sam wynik.
    """


class PrzejsciowyError(MondayError):
    """Limit chwilowy albo błąd sieci. Ponawiamy z backoffem."""

    def __init__(self, komunikat: str, retry_after: float | None = None) -> None:
        super().__init__(komunikat)
        self.retry_after = retry_after


class LimitDziennyError(MondayError):
    """Dzienny limit wywołań konta klienta wyczerpany. Run trzeba przerwać."""


class BudzetWyczerpanyError(MondayError):
    """Nasz własny bezpiecznik zadziałał przed limitem klienta."""


class PaginacjaError(MondayError):
    """Paginacja nie domknęła się w spodziewanej liczbie stron."""


class Rejestr(Protocol):
    """Odbiorca wpisów do tabeli `wywolania`.

    Klient definiuje interfejs, którego potrzebuje, a nie importuje warstwy
    bazy — implementacja siedzi w `monday_audit.baza.RejestrWywolan`.
    """

    def zapisz(
        self,
        *,
        narzedzie: str,
        latency_ms: int | None = None,
        complexity: int | None = None,
        hipoteza_id: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class Postep:
    """Stan collectora po jednym kroku — wejście dla wskaźnika postępu.

    Collector potrafi zbierać kilka minut (paginacja setek stron plus pauzy
    na reset complexity). Bez tego run wygląda jak zawieszony, a wtedy
    człowiek go przerywa w połowie i zostaje niekompletny snapshot.

    Dane, nie tekst: `opis()` daje gotową linię, ale renderer decyduje,
    co z nią zrobić — konsola teraz, FastAPI w etapie 5.
    """

    narzedzie: str
    wywolania: int
    budzet: int
    complexity_suma: int
    complexity_pozostalo: int | None = None
    strona: int | None = None
    zebrane: int | None = None
    czekanie_s: float | None = None

    def opis(self) -> str:
        czesci = [
            f"{self.wywolania}/{self.budzet} wywołań",
            f"complexity {_grupuj(self.complexity_suma)}",
        ]
        if self.complexity_pozostalo is not None:
            czesci.append(f"zapas {_grupuj(self.complexity_pozostalo)}")
        if self.strona is not None:
            czesci.append(f"strona {self.strona}")
        if self.zebrane is not None:
            czesci.append(f"zebranych {self.zebrane}")
        if self.czekanie_s:
            czesci.append(f"PAUZA {self.czekanie_s:.0f} s na reset complexity")
        return f"{self.narzedzie}: {', '.join(czesci)}"


def _narzedzie(etykieta: str | None) -> str:
    """Wartość kolumny `wywolania.narzedzie` — etap 6 pyta po niej o koszty."""
    return f"graphql:{etykieta}" if etykieta else "graphql"


def _grupuj(liczba: int) -> str:
    """9783000 → „9 783 000". Te liczby czyta człowiek, nie parser.

    Separatorem jest TWARDA SPACJA (U+00A0), zgodnie z polską typografią —
    liczba nie łamie się na końcu linii. Nie zamieniaj jej na zwykłą spację.
    """
    return f"{liczba:,}".replace(",", " ")


def _pozycja_selekcji(gql: str) -> int:
    """Zwraca indeks `{` otwierającego korzeń zapytania.

    Naiwne `gql.find("{")` trafiłoby w nawias obiektu w wartości domyślnej
    argumentu (`query ($f: JSON = {a: 1})`), więc skaner pomija stringi,
    komentarze i wszystko wewnątrz `(` oraz `[`.
    """
    glebokosc = 0
    i = 0
    dlugosc = len(gql)

    while i < dlugosc:
        znak = gql[i]

        if znak == "#":
            koniec = gql.find("\n", i)
            i = dlugosc if koniec == -1 else koniec + 1
            continue

        if znak == '"':
            if gql.startswith('"""', i):
                koniec = gql.find('"""', i + 3)
                if koniec == -1:
                    raise ZapytanieError("niedomknięty blok string w zapytaniu")
                i = koniec + 3
            else:
                i += 1
                while i < dlugosc and gql[i] != '"':
                    i += 2 if gql[i] == "\\" else 1
                i += 1
            continue

        if znak in "([":
            glebokosc += 1
        elif znak in ")]":
            glebokosc -= 1
        elif znak == "{" and glebokosc == 0:
            return i

        i += 1

    raise ZapytanieError("zapytanie bez zbioru selekcji — brak `{` na poziomie korzenia")


def przygotuj_zapytanie(gql: str) -> str:
    """Wstawia pole `complexity` do korzenia zapytania i pilnuje, że to odczyt.

    Zapytanie, które już pyta o complexity, zostaje bez zmian — nie dublujemy
    pola, bo suma complexity liczona przez klienta przestałaby się zgadzać.
    """
    poz = _pozycja_selekcji(gql)

    # Collector czyta i nic więcej. To nie jest polityka do obejścia promptem,
    # tylko warunek na wejściu do jedynej ścieżki, którą collector gada z API.
    naglowek = _BEZ_KOMENTARZY.sub("", gql[:poz]).lower()
    if "mutation" in naglowek or "subscription" in naglowek:
        raise ZapytanieError(
            "collector wyłącznie czyta — mutacje i subskrypcje są tu zakazane (D6)"
        )

    if "complexity" in gql:
        return gql

    return f"{gql[: poz + 1]}\n  {POLE_COMPLEXITY}{gql[poz + 1 :]}"


def _retry_after(odpowiedz: httpx.Response) -> float | None:
    surowy = odpowiedz.headers.get("retry-after")
    if not surowy:
        return None
    try:
        return max(0.0, float(surowy))
    except ValueError:
        # Nagłówek w formacie daty HTTP. Nie parsujemy — jitter wystarczy.
        return None


def _tekst_bledow(cialo: Mapping[str, Any]) -> str:
    """Skleja opis błędów z odpowiedzi 200. monday używa kilku kształtów naraz."""
    czesci: list[str] = []

    bledy = cialo.get("errors")
    if isinstance(bledy, list):
        czesci += [
            str(wpis.get("message") or wpis) if isinstance(wpis, Mapping) else str(wpis)
            for wpis in bledy
        ]
    elif isinstance(bledy, str):
        czesci.append(bledy)

    for klucz in ("error_code", "error_message", "errorMessage"):
        wartosc = cialo.get(klucz)
        if wartosc:
            czesci.append(f"{klucz}={wartosc}")

    return "; ".join(czesci)[:500]


def _rozpakuj(odpowiedz: httpx.Response) -> dict[str, Any]:
    """Zwraca `data` albo rzuca błąd właściwej kategorii."""
    if odpowiedz.status_code == 429:
        raise PrzejsciowyError(
            "HTTP 429 — limit zapytań na minutę albo współbieżności",
            _retry_after(odpowiedz),
        )
    if odpowiedz.status_code >= 500:
        raise PrzejsciowyError(f"HTTP {odpowiedz.status_code} — błąd po stronie monday")
    if odpowiedz.status_code != 200:
        raise ZapytanieError(f"HTTP {odpowiedz.status_code}: {odpowiedz.text[:300]}")

    try:
        cialo = odpowiedz.json()
    except ValueError as blad:
        raise ZapytanieError(f"odpowiedź nie jest JSON-em: {odpowiedz.text[:300]}") from blad
    if not isinstance(cialo, dict):
        raise ZapytanieError(f"odpowiedź nie jest obiektem JSON: {odpowiedz.text[:300]}")

    # GraphQL nie zwraca 4xx przy błędzie zapytania. Odpowiedź 200 z tablicą
    # `errors` to najczęstsza pułapka tego API (skill monday-graphql).
    opis = _tekst_bledow(cialo)
    if opis:
        maly = opis.lower()
        if any(wzorzec in maly for wzorzec in _WZORCE_DZIENNE):
            raise LimitDziennyError(f"dzienny limit wywołań konta klienta wyczerpany: {opis}")
        if any(wzorzec in maly for wzorzec in _WZORCE_PRZEJSCIOWE):
            raise PrzejsciowyError(f"limit chwilowy: {opis}", _retry_after(odpowiedz))
        # Nieznany błąd traktujemy jako błąd zapytania, czyli BEZ ponowienia.
        # Ponowienie w najlepszym razie nic nie da, w najgorszym zje limit klienta.
        raise ZapytanieError(opis)

    dane = cialo.get("data")
    if not isinstance(dane, dict):
        raise ZapytanieError(f"odpowiedź bez pola `data`: {str(cialo)[:300]}")

    # Świadomie nie wpuszczamy częściowych danych: `errors` obok `data` kończy
    # się wyjątkiem powyżej. Niepełny audyt udający pełny jest gorszy od braku.
    return dane


def _wyluskaj(dane: Mapping[str, Any], sciezka: str) -> list[Any]:
    """Wyciąga listę z odpowiedzi po ścieżce z kropkami, np. `boards`."""
    biezacy: Any = dane
    for krok in sciezka.split("."):
        if not isinstance(biezacy, Mapping) or krok not in biezacy:
            raise PaginacjaError(f"ścieżka `{sciezka}` nie istnieje w odpowiedzi")
        biezacy = biezacy[krok]

    if not isinstance(biezacy, list):
        raise PaginacjaError(f"ścieżka `{sciezka}` nie wskazuje na listę: {type(biezacy).__name__}")
    return biezacy


class MondayClient:
    """Jedyna droga collectora do API monday.

    Parametry:
        token: token read-only admina konta klienta (D11). Nie jest logowany.
        rejestr: odbiorca wpisów do `wywolania`. Wymaga otwartego runu.
        budzet_wywolan: twardy limit wywołań na cały cykl życia klienta.
            Domyślne 400 to ~40% dziennego limitu planu Pro. Po rozpoznaniu
            planu (3.3) podnieś albo obniż przez `ustaw_budzet()`.
        wersja_api: wartość nagłówka `API-Version`. `None` = wersja domyślna
            konta. Przypięcie wersji jest wymogiem odtwarzalności z etapu 5,
            ale konkretny numer wymaga potwierdzenia empirycznego (3.8).
        postep: wywoływany po każdym kroku. `None` = cisza.
        margines_complexity: mnożnik zapasu. Czekamy na reset, gdy pozostały
            zapas nie pokrywa szacowanego kosztu razy tyle. Szacunek bierzemy
            z najdroższego dotychczasowego zapytania o tej samej etykiecie,
            bo koszt strony `boards` jest stały, a `me` tani.
    """

    def __init__(
        self,
        token: str,
        rejestr: Rejestr,
        *,
        budzet_wywolan: int = 400,
        wersja_api: str | None = None,
        maks_prob: int = 4,
        baza_czekania: float = 1.0,
        maks_czekanie: float = 60.0,
        postep: Callable[[Postep], None] | None = None,
        margines_complexity: float = 1.5,
        zapas_po_resecie: float = 1.0,
        timeout: httpx.Timeout | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if maks_prob < 1:
            raise ValueError("maks_prob musi być co najmniej 1")

        self.budzet_wywolan = budzet_wywolan
        self.liczba_wywolan = 0
        self.complexity_suma = 0

        self._rejestr = rejestr
        self._maks_prob = maks_prob
        self._baza_czekania = baza_czekania
        self._maks_czekanie = maks_czekanie
        self._postep = postep
        self._margines_complexity = margines_complexity
        self._zapas_po_resecie = zapas_po_resecie

        # Stan okna complexity, odtwarzany z każdej odpowiedzi.
        self._complexity_pozostalo: int | None = None
        self._complexity_reset_at: float | None = None
        self._koszt_wg_narzedzia: dict[str, int] = {}

        naglowki = {"Authorization": token, "Content-Type": "application/json"}
        if wersja_api:
            naglowki["API-Version"] = wersja_api
        logger.info("klient monday gotowy, wersja API: %s", wersja_api or "domyślna konta")

        self._http = httpx.AsyncClient(
            headers=naglowki,
            timeout=timeout or httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            transport=transport,
        )

    def __repr__(self) -> str:
        # Bez tokena. `repr` trafia do komunikatów wyjątków i logów, a te
        # potrafią dojechać do kontekstu modelu (D6).
        return (
            f"MondayClient(wywolania={self.liczba_wywolan}/{self.budzet_wywolan}, "
            f"complexity_suma={self.complexity_suma})"
        )

    async def __aenter__(self) -> MondayClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.zamknij()

    async def zamknij(self) -> None:
        await self._http.aclose()

    def ustaw_budzet(self, budzet: int) -> None:
        """Zmienia limit wywołań w trakcie runu — po rozpoznaniu planu (3.3)."""
        if budzet < self.liczba_wywolan:
            raise BudzetWyczerpanyError(
                f"nowy budżet {budzet} jest niższy od liczby już wykonanych wywołań "
                f"({self.liczba_wywolan})"
            )
        logger.info("budżet wywołań: %d → %d", self.budzet_wywolan, budzet)
        self.budzet_wywolan = budzet

    def _zajmij_wywolanie(self) -> None:
        if self.liczba_wywolan >= self.budzet_wywolan:
            raise BudzetWyczerpanyError(
                f"budżet {self.budzet_wywolan} wywołań wyczerpany — przerywam, "
                f"żeby nie zjeść dziennego limitu konta klienta"
            )
        self.liczba_wywolan += 1

    def _czekanie(self, proba: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self._maks_czekanie)

        gorna = min(self._maks_czekanie, self._baza_czekania * 2**proba)
        # Pełny jitter: losujemy z CAŁEGO przedziału [0, gorna]. Stały delay
        # albo wąski jitter zsynchronizowałby ponowienia równoległych zapytań
        # i uderzył w limit ponownie w tej samej sekundzie.
        # S311: to rozsuwanie ponowień w czasie, nie kryptografia.
        return random.uniform(0, gorna)  # noqa: S311

    def _zglos(
        self,
        narzedzie: str,
        *,
        strona: int | None = None,
        zebrane: int | None = None,
        czekanie_s: float | None = None,
    ) -> None:
        if self._postep is None:
            return
        self._postep(
            Postep(
                narzedzie=narzedzie,
                wywolania=self.liczba_wywolan,
                budzet=self.budzet_wywolan,
                complexity_suma=self.complexity_suma,
                complexity_pozostalo=self._complexity_pozostalo,
                strona=strona,
                zebrane=zebrane,
                czekanie_s=czekanie_s,
            )
        )

    async def _przetrzymaj_complexity(self, narzedzie: str) -> None:
        """Czeka na reset okna, jeśli zapas nie pokryje następnego zapytania.

        Limit complexity jest minutowy i odnawialny — w przeciwieństwie do
        dziennego limitu wywołań, którego nie da się odczekać. Dlatego tutaj
        pauza jest właściwą reakcją, a przy limicie dziennym przerwanie.

        Stan zerujemy PRZED czekaniem, więc jedna próba czeka najwyżej raz.
        Bez tego wystarczyłby jeden zły szacunek, żeby zrobić pętlę.
        """
        if self._complexity_pozostalo is None or self._complexity_reset_at is None:
            return

        szacunek = self._koszt_wg_narzedzia.get(narzedzie) or max(
            self._koszt_wg_narzedzia.values(), default=0
        )
        if not szacunek or self._complexity_pozostalo >= szacunek * self._margines_complexity:
            return

        czekanie = max(0.0, self._complexity_reset_at - time.monotonic()) + self._zapas_po_resecie
        pozostalo = self._complexity_pozostalo
        self._complexity_pozostalo = None
        self._complexity_reset_at = None
        if czekanie <= 0:
            return

        logger.warning(
            "zapas complexity %d nie pokrywa zapytania %s (~%d) — czekam %.1f s na reset okna",
            pozostalo,
            narzedzie,
            szacunek,
            czekanie,
        )
        self._zglos(narzedzie, czekanie_s=czekanie)
        await asyncio.sleep(czekanie)

    def _policz_complexity(self, complexity: Any, narzedzie: str) -> int | None:
        if not isinstance(complexity, Mapping):
            logger.warning("wywołanie %s nie zwróciło complexity", narzedzie)
            return None

        koszt = complexity.get("query")
        if not isinstance(koszt, int):
            logger.warning("wywołanie %s: complexity bez pola `query`", narzedzie)
            return None

        pozostalo = complexity.get("after")
        reset_za = complexity.get("reset_in_x_seconds")
        self.complexity_suma += koszt
        self._koszt_wg_narzedzia[narzedzie] = max(koszt, self._koszt_wg_narzedzia.get(narzedzie, 0))
        self._complexity_pozostalo = pozostalo if isinstance(pozostalo, int) else None
        self._complexity_reset_at = (
            time.monotonic() + reset_za if isinstance(reset_za, int | float) else None
        )

        logger.info(
            "wywołanie %s: complexity %d, pozostało %s, reset za %s s",
            narzedzie,
            koszt,
            pozostalo,
            reset_za,
        )
        return koszt

    async def query(
        self,
        gql: str,
        variables: Mapping[str, Any] | None = None,
        *,
        etykieta: str | None = None,
    ) -> dict[str, Any]:
        """Wykonuje jedno zapytanie i zwraca `data` bez pola `complexity`.

        `etykieta` idzie do kolumny `wywolania.narzedzie` jako `graphql:<etykieta>`,
        żeby etap 6 umiał odpowiedzieć, które zapytanie jest drogie.
        """
        tresc = przygotuj_zapytanie(gql)
        narzedzie = _narzedzie(etykieta)
        ladunek = {"query": tresc, "variables": dict(variables or {})}
        ostatni: PrzejsciowyError | None = None

        for proba in range(self._maks_prob):
            await self._przetrzymaj_complexity(narzedzie)
            # Licznik zajmuje wywołanie PRZED wysłaniem i liczy też ponowienia:
            # ponowienie zjada limit klienta tak samo jak pierwsza próba.
            self._zajmij_wywolanie()
            start = time.monotonic()

            try:
                odpowiedz = await self._http.post(URL_API, json=ladunek)
                dane = _rozpakuj(odpowiedz)
            except httpx.RequestError as blad:
                # Timeout, DNS, zerwane połączenie — nie wiemy, czy zapytanie
                # doszło, więc traktujemy jak przejściowe i ponawiamy.
                ostatni = PrzejsciowyError(f"błąd połączenia: {type(blad).__name__}")
                self._zapisz_nieudane(narzedzie, start)
            except PrzejsciowyError as blad:
                ostatni = blad
                self._zapisz_nieudane(narzedzie, start)
            except MondayError:
                # Błąd zapytania albo limit dzienny — bez ponowienia.
                self._zapisz_nieudane(narzedzie, start)
                raise
            else:
                koszt = self._policz_complexity(dane.pop("complexity", None), narzedzie)
                self._rejestr.zapisz(
                    narzedzie=narzedzie,
                    latency_ms=_ms(start),
                    complexity=koszt,
                )
                self._zglos(narzedzie)
                return dane

            if proba + 1 < self._maks_prob:
                czekanie = self._czekanie(proba, ostatni.retry_after)
                logger.warning(
                    "wywołanie %s: %s — ponawiam za %.1f s (próba %d z %d)",
                    narzedzie,
                    ostatni,
                    czekanie,
                    proba + 1,
                    self._maks_prob,
                )
                await asyncio.sleep(czekanie)

        raise PrzejsciowyError(
            f"wywołanie {narzedzie} nie udało się po {self._maks_prob} próbach: {ostatni}"
        ) from ostatni

    def _zapisz_nieudane(self, narzedzie: str, start: float) -> None:
        """Wpis nieudanej próby.

        KONWENCJA: `complexity IS NULL` w tabeli `wywolania` znaczy próbę
        nieudaną — schemat D7 nie ma kolumny na status, a udany odczyt zawsze
        wraca z complexity, bo klient wstawia to pole do każdego zapytania.
        """
        self._rejestr.zapisz(narzedzie=narzedzie, latency_ms=_ms(start))

    async def paginate(
        self,
        gql: str,
        sciezka: str,
        variables: Mapping[str, Any] | None = None,
        *,
        zmienna_strony: str = "p",
        od_strony: int = 1,
        maks_stron: int = 200,
        etykieta: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Przechodzi strony, dokładając `$p` do zmiennych, i yielduje elementy.

        Kończy na pierwszej pustej stronie. `maks_stron` jest bezpiecznikiem:
        zapytanie, które ignoruje `page`, kręciłoby się w kółko i zjadło dzienny
        limit klienta. Przekroczenie rzuca wyjątek, bo cicho ucięty audyt
        wygląda jak kompletny.
        """
        zebrane = 0
        for numer in range(od_strony, od_strony + maks_stron):
            zmienne = {**(variables or {}), zmienna_strony: numer}
            dane = await self.query(gql, zmienne, etykieta=etykieta)
            partia = _wyluskaj(dane, sciezka)

            if not partia:
                self._zglos(_narzedzie(etykieta), strona=numer, zebrane=zebrane)
                return

            zebrane += len(partia)
            self._zglos(_narzedzie(etykieta), strona=numer, zebrane=zebrane)
            for element in partia:
                yield element

        raise PaginacjaError(
            f"paginacja `{sciezka}` nie domknęła się w {maks_stron} stronach — "
            f"sprawdź, czy zapytanie faktycznie używa zmiennej `${zmienna_strony}`"
        )


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
