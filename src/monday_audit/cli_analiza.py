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
import sqlite3
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
from monday_audit.baza import MapowanieOsob, polacz, zastosuj_migracje
from monday_audit.cli import zbuduj_zakres
from monday_audit.detektory import uruchom_detektory
from monday_audit.konfiguracja import KonfiguracjaError, klucz_anthropic, sol_z_ustawien, wczytaj
from monday_audit.kontrakt import KontraktError
from monday_audit.koszt import historia_analiz, oszacuj, porownaj, zapisz_zuzycie_analizy
from monday_audit.narzedzia import Narzedzia
from monday_audit.obserwowalnosc import hasz_obrazu, wyslij_bezpiecznie, zbuduj_trace_analizy
from monday_audit.przebieg import wykonaj_run, zapisz_zuzycie
from monday_audit.przechowanie import PrzechowanieError, zapisz_statystyki, zapisz_uwagi
from monday_audit.raport_uwag import oddaj_raport, zbuduj_raport_uwag
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.uwagi import waliduj_uwagi
from monday_audit.wysylka_langfuse import wysylka_z_ustawien

logger = logging.getLogger(__name__)

BAZA_W_PAMIECI = ":memory:"


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


def _oddaj_raport(
    argumenty: argparse.Namespace,
    wynik: Any,
    *,
    zrodlo: sqlite3.Connection,
    run_id: str,
    rubryka: Any,
    wejscie: dict[str, Any],
) -> None:
    """Raport z nazwiskami do pliku — tylko z `--raport`. Nigdy nie wywraca runu.

    Awaria renderowania nie może kosztować wyniku, za który zapłacono: ten
    już jest na ekranie, a zapis minimalny idzie dalej. Tracimy wtedy raport
    z nazwiskami i mówimy o tym głośno.
    """
    if argumenty.raport is None:
        print(
            "\n  raport z nazwiskami NIE powstał (bez `--raport PLIK`). Po tym runie "
            "złożyć go już się nie da — mapowanie osób znika z procesem."
        )
        return
    try:
        raport = zbuduj_raport_uwag(
            wynik.przyjete,
            con=zrodlo,
            client_id=argumenty.klient,
            run_id=run_id,
            run_at=_teraz(),
            rubryka=rubryka,
            pominietych=len(wynik.pominiete),
            zastrzezenia=tuple(wejscie.get("zastrzezenia") or ()),
        )
        sciezka = oddaj_raport(raport, argumenty.raport)
    except Exception:  # raport nie jest wynikiem — wynik jest już na ekranie
        logger.exception(
            "raport z nazwiskami NIE powstał — wynik jest na ekranie, zapis idzie dalej"
        )
        return
    print(
        f"\n  raport z nazwiskami: {sciezka} (prawa 600). Zawiera dane osób — "
        "przekaż klientowi i usuń. Kopii na serwerze nie ma."
    )


def _teraz() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _zbierz_do_pamieci(
    argumenty: argparse.Namespace, ustawienia: Any
) -> tuple[sqlite3.Connection, int, int | None]:
    """Collector do bazy W PAMIĘCI. Zwraca (połączenie, snapshot_id, wywołań).

    `wykonaj_run` zapisuje snapshot i tabelę `osoby_mapowanie` w połączeniu,
    które dostaje — więc wystarczy podać mu bazę w RAM-ie, żeby żadne z nich
    nie dotknęło dysku. Collector nie wie, że pracuje w pamięci, i nie musi.
    """
    zrodlo = polacz(BAZA_W_PAMIECI)
    zastosuj_migracje(zrodlo)
    raport = await wykonaj_run(
        token=ustawienia.monday_token.get_secret_value(),
        con=zrodlo,
        client_id=argumenty.klient,
        zakres=zbuduj_zakres(argumenty.zakres, argumenty.id),
        sol=sol_z_ustawien(ustawienia),
    )
    return zrodlo, raport.snapshot_id, raport.wywolan


