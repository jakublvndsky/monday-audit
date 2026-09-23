"""Szacowany koszt runu agenta, liczony PRZED jego uruchomieniem (faza 5b-3).

Wytyczne nie chcą wyceny znalezisk, ale chcą wiedzieć, ile będzie kosztować sam
run. To dwie różne rzeczy i tylko ta druga jest tutaj.

## Skąd bierzemy stawkę — i dlaczego nie z cennika

**Z pomiaru, nie z cennika zaszytego w kodzie.** `agent._zuzycie` mówi to
wprost: „`costUSD` bierzemy z SDK, zamiast mnożyć tokeny przez cennik zaszyty
u nas — cennik jest po stronie dostawcy i to on wie, ile policzył". Ta sama
zasada obowiązuje przy szacowaniu: liczymy stawkę z tego, co dostawca policzył
nam w PRZESZŁYCH runach, zapisanych w `zuzycie_hipotez`.

Zaleta jest praktyczna, nie ideologiczna: stawka poprawia się sama, gdy
dostawca zmieni ceny albo gdy zmienimy model — bez edycji kodu i bez tabelki,
o której ktoś zapomni.

## Czego ten szacunek NIE potrafi

**Jest stawką MIESZANĄ.** Wejście, wyjście i odczyt z cache'u mają u dostawcy
bardzo różne ceny, a `zuzycie_hipotez` zapisuje jeden `koszt_usd` na wiersz,
bez rozbicia. Nie da się z tego odtworzyć trzech osobnych stawek, więc liczymy
jedną: koszt na token, po wszystkich tokenach razem.

Konsekwencja, którą trzeba znać: run o innym PROPORCJACH wejścia do wyjścia niż
historia będzie oszacowany z błędem. A dokładnie tak jest przy przejściu na
jedną sesję (5b-2) — historia pochodzi z sesji per hipoteza, gdzie odczyt
z cache'u stanowił większość wejścia. Dlatego `Szacunek` niesie `podstawa`
i `z_pomiaru`, a `porownaj` zapisuje, o ile szacunek się pomylił. Pierwszy
szacunek dla nowej architektury BĘDZIE zły; drugi będzie lepszy, bo policzy
się z pierwszego.

Alternatywą było zaszycie cen za token dla `claude-sonnet-5`. Odrzucone: dałoby
liczbę dokładniejszą dziś i cicho fałszywą po pierwszej zmianie cennika albo
modelu — a nikt nie zauważy, bo szacunek nadal będzie wyglądał wiarygodnie.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Znaków na token. ZMIERZONE i już używane w `agent.py` przy rozbiciu wyjścia
# („`tokens_out` minus (znaki / ~3,3)"). Polski tekst z JSON-em w środku wypada
# gorzej niż angielski, stąd 3,3, a nie podręcznikowe 4.
ZNAKOW_NA_TOKEN = 3.3

# Ile tokenów wyjścia kosztuje rozstrzygnięcie JEDNEJ hipotezy. Uwaga krytyczna
# to opis, rekomendacja i dowód; pominięcie to jedno zdanie. ZMIERZONE na runie
# `agent-20260922T124624Z`: 3 hipotezy, 6481 tokenów wyjścia — ale to była
# architektura per hipoteza, z osobnym rozumowaniem do każdej. Przy jednej sesji
# rozumowanie jest wspólne, więc bierzemy liczbę OSTROŻNIE niższą i pozwalamy
# `porownaj` ją skorygować.
TOKENOW_WYJSCIA_NA_HIPOTEZE = 700

# Stawka awaryjna, gdy historii nie ma. ZMIERZONA na tym samym runie:
# 0,423546 USD wobec 190 686 tokenów łącznie. Jest to stawka MIESZANA
# i architektury per hipoteza — czyli podwójnie przybliżona. Używana wyłącznie
# po to, żeby pierwszy run w świeżej bazie nie zostawał bez żadnej liczby.
STAWKA_AWARYJNA_USD_ZA_TOKEN = 0.423546 / 190_686


@dataclass(frozen=True)
class StawkaTokenow:
    """Ile kosztuje token, policzone z przeszłych runów."""

    usd_za_token: float
    tokenow: int
    wierszy: int

    @property
    def z_pomiaru(self) -> bool:
        return self.wierszy > 0


@dataclass(frozen=True)
class Szacunek:
    tokenow_wejscia: int
    tokenow_wyjscia: int
    koszt_usd: float
    podstawa: str
    z_pomiaru: bool

    @property
    def tokenow_razem(self) -> int:
        return self.tokenow_wejscia + self.tokenow_wyjscia

    def opis(self) -> str:
        pewnosc = "z pomiaru" if self.z_pomiaru else "BEZ POMIARU, stawka awaryjna"
        return (
            f"szacowany koszt runu: ~{self.koszt_usd:.2f} USD "
            f"({self.tokenow_wejscia} tokenów wejścia + ~{self.tokenow_wyjscia} wyjścia, "
            f"{pewnosc}: {self.podstawa})"
        )


def stawka_z_historii(con: sqlite3.Connection) -> StawkaTokenow:
    """Stawka mieszana z `zuzycie_hipotez`. Puste wiersze nie psują średniej.

    Bierzemy tylko wiersze z NIEZEROWYM kosztem i niezerowymi tokenami. Wiersz
    z kosztem zerowym znaczy „run poszedł z subskrypcji" (D17) — tam `koszt_usd`
    jest wyceną teoretyczną albo zerem, a nie fakturą, więc wliczenie go
    zaniżyłoby stawkę i szacunek byłby optymistyczny. Szacunek optymistyczny
    jest gorszy od żadnego.
    """
    wiersz = con.execute(
        """
        SELECT COALESCE(SUM(koszt_usd), 0)                       AS koszt,
               COALESCE(SUM(tokens_in + tokens_out
                            + tokens_cache_read + tokens_cache_write), 0) AS tokenow,
               COUNT(*)                                           AS wierszy
        FROM zuzycie_hipotez
        WHERE koszt_usd IS NOT NULL AND koszt_usd > 0
          AND (tokens_in + tokens_out + tokens_cache_read + tokens_cache_write) > 0
        """
    ).fetchone()

    tokenow = int(wiersz["tokenow"] or 0)
    koszt = float(wiersz["koszt"] or 0.0)
    if not tokenow or not koszt:
        return StawkaTokenow(usd_za_token=STAWKA_AWARYJNA_USD_ZA_TOKEN, tokenow=0, wierszy=0)
    return StawkaTokenow(
        usd_za_token=koszt / tokenow,
        tokenow=tokenow,
        wierszy=int(wiersz["wierszy"] or 0),
    )


def oszacuj(
    zadanie: str,
    *,
    ile_hipotez: int,
    prompt: str = "",
    stawka: StawkaTokenow | None = None,
) -> Szacunek:
    """Treść zadania + liczba hipotez → szacunek kosztu jednej sesji.

    `prompt` osobno od `zadania`, choć oba idą do modelu: prompt systemowy jest
    prefiksem cache'u, więc przy drugim i kolejnym runie kosztuje ułamek. Nie
    modelujemy tego rabatu — liczymy prompt pełną stawką i **świadomie
    przeszacowujemy**. Błąd w stronę wyższej kwoty jest bezpieczny: klient,
    który zapłacił mniej, niż usłyszał, nie ma pretensji.
    """
    stawka = stawka or StawkaTokenow(STAWKA_AWARYJNA_USD_ZA_TOKEN, 0, 0)

    wejscia = int((len(zadanie) + len(prompt)) / ZNAKOW_NA_TOKEN)
    wyjscia = ile_hipotez * TOKENOW_WYJSCIA_NA_HIPOTEZE
    koszt = (wejscia + wyjscia) * stawka.usd_za_token

    podstawa = (
        f"{stawka.wierszy} wierszy historii, {stawka.tokenow} tokenów"
        if stawka.z_pomiaru
        else "brak historii w bazie"
    )
    return Szacunek(
        tokenow_wejscia=wejscia,
        tokenow_wyjscia=wyjscia,
        koszt_usd=round(koszt, 4),
        podstawa=podstawa,
        z_pomiaru=stawka.z_pomiaru,
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
    ile_uwag: int = 0,
    wywolan_narzedzi: int = 0,
    sekund: float | None = None,
) -> None:
    """Jeden wiersz w `zuzycie_hipotez` dla CAŁEJ sesji analizy.

    Bez migracji: tabela przyjmuje `klasa_id` jako zwykły tekst, więc sesja
    zbiorcza zapisuje się pod `ANALIZA_KONTA`. Dzięki temu następny szacunek
    policzy się z tego runu — i będzie lepszy, bo pochodzi z tej samej
    architektury, a nie z sesji per hipoteza.
    """
    with con:
        con.execute(
            "INSERT INTO zuzycie_hipotez (run_id, klasa_id, obiekt_id, tokens_in, "
            "tokens_out, tokens_cache_read, tokens_cache_write, koszt_usd, sekund, "
            "wywolan_narzedzi, byl_finding, zapisano) "
            "VALUES (?, 'ANALIZA_KONTA', 'konto', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                run_id,
                int(zuzycie.get("tokens_in", 0)),
                int(zuzycie.get("tokens_out", 0)),
                int(zuzycie.get("tokens_cache_read", 0)),
                int(zuzycie.get("tokens_cache_write", 0)),
                float(zuzycie.get("koszt_usd") or 0.0) or None,
                round(sekund, 3) if sekund else None,
                wywolan_narzedzi,
                1 if ile_uwag else 0,
            ),
        )
