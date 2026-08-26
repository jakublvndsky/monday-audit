"""Generuje `front/src/api.ts` z kontraktu `pulpit.do_json()`.

    uv run python -m monday_audit.generuj_typy          # zapisz
    uv run python -m monday_audit.generuj_typy --sprawdz  # tylko sprawdź (CI)

## Dlaczego generowane, a nie pisane ręcznie

`pulpit.do_json()` zwraca strukturę, którą front musi znać. Gdyby typy
TypeScriptu były pisane ręcznie, **rozjechałyby się przy pierwszej zmianie
pola — i to cicho**, bo `tsc` nie widzi Pythona, a Python nie widzi `.ts`.
Objawiłoby się to `undefined` w interfejsie u klienta, nie błędem u nas.

Więc: jedno źródło prawdy (dataclassy w `pulpit.py`), generator, i test
`--sprawdz`, który zatrzymuje CI, gdy plik jest nieaktualny. Ten sam wzorzec
co `ruff format --check`.

Nie sięgamy po `pydantic2ts` ani generator z OpenAPI: obie drogi dokładają
zależność i krok budowania, a mamy do przeniesienia sześć dataclass.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import types
import typing
from pathlib import Path

from monday_audit.podglad_zakresu import PodgladKonta, TablicaDoWyboru, WorkspaceDoWyboru
from monday_audit.pulpit import (
    KLUCZE_WEWNETRZNE,
    Ludzie,
    Metryka,
    PozycjaKlienta,
    PozycjaRunu,
    ProfilOsoby,
    ProfilTablicy,
    Pulpit,
    Sekcja,
    UdzialOsoby,
    UdzialWTablicy,
)
from monday_audit.raport import Finding
from monday_audit.wybor_zakresu import (
    PozycjaTablicy,
    PozycjaWorkspace,
    Widelki,
    WyborZakresu,
)

logger = logging.getLogger(__name__)

CEL = Path("front/src/api.ts")

# Kolejność ma znaczenie: TypeScript wymaga definicji przed użyciem tylko
# w typach rekurencyjnych, ale czytelniej jest od dołu do góry.
# Struktury zakładki „Ludzie" PRZED `Pulpit`, bo on się do nich odwołuje.
# Lista jest jawna, nie skanowaniem modułu: dopisanie dataclassy do `pulpit.py`
# nie może po cichu wystawić jej do frontu — a test `test_typy_frontu_sa_aktualne`
# przypomni o dopisaniu tutaj, gdy pole wejdzie do `Pulpit`.
KLASY = (
    Metryka,
    Sekcja,
    Finding,
    PozycjaRunu,
    UdzialWTablicy,
    UdzialOsoby,
    ProfilOsoby,
    ProfilTablicy,
    Ludzie,
    Pulpit,
    PozycjaKlienta,
    # Ekran wyboru zakresu — z `wybor_zakresu.py`, przez `wybor_do_json()`.
    # Ten sam powód, co przy pulpicie: front musi znać kształt, a ręczne typy
    # rozjechałyby się cicho. `WyborZakresu` na końcu, bo odwołuje się do trzech
    # poprzednich.
    PozycjaWorkspace,
    PozycjaTablicy,
    Widelki,
    WyborZakresu,
    # Szybki podgląd PRZED zbieraniem — z `podglad_zakresu.py`. Osobne typy od
    # `PozycjaTablicy`, bo podgląd WIE MNIEJ: bez logów nie zna wpisów ani ciszy.
    # Wspólny typ zmuszałby front do pól, które w jednym trybie zawsze są puste.
    WorkspaceDoWyboru,
    TablicaDoWyboru,
    PodgladKonta,
)

NAGLOWEK = """// PLIK GENEROWANY — nie edytuj ręcznie.
//
// Źródło: dataclassy w `src/monday_audit/pulpit.py`, przez `pulpit.do_json()`.
// Regeneracja:  uv run python -m monday_audit.generuj_typy
//
// Ręczne typy rozjechałyby się z Pythonem przy pierwszej zmianie pola, i to
// cicho — `tsc` nie widzi Pythona. Test `--sprawdz` zatrzymuje CI, gdy ten plik
// jest nieaktualny.
//
// UWAGA na pola opcjonalne: w wariancie KLIENTOWYM `do_json()` USUWA klucze
// wewnętrzne ze struktury (nie zeruje ich), dlatego są tu oznaczone `?`.
// To nie luźność typu, a odwzorowanie granicy bezpieczeństwa (D16).
"""


def _typ_ts(adnotacja: object) -> str:
    """Adnotacja Pythona → typ TypeScriptu. Tylko to, co faktycznie mamy."""
    if adnotacja is type(None):
        return "null"
    proste: dict[object, str] = {
        int: "number",
        float: "number",
        str: "string",
        bool: "boolean",
    }
    if adnotacja in proste:
        return proste[adnotacja]
    if adnotacja is typing.Any:
        return "unknown"

    pochodzenie = typing.get_origin(adnotacja)
    argumenty = typing.get_args(adnotacja)

    if pochodzenie in (types.UnionType, typing.Union):
        # `dict.fromkeys` zachowuje kolejność i usuwa duplikaty. Bez tego
        # `float | int` wychodziło jako „number | number" — poprawne dla tsc,
        # ale wygląda na usterkę generatora i podważa zaufanie do pliku.
        return " | ".join(dict.fromkeys(_typ_ts(a) for a in argumenty))
    if pochodzenie in (list, tuple):
        # `tuple[X, ...]` i `list[X]` to po stronie JSON-a to samo: tablica.
        wnetrze = argumenty[0] if argumenty else typing.Any
        return f"{_typ_ts(wnetrze)}[]"
    if pochodzenie is dict:
        klucz = _typ_ts(argumenty[0]) if argumenty else "string"
        wartosc = _typ_ts(argumenty[1]) if len(argumenty) > 1 else "unknown"
        return f"Record<{klucz}, {wartosc}>"
    if dataclasses.is_dataclass(adnotacja) and isinstance(adnotacja, type):
        return adnotacja.__name__
    return "unknown"


def _interfejs(klasa: type) -> str:
    pola = []
    wskazowki = typing.get_type_hints(klasa)
    for pole in dataclasses.fields(klasa):
        typ = _typ_ts(wskazowki[pole.name])
        # Klucze wewnętrzne NIE ISTNIEJĄ w payloadzie klienta, więc muszą być
        # opcjonalne — inaczej front zakładałby ich obecność i czytał `undefined`
        # jak wartość.
        znak = "?" if pole.name in KLUCZE_WEWNETRZNE else ""
        pola.append(f"  {pole.name}{znak}: {typ};")
    return f"export interface {klasa.__name__} {{\n" + "\n".join(pola) + "\n}"


def _wlasciwosci_pulpitu() -> str:
    """Właściwości `@property` nie są polami, a `do_json` je dokłada.

    Bez tego front nie wiedziałby o `findingow`, `ma_kwoty` ani `dla_klienta` —
    a to na nich stoją warunki w widoku.
    """
    return (
        "  // dokładane przez `do_json()` z `@property` — nie są polami dataclassy\n"
        "  findingow: number;\n"
        "  ma_kwoty: boolean;\n"
        "  ma_porownanie: boolean;\n"
        "  dla_klienta: boolean;"
    )


def zbuduj_tresc() -> str:
    czesci = [NAGLOWEK]
    for klasa in KLASY:
        tekst = _interfejs(klasa)
        if klasa is Pulpit:
            tekst = tekst.rstrip("}\n").rstrip() + "\n" + _wlasciwosci_pulpitu() + "\n}"
        if klasa is Ludzie:
            tekst = tekst.rstrip("}\n").rstrip() + (
                "\n  // z `@property` — liczniki kategorii. Front ich NIE liczy sam:\n"
                "  // nagłówek `3 osoby, 3 agenty AI, 2 konta nieznane` musi być\n"
                "  // spójny z listą niżej, a dwa liczenia to dwa miejsca na rozjazd.\n"
                "  ludzi: number;\n"
                "  agentow_ai: number;\n"
                "  nieznanych: number;\n}"
            )
        if klasa is ProfilOsoby:
            tekst = tekst.rstrip("}\n").rstrip() + (
                '\n  // z `@property` — skrót na `rodzaj === "czlowiek"`\n'
                "  to_czlowiek: boolean;\n}"
            )
        if klasa is Metryka:
            tekst = tekst.rstrip("}\n").rstrip() + (
                "\n  // z `@property` — udział wyliczony, `null` gdy brak mianownika\n"
                "  udzial: number | null;\n}"
            )
        if klasa is PozycjaTablicy:
            tekst = tekst.rstrip("}\n").rstrip() + (
                "\n  // z `@property` — czy tablica ma choć jedną etykietę.\n"
                '  // Front NIE liczy tego sam: przycisk „odznacz oflagowane"\n'
                "  // musi działać na tym samym kryterium, co widoczne etykiety.\n"
                "  oflagowana: boolean;\n}"
            )
        if klasa is Widelki:
            tekst = tekst.rstrip("}\n").rstrip() + (
                "\n  // z `@property` — true, gdy któraś klasa nie ma historii kosztu\n"
                "  // i weszła z wartości zapasowej. Ekran musi to powiedzieć, bo\n"
                "  // widełki są wtedy słabszą obietnicą.\n"
                "  oszacowane_z_zapasu: boolean;\n}"
            )
        if klasa is TablicaDoWyboru:
            tekst = tekst.rstrip("}\n").rstrip() + (
                "\n  // z `@property` — czy tablica ma choć jedną etykietę\n"
                "  oflagowana: boolean;\n}"
            )
        if klasa is PodgladKonta:
            tekst = tekst.rstrip("}\n").rstrip() + (
                "\n  // dokładane przez endpoint: ile wywołań monday zużył podgląd\n"
                "  wywolan: number;\n}"
            )
        if klasa is WyborZakresu:
            tekst = tekst.rstrip("}\n").rstrip() + (
                "\n  // dokładane przez endpoint `/api/audyt/{id}/wybor`, nie przez\n"
                "  // `wybor_do_json()` — termin ważności zgody żyje w zadaniu,\n"
                "  // nie w snapshocie.\n"
                "  zgoda_do: string | null;\n}"
            )
        czesci.append(tekst)

    czesci.append(
        "export interface Ja {\n"
        '  rola: "klient" | "zespol";\n'
        "  client_id: string | null;\n"
        "  email: string | null;\n"
        "}"
    )
    czesci.append(
        "export interface StanAudytu {\n"
        "  id: string;\n"
        "  stan: string;\n"
        "  etap: string | null;\n"
        "  postep: number | null;\n"
        "  run_id: string | null;\n"
        "  blad: string | null;\n"
        "  trwa: boolean;\n"
        "  // `trwa: false` ma DWA znaczenia: skończone albo czekające na decyzję\n"
        "  // o zakresie. Bez tego pola front zatrzymywałby odpytywanie i nie\n"
        "  // wiedział, że ma pokazać ekran wyboru.\n"
        "  czeka_na_zgode: boolean;\n"
        "}"
    )
    czesci.append(
        "export interface Mozliwosc {\n"
        "  wolno: boolean;\n"
        "  powod: string;\n"
        "  client_id: string;\n"
        "  // Zadanie czekające na wybór zakresu, jeśli takie jest. Front wraca\n"
        "  // po nim po odświeżeniu strony — bez tego zebrane dane byłyby\n"
        "  // nieosiągalne, a limit monday już zużyty.\n"
        "  zadanie_czekajace: string | null;\n"
        "}"
    )
    return "\n\n".join(czesci) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monday_audit.generuj_typy")
    parser.add_argument(
        "--sprawdz",
        action="store_true",
        help="nie zapisuj, tylko sprawdź aktualność (jak `ruff format --check`)",
    )
    parser.add_argument("--cel", type=Path, default=CEL)
    argumenty = parser.parse_args(argv)

    tresc = zbuduj_tresc()
    if argumenty.sprawdz:
        if not argumenty.cel.is_file():
            print(f"  BRAK {argumenty.cel} — uruchom generator", file=sys.stderr)
            return 1
        if argumenty.cel.read_text(encoding="utf-8") != tresc:
            print(
                f"  {argumenty.cel} jest NIEAKTUALNY wobec `pulpit.py` — "
                f"uruchom `uv run python -m monday_audit.generuj_typy`",
                file=sys.stderr,
            )
            return 1
        print(f"  {argumenty.cel} aktualny")
        return 0

    argumenty.cel.parent.mkdir(parents=True, exist_ok=True)
    argumenty.cel.write_text(tresc, encoding="utf-8")
    print(f"  zapisano {argumenty.cel} ({len(KLASY) + 3} typy)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
