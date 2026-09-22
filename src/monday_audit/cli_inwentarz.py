"""Sześć kafelków dla konta monday — pierwszy ekran z user story.

    uv run python -m monday_audit.cli_inwentarz
    uv run python -m monday_audit.cli_inwentarz --json

Token bierze się z konfiguracji procesu (D12), nigdy z argv — `ps` pokazuje
argumenty każdemu na maszynie.

Nic nie zapisuje: to nie jest run, więc nie zakłada wiersza w `runy` ani
snapshotu. `RejestrPodgladu` trzyma liczniki w pamięci.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from monday_audit.inwentarz import zbuduj_inwentarz
from monday_audit.klient import MondayClient, MondayError
from monday_audit.konfiguracja import wczytaj
from monday_audit.konto import Zakres, rozpoznaj_konto
from monday_audit.podglad_zakresu import RejestrPodgladu

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inwentarz konta monday — sześć kafelków")
    parser.add_argument("--json", action="store_true", help="wypisz surowy JSON zamiast tabelki")
    return parser


async def _wykonaj(jako_json: bool) -> int:
    ustawienia = wczytaj()
    token = ustawienia.monday_token.get_secret_value() if ustawienia.monday_token else ""
    if not token:
        print("BŁĄD: MONDAY_TOKEN pusty. Wpisz go do .env.", file=sys.stderr)
        return 1

    rejestr = RejestrPodgladu()
    async with MondayClient(token, rejestr) as klient:
        # `cale_konto` celowo: inwentarz bez pełnego zakresu jest inwentarzem
        # czegoś innego niż konto. Tokenem bez admina `rozpoznaj_konto` przerwie
        # i to jest właściwe zachowanie — lepiej brak liczby niż liczba zaniżona.
        konto = await rozpoznaj_konto(klient, Zakres(typ="cale_konto"))
        inwentarz = await zbuduj_inwentarz(klient, konto, rejestr)

    if jako_json:
        print(json.dumps(inwentarz.do_json(), ensure_ascii=False, indent=2))
        return 0

    print(f"\n  {inwentarz.konto_nazwa}")
    print(f"  {'─' * 46}")
    print(f"  workspace'ów      {inwentarz.workspacow:>6}")
    for produkt, ile in inwentarz.po_produktach.items():
        print(f"      {produkt:<14}{ile:>6}")
    print(f"  tablic aktywnych  {inwentarz.tablic_aktywnych:>6}")
    for stan, ile in inwentarz.tablic_po_stanie.items():
        print(f"      {stan:<14}{ile:>6}")
    print(f"  użytkowników      {inwentarz.uzytkownikow:>6}   (admin + member)")
    print(f"  gości             {inwentarz.gosci:>6}")
    print(f"  agentów AI        {inwentarz.agentow_ai:>6}   (konta agentowe, w tym zewnętrzne)")
    print(f"  podglądających    {inwentarz.podgladajacych:>6}")
    licencja = inwentarz.licencja_tier or "nieznana"
    miejsca = inwentarz.licencja_max_users
    print(f"  licencja          {licencja:>6}" + (f"   (max {miejsca} miejsc)" if miejsca else ""))
    print(f"  {'─' * 46}")
    print(f"  {inwentarz.wywolan} wywołań z limitu konta\n")

    for uwaga in inwentarz.zastrzezenia:
        print(f"  UWAGA: {uwaga}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_wykonaj(jako_json=args.json))
    except MondayError as blad:
        # Treść błędu z API może nieść fragment odpowiedzi, ale nie token —
        # `MondayClient` go nie wkłada do komunikatu.
        print(f"BŁĄD monday: {blad}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
