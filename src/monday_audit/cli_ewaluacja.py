"""Raport ewaluacji jednego runu jako HTML.

    uv run python -m monday_audit.cli_ewaluacja --run acme-20260811T093330Z-agent
    uv run python -m monday_audit.cli_ewaluacja --run <nowy> --wobec <baseline>

`--wobec` daje kolumnę porównania: baseline obok eksperymentu. To jest sedno przy
optymalizacji — koszt zawsze da się obniżyć, pytanie tylko, czy nie za cenę
trafności. Bez tej pary każdy eksperyment wygląda na sukces.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.ewaluacja import wyrenderuj, zbierz_zuzycie
from monday_audit.konfiguracja import wczytaj
from monday_audit.rubryka import wczytaj_rubryke

logger = logging.getLogger(__name__)

KATALOG = Path("raporty")


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monday_audit.cli_ewaluacja",
        description="Rozbicie kosztu runu i jakość wobec progów etapu 4.",
    )
    parser.add_argument("--run", required=True, help="run_id do zewaluowania")
    parser.add_argument("--wobec", default=None, metavar="RUN_ID", help="baseline do porównania")
    parser.add_argument("--baza", type=Path, default=None)
    parser.add_argument("--plik-env", type=Path, default=None, metavar="PLIK")
    return parser


def uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj(argumenty.plik_env)
    baza = (argumenty.baza or ustawienia.monday_audit_db).absolute()
    rubryka = wczytaj_rubryke()

    con = polacz(baza)
    zastosowane = zastosuj_migracje(con)
    if zastosowane:
        logger.info("zastosowano migracje: %s", zastosowane)
    try:
        z = zbierz_zuzycie(con, argumenty.run, rubryka)
        poprzedni = zbierz_zuzycie(con, argumenty.wobec, rubryka) if argumenty.wobec else None
    except ValueError as blad:
        print(f"  BŁĄD: {blad}", file=sys.stderr)
        return 1
    finally:
        con.close()

    KATALOG.mkdir(exist_ok=True)
    cel = KATALOG / f"ewaluacja_{argumenty.run}.html"
    cel.write_text(wyrenderuj(z, poprzedni=poprzedni), encoding="utf-8")

    print(f"\n  {z.run_id}: {z.hipotez} hipotez, {z.findingow} znalezisk")
    print(f"  koszt: {z.koszt_usd:.2f} USD", end="")
    if z.usd_na_finding:
        print(f" ({z.usd_na_finding} USD za znalezisko)", end="")
    print()
    if z.sekund:
        print(f"  czas:  {z.sekund / 60:.0f} min ({z.sekund / max(z.hipotez, 1):.0f} s/hipoteza)")
    if not z.ma_rozbicie:
        # Ostrzeżenie, nie błąd: run sprzed migracji 010 nadal warto zobaczyć,
        # tylko bez rozbicia per klasa.
        print("\n  UWAGA: ten run nie ma rozbicia per hipoteza (sprzed migracji 010).")
        print("  Sumy NIE dzielimy po równo — raport mówi o tym wprost.")
    print(f"\n  raport: {cel}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")
    return uruchom(zbuduj_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
