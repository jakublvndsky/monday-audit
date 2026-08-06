"""Renderowanie makiety dashboardów (front, makieta).

    uv run python -m monday_audit.cli_pulpit                  # wszyscy klienci
    uv run python -m monday_audit.cli_pulpit --klient cxlabs   # jeden

Wzorem `cli_raport`: czyta **wyłącznie bazę**, nie dotyka monday ani modelu,
więc jest darmowe i powtarzalne. Wychodzą statyczne pliki linkowane relatywnie
— klika się jak aplikacja, a jest zwykłym HTML-em.

**To makieta do oceny układu, nie serwer.** Bez FastAPI, uwierzytelniania
i hostingu; te wchodzą po zatwierdzeniu wyglądu i po decyzji, gdzie front
zamieszka (moduł w Docs Publisherze czy osobna aplikacja).

`--json` zapisuje obok payload, który zobaczy front w JS. To on jest dowodem,
że przejście na React będzie podmianą szablonu, nie przepisaniem logiki —
i że wariant klientowy **nie zawiera** treści wewnętrznych, a nie tylko ich
nie wyświetla.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.konfiguracja import wczytaj
from monday_audit.pulpit import (
    do_json,
    wyrenderuj_indeks,
    wyrenderuj_pulpit,
    zbuduj_liste_klientow,
    zbuduj_pulpit,
)
from monday_audit.raport import ODBIORCA_KLIENT, ODBIORCA_WEWNETRZNY, RaportError
from monday_audit.rubryka import wczytaj_rubryke

logger = logging.getLogger(__name__)


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monday_audit.cli_pulpit",
        description="Renderuje makietę dashboardów z bazy. Nie dotyka monday ani modelu.",
    )
    parser.add_argument(
        "--klient",
        default=None,
        help="zawęź do jednego klienta; bez tego renderuje wszystkich z bazy",
    )
    parser.add_argument("--katalog", type=Path, default=Path("pulpity"))
    parser.add_argument(
        "--json",
        action="store_true",
        help="zapisz też payload dla frontu w JS — kontrakt, nie widok",
    )
    parser.add_argument("--baza", type=Path, default=None)
    parser.add_argument("--plik-env", type=Path, default=None, metavar="PLIK")
    return parser


def uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj(argumenty.plik_env)
    baza = (argumenty.baza or ustawienia.monday_audit_db).absolute()

    con = polacz(baza)
    try:
        zastosowane = zastosuj_migracje(con)
        if zastosowane:
            logger.info("zastosowane migracje: %s", zastosowane)
        rubryka = wczytaj_rubryke()

        klienci = zbuduj_liste_klientow(con)
        if not klienci:
            print("  brak klientów z zakończonym audytem — nie ma czego renderować")
            return 1
        wybrani = [k for k in klienci if not argumenty.klient or k.client_id == argumenty.klient]
        if not wybrani:
            print(f"  BŁĄD: klient {argumenty.klient} nie ma zakończonego audytu", file=sys.stderr)
            return 1

        argumenty.katalog.mkdir(parents=True, exist_ok=True)
        indeks = argumenty.katalog / "index.html"
        indeks.write_text(wyrenderuj_indeks(klienci), encoding="utf-8")
        print(f"  panel wewnętrzny   {len(klienci)} klientów  →  {indeks.absolute()}")

        for pozycja in wybrani:
            katalog = argumenty.katalog / pozycja.client_id
            katalog.mkdir(parents=True, exist_ok=True)
            for odbiorca, plik in (
                (ODBIORCA_WEWNETRZNY, "wewnetrzny.html"),
                (ODBIORCA_KLIENT, "klient.html"),
            ):
                pulpit = zbuduj_pulpit(
                    con, client_id=pozycja.client_id, rubryka=rubryka, odbiorca=odbiorca
                )
                cel = katalog / plik
                cel.write_text(wyrenderuj_pulpit(pulpit, klienci=klienci), encoding="utf-8")
                kwota = (
                    f"{pulpit.suma_kwot:,.0f} PLN".replace(",", " ")
                    if pulpit.ma_kwoty
                    else "bez kwot"
                )
                print(
                    f"  {pozycja.client_id}/{odbiorca:11} {pulpit.findingow} znalezisk, "
                    f"{kwota}  →  {cel.absolute()}"
                )
                if argumenty.json:
                    cel_json = cel.with_suffix(".json")
                    cel_json.write_text(
                        json.dumps(do_json(pulpit), ensure_ascii=False, indent=1),
                        encoding="utf-8",
                    )
                    print(f"  {'':24} payload dla frontu  →  {cel_json.name}")
    except RaportError as blad:
        print(f"  BŁĄD: {blad}", file=sys.stderr)
        return 1
    finally:
        con.close()

    print(f"\n  otwórz: {indeks.absolute()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
    logging.getLogger("monday_audit.konfiguracja").setLevel(logging.INFO)
    logging.getLogger("monday_audit.pulpit").setLevel(logging.INFO)
    return uruchom(zbuduj_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
