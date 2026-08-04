"""Odświeżanie cennika ze stron monday i przegląd stawek.

    uv run python -m monday_audit.cli_cennik --odswiez
    uv run python -m monday_audit.cli_cennik --pokaz --klient cxlabs

**Osobna komenda, NIGDY w trakcie audytu.** Dwa powody, oba twarde:

1. Run audytu nie może zależeć od tego, czy cudza strona odpowiada w danej
   minucie. Collector ma swoje limity i swój budżet; dokładanie mu zewnętrznej
   zależności sieciowej to nowy tryb awarii bez żadnego zysku.
2. Audyt musi być odtwarzalny (D7, 05-deploy). Stawka użyta w raporcie to
   **zapis w bazie z datą**, a nie wynik pobrania w locie.

## Dlaczego wzorce, a nie selektory HTML

Wyciągamy liczby wzorcami zakotwiczonymi w ZDANIACH („N credits per action"),
nie po pozycji w drzewie dokumentu. Zdania na stronach pomocy zmieniają się
rzadziej niż klasy CSS, a gdy się zmienią, wzorzec po prostu nie trafi —
i wtedy nie nadpisujemy niczego, zamiast zapisać liczbę wyjętą z sąsiedniego
akapitu.

## Co robimy, gdy się nie udaje

**Nic nie nadpisujemy.** Zostaje ostatnia dobra wartość ze swoją datą,
a komenda kończy się kodem błędu i wypisuje, czego nie znalazła. Cichy zapis
śmiecia jest tu groźniejszy od braku odświeżenia: stawka wchodzi do raportu
klienta.

## Czego ta komenda NIE dotyka

`stawki_klienta` — czyli ceny licencji u konkretnego klienta. Na Enterprise
jest negocjowana i publiczny cennik jej nie zawiera (O7). Podstawienie ceny
z listy dałoby liczbę pewnie brzmiącą i błędną.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.cennik import (
    CennikError,
    przeglad,
    zapisz_stawke,
)
from monday_audit.konfiguracja import wczytaj

logger = logging.getLogger(__name__)

# Bez tego nagłówka support.monday.com zwraca 403 (zmierzone 2026-08-04).
# Zwykła przeglądarkowa wartość, bez udawania konkretnej wersji.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

STRONA_KREDYTY = "https://support.monday.com/hc/en-us/articles/29544502265746-AI-Credits"
STRONA_CENNIK = "https://monday.com/pricing"


@dataclass(frozen=True, slots=True)
class Wzorzec:
    """Jedna pozycja cennika i sposób jej znalezienia w tekście strony."""

    pozycja: str
    jednostka: str
    wyrazenie: str
    zrodlo: str
    wiarygodnosc: str
    waluta: str | None = None


# Wzorce potwierdzone na żywym tekście 2026-08-04. Każdy zakotwiczony w zdaniu,
# nie w strukturze — i każdy z jawną wiarygodnością, bo stawka kredytu w USD
# NIE pojawia się na stronie monday i pozostaje źródłem zewnętrznym.
WZORCE = (
    Wzorzec(
        pozycja="ai_block_kredyty",
        jednostka="akcja",
        # „AI blocks 8 credits per action."
        wyrazenie=r"AI\s+blocks?\D{0,40}?(\d+(?:[.,]\d+)?)\s*credits?\s+per\s+action",
        zrodlo=STRONA_KREDYTY,
        wiarygodnosc="zrodlo_pierwotne",
    ),
    Wzorzec(
        pozycja="ai_notetaker_kredyty_godzina",
        jednostka="godzina",
        # „AI Notetaker 120 credits per meeting hour"
        wyrazenie=r"Notetaker\D{0,40}?(\d+(?:[.,]\d+)?)\s*credits?\s+per\s+meeting\s+hour",
        zrodlo=STRONA_KREDYTY,
        wiarygodnosc="zrodlo_pierwotne",
    ),
    # UWAGA: strona ma DWIE tabele złożoności o identycznym kształcie zdania —
    # jedną dla agentów (10–50/50–150/150–250/250+) i jedną dla sidekicka
    # (10–30/30–80/80–150/150+). Wzorzec bez kotwicy łapie pierwszą z nich
    # przypadkiem. Zakotwiczenie w słowie `agents` jest tu warunkiem
    # poprawności, nie ostrożnością: bez niego stawka podpisana „agent"
    # mogłaby pochodzić z tabeli sidekicka po zmianie kolejności sekcji.
    # Wykryte przez `surowy_fragment` przy pierwszym uruchomieniu scrapera.
    Wzorzec(
        pozycja="agent_run_kredyty_min",
        jednostka="uruchomienie",
        # „agents Credit consumption varies based on task complexity:
        #  Simple (~10–50 credits)"
        wyrazenie=(r"agents\s+Credit\s+consumption\D{0,80}?Simple\s*\(~?\s*(\d+)\s*[–\-]\s*\d+"),
        zrodlo=STRONA_KREDYTY,
        wiarygodnosc="zrodlo_pierwotne",
    ),
    Wzorzec(
        pozycja="agent_run_kredyty_max",
        jednostka="uruchomienie",
        # „... Extra complex (~250+ credits). Each agent run may include ..."
        wyrazenie=(
            r"Extra\s+complex\s*\(~?\s*(\d+)\s*\+?\s*credits?\)"
            r"\.?\s*Each\s+agent\s+run"
        ),
        zrodlo=STRONA_KREDYTY,
        wiarygodnosc="zrodlo_pierwotne",
    ),
)


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monday_audit.cli_cennik",
        description="Odświeża stawki ze stron monday. NIE uruchamiaj w trakcie audytu.",
    )
    parser.add_argument("--odswiez", action="store_true", help="pobierz strony i zapisz stawki")
    parser.add_argument("--pokaz", action="store_true", help="wypisz aktualne stawki z bazy")
    parser.add_argument("--klient", default=None, help="uwzględnij stawki tego klienta w --pokaz")
    parser.add_argument("--baza", type=Path, default=None)
    parser.add_argument("--plik-env", type=Path, default=None, metavar="PLIK")
    return parser


def na_tekst(strona: str) -> str:
    """HTML → płaski tekst. Bez parsera drzewa, bo wzorce działają na zdaniach."""
    bez_skryptow = re.sub(r"<(script|style)\b.*?</\1>", " ", strona, flags=re.DOTALL | re.I)
    bez_tagow = re.sub(r"<[^>]+>", " ", bez_skryptow)
    # Strony monday oddają część treści jako JSON w HTML-u, z escapowanymi
    # znakami — dlatego rozkodowujemy dwa razy i normalizujemy myślniki.
    tekst = html.unescape(html.unescape(bez_tagow))
    tekst = tekst.replace("\\u2013", "–").replace("\\n", " ")
    return re.sub(r"\s+", " ", tekst)


async def pobierz(url: str) -> str:
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as klient:
        odpowiedz = await klient.get(url, headers={"User-Agent": UA})
    odpowiedz.raise_for_status()
    return odpowiedz.text


def wyciagnij(tekst: str, wzorzec: Wzorzec) -> tuple[float, str] | None:
    """Zwraca `(wartosc, cytat)` albo `None`, gdy wzorzec nie trafił."""
    trafienie = re.search(wzorzec.wyrazenie, tekst, flags=re.I)
    if trafienie is None:
        return None
    surowa = trafienie.group(1).replace(",", ".")
    try:
        wartosc = float(surowa)
    except ValueError:
        return None
    # Cytat z otoczeniem, żeby człowiek mógł sprawdzić, skąd liczba.
    poczatek = max(0, trafienie.start() - 60)
    return wartosc, tekst[poczatek : trafienie.end() + 60].strip()


async def odswiez(baza: Path) -> int:
    """Pobiera strony i zapisuje stawki. Przy niepowodzeniu nic nie nadpisuje."""
    con = polacz(baza)
    zastosuj_migracje(con)

    strony: dict[str, str] = {}
    for url in {w.zrodlo for w in WZORCE}:
        try:
            strony[url] = na_tekst(await pobierz(url))
            logger.info("pobrano %s", url)
        except httpx.HTTPError as blad:
            logger.exception("nie udało się pobrać %s: %s", url, blad)  # noqa: TRY401

    zapisane, nieznalezione = 0, []
    for wzorzec in WZORCE:
        tekst = strony.get(wzorzec.zrodlo)
        if tekst is None:
            nieznalezione.append(f"{wzorzec.pozycja} (strona niedostępna)")
            continue
        wynik = wyciagnij(tekst, wzorzec)
        if wynik is None:
            # Wzorzec nie trafił = prawdopodobnie zmieniło się zdanie na stronie.
            # NIE zapisujemy niczego: ostatnia dobra wartość zostaje.
            nieznalezione.append(f"{wzorzec.pozycja} (wzorzec nie trafił)")
            continue
        wartosc, cytat = wynik
        try:
            zapisz_stawke(
                con,
                pozycja=wzorzec.pozycja,
                wartosc=wartosc,
                jednostka=wzorzec.jednostka,
                waluta=wzorzec.waluta,
                sposob="scraper",
                wiarygodnosc=wzorzec.wiarygodnosc,
                zrodlo_url=wzorzec.zrodlo,
                surowy_fragment=cytat,
            )
            zapisane += 1
        except CennikError as blad:
            # Poza przedziałem rozsądku — najczęściej znaczy, że wzorzec złapał
            # inną liczbę niż powinien. Też nie nadpisujemy.
            nieznalezione.append(f"{wzorzec.pozycja} ({blad})")

    print(f"zapisanych stawek: {zapisane} z {len(WZORCE)}")
    for opis in nieznalezione:
        print(f"  NIE ZAPISANO: {opis}")
    if nieznalezione:
        print("\n  Ostatnie dobre wartości zostały nietknięte. Sprawdź, czy zmienił się")
        print("  tekst na stronie — wzorce są w `WZORCE` w cli_cennik.py.")
    con.close()
    return 1 if nieznalezione else 0


def pokaz(baza: Path, client_id: str | None) -> int:
    con = polacz(baza)
    zastosuj_migracje(con)
    stawki = przeglad(con, client_id=client_id)
    if not stawki:
        print("cennik jest pusty — uruchom `--odswiez`")
        con.close()
        return 1

    print(f"{'pozycja':32} {'wartość':>10}  {'jednostka':12} {'źródło':18} wiek")
    print("-" * 92)
    for stawka in stawki:
        wiek = f"{stawka.dni_od_odswiezenia} dni" if stawka.dni_od_odswiezenia is not None else "?"
        znacznik = "  PRZETERMINOWANA" if stawka.przeterminowana else ""
        zrodlo = "klient" if stawka.per_klient else stawka.wiarygodnosc
        waluta = stawka.waluta or "kredyty"
        print(
            f"{stawka.pozycja:32} {stawka.wartosc:>10.4g}  {waluta:12} {zrodlo:18} {wiek}{znacznik}"
        )
    con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")
    argumenty = zbuduj_parser().parse_args(argv)
    if not (argumenty.odswiez or argumenty.pokaz):
        zbuduj_parser().print_help()
        return 1

    ustawienia = wczytaj(argumenty.plik_env)
    baza = argumenty.baza or ustawienia.monday_audit_db

    kod = 0
    if argumenty.odswiez:
        kod |= asyncio.run(odswiez(baza))
    if argumenty.pokaz:
        kod |= pokaz(baza, argumenty.klient)
    return kod


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
