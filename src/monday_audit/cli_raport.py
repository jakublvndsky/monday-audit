"""Renderowanie raportu z zapisanego runu (etap 3.12).

    uv run python -m monday_audit.cli_raport --run-id agent-pelny-19

**Osobna komenda, nie doklejona do `cli_agent`.** Renderowanie jest darmowe
i musi dać się powtórzyć: etap 4 przepuszcza te same runy przez nową rubrykę
i nowy szablon, a płacenie za analizę tylko po to, żeby zobaczyć inny układ
strony, byłoby absurdem. Ta komenda nie dotyka ani monday, ani modelu — czyta
wyłącznie bazę.

Wychodzą DWA pliki: wewnętrzny i klientowy. Zawsze oba, bo różnica między nimi
jest sensem tej warstwy i trzeba ją widzieć obok siebie.

Katalog `raporty/` jest w `.gitignore`: dokument jest PO deanonimizacji, czyli
zawiera prawdziwe imiona, e-maile i nazwy tablic klienta.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.konfiguracja import wczytaj
from monday_audit.raport import (
    ODBIORCA_KLIENT,
    ODBIORCA_WEWNETRZNY,
    RaportError,
    zapisz,
    zbuduj_raport,
)
from monday_audit.rubryka import wczytaj_rubryke

logger = logging.getLogger(__name__)


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monday_audit.cli_raport",
        description="Renderuje raport z zapisanego runu. Nie dotyka monday ani modelu.",
    )
    parser.add_argument("--run-id", required=True, help="run agenta z tabeli `runy`")
    parser.add_argument(
        "--odbiorca",
        choices=[ODBIORCA_WEWNETRZNY, ODBIORCA_KLIENT],
        default=None,
        help="domyślnie oba warianty — różnicę między nimi trzeba widzieć obok siebie",
    )
    parser.add_argument("--katalog", type=Path, default=Path("raporty"))
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

        odbiorcy = (
            [argumenty.odbiorca] if argumenty.odbiorca else [ODBIORCA_WEWNETRZNY, ODBIORCA_KLIENT]
        )
        for odbiorca in odbiorcy:
            raport = zbuduj_raport(con, run_id=argumenty.run_id, rubryka=rubryka, odbiorca=odbiorca)
            cel = zapisz(raport, katalog=argumenty.katalog)
            kwota = (
                f"{raport.suma_kwot:,.0f} PLN".replace(",", " ") if raport.ma_kwoty else "bez kwot"
            )
            print(f"  {odbiorca:11} {raport.findingow} znalezisk, {kwota}  →  {cel.absolute()}")
            if raport.nieznane_hashe:
                print(
                    f"  {'':11} UWAGA: {raport.nieznane_hashe} hashy bez mapowania — "
                    f"sprawdź, czy snapshot i sól są od tego klienta"
                )
    except RaportError as blad:
        print(f"  BŁĄD: {blad}", file=sys.stderr)
        return 1
    finally:
        con.close()

    print(f"\n  baza: {baza}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
    logging.getLogger("monday_audit.konfiguracja").setLevel(logging.INFO)
    logging.getLogger("monday_audit.raport").setLevel(logging.INFO)
    logging.getLogger("monday_audit.deanonimizacja").setLevel(logging.INFO)
    return uruchom(zbuduj_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
