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

from monday_audit.agent import MODEL, AgentError, hash_promptu
from monday_audit.analiza import (
    SCIEZKA_PROMPTU_ANALIZY,
    rozdziel_hipotezy,
    zbadaj_konto,
)
from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.detektory import uruchom_detektory
from monday_audit.konfiguracja import KonfiguracjaError, klucz_anthropic, sol_z_ustawien, wczytaj
from monday_audit.kontrakt import KontraktError
from monday_audit.koszt import historia_analiz, oszacuj, porownaj, zapisz_zuzycie_analizy
from monday_audit.narzedzia import Narzedzia
from monday_audit.obserwowalnosc import hasz_obrazu, wyslij_bezpiecznie, zbuduj_trace_analizy
from monday_audit.przebieg import zapisz_zuzycie
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.uwagi import waliduj_uwagi
from monday_audit.wysylka_langfuse import wysylka_z_ustawien

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


KATALOG_WYNIKOW = Path("raporty")


def zapisz_surowa_odpowiedz(run_id: str, odpowiedz: dict[str, Any], katalog: Path) -> Path:
    """Surowa odpowiedź modelu na dysk — PIERWSZA rzecz po sesji.

    ZMIERZONE na pierwszym runie 5b-2: model odpowiedział, walidacja zadziałała,
    a potem zapis zużycia padł na kluczu obcym i proces zakończył się PRZED
    wypisaniem uwag. Zapłaciliśmy za run i straciliśmy i treść, i faktyczny
    koszt. Od teraz nic, co może paść, nie stoi między sesją a tym zapisem.

    `raporty/` jest w `.gitignore` — plik niesie treść o koncie klienta.
    """
    katalog.mkdir(parents=True, exist_ok=True)
    sciezka = katalog / f"analiza_{run_id}.json"
    sciezka.write_text(json.dumps(odpowiedz, ensure_ascii=False, indent=1), encoding="utf-8")
    return sciezka


