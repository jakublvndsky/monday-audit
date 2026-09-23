"""Analiza konta jedną sesją: detektory → model → uwagi krytyczne (faza 5b-3).

    uv run python -m monday_audit.cli_analiza --klient cxlabs --snapshot 1
    uv run python -m monday_audit.cli_analiza --snapshot 1 --wejscie obraz.json
    uv run python -m monday_audit.cli_analiza --snapshot 1 --tylko-szacunek

## Dwa źródła, i to jest decyzja, nie prowizorka

**Detektory potrzebują SNAPSHOTU**, bo ich warunkiem odbioru jest „ten sam
snapshot daje tę samą listę hipotez" — czysty SQL po zamrożonym payloadzie.

**Obraz konta dla modelu pochodzi z NOWEGO potoku** (inwentarz → przegląd →
itemy → zestawienia), bo tylko tam są rollupy produktowe i pokrycia.

Te dwa potoki **współistnieją celowo**: nowy jest tanim pierwszym ekranem,
stary — głęboką analizą. Dlatego obraz konta wchodzi tu PLIKIEM (`--wejscie`),
a nie jest zbierany na nowo: pełny przebieg po itemach kosztuje ~1100 wywołań
z limitu klienta i nie ma powodu płacić go drugi raz przy każdej analizie.

Bez `--wejscie` analiza też działa — model dostaje wtedy sam snapshot
i mniej rzeczy do powiedzenia. Jest to stan gorszy, ale uczciwy, i CLI mówi
o nim wprost zamiast udawać komplet.

## Nic nie zapisuje do monday

Ta ścieżka czyta snapshot i woła model. Narzędzia agenta idą przez
`MondayClient`, który odrzuca `mutation` — ale tutaj klient nie jest nawet
tworzony, bo analiza stoi na zamrożonych danych.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from monday_audit.agent import AgentError, _tekst_promptu
from monday_audit.analiza import SCIEZKA_PROMPTU_ANALIZY, zbadaj_konto, zbuduj_zadanie
from monday_audit.baza import polacz
from monday_audit.detektory import uruchom_detektory
from monday_audit.konfiguracja import KonfiguracjaError, klucz_anthropic, sol_z_ustawien, wczytaj
from monday_audit.kontrakt import KontraktError
from monday_audit.koszt import oszacuj, porownaj, stawka_z_historii, zapisz_zuzycie_analizy
from monday_audit.narzedzia import Narzedzia
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.uwagi import waliduj_uwagi

logger = logging.getLogger(__name__)


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analiza konta jedną sesją (uwagi krytyczne)")
    parser.add_argument("--klient", default="cxlabs", help="identyfikator klienta")
    parser.add_argument("--snapshot", type=int, required=True, help="id snapshotu z bazy")
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


async def uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj(argumenty.baza and None)
    baza = argumenty.baza or ustawienia.monday_audit_db
    con = polacz(baza)
    try:
        rubryka = wczytaj_rubryke()
        hipotezy, raport = uruchom_detektory(con, argumenty.snapshot, rubryka)
        if not hipotezy:
            print("Detektory nie wzbudziły ani jednej hipotezy — nie ma czego analizować.")
            return 0

        wejscie = _wczytaj_wejscie(argumenty.wejscie)
        if not wejscie:
            # Mówimy wprost zamiast udawać komplet. Model bez obrazu konta
            # rozstrzygnie hipotezy, ale nie powie, CO TO ZA KONTO.
            print(
                "UWAGA: bez `--wejscie` model nie widzi rollupów ani pokryć. "
                "Obraz konta robi `cli_inwentarz --wejscie-modelu`."
            )

        zadanie = zbuduj_zadanie(hipotezy, wejscie)
        szacunek = oszacuj(
            zadanie,
            ile_hipotez=len(hipotezy),
            prompt=_tekst_promptu(SCIEZKA_PROMPTU_ANALIZY),
            stawka=stawka_z_historii(con),
        )
        print(f"\n  hipotez do rozstrzygnięcia: {len(hipotezy)}")
        print(f"  klasy bez detektora: {raport.get('bez_detektora') or []}")
        print(f"  {szacunek.opis()}")

        if argumenty.tylko_szacunek:
            return 0

        run_id = argumenty.run_id or f"analiza-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        zestaw = Narzedzia(
            con=con,
            snapshot_id=argumenty.snapshot,
            client_id=argumenty.klient,
            sol=sol_z_ustawien(ustawienia),
            klient=None,
        )

        zaczeto = time.monotonic()
        odpowiedz = await zbadaj_konto(
            hipotezy,
            zestaw=zestaw,
            wejscie=wejscie,
            klucz_api=klucz_anthropic(ustawienia),
        )
        sekund = round(time.monotonic() - zaczeto, 3)

        wynik = waliduj_uwagi(odpowiedz, rubryka)
        zuzycie = odpowiedz.get("zuzycie") or {}
        zapisz_zuzycie_analizy(
            con,
            run_id,
            zuzycie,
            ile_uwag=len(wynik.przyjete),
            wywolan_narzedzi=len(odpowiedz.get("wywolania_narzedzi") or []),
            sekund=sekund,
        )

        if argumenty.json:
            print(json.dumps({"uwagi": wynik.przyjete, "zuzycie": zuzycie}, ensure_ascii=False))
        else:
            _wypisz(wynik, wynik.przyjete)

        # Szacunek OBOK rachunku. Szacunek, którego nikt nie konfrontuje
        # z rachunkiem, po kilku runach staje się ozdobą.
        print(f"\n  {porownaj(szacunek, zuzycie)}")
        print(f"  run: {run_id}, {sekund:.1f} s")
        return 0
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    argumenty = zbuduj_parser().parse_args(argv)
    try:
        return asyncio.run(uruchom(argumenty))
    except (AgentError, KontraktError, KonfiguracjaError) as blad:
        print(f"BŁĄD: {blad}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
