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
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from monday_audit.agent import MODEL, zapisz_do_pliku, zbadaj_hipotezy
from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.cennik import Stawka, stawki_dla, wersja_uzytych, zapisz_stawke_klienta
from monday_audit.detektory import Hipoteza, uruchom_detektory
from monday_audit.klient import MondayClient
from monday_audit.konfiguracja import Ustawienia, klucz_anthropic, sol_z_ustawien, wczytaj
from monday_audit.kontrakt import (
    waliduj,
    zapisz_findingi,
    zapisz_hipotezy_odrzucone,
    zapisz_odrzucone,
)
from monday_audit.narzedzia import Narzedzia
from monday_audit.przebieg import przerwij_run
from monday_audit.rubryka import Rubryka, wczytaj_rubryke

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
    # Cena licencji NIE jest scrapowalna: na Enterprise jest negocjowana,
    # a publiczny cennik jej nie zawiera (O7). Bez tego argumentu findingi
    # wychodzą bez kwot i to jest poprawne zachowanie, nie awaria.
    parser.add_argument(
        "--koszt-licencji-mies",
        type=float,
        default=None,
        metavar="KWOTA",
        help="cena jednego miejsca u TEGO klienta, miesięcznie; bez niej kwoty zostają puste",
    )
    parser.add_argument(
        "--waluta-stawki", default="PLN", help="waluta --koszt-licencji-mies (domyślnie PLN)"
    )
    parser.add_argument(
        "--zrodlo-stawki",
        default=None,
        metavar="OPIS",
        help='skąd wzięta kwota, np. "faktura 07/2026" — raport musi powiedzieć, na czym stoi',
    )
    return parser


async def uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj(argumenty.plik_env)
    # Przed czymkolwiek innym: brak klucza ma przerwać, zanim wydamy wywołanie
    # monday, którego nie odzyskamy.
    klucz_anthropic(ustawienia)
    sol = sol_z_ustawien(ustawienia)
    rubryka = wczytaj_rubryke()

    baza = (argumenty.baza or ustawienia.monday_audit_db).absolute()
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

    # Stawka podana przy uruchomieniu jest DOPISYWANA do `stawki_klienta`,
    # nie trzymana w pamięci runu: kwota w raporcie klienta musi dać się
    # sprawdzić po fakcie, razem z datą i źródłem.
    if argumenty.koszt_licencji_mies is not None:
        zapisz_stawke_klienta(
            con,
            client_id=argumenty.klient,
            pozycja="koszt_licencji_mies",
            wartosc=argumenty.koszt_licencji_mies,
            waluta=argumenty.waluta_stawki,
            zrodlo=argumenty.zrodlo_stawki or f"podana przy uruchomieniu runu {run_id}",
        )

    # Bierzemy tylko te pozycje, których naprawdę żądają wzory klas W ZAKRESIE
    # tego runu. Wrzucanie agentowi stawek, których nie ma czym użyć, tylko
    # zachęca go do liczenia na nich czegokolwiek.
    potrzebne = {z for h in hipotezy for z in rubryka.po_id[h.klasa_id].zmienne_od_klienta}
    stawki = stawki_dla(con, potrzebne, client_id=argumenty.klient)
    brakuje = sorted(potrzebne - set(stawki))

    print(f"run {run_id}: {len(hipotezy)} hipotez, model {argumenty.model}")
    print(f"  budżet zamówiony przez hipotezy: {sum(h.budzet_wywolan for h in hipotezy)} wywołań")
    print(f"  sufit runu: {argumenty.budzet_monday} wywołań monday")
    for nazwa, stawka in sorted(stawki.items()):
        wiek = f", odczyt {stawka.dni_od_odswiezenia} dni temu" if not stawka.per_klient else ""
        blokada = "" if stawka.wolno_liczyc else "  NIE WOLNO LICZYĆ (przeterminowana)"
        print(
            f"  stawka {nazwa}: {stawka.wartosc:g} {stawka.waluta or stawka.jednostka} "
            f"({stawka.zrodlo}{wiek}){blokada}"
        )
    if brakuje:
        # Nie błąd. Findingi wyjdą bez kwot, a walidacja odrzuci każdą kwotę
        # podaną mimo braku stawki — świadomie, bo wymyślona kwota jest
        # gorsza od braku kwoty.
        print(f"  BEZ STAWKI (kwoty zostaną puste): {', '.join(brakuje)}")
    print()

    # Od tego miejsca istnieje wiersz w `runy` ze statusem `w_toku`, więc każde
    # wyjście MUSI go domknąć — sukcesem albo `przerwany`.
    con.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
        "rubric_ver, cennik_ver) VALUES (?, ?, ?, 'w_toku', ?, ?, ?, ?)",
        (
            run_id,
            argumenty.klient,
            argumenty.snapshot,
            teraz.isoformat(),
            argumenty.model,
            rubryka.wersja,
            wersja_uzytych(stawki),
        ),
    )
    con.commit()

    try:
        return await _zbadaj_i_zapisz(
            argumenty,
            con=con,
            ustawienia=ustawienia,
            sol=sol,
            rubryka=rubryka,
            hipotezy=hipotezy,
            stawki=stawki,
            run_id=run_id,
            baza=baza,
            raport_detektorow=raport_detektorow,
        )
    except BaseException as blad:
        # Ctrl+C w środku siedemnastominutowego runu jest częstszy od wyjątku.
        # Status musi to odnotować, inaczej `w_toku` znaczy „nie wiadomo".
        przerwij_run(con, run_id=run_id, powod=f"{type(blad).__name__}: {blad}")
        raise
    finally:
        con.close()


