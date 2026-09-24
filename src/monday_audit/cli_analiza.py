"""Analiza konta jedną sesją: detektory → model → uwagi krytyczne (fazy 5b, 5c).

    uv run python -m monday_audit.cli_analiza --klient cxlabs --zakres workspace --id 7465500
    uv run python -m monday_audit.cli_analiza --zakres cale_konto --wejscie obraz.json
    uv run python -m monday_audit.cli_analiza --zakres cale_konto --tylko-szacunek
    uv run python -m monday_audit.cli_analiza --snapshot 1      # stary, zapisany snapshot

## Minimalne przechowywanie (faza 5c) — dwa połączenia, i to jest sedno

Decyzja Kuby z 2026-09-23: po runie na dysku nie zostaje nic, co dotyczy
konkretnej osoby. Kod robi to przez podział na dwie bazy, a nie przez
sprzątanie po fakcie:

* **`zrodlo` — SQLite W PAMIĘCI.** Tu collector zapisuje snapshot i tabelę
  `osoby_mapowanie` (prawdziwe imiona, nazwiska, maile). Tu pracują detektory
  i narzędzia agenta. Znika razem z procesem — nie ma czego usuwać, bo nigdy
  nie było na dysku.
* **`trwala` — baza na dysku.** Trafia do niej WYŁĄCZNIE to, co przeszło przez
  `przechowanie.py`: metadane runu, koszt, liczby i uwagi po maskowaniu.

Sprzątanie po fakcie zawodzi w najgorszym momencie: wystarczy, że proces padnie
między zapisem a usunięciem. Baza w pamięci nie ma takiego okna.

`--snapshot N` zostaje dla snapshotów zebranych przed tą fazą — wtedy `zrodlo`
jest bazą trwałą, bo tam już leżą. Nowych tak nie zbieramy.

## Najpierw wynik, potem zapis

Raport idzie na wyjście PRZED zapisem do bazy. Pierwszy prawdziwy run tej
ścieżki padł na zapisie po opłaconej sesji i nie zostawił po sobie nic. Plik
z surową odpowiedzią tamto naprawiał, ale niósł pseudonimy na dysk — więc teraz
jest tylko na wyraźne żądanie (`--wyjscie`), a domyślnie ratunkiem jest ekran.

## Nic nie zapisuje do monday

Collector i narzędzia agenta idą przez `MondayClient`, który odrzuca `mutation`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from monday_audit.agent import AgentError
from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.cli import zbuduj_zakres
from monday_audit.konfiguracja import KonfiguracjaError, klucz_anthropic, sol_z_ustawien, wczytaj
from monday_audit.kontrakt import KontraktError
from monday_audit.koszt import porownaj
from monday_audit.raport_uwag import zapisz_html
from monday_audit.usluga import (
    AnalizaError,
    UslugaError,
    WynikAnalizy,
    analiza_konta,
    analizuj_snapshot,
)
from monday_audit.wysylka_langfuse import wysylka_z_ustawien

logger = logging.getLogger(__name__)


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analiza konta jedną sesją (uwagi krytyczne)")
    parser.add_argument("--klient", default="cxlabs", help="identyfikator klienta")
    zrodlo = parser.add_mutually_exclusive_group(required=True)
    zrodlo.add_argument(
        "--zakres",
        choices=("cale_konto", "workspace", "tablice"),
        help="zbierz snapshot DO PAMIĘCI i przeanalizuj — nic o osobach nie trafia na dysk",
    )
    zrodlo.add_argument(
        "--snapshot",
        type=int,
        help="przeanalizuj snapshot zapisany w bazie przed fazą 5c",
    )
    parser.add_argument(
        "--id",
        action="append",
        default=[],
        help="workspace_id albo board_id przy --zakres; można podać wielokrotnie",
    )
    parser.add_argument("--baza", type=Path, default=None)
    parser.add_argument(
        "--wejscie",
        type=Path,
        default=None,
        help=(
            "plik z obrazem konta — wynik `cli_inwentarz --wejscie-modelu`. "
            "Bez niego model widzi sam snapshot i ma mniej do powiedzenia"
        ),
    )
    parser.add_argument(
        "--wyjscie",
        type=Path,
        default=None,
        metavar="KATALOG",
        help=(
            "zapisz PEŁNĄ odpowiedź modelu w tym katalogu (analiza_<run_id>.json). "
            "Domyślnie nie — plik niesie pseudonimy, więc ląduje na dysku tylko "
            "na wyraźne żądanie"
        ),
    )
    parser.add_argument(
        "--raport",
        type=Path,
        default=None,
        metavar="PLIK",
        help=(
            "zapisz raport Z NAZWISKAMI (HTML) do tego pliku. Tylko teraz — po runie "
            "mapowanie osób znika i raportu nie da się złożyć. Plik przekaż i usuń"
        ),
    )
    parser.add_argument(
        "--tylko-szacunek",
        action="store_true",
        help="policz koszt i wyjdź, BEZ wołania modelu — do decyzji przed wydaniem pieniędzy",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--json", action="store_true", help="wypisz surowy wynik zamiast tabelki")
    return parser


def _wczytaj_wejscie(sciezka: Path | None) -> dict[str, Any]:
    """Obraz konta z pliku albo pusty, gdy go nie podano."""
    if sciezka is None:
        return {}
    if not sciezka.is_file():
        raise AgentError(f"nie ma pliku z obrazem konta: {sciezka}")
    dane = json.loads(sciezka.read_text(encoding="utf-8"))
    if not isinstance(dane, dict):
        raise AgentError(f"{sciezka}: obraz konta nie jest obiektem JSON")
    return dane


def _wypisz(wynik: Any, uwagi: list[dict[str, Any]]) -> None:
    print(f"\n  {wynik.opis()}")
    print(f"  {'─' * 58}")
    for uwaga in uwagi:
        print(f"\n  [{uwaga['klasa_id']}]")
        print(f"  {uwaga['opis']}")
        print(f"  → {uwaga['rekomendacja']}")
        # Dowód wypisujemy ZAWSZE. To on odróżnia uwagę od opinii, więc
        # schowanie go za flagą `--szczegoly` byłoby schowaniem sedna.
        print(f"    dowód: {json.dumps(uwaga['dowod'], ensure_ascii=False)[:200]}")
    print(f"\n  {'─' * 58}")
    for odrzucona in wynik.odrzucone:
        print(f"  ODRZUCONA [{odrzucona.klasa_id}]: {odrzucona.powod}")


def zapisz_surowa_odpowiedz(run_id: str, odpowiedz: dict[str, Any], katalog: Path) -> Path:
    """Pełna odpowiedź modelu do pliku — TYLKO na wyraźne żądanie (`--wyjscie`).

    Do fazy 5c ten zapis szedł zawsze, jako ratunek po pierwszym runie, który
    padł po opłaconej sesji. Ale plik niesie pseudonimy, więc był danymi osoby
    na dysku. Ratunkiem jest teraz kolejność (wynik na wyjście przed zapisem),
    a plik — decyzją operatora, który wie, co z nim zrobi.
    """
    katalog.mkdir(parents=True, exist_ok=True)
    sciezka = katalog / f"analiza_{run_id}.json"
    sciezka.write_text(json.dumps(odpowiedz, ensure_ascii=False, indent=1), encoding="utf-8")
    return sciezka


def _oddaj_raport(argumenty: argparse.Namespace, wynik: WynikAnalizy) -> None:
    """Raport z nazwiskami do pliku — tylko z `--raport`. Pakiet oddał go w pamięci."""
    if argumenty.raport is None:
        print(
            "\n  raport z nazwiskami NIE powstał (bez `--raport PLIK`). Po tym runie "
            "złożyć go już się nie da — mapowanie osób znika z procesem."
        )
        return
    if wynik.raport_html is None:
        print("\n  raport z nazwiskami NIE powstał — renderowanie padło (szczegóły w logu).")
        return
    try:
        sciezka = zapisz_html(wynik.raport_html, argumenty.raport)
    except OSError:
        logger.exception("raport z nazwiskami NIE zapisany")
        return
    print(
        f"\n  raport z nazwiskami: {sciezka} (prawa 600). Zawiera dane osób — "
        "przekaż klientowi i usuń. Kopii na serwerze nie ma."
    )


def _wypisz_szacunek(podstawa: WynikAnalizy) -> None:
    print(
        f"\n  hipotez: {podstawa.hipotez} — do modelu {podstawa.do_modelu}, "
        f"z szablonu {podstawa.z_szablonu} (bez kosztu)"
    )
    for p in podstawa.poza_sufitem:
        print(f"  sufit {p.klasa_id}: {p.zbadanych} z {p.wszystkich} ({p.kryterium})")
    print(f"  {podstawa.szacunek.opis()}")


async def uruchom(argumenty: argparse.Namespace) -> int:
    """Cienka nakładka na `usluga.analiza_konta` (faza 6-2).

    Całe sedno — tryb pamięci, szablony, walidacja, zapis minimalny, trace'y,
    raport z nazwiskami — mieszka w pakiecie, bo to samo woła portal. Tu
    zostaje to, co jest sprawą operatora: skąd wziąć klucze, co wypisać
    i czy zapisać plik.
    """
    ustawienia = wczytaj()
    trwala = polacz(argumenty.baza or ustawienia.monday_audit_db)
    slad = None
    try:
        zastosuj_migracje(trwala)
        wejscie = _wczytaj_wejscie(argumenty.wejscie)
        if not wejscie:
            # Mówimy wprost zamiast udawać komplet. Model bez obrazu konta
            # rozstrzygnie hipotezy, ale nie powie, CO TO ZA KONTO.
            print(
                "UWAGA: bez `--wejscie` model nie widzi rollupów ani pokryć. "
                "Obraz konta robi `cli_inwentarz --wejscie-modelu`."
            )
        # Klucz modelu sprawdzany PRZED wywołaniami monday: brak ma przerwać,
        # zanim zużyjemy limit klienta (`konfiguracja.klucz_anthropic`).
        klucz_modelu = "" if argumenty.tylko_szacunek else klucz_anthropic(ustawienia)
        slad = None if argumenty.tylko_szacunek else wysylka_z_ustawien(ustawienia)
        wspolne: dict[str, Any] = {
            "klucz_anthropic": klucz_modelu,
            "client_id": argumenty.klient,
            "sol": sol_z_ustawien(ustawienia),
            "trwala": trwala,
            "wejscie": wejscie,
            "slad": slad,
            "run_id": argumenty.run_id,
            "tylko_szacunek": argumenty.tylko_szacunek,
            "przed_sesja": _wypisz_szacunek,
        }
        try:
            if argumenty.snapshot is not None:
                # Snapshot sprzed 5c leży w bazie trwałej — tam go analizujemy.
                wynik = await analizuj_snapshot(
                    zrodlo=trwala, snapshot_id=argumenty.snapshot, **wspolne
                )
            else:
                wynik = await analiza_konta(
                    ustawienia.monday_token.get_secret_value(),
                    zakres=zbuduj_zakres(argumenty.zakres, argumenty.id),
                    **wspolne,
                )
        except AnalizaError as blad:
            # Odpowiedź bez struktury: treść na EKRAN, nie na dysk. Za sesję
            # już zapłacono i nie wolno jej zgubić bez śladu.
            print(json.dumps(blad.odpowiedz_modelu, ensure_ascii=False, indent=1))
            raise KontraktError(str(blad)) from blad
    finally:
        # Bez dosłania bufora krótki proces CLI kończy się przed eksportem.
        if slad is not None:
            slad.zamknij()
        trwala.close()

    if not wynik.hipotez:
        print("Detektory nie wzbudziły ani jednej hipotezy — nie ma czego analizować.")
        return 0
    if wynik.run_id is None:
        return 0  # `--tylko-szacunek`

    if argumenty.json:
        print(json.dumps(wynik.do_json(), ensure_ascii=False))
    elif wynik.walidacja is not None:
        _wypisz(wynik.walidacja, wynik.walidacja.przyjete)

    if argumenty.wyjscie is not None and wynik.odpowiedz_modelu is not None:
        zapisz_surowa_odpowiedz(wynik.run_id, wynik.odpowiedz_modelu, argumenty.wyjscie)
    _oddaj_raport(argumenty, wynik)
    if wynik.blad_zapisu:
        print(f"\n  UWAGA: {wynik.blad_zapisu} — wynik jest wyżej, w bazie go brakuje.")

    # Szacunek OBOK rachunku — inaczej po kilku runach staje się ozdobą.
    print(f"\n  {porownaj(wynik.szacunek, wynik.zuzycie)}")
    miejsce = (
        f"snapshot {argumenty.snapshot} z bazy"
        if argumenty.snapshot is not None
        else "snapshot w PAMIĘCI, nic o osobach nie zostało na dysku"
    )
    print(f"  run: {wynik.run_id}, {wynik.sekund:.1f} s, {miejsce}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    argumenty = zbuduj_parser().parse_args(argv)
    try:
        return asyncio.run(uruchom(argumenty))
    except (AgentError, KontraktError, KonfiguracjaError, UslugaError) as blad:
        print(f"BŁĄD: {blad}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