async def uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj()
    trwala = polacz(argumenty.baza or ustawienia.monday_audit_db)
    zrodlo: sqlite3.Connection | None = None
    try:
        # Jak w każdym innym CLI tego repo. Pierwsza wersja to pominęła,
        # a bez tego nowe tabele nie powstałyby w istniejącej bazie.
        zastosuj_migracje(trwala)

        if argumenty.snapshot is not None:
            zrodlo, snapshot_id, wywolan_monday = trwala, argumenty.snapshot, None
        else:
            zrodlo, snapshot_id, wywolan_monday = await _zbierz_do_pamieci(argumenty, ustawienia)
        w_pamieci = zrodlo is not trwala

        rubryka = wczytaj_rubryke()
        hipotezy, raport = uruchom_detektory(zrodlo, snapshot_id, rubryka)
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
        szacunek = oszacuj(len(do_modelu), historia_analiz(trwala))
        print(
            f"\n  hipotez: {len(hipotezy)} — do modelu {len(do_modelu)}, "
            f"z szablonu {len(z_szablonow)} (bez kosztu)"
        )
        print(f"  klasy bez detektora: {raport.get('bez_detektora') or []}")
        print(f"  {szacunek.opis()}")

        if argumenty.tylko_szacunek:
            return 0

        run_id = argumenty.run_id or f"analiza-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        # Wiersz w `runy` PRZED sesją, jak w `cli_agent` — `zuzycie_hipotez.run_id`
        # ma klucz obcy do `runy`. `snapshot_id` tylko przy starym snapshocie:
        # snapshot z pamięci nie istnieje w tej bazie i klucz obcy by go odrzucił.
        trwala.execute(
            "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
            "rubric_ver, prompt_hash) VALUES (?, ?, ?, 'w_toku', ?, ?, ?, ?)",
            (
                run_id,
                argumenty.klient,
                None if w_pamieci else snapshot_id,
                _teraz(),
                MODEL,
                rubryka.wersja,
                hash_promptu(SCIEZKA_PROMPTU_ANALIZY),
            ),
        )
        trwala.commit()

        # Ślad do Langfuse — `None`, gdy nie jest skonfigurowany.
        slad = wysylka_z_ustawien(ustawienia)
        wspolne: dict[str, Any] = {
            # Znane osoby dla drugiej siatki maskowania. Z `zrodlo`, bo tam jest
            # tabela mapowania — w trybie pamięci znika razem z procesem.
            "wpisy": (
                tuple(MapowanieOsob(zrodlo, argumenty.klient).wczytaj()) if slad is not None else ()
            ),
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "model": MODEL,
            "prompt_hash": hash_promptu(SCIEZKA_PROMPTU_ANALIZY),
            "obraz_hash": hasz_obrazu(wejscie),
            "hipotezy": [h.do_zapisu() for h in do_modelu],
            "z_szablonu": len(z_szablonow),
            "szacunek_usd": szacunek.koszt_usd,
        }

        try:
            zaczeto = time.monotonic()
            if do_modelu:
                zestaw = Narzedzia(
                    con=zrodlo,
                    snapshot_id=snapshot_id,
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

            # Kopia PRZED doklejeniem szablonów. Trace generacji opisuje
            # wywołanie modelu — uwaga z szablonu w jego wyjściu kazałaby
            # przypisać modelowi coś, czego nie napisał.
            odpowiedz_modelu = dict(odpowiedz)
            if argumenty.wyjscie is not None:
                zapisz_surowa_odpowiedz(run_id, odpowiedz_modelu, argumenty.wyjscie)

            odpowiedz["uwagi"] = z_szablonow + list(odpowiedz.get("uwagi") or [])
            try:
                wynik = waliduj_uwagi(odpowiedz, rubryka)
            except KontraktError:
                # Odpowiedź bez struktury: treść na EKRAN, nie na dysk. Za sesję
                # już zapłacono i nie wolno jej zgubić bez śladu.
                print(json.dumps(odpowiedz_modelu, ensure_ascii=False, indent=1))
                raise
            zuzycie = odpowiedz.get("zuzycie") or {}

            # 1. WYNIK NA WYJŚCIE — zanim cokolwiek, co może paść, dotknie bazy.
            if argumenty.json:
                print(json.dumps({"uwagi": wynik.przyjete, "zuzycie": zuzycie}, ensure_ascii=False))
            else:
                _wypisz(wynik, wynik.przyjete)

            # 1b. RAPORT Z NAZWISKAMI — teraz albo nigdy (faza 5c, wariant A).
            # Mapowanie osób żyje w `zrodlo`, a w trybie pamięci znika razem
            # z procesem. Po tym miejscu raportu z nazwiskami nie da się złożyć.
            _oddaj_raport(
                argumenty, wynik, zrodlo=zrodlo, run_id=run_id, rubryka=rubryka, wejscie=wejscie
            )

            # 2. ZAPIS MINIMALNY — wyłącznie przez `przechowanie.py`.
            zapisz_zuzycie(trwala, run_id, zuzycie)
            zapisz_zuzycie_analizy(
                trwala,
                run_id,
                zuzycie,
                ile_hipotez=len(do_modelu),
                ile_uwag=len(wynik.przyjete),
                wywolan_narzedzi=len(odpowiedz.get("wywolania_narzedzi") or []),
                sekund=sekund,
            )
            # Dwa osobne `try`, bo to dwa osobne zapisy. W jednym bloku padnięte
            # statystyki zgłaszały „uwagi NIE zapisane" i zerowały `findingow`,
            # choć uwagi były już zatwierdzone w bazie (review 2026-09-23).
            try:
                zapisanych = zapisz_uwagi(trwala, run_id, wynik.przyjete)
            except PrzechowanieError:
                # Bramka zadziałała: w zapisie został identyfikator osoby. Raport
                # już wyszedł, więc tracimy wiersz w bazie, a nie wynik audytu.
                logger.exception("uwagi NIE zapisane — bramka przechowania zadziałała")
                zapisanych = 0
            if wejscie:
                try:
                    zapisz_statystyki(trwala, run_id, wejscie)
                except PrzechowanieError:
                    logger.exception(
                        "statystyki NIE zapisane — bramka przechowania zadziałała (uwagi bez zmian)"
                    )

            trwala.execute(
                "UPDATE runy SET status = 'zakonczony', finished_at = ?, findingow = ?, "
                "odrzuconych_walidacja = ?, hipotez_zbadanych = ?, hipotez_odrzuconych = ?, "
                "wywolania_monday = ? WHERE run_id = ?",
                (
                    _teraz(),
                    zapisanych,
                    len(wynik.odrzucone),
                    len(hipotezy),
                    len(wynik.pominiete),
                    wywolan_monday,
                    run_id,
                ),
            )
            trwala.commit()

            # Trace PO zapisie — padnięty eksport nie może zabrać wyniku.
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
            # Run, który padł, jest NAJCIEKAWSZY w trace'ach. Tylko przy
            # `Exception`: przy Ctrl-C człowiek chce przerwać, a nie czekać.
            if isinstance(awaria, Exception) and do_modelu:
                # Tekst liczony TU, nie w lambdzie: nazwa z `except ... as` znika
                # po wyjściu z bloku, a lambda odwołuje się do nazw leniwie.
                opis_awarii = f"{type(awaria).__name__}: {awaria}"[:500]
                wyslij_bezpiecznie(
                    slad,
                    lambda: zbuduj_trace_analizy(**wspolne, odpowiedz=None, blad=opis_awarii),
                    opis=f"analiza {run_id} (awaria)",
                )
            # `przerwany`, nie zostawiony w `w_toku` — wiersz wiszący w toku na
            # zawsze wygląda jak run, który wciąż trwa.
            trwala.execute(
                "UPDATE runy SET status = 'przerwany', finished_at = ? WHERE run_id = ?",
                (_teraz(), run_id),
            )
            trwala.commit()
            raise
        finally:
            # Bez dosłania bufora krótki proces CLI kończy się przed eksportem.
            if slad is not None:
                slad.zamknij()

        # Szacunek OBOK rachunku — inaczej po kilku runach staje się ozdobą.
        print(f"\n  {porownaj(szacunek, zuzycie)}")
        miejsce = (
            "snapshot w PAMIĘCI, nic o osobach nie zostało na dysku"
            if w_pamieci
            else (f"snapshot {snapshot_id} z bazy")
        )
        print(f"  run: {run_id}, {sekund:.1f} s, {miejsce}")
        return 0
    finally:
        # Baza w pamięci znika razem z tym zamknięciem — i to jest cały mechanizm.
        if zrodlo is not None and zrodlo is not trwala:
            zrodlo.close()
        trwala.close()


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