async def _zbadaj_i_zapisz(
    argumenty: argparse.Namespace,
    *,
    con: sqlite3.Connection,
    ustawienia: Ustawienia,
    sol: bytes,
    rubryka: Rubryka,
    hipotezy: list[Hipoteza],
    stawki: dict[str, Stawka],
    run_id: str,
    baza: Path,
    raport_detektorow: dict[str, Any],
) -> int:
    """Ciało runu agenta. Wydzielone, żeby `uruchom` był samym `try`."""
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
            stawki=stawki,
            klucz_api=klucz_anthropic(ustawienia),
        )

    # Te same stawki idą do walidacji. Kontrakt sprawdza MECHANICZNIE, czy
    # kwota ma z czego wyjść — prompt też o tym mówi, ale prompt jest warstwą
    # dodatkową (D6).
    wynik = waliduj(odpowiedz, rubryka, stawki)
    zapisz_findingi(
        con, wynik.przyjete, run_id=run_id, snapshot_id=argumenty.snapshot, rubryka=rubryka
    )
    zapisz_odrzucone(con, wynik.odrzucone, run_id=run_id, snapshot_id=argumenty.snapshot)
    # Hipotezy obalone przez AGENTA — inna rzecz niż findingi odrzucone przez
    # walidację i inna metryka. Renderer pokazuje je w wersji wewnętrznej.
    zapisz_hipotezy_odrzucone(con, wynik.hipotezy_odrzucone, run_id=run_id)

    con.execute(
        "UPDATE runy SET status = 'zakonczony', finished_at = ?, findingow = ?, "
        "odrzuconych_walidacja = ?, hipotez_zbadanych = ?, hipotez_odrzuconych = ?, "
        "tokens_in = ?, tokens_out = ?, koszt_usd = ? WHERE run_id = ?",
        (
            datetime.now(tz=UTC).isoformat(),
            len(wynik.przyjete),
            len(wynik.odrzucone),
            len(hipotezy),
            len(wynik.hipotezy_odrzucone),
            int(odpowiedz["zuzycie"].get("tokens_in", 0)),
            int(odpowiedz["zuzycie"].get("tokens_out", 0)),
            # Z `total_cost_usd` Agent SDK, nie z mnożenia tokenów przez własny
            # cennik — ten rozjechałby się przy pierwszej zmianie cen.
            float(odpowiedz["zuzycie"].get("koszt_usd") or 0.0) or None,
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
    print(f"\n  raport do przeglądu: {cel.absolute()}")
    print(f"  baza: {baza}")
    # Renderowanie jest darmowe i osobne (3.12) — podpowiadamy komendę,
    # żeby nikt nie szukał, jak zrobić z tego dokument.
    print(f"\n  dokument HTML: uv run python -m monday_audit.cli_raport --run-id {run_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
    logging.getLogger("monday_audit.konfiguracja").setLevel(logging.INFO)
    logging.getLogger("monday_audit.agent").setLevel(logging.INFO)
    argumenty = zbuduj_parser().parse_args(argv)
    return asyncio.run(uruchom(argumenty))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