async def uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj()
    baza = argumenty.baza or ustawienia.monday_audit_db
    con = polacz(baza)
    try:
        # Jak w każdym innym CLI tego repo. Pierwsza wersja to pominęła,
        # a bez tego kolumna `zuzycie_hipotez.hipotez` (migracja 013) nie
        # powstałaby w istniejącej bazie i zapis zużycia padłby po opłaconym runie.
        zastosuj_migracje(con)
        rubryka = wczytaj_rubryke()
        hipotezy, raport = uruchom_detektory(con, argumenty.snapshot, rubryka)
        if not hipotezy:
            print("Detektory nie wzbudziły ani jednej hipotezy — nie ma czego analizować.")
            return 0

        # Szablony PRZED modelem — to jest wiedza starej ścieżki, którą pierwsza
        # wersja nowej zgubiła (patrz `analiza.rozdziel_hipotezy`).
        do_modelu, z_szablonow = rozdziel_hipotezy(hipotezy, rubryka)

        wejscie = _wczytaj_wejscie(argumenty.wejscie)
        if not wejscie:
            # Mówimy wprost zamiast udawać komplet. Model bez obrazu konta
            # rozstrzygnie hipotezy, ale nie powie, CO TO ZA KONTO.
            print(
                "UWAGA: bez `--wejscie` model nie widzi rollupów ani pokryć. "
                "Obraz konta robi `cli_inwentarz --wejscie-modelu`."
            )

        # Szacunek liczymy WYŁĄCZNIE dla tego, co pójdzie do modelu. Szablon
        # kosztuje zero, więc liczenie go zawyżałoby kwotę bez powodu.
        szacunek = oszacuj(len(do_modelu), historia_analiz(con))
        print(
            f"\n  hipotez: {len(hipotezy)} — do modelu {len(do_modelu)}, "
            f"z szablonu {len(z_szablonow)} (bez kosztu)"
        )
        print(f"  klasy bez detektora: {raport.get('bez_detektora') or []}")
        print(f"  {szacunek.opis()}")

        if argumenty.tylko_szacunek:
            return 0

        run_id = argumenty.run_id or f"analiza-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        # Wiersz w `runy` PRZED sesją, jak w `cli_agent`. `zuzycie_hipotez.run_id`
        # ma klucz obcy do `runy` — pierwsza wersja tego nie robiła i padła na
        # zapisie zużycia po opłaconym runie. Test tego nie złapał, bo stawiał
        # schemat w pamięci BEZ klucza obcego.
        con.execute(
            "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
            "rubric_ver, prompt_hash) VALUES (?, ?, ?, 'w_toku', ?, ?, ?, ?)",
            (
                run_id,
                argumenty.klient,
                argumenty.snapshot,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                MODEL,
                rubryka.wersja,
                hash_promptu(SCIEZKA_PROMPTU_ANALIZY),
            ),
        )
        con.commit()

        # Ślad do Langfuse — `None`, gdy nie jest skonfigurowany, i to jest stan
        # domyślny. Do fazy 5b nowa ścieżka nie wysyłała trace'ów WCALE: tracing
        # z fazy 4 siedział w `agent.zbadaj_hipotezy`, a `zbadaj_konto` to osobna
        # funkcja. Obserwowalność nie obejmowała ścieżki, która ma ją zastąpić.
        slad = wysylka_z_ustawien(ustawienia)
        hipotezy_do_trace = [h.do_zapisu() for h in do_modelu]
        wspolne = {
            "run_id": run_id,
            "snapshot_id": argumenty.snapshot,
            "model": MODEL,
            "prompt_hash": hash_promptu(SCIEZKA_PROMPTU_ANALIZY),
            "obraz_hash": hasz_obrazu(wejscie),
            "hipotezy": hipotezy_do_trace,
            "z_szablonu": len(z_szablonow),
            "szacunek_usd": szacunek.koszt_usd,
        }

        try:
            zaczeto = time.monotonic()
            if do_modelu:
                zestaw = Narzedzia(
                    con=con,
                    snapshot_id=argumenty.snapshot,
                    client_id=argumenty.klient,
                    sol=sol_z_ustawien(ustawienia),
                    klient=None,
                )
                odpowiedz = await zbadaj_konto(
                    do_modelu,
                    zestaw=zestaw,
                    wejscie=wejscie,
                    klucz_api=klucz_anthropic(ustawienia),
                    rubryka=rubryka,
                )
            else:
                odpowiedz = {"uwagi": [], "pominiete": [], "zuzycie": {}}
            sekund = round(time.monotonic() - zaczeto, 3)

            # PIERWSZY zapis po sesji. Nic, co może paść, nie stoi przed nim.
            plik = zapisz_surowa_odpowiedz(run_id, odpowiedz, KATALOG_WYNIKOW)

            # Kopia PRZED doklejeniem szablonów. Trace generacji opisuje wywołanie
            # modelu — uwaga z szablonu w jego wyjściu kazałaby przypisać modelowi
            # coś, czego nie napisał.
            odpowiedz_modelu = dict(odpowiedz)

            odpowiedz["uwagi"] = z_szablonow + list(odpowiedz.get("uwagi") or [])
            wynik = waliduj_uwagi(odpowiedz, rubryka)
            zuzycie = odpowiedz.get("zuzycie") or {}

            zapisz_zuzycie(con, run_id, zuzycie)
            zapisz_zuzycie_analizy(
                con,
                run_id,
                zuzycie,
                ile_hipotez=len(do_modelu),
                ile_uwag=len(wynik.przyjete),
                wywolan_narzedzi=len(odpowiedz.get("wywolania_narzedzi") or []),
                sekund=sekund,
            )
            con.execute(
                "UPDATE runy SET status = 'zakonczony', finished_at = ?, findingow = ? "
                "WHERE run_id = ?",
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), len(wynik.przyjete), run_id),
            )
            con.commit()

            # Trace PO zapisie do bazy, nie przed. Kolejność ma znaczenie tylko
            # w jedną stronę: padnięty eksport nie może zabrać wyniku, a
            # `wyslij_bezpiecznie` i tak nie przepuszcza żadnego wyjątku.
            if do_modelu:
                wyslij_bezpiecznie(
                    slad,
                    lambda: zbuduj_trace_analizy(
                        **wspolne,
                        odpowiedz=odpowiedz_modelu,
                        przyjetych=len(wynik.przyjete),
                        odrzucone_reguly=[o.regula for o in wynik.odrzucone],
                    ),
                    opis=f"analiza {run_id}",
                )
        except BaseException as awaria:
            # Run, który padł, jest NAJCIEKAWSZY w trace'ach — więc też go
            # wysyłamy. Tylko przy `Exception`: przy Ctrl-C człowiek chce
            # przerwać, a nie czekać na eksport.
            if isinstance(awaria, Exception) and do_modelu:
                # Tekst liczony TU, nie w lambdzie: nazwa z `except ... as` znika
                # po wyjściu z bloku, a lambda odwołuje się do nazw leniwie.
                opis_awarii = f"{type(awaria).__name__}: {awaria}"[:500]
                wyslij_bezpiecznie(
                    slad,
                    lambda: zbuduj_trace_analizy(
                        **wspolne,
                        odpowiedz=None,
                        blad=opis_awarii,
                    ),
                    opis=f"analiza {run_id} (awaria)",
                )
            # `przerwany`, nie zostawiony w `w_toku`. Wiersz, który wisi w toku
            # na zawsze, wygląda jak run, który wciąż trwa.
            con.execute(
                "UPDATE runy SET status = 'przerwany', finished_at = ? WHERE run_id = ?",
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), run_id),
            )
            con.commit()
            raise
        finally:
            # Bez dosłania bufora krótki proces CLI kończy się przed eksportem
            # i trace nie wychodzi wcale — także ten o awarii.
            if slad is not None:
                slad.zamknij()

        if argumenty.json:
            print(json.dumps({"uwagi": wynik.przyjete, "zuzycie": zuzycie}, ensure_ascii=False))
        else:
            _wypisz(wynik, wynik.przyjete)

        # Szacunek OBOK rachunku. Szacunek, którego nikt nie konfrontuje
        # z rachunkiem, po kilku runach staje się ozdobą.
        print(f"\n  {porownaj(szacunek, zuzycie)}")
        print(f"  run: {run_id}, {sekund:.1f} s, surowa odpowiedź: {plik}")
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
