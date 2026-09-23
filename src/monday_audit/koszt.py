"""Szacowany koszt runu agenta, liczony PRZED jego uruchomieniem (faza 5b-3).

Wytyczne nie chcą wyceny znalezisk, ale chcą wiedzieć, ile będzie kosztować sam
run. To dwie różne rzeczy i tylko ta druga jest tutaj.

## Skąd szacunek — i dlaczego nie z długości tekstu

**Z faktycznego kosztu wcześniejszych ANALIZ, w przeliczeniu na hipotezę.**

Pierwsza wersja liczyła tokeny z długości zadania (znaki / 3,3) i mnożyła przez
stawkę mieszaną z historii. ZMIERZONE na runie `analiza-20260923T085706Z`:

    szacunek   0,05 USD   (10 955 tokenów wejścia + 11 200 wyjścia)
    rachunek   0,63 USD   (173 319 tokenów wejścia + 23 129 wyjścia)

Po tym runie szacunek wyszedł 0,06 USD — **estymator się nie nauczył**, choć
tak go opisałem. Błąd nie był w stawce, tylko w modelu tokenów: sesja
z narzędziami czyta cały kontekst na nowo przy każdym obrocie, a SDK dokłada
własny prompt i definicje narzędzi. Tego długość tekstu nie pokaże nigdy.

Liczenie z kosztu na hipotezę omija ten problem, bo rachunek dostawcy już
zawiera wszystkie obroty, cały cache i cały narzut SDK. Nie musimy go
modelować — wystarczy go zmierzyć.

**Z historii ANALIZ, nie całej tabeli.** Wiersze starej ścieżki (sesja na
hipotezę) mają inną strukturę kosztu: każda płaci za własny prompt i własny
kontekst. Wliczenie ich zepsułoby szacunek tak samo, jak zepsuła go stawka
mieszana. Stąd filtr na `klasa_id = 'ANALIZA_KONTA'` i niepustą liczbę hipotez.

## Dlaczego ten model wolno stosować

Koszt sesji zależy od liczby hipotez ORAZ od rozmiaru kontekstu. Liczymy
wyłącznie po hipotezach, a to jest do obrony tylko dlatego, że **kontekst ma
sufit**: `wejscie_analizy` przycina obraz konta (30 tablic, 25 automatyzacji),
a narzędzia oddają podsumowania, nie listy. Gdyby któraś z tych warstw przestała
przycinać, ten estymator zacznie zaniżać na dużych kontach — i to jest pierwsze
miejsce do sprawdzenia, gdy `porownaj` pokaże rosnący błąd.

## W którą stronę się myli — celowo

Sesja ma koszt STAŁY (prompt, obraz konta, narzut SDK) i ZMIENNY (hipotezy).
Z jednego czy kilku runów nie da się ich rozdzielić, więc szacunek jest
skonstruowany tak, żeby mylić się W GÓRĘ:

    szacunek = koszt_na_hipoteze × max(liczba_hipotez, liczba_odniesienia)

Poniżej liczby odniesienia nie skalujemy w dół, bo tam dominuje koszt stały —
mniejszy run nie jest proporcjonalnie tańszy. Powyżej skalujemy liniowo, co
przy dodatnim koszcie stałym daje górne oszacowanie. Błąd w stronę wyższej
kwoty jest bezpieczny: klient, który zapłacił mniej niż usłyszał, nie ma
pretensji. Klient, który zapłacił dziesięć razy więcej — ma.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

KLASA_ANALIZY = "ANALIZA_KONTA"

# Punkt startowy, gdy w bazie nie ma jeszcze ani jednej analizy — np. na
# serwerze przed pierwszym runem nowej ścieżki. ZMIERZONE na runie
# `analiza-20260923T085706Z` (CXLABS, snapshot 1, 16 hipotez do modelu,
# 8 z szablonu): 0,63140445 USD.
#
# To JEDEN pomiar na JEDNYM koncie, więc opis szacunku mówi o tym wprost.
# Ale jest to pomiar TEJ architektury — w odróżnieniu od poprzedniej stawki
# awaryjnej, która pochodziła z sesji per hipoteza i zaniżała dziesięciokrotnie.
KOSZT_POMIARU_USD = 0.63140445
HIPOTEZ_W_POMIARZE = 16


@dataclass(frozen=True)
class HistoriaAnaliz:
    """Ile kosztuje hipoteza w sesji analizy, policzone z przeszłych runów."""

    usd_na_hipoteze: float
    hipotez_odniesienia: int
    runow: int

    @property
    def z_pomiaru_w_bazie(self) -> bool:
        return self.runow > 0


POMIAR_STARTOWY = HistoriaAnaliz(
    usd_na_hipoteze=KOSZT_POMIARU_USD / HIPOTEZ_W_POMIARZE,
    hipotez_odniesienia=HIPOTEZ_W_POMIARZE,
    runow=0,
)


@dataclass(frozen=True)
class Szacunek:
    ile_hipotez: int
    koszt_usd: float
    podstawa: str
    z_pomiaru_w_bazie: bool

    def opis(self) -> str:
        return f"szacowany koszt runu: ~{self.koszt_usd:.2f} USD ({self.podstawa})"


def historia_analiz(con: sqlite3.Connection) -> HistoriaAnaliz:
    """Koszt na hipotezę z analiz w bazie; pomiar startowy, gdy ich brak.

    Stawka to SUMA kosztów przez SUMĘ hipotez, nie średnia po runach — średnia
    ważyłaby tak samo run na 3 hipotezy i na 60.

    Wykluczone są runy z kosztem zerowym albo pustym: to runy z subskrypcji
    (D17), gdzie `koszt_usd` jest wyceną teoretyczną albo zerem, a nie fakturą.
    Wliczenie ich zaniżyłoby stawkę — a szacunek zaniżony jest gorszy od żadnego.
    """
    wiersz = con.execute(
        """
        SELECT COALESCE(SUM(koszt_usd), 0) AS koszt,
               COALESCE(SUM(hipotez), 0)   AS hipotez,
               COUNT(*)                    AS runow
        FROM zuzycie_hipotez
        WHERE klasa_id = ?
          AND hipotez IS NOT NULL AND hipotez > 0
          AND koszt_usd IS NOT NULL AND koszt_usd > 0
        """,
        (KLASA_ANALIZY,),
    ).fetchone()

    runow = int(wiersz["runow"] or 0)
    hipotez = int(wiersz["hipotez"] or 0)
    koszt = float(wiersz["koszt"] or 0.0)
    if not runow or not hipotez:
        return POMIAR_STARTOWY
    return HistoriaAnaliz(
        usd_na_hipoteze=koszt / hipotez,
        # Średnia liczba hipotez na run — poniżej niej nie skalujemy w dół.
        hipotez_odniesienia=round(hipotez / runow),
        runow=runow,
    )


def oszacuj(ile_hipotez: int, historia: HistoriaAnaliz | None = None) -> Szacunek:
    """Liczba hipotez do modelu → szacunek kosztu jednej sesji.

    Zero hipotez daje zero — cała analiza poszła szablonami i model nie jest
    wołany. To nie jest przypadek brzegowy do pominięcia: przy koncie, na którym
    wychodzą wyłącznie martwe konta, tak właśnie będzie.
    """
    historia = historia or POMIAR_STARTOWY
    if ile_hipotez <= 0:
        return Szacunek(0, 0.0, "wszystko z szablonów, model nie jest wołany", True)

    rozliczane = max(ile_hipotez, historia.hipotez_odniesienia)
    koszt = historia.usd_na_hipoteze * rozliczane

    if historia.z_pomiaru_w_bazie:
        podstawa = (
            f"{historia.runow} analiz w bazie, {historia.usd_na_hipoteze:.3f} USD na hipotezę"
        )
    else:
        podstawa = (
            f"BRAK ANALIZ W BAZIE — pomiar startowy z jednego runu CXLABS, "
            f"{historia.usd_na_hipoteze:.3f} USD na hipotezę"
        )
    if rozliczane > ile_hipotez:
        # Mówimy wprost, że liczba jest zawyżona i dlaczego. Bez tego ktoś
        # zobaczy „5 hipotez, 0,63 USD" i uzna estymator za zepsuty.
        podstawa += f"; liczone jak dla {rozliczane}, bo poniżej dominuje koszt stały sesji"

    return Szacunek(
        ile_hipotez=ile_hipotez,
        koszt_usd=round(koszt, 4),
        podstawa=podstawa,
        z_pomiaru_w_bazie=historia.z_pomiaru_w_bazie,
    )


def porownaj(szacunek: Szacunek, zuzycie: dict[str, Any]) -> str:
    """Szacunek obok faktycznego kosztu. Jedno zdanie do logu i do raportu.

    Istnieje po to, żeby błąd szacunku był WIDOCZNY, a nie zapomniany. Szacunek,
    którego nikt nie konfrontuje z rachunkiem, po kilku runach staje się
    ozdobą — a wtedy lepiej go nie pokazywać wcale.
    """
    faktyczny = float(zuzycie.get("koszt_usd") or 0.0)
    if not faktyczny:
        return (
            f"{szacunek.opis()}; koszt faktyczny NIEZNANY "
            "(run poszedł z subskrypcji albo dostawca nie podał kwoty)"
        )

    roznica = faktyczny - szacunek.koszt_usd
    kierunek = "drożej" if roznica > 0 else "taniej"
    udzial = abs(roznica) / faktyczny
    return (
        f"szacowano ~{szacunek.koszt_usd:.2f} USD, wyszło {faktyczny:.2f} USD "
        f"({kierunek} o {abs(roznica):.2f}, czyli {udzial:.0%})"
    )


def zapisz_zuzycie_analizy(
    con: sqlite3.Connection,
    run_id: str,
    zuzycie: dict[str, Any],
    *,
    ile_hipotez: int,
    ile_uwag: int = 0,
    wywolan_narzedzi: int = 0,
    sekund: float | None = None,
) -> None:
    """Jeden wiersz w `zuzycie_hipotez` dla CAŁEJ sesji analizy.

    `ile_hipotez` jest OBOWIĄZKOWE, nie domyślne. Bez niego wiersz nie zasila
    `historia_analiz` — a wtedy estymator nie uczy się z tego runu, co jest
    dokładnie usterką, którą ta wersja naprawia. Wartość domyślna pozwoliłaby
    o tym zapomnieć bez żadnego sygnału.

    Liczymy hipotezy, które poszły DO MODELU, nie wszystkie. Szablon kosztuje
    zero, więc wliczenie go rozcieńczałoby koszt na hipotezę i szacunek
    wychodziłby za niski.
    """
    with con:
        con.execute(
            "INSERT INTO zuzycie_hipotez (run_id, klasa_id, obiekt_id, tokens_in, "
            "tokens_out, tokens_cache_read, tokens_cache_write, koszt_usd, sekund, "
            "wywolan_narzedzi, byl_finding, hipotez, zapisano) "
            "VALUES (?, ?, 'konto', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                run_id,
                KLASA_ANALIZY,
                int(zuzycie.get("tokens_in", 0)),
                int(zuzycie.get("tokens_out", 0)),
                int(zuzycie.get("tokens_cache_read", 0)),
                int(zuzycie.get("tokens_cache_write", 0)),
                float(zuzycie.get("koszt_usd") or 0.0) or None,
                round(sekund, 3) if sekund else None,
                wywolan_narzedzi,
                1 if ile_uwag else 0,
                ile_hipotez,
            ),
        )
