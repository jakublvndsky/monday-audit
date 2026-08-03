"""Ręczne uruchomienie pętli agenta na zamrożonym snapshocie (etap 3.11).

    uv run python -m monday_audit.cli_agent --klient cxlabs --snapshot 5

Osobne wejście od `cli`, bo to są dwie warstwy, które CLAUDE.md rozdziela
świadomie: collector spisuje deterministycznie, agent bada hipotezy. Collector
uderza w monday i kosztuje wywołania klienta; agent uderza w model i kosztuje
pieniądze. Zlanie ich w jedną komendę znaczyłoby, że nie da się powtórzyć
analizy bez ponownego zbierania danych — a etap 4 wymaga dokładnie tego:
przepuszczania TEGO SAMEGO snapshotu przez nową rubrykę i nowy prompt.

Sekrety przez `konfiguracja.wczytaj()` (D12). Brak klucza Anthropic przerywa
PRZED pierwszym wywołaniem monday, żeby nie zapłacić za dane, których i tak
nie zdążymy przeanalizować.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from monday_audit.agent import MODEL, zapisz_do_pliku, zbadaj_hipotezy
from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.detektory import uruchom_detektory
from monday_audit.klient import MondayClient
from monday_audit.konfiguracja import klucz_anthropic, sol_z_ustawien, wczytaj
from monday_audit.kontrakt import waliduj, zapisz_findingi, zapisz_odrzucone
from monday_audit.narzedzia import Narzedzia
from monday_audit.rubryka import wczytaj_rubryke

logger = logging.getLogger(__name__)


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monday_audit.cli_agent",
        description="Bada hipotezy ze snapshotu. Agent tylko czyta i tylko proponuje.",
    )
    parser.add_argument("--klient", required=True)
    parser.add_argument("--snapshot", type=int, required=True, help="id snapshotu z bazy")
    parser.add_argument("--baza", type=Path, default=None)
    parser.add_argument("--plik-env", type=Path, default=None, metavar="PLIK")
    parser.add_argument(
        "--klasy",
        action="append",
        default=[],
        metavar="KLASA",
        help="zawęź do wybranych klas; można podać wielokrotnie",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="zbadaj najwyżej N hipotez — do tanich prób przed pełnym runem",
    )
    parser.add_argument("--model", default=MODEL, help=f"domyślnie przypięty {MODEL}")
    parser.add_argument(
        "--budzet-monday",
        type=int,
        default=100,
        metavar="N",
        help="twardy sufit wywołań monday na CAŁY run agenta",
    )
    parser.add_argument("--run-id", default=None, help="domyślnie generowany ze znacznika czasu")
    return parser


async def uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj(argumenty.plik_env)
    # Przed czymkolwiek innym: brak klucza ma przerwać, zanim wydamy wywołanie
    # monday, którego nie odzyskamy.
    klucz_anthropic(ustawienia)
    sol = sol_z_ustawien(ustawienia)
    rubryka = wczytaj_rubryke()

    baza = argumenty.baza or ustawienia.monday_audit_db
    con = polacz(baza)
    # Migracje TUTAJ, nie tylko w `cli`. Baza produkcyjna powstała przed 002,
    # a `findings_odrzucone` doszła właśnie w 002 — bez tego run agenta wywala
    # się na zapisie odrzuconego findingu. Pełny run 19 hipotez tego nie
    # wykrył, bo lista odrzuconych była pusta i zapis nie dotknął tabeli.
    zastosowane = zastosuj_migracje(con)
    if zastosowane:
        logger.info("zastosowane migracje: %s", zastosowane)
    teraz = datetime.now(tz=UTC)
    run_id = argumenty.run_id or f"agent-{teraz.strftime('%Y%m%dT%H%M%SZ')}"

    hipotezy, raport_detektorow = uruchom_detektory(con, argumenty.snapshot, rubryka)
    if argumenty.klasy:
        hipotezy = [h for h in hipotezy if h.klasa_id in set(argumenty.klasy)]
    if argumenty.limit is not None:
        hipotezy = hipotezy[: argumenty.limit]
    if not hipotezy:
        print("zero hipotez do zbadania — nic do roboty")
        con.close()
        return 1

    print(f"run {run_id}: {len(hipotezy)} hipotez, model {argumenty.model}")
    print(f"  budżet zamówiony przez hipotezy: {sum(h.budzet_wywolan for h in hipotezy)} wywołań")
    print(f"  sufit runu: {argumenty.budzet_monday} wywołań monday\n")

    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at, model, rubric_ver) "
        "VALUES (?, ?, 'w_toku', ?, ?, ?)",
        (run_id, argumenty.klient, teraz.isoformat(), argumenty.model, rubryka.wersja),
    )
    con.commit()

    async with MondayClient(
        ustawienia.monday_token.get_secret_value(),
        RejestrWywolan(con, run_id),
        budzet_wywolan=argumenty.budzet_monday,
    ) as klient:
        zestaw = Narzedzia(
            con=con,
            snapshot_id=argumenty.snapshot,
            client_id=argumenty.klient,
            sol=sol,
            klient=klient,
        )
        odpowiedz = await zbadaj_hipotezy(
            hipotezy,
            zestaw=zestaw,
            rubryka=rubryka,
            run_id=run_id,
            model=argumenty.model,
        )

    wynik = waliduj(odpowiedz, rubryka)
    zapisz_findingi(
        con, wynik.przyjete, run_id=run_id, snapshot_id=argumenty.snapshot, rubryka=rubryka
    )
    zapisz_odrzucone(con, wynik.odrzucone, run_id=run_id, snapshot_id=argumenty.snapshot)

    con.execute(
        "UPDATE runy SET status = 'zakonczony', finished_at = ?, findingow = ?, "
        "odrzuconych_walidacja = ?, hipotez_zbadanych = ?, hipotez_odrzuconych = ?, "
        "tokens_in = ?, tokens_out = ? WHERE run_id = ?",
        (
            datetime.now(tz=UTC).isoformat(),
            len(wynik.przyjete),
            len(wynik.odrzucone),
            len(hipotezy),
            len(wynik.hipotezy_odrzucone),
            int(odpowiedz["zuzycie"].get("tokens_in", 0)),
            int(odpowiedz["zuzycie"].get("tokens_out", 0)),
            run_id,
        ),
    )
    con.commit()

    cel = zapisz_do_pliku(odpowiedz, wynik)
    print()
    print(f"  {wynik.opis()}")
    print(f"  nierozstrzygnięte: {len(odpowiedz.get('hipotezy_nierozstrzygniete') or [])}")
    print(f"  zużycie: {json.dumps(odpowiedz['zuzycie'], ensure_ascii=False)}")
    print(f"  klasy bez detektora: {raport_detektorow['klasy_bez_detektora']}")
    print(f"\n  raport do przeglądu: {cel.resolve()}")
    print(f"  baza: {baza.resolve()}")
    con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
    logging.getLogger("monday_audit.konfiguracja").setLevel(logging.INFO)
    logging.getLogger("monday_audit.agent").setLevel(logging.INFO)
    argumenty = zbuduj_parser().parse_args(argv)
    return asyncio.run(uruchom(argumenty))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
