"""Brama promocji — czy tę wersję wolno wypuścić na produkcję.

Definition of Done etapu 5 wymaga jej wprost: „brama promocji zaimplementowana
jako **skrypt, nie procedura w głowie**". Procedura w głowie ma jedną wadę,
której nie widać, dopóki nie zaboli: pod presją człowiek pomija punkt, o którym
pamiętał wczoraj.

    uv run python evals/brama.py --run acme-20260825T183321Z-agent \\
        --zestaw evals/zloty_zestaw/acme_snapshot7.yaml

    # z porównaniem powtarzalności (dwa runy na TYM SAMYM snapshocie)
    uv run python evals/brama.py --run A --run-b B --zestaw ...

## Kod wyjścia jest odpowiedzią, nie wydruk

`0` — wolno promować. `1` — bloker, nie wolno. `2` — regresja wobec baseline'u,
**decyzja człowieka** (nie blokujemy automatycznie, ale nie przemilczamy).

Rozdzielenie 1 i 2 jest celowe: bloker to fakt („fałszywki 0,14 przy progu
0,1"), regresja to zmiana trendu, która może być świadomym kompromisem.
Sklejenie ich w jeden kod wyjścia zmuszałoby do czytania wydruku, żeby
zrozumieć, co się stało.

## Metryki NIE są tu liczone drugi raz

Wszystko przez `evals.mierz` — `zmierz()`, `zmierz_powtarzalnosc()`,
`PROGI_JAKOSCI`. Druga implementacja tych samych progów rozjechałaby się
z pierwszą przy najbliższej zmianie definicji, a wtedy brama mówiłaby coś
innego niż `mierz.py` i nie dałoby się rozstrzygnąć, która ma rację.

## Czego ta brama NIE robi

Nie odpala runu — wymaga runu, który już jest w bazie. Powód: run kosztuje
pieniądze (~1,5 USD za 24 hipotezy) i wywołania z limitu klienta. Brama, która
sama płaci, byłaby uruchamiana rzadziej, nie częściej.

Dlatego **nie wchodzi do CI**. GitHub Actions sprawdza kod; ta brama sprawdza
JAKOŚĆ WYNIKU, a to dwie różne rzeczy i tylko jedna z nich jest darmowa.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# `from mierz import`, nie `from evals.mierz` — `evals/` nie jest pakietem
# (nie ma `__init__.py`) i jest w `mypy_path`, więc podwójna ścieżka do tego
# samego pliku wywala mypy: „Source file found twice under different module
# names". Reszta katalogu też importuje płasko i uruchamia się jako skrypt.
from mierz import (
    PROG_POWTARZALNOSCI,
    PROGI_JAKOSCI,
    Wynik,
    zmierz,
    zmierz_powtarzalnosc,
)

from monday_audit.baza import polacz

# Progi z `docs/etapy/05-deploy.md`, sekcja „Brama promocji". Nie kopiujemy tych,
# które zna `mierz.PROGI_JAKOSCI` (trafność, fałszywki) — tamte importujemy.
PROG_ODRZUCONYCH_WALIDACJA = 0.15

# Regresja trafności wobec baseline'u, powyżej której potrzebna jest decyzja
# człowieka. Nie bloker: spadek o 0,06 może być ceną świadomej zmiany rubryki.
PROG_REGRESJI = 0.05

KATALOG_BASELINE = Path("evals/baseline")

KOD_OK = 0
KOD_BLOKER = 1
KOD_DECYZJA_CZLOWIEKA = 2


@dataclass(slots=True)
class Ocena:
    """Wynik bramy: co przeszło, co nie, i czego nie dało się sprawdzić."""

    run_id: str
    blokery: list[str] = field(default_factory=list)
    ostrzezenia: list[str] = field(default_factory=list)
    # Rzeczy, których brama NIE sprawdziła, bo nie miała danych. Osobna lista,
    # bo „nie sprawdzone" nie jest tym samym co „przeszło" — a przy pustej liście
    # blokerów łatwo pomylić jedno z drugim.
    niesprawdzone: list[str] = field(default_factory=list)
    liczby: dict[str, float] = field(default_factory=dict)

    @property
    def kod_wyjscia(self) -> int:
        if self.blokery:
            return KOD_BLOKER
        if self.ostrzezenia:
            return KOD_DECYZJA_CZLOWIEKA
        return KOD_OK


def _odsetek_odrzuconych(con: sqlite3.Connection, run_id: str) -> float | None:
    """Ile findingów odpadło na walidacji kontraktu. `None`, gdy run nie zapisał liczb.

    Liczymy z `runy`, nie z tabeli `findings_odrzucone`: run może mieć zero
    odrzuconych i wtedy tabela jest pusta, co jest nierozróżnialne od „run nie
    zapisał tych liczb".
    """
    wiersz = con.execute(
        "SELECT findingow, odrzuconych_walidacja FROM runy WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if wiersz is None:
        return None
    przyjete = wiersz["findingow"]
    odrzucone = wiersz["odrzuconych_walidacja"]
    if przyjete is None or odrzucone is None:
        return None
    razem = int(przyjete) + int(odrzucone)
    return int(odrzucone) / razem if razem else 0.0


def _puste_hipotezy_odrzucone(con: sqlite3.Connection, run_id: str) -> int:
    """Ile findingów nie ma ANI JEDNEJ obalonej hipotezy.

    `05-deploy.md` traktuje to jako bloker bezwzględny: finding bez śladu
    rozważania alternatyw to finding, którego agent nie zbadał, a stwierdził.
    Etap 4 mierzył to jako „niepuste `hipotezy_odrzucone` 100%".
    """
    wiersz = con.execute(
        "SELECT COUNT(*) c FROM findings f WHERE f.run_id = ? AND NOT EXISTS ("
        "  SELECT 1 FROM hipotezy_odrzucone h WHERE h.run_id = f.run_id"
        ")",
        (run_id,),
    ).fetchone()
    return int(wiersz["c"] or 0)


def _baseline(zestaw: Path) -> dict[str, float] | None:
    """Trafność z poprzedniej promocji, do porównania regresji.

    `evals/baseline/` jest świadomie WERSJONOWANY (`.gitignore` blokuje
    `zloty_zestaw/` i `wyniki/`, ale nie to) — bo brama promocji musi mieć
    z czym porównywać także na świeżym klonie repo.

    Brak pliku zwraca `None` i trafia do `niesprawdzone`, nie do `blokery`:
    pierwsza promocja nie ma z czym się porównać i to nie jest usterka.
    """
    plik = KATALOG_BASELINE / f"{zestaw.stem}.json"
    if not plik.exists():
        return None
    try:
        dane = json.loads(plik.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as blad:
        # Nieczytelny baseline to nie „brak baseline'u" — to zepsuty plik,
        # o którym trzeba powiedzieć, zamiast cicho pominąć porównanie.
        raise SystemExit(f"baseline {plik} nie da się odczytać: {blad}") from None
    return {k: float(v) for k, v in dane.items() if isinstance(v, int | float)}


def ocen_run(
    baza: Path,
    run_id: str,
    zestaw: Path,
    *,
    run_b: str | None = None,
) -> Ocena:
    """Cała brama. Zwraca ocenę, nie drukuje — wydruk jest w `_main`."""
    ocena = Ocena(run_id=run_id)
    wynik: Wynik = zmierz(baza, run_id, zestaw)

    ocena.liczby = {
        "trafnosc": wynik.trafnosc,
        "trafnosc_w_zasiegu": wynik.trafnosc_w_zasiegu,
        "falszywki": wynik.odsetek_falszywek,
        "rzeczowosc": wynik.rzeczowosc,
        "findingow": float(wynik.findingow),
    }

    # ── progi jakości: trafność i fałszywki, z `mierz.PROGI_JAKOSCI` ──
    for nazwa, spelniony in wynik.progi_spelnione.items():
        if spelniony:
            continue
        kierunek, prog = PROGI_JAKOSCI[nazwa]
        wartosc = ocena.liczby.get(nazwa, float("nan"))
        ocena.blokery.append(f"{nazwa} = {wartosc:.3f}, próg {kierunek} {prog}")

    # ── zgłoszenia niedopuszczalne: złoty zestaw ma je jawnie zakazane ──
    if wynik.zgloszone_niedopuszczalne:
        ocena.blokery.append(
            f"zgłoszono {len(wynik.zgloszone_niedopuszczalne)} findingów zakazanych "
            f"przez złoty zestaw: {', '.join(wynik.zgloszone_niedopuszczalne[:3])}"
        )

    con = polacz(baza)
    try:
        # ── odrzucone na walidacji kontraktu ──
        odrzucone = _odsetek_odrzuconych(con, run_id)
        if odrzucone is None:
            ocena.niesprawdzone.append(
                "odsetek odrzuconych na walidacji — run nie zapisał `findingow` "
                "i `odrzuconych_walidacja` w tabeli `runy`"
            )
        else:
            ocena.liczby["odrzuconych_walidacja"] = odrzucone
            if odrzucone > PROG_ODRZUCONYCH_WALIDACJA:
                ocena.blokery.append(
                    f"odrzuconych na walidacji = {odrzucone:.3f}, "
                    f"próg max {PROG_ODRZUCONYCH_WALIDACJA}"
                )

        # ── findingi bez obalonych hipotez ──
        bez_hipotez = _puste_hipotezy_odrzucone(con, run_id)
        if bez_hipotez:
            ocena.blokery.append(
                f"{bez_hipotez} findingów bez ani jednej obalonej hipotezy "
                "— agent stwierdził, nie zbadał"
            )

        # ── powtarzalność: wymaga DRUGIEGO runu na tym samym snapshocie ──
        if run_b:
            powt = zmierz_powtarzalnosc(con, run_id, run_b)
            ocena.liczby["powtarzalnosc"] = powt.zgodnosc
            # `prog_spelniony` z `mierz.py`, nie własne porównanie: gdyby próg
            # kiedyś się zmienił, brama nie może mieć o nim innego zdania niż
            # miernik.
            if not powt.prog_spelniony:
                ocena.blokery.append(
                    f"powtarzalność = {powt.zgodnosc:.3f}, próg min {PROG_POWTARZALNOSCI} "
                    f"(runy {run_id} vs {run_b}, wspólnych hipotez {powt.hipotez_wspolnych})"
                )
        else:
            ocena.niesprawdzone.append(
                "powtarzalność — podaj `--run-b` z drugim runem na TYM SAMYM "
                "snapshocie; bez tego nie wiemy, czy wynik jest stabilny"
            )
    finally:
        con.close()

    # ── regresja wobec baseline'u: OSTRZEŻENIE, nie bloker ──
    baza_odniesienia = _baseline(zestaw)
    if baza_odniesienia is None:
        ocena.niesprawdzone.append(
            f"regresja wobec baseline'u — brak {KATALOG_BASELINE / (zestaw.stem + '.json')}. "
            "Pierwsza promocja nie ma z czym porównywać; zapisz ten wynik jako baseline"
        )
    else:
        poprzednia = baza_odniesienia.get("trafnosc")
        if poprzednia is not None:
            spadek = poprzednia - wynik.trafnosc
            ocena.liczby["baseline_trafnosc"] = poprzednia
            if spadek > PROG_REGRESJI:
                ocena.ostrzezenia.append(
                    f"trafność spadła o {spadek:.3f} wobec baseline'u "
                    f"({poprzednia:.3f} → {wynik.trafnosc:.3f}), próg {PROG_REGRESJI} "
                    "— DECYZJA CZŁOWIEKA, nie bloker"
                )

    # ── testy antyprzeciekowe PII i injection ──
    #
    # `05-deploy.md` wymienia je jako blokery bezwzględne. Brama ich nie
    # uruchamia, bo to testy pytest, a nie metryka z runu — i uruchamianie
    # pytest z wnętrza bramy dawałoby dwa miejsca, w których „testy przeszły".
    # Zamiast tego mówimy WPROST, że to osobny warunek.
    ocena.niesprawdzone.append(
        "testy antyprzeciekowe PII i injection — uruchom `make sprawdz` "
        "(pilnują tego `tests/test_narzedzia.py` i `tests/test_web_granice.py`)"
    )
    return ocena


def _wypisz(ocena: Ocena) -> None:
    print(f"\n  BRAMA PROMOCJI — run {ocena.run_id}\n")
    for nazwa, wartosc in ocena.liczby.items():
        print(f"    {nazwa:26} {wartosc:.3f}")

    if ocena.blokery:
        print(f"\n  BLOKERY ({len(ocena.blokery)}) — NIE WOLNO PROMOWAĆ:")
        for b in ocena.blokery:
            print(f"    ✗ {b}")

    if ocena.ostrzezenia:
        print(f"\n  DO DECYZJI CZŁOWIEKA ({len(ocena.ostrzezenia)}):")
        for o in ocena.ostrzezenia:
            print(f"    ? {o}")

    if ocena.niesprawdzone:
        print(f'\n  NIESPRAWDZONE ({len(ocena.niesprawdzone)}) — brak danych, NIE „przeszło":')
        for n in ocena.niesprawdzone:
            print(f"    · {n}")

    if not ocena.blokery and not ocena.ostrzezenia:
        print("\n  Progi spełnione. Sprawdź jeszcze listę niesprawdzonych wyżej.")
    print()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brama",
        description="Brama promocji etapu 5 — czy tę wersję wolno wypuścić.",
    )
    parser.add_argument("--run", required=True, help="run_id agenta do oceny")
    parser.add_argument(
        "--run-b",
        default=None,
        help="drugi run na TYM SAMYM snapshocie — do powtarzalności",
    )
    parser.add_argument("--zestaw", required=True, type=Path, help="złoty zestaw YAML")
    parser.add_argument("--baza", type=Path, default=Path("monday_audit.db"))
    argumenty = parser.parse_args(argv)

    ocena = ocen_run(
        argumenty.baza,
        argumenty.run,
        argumenty.zestaw,
        run_b=argumenty.run_b,
    )
    _wypisz(ocena)
    return ocena.kod_wyjscia


if __name__ == "__main__":
    sys.exit(_main())
