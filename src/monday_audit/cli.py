"""Ręczne uruchomienie collectora (etap 3.8).

    export MONDAY_TOKEN=...
    export SOL_PSEUDONIMIZACJI=...
    uv run python -m monday_audit.cli --klient cxlabs --zakres workspace --id 6576039

**Sekrety wyłącznie ze środowiska procesu.** Ten moduł nie zna ścieżki do
`.env` i nigdy jej nie otworzy — plik z sekretami udostępnia człowiek, na czas
jednego uruchomienia, przez `export`. Dostęp do samego pliku jest dodatkowo
zablokowany w `.claude/settings.json`.

Powód istnienia tego pliku: snapshot musi być odtwarzalny przez kogokolwiek
i kiedykolwiek. Pierwszy run poszedł ze skryptu w katalogu tymczasowym sesji,
czyli z miejsca, które przestaje istnieć — a `04-test.md` wymaga porównywania
dwóch runów na tym samym koncie.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.konto import Zakres
from monday_audit.logi import MAKS_STRON_LOGOW, TOP_PO_ITEMACH, Z_OGONA
from monday_audit.osoby import sol_z_env
from monday_audit.postep import LicznikKonsolowy
from monday_audit.przebieg import RaportRunu, wykonaj_run

DOMYSLNA_BAZA = Path("monday_audit.db")
KATALOG_EKSPORTU = Path("snapshoty")


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monday_audit.cli",
        description="Zbiera snapshot konta monday.com. Read-only.",
    )
    parser.add_argument("--klient", required=True, help="identyfikator klienta, np. cxlabs")
    parser.add_argument(
        "--zakres",
        required=True,
        choices=("cale_konto", "workspace", "tablice"),
        help="cale_konto wymaga tokena admina; workspace i tablice nie",
    )
    parser.add_argument(
        "--id",
        action="append",
        default=[],
        metavar="ID",
        help="workspace_id albo board_id; można podać wielokrotnie",
    )
    parser.add_argument("--baza", type=Path, default=DOMYSLNA_BAZA)
    parser.add_argument("--budzet-wywolan", type=int, default=400)
    parser.add_argument("--dni-okna", type=int, default=90)
    parser.add_argument("--maks-sond", type=int, default=10, help="sufit sond automatyzacji")
    parser.add_argument(
        "--top-logow", type=int, default=TOP_PO_ITEMACH, help="tablic w próbce logów"
    )
    parser.add_argument("--z-ogona", type=int, default=Z_OGONA, help="tablic z ogona próbki")
    parser.add_argument(
        "--maks-stron-logow",
        type=int,
        default=MAKS_STRON_LOGOW,
        help="sufit stron logu na tablicę (100 wpisów na stronę)",
    )
    parser.add_argument(
        "--bez-eksportu",
        action="store_true",
        help=f"nie zapisuj JSON-a do {KATALOG_EKSPORTU}/ (i tak jest w bazie)",
    )
    return parser


def zbuduj_zakres(nazwa: str, identyfikatory: list[str]) -> Zakres:
    """Zamienia argumenty na deklarację zakresu. Błąd, nie domysł."""
    if nazwa == "cale_konto":
        if identyfikatory:
            raise SystemExit("--zakres cale_konto nie przyjmuje --id")
        return Zakres.cale_konto()
    if not identyfikatory:
        raise SystemExit(f"--zakres {nazwa} wymaga co najmniej jednego --id")
    if nazwa == "workspace":
        return Zakres.workspace(*identyfikatory)
    return Zakres.tablice(*identyfikatory)


def eksportuj(raport: RaportRunu, *, baza: Path, katalog: Path = KATALOG_EKSPORTU) -> Path:
    """Zapisuje payload do czytelnego JSON-a — wejście dla BRAMY z 3.8.

    Katalog jest w `.gitignore`: snapshot zawiera nazwy tablic i kolumn klienta.
    """
    con = polacz(baza)
    try:
        payload = con.execute(
            "SELECT payload FROM snapshots WHERE id = ?", (raport.snapshot_id,)
        ).fetchone()["payload"]
    finally:
        con.close()

    katalog.mkdir(parents=True, exist_ok=True)
    cel = katalog / f"snapshot_{raport.snapshot_id}_{raport.client_id}.json"
    cel.write_text(json.dumps(json.loads(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return cel


async def uruchom(argumenty: argparse.Namespace) -> RaportRunu:
    token = os.environ.get("MONDAY_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "brak MONDAY_TOKEN w środowisku — wyeksportuj go przed uruchomieniem "
            "(ten program nie czyta pliku .env)"
        )
    sol = sol_z_env()

    con = polacz(argumenty.baza)
    zastosuj_migracje(con)
    licznik = LicznikKonsolowy(sys.stderr, co_ile=5)
    try:
        return await wykonaj_run(
            token=token,
            con=con,
            client_id=argumenty.klient,
            zakres=zbuduj_zakres(argumenty.zakres, argumenty.id),
            sol=sol,
            postep=licznik,
            budzet_wywolan=argumenty.budzet_wywolan,
            dni_okna=argumenty.dni_okna,
            maks_sond=argumenty.maks_sond,
            top_logow=argumenty.top_logow,
            z_ogona=argumenty.z_ogona,
            maks_stron_logow=argumenty.maks_stron_logow,
        )
    finally:
        licznik.zakoncz()
        con.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
    argumenty = zbuduj_parser().parse_args(argv)

    raport = asyncio.run(uruchom(argumenty))
    print(raport.opis())

    if not argumenty.bez_eksportu:
        cel = eksportuj(raport, baza=argumenty.baza)
        print(f"\n  snapshot do przeglądu: {cel.resolve()}")
    print(f"  baza: {argumenty.baza.resolve()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
