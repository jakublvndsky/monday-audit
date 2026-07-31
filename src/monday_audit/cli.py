"""Ręczne uruchomienie collectora (etap 3.8).

    uv run python -m monday_audit.cli --klient cxlabs --zakres workspace --id 6576039

Sekrety idą przez `konfiguracja.wczytaj()` (D12): wypełniony `.env` obok repo
wystarcza, `export` nie jest potrzebny, a zmienna ustawiona w środowisku i tak
przebija plik. Token nie przechodzi przez argv — argv jest widoczne w `ps`.

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
import sys
from pathlib import Path

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.konfiguracja import Ustawienia, sol_z_ustawien, wczytaj
from monday_audit.konto import Zakres
from monday_audit.logi import MAKS_STRON_LOGOW, TOP_PO_ITEMACH, Z_OGONA
from monday_audit.postep import LicznikKonsolowy
from monday_audit.przebieg import BUDZET_STARTOWY, RaportRunu, wykonaj_run

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
    parser.add_argument(
        "--baza",
        type=Path,
        default=None,
        help="domyślnie MONDAY_AUDIT_DB ze środowiska albo .env, potem ./monday_audit.db",
    )
    parser.add_argument(
        "--plik-env",
        type=Path,
        default=None,
        metavar="PLIK",
        help="skąd wziąć sekrety; domyślnie MONDAY_AUDIT_ENV_FILE albo ./.env",
    )
    parser.add_argument(
        "--budzet-wywolan",
        type=int,
        default=None,
        metavar="N",
        help="twardy sufit wywołań; bez tej flagi sufit wynika z planu konta",
    )
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
        "--wszystkie-logi",
        action="store_true",
        help="bez próbkowania: activity logs z KAŻDEJ tablicy w zakresie "
        "(unieważnia --top-logow i --z-ogona)",
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


def ustal_baze(argumenty: argparse.Namespace, ustawienia: Ustawienia) -> Path:
    """Flaga bije środowisko, środowisko bije `.env`, `.env` bije wartość domyślną.

    Ta sama precedencja co dla sekretów, tylko ostatni szczebel jest tu jawny:
    `MONDAY_AUDIT_DB` na serwerze wskazuje bazę poza katalogiem repo, więc
    worker z etapu 5 nie potrzebuje wtedy żadnej flagi.
    """
    return argumenty.baza if argumenty.baza is not None else ustawienia.monday_audit_db


async def uruchom(
    argumenty: argparse.Namespace,
    *,
    ustawienia: Ustawienia | None = None,
    baza: Path | None = None,
) -> RaportRunu:
    """Argumenty opcjonalne, żeby `main` wczytał konfigurację raz, nie dwa razy.

    Jedno wczytanie to jedna linia w logu mówiąca, skąd wzięły się sekrety —
    dwie linie o tym samym pliku wyglądałyby jak dwa różne źródła.
    """
    if ustawienia is None:
        ustawienia = wczytaj(argumenty.plik_env)
    if baza is None:
        baza = ustal_baze(argumenty, ustawienia)
    sol = sol_z_ustawien(ustawienia)

    con = polacz(baza)
    zastosuj_migracje(con)
    licznik = LicznikKonsolowy(sys.stderr, co_ile=5)
    try:
        return await wykonaj_run(
            token=ustawienia.monday_token.get_secret_value(),
            con=con,
            client_id=argumenty.klient,
            zakres=zbuduj_zakres(argumenty.zakres, argumenty.id),
            sol=sol,
            postep=licznik,
            # Podany ręcznie budżet jest hamulcem — plan nie ma prawa go zwolnić.
            budzet_wywolan=argumenty.budzet_wywolan or BUDZET_STARTOWY,
            budzet_z_planu=argumenty.budzet_wywolan is None,
            dni_okna=argumenty.dni_okna,
            maks_sond=argumenty.maks_sond,
            top_logow=None if argumenty.wszystkie_logi else argumenty.top_logow,
            z_ogona=None if argumenty.wszystkie_logi else argumenty.z_ogona,
            maks_stron_logow=argumenty.maks_stron_logow,
        )
    finally:
        licznik.zakoncz()
        con.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
    # Reszta systemu zostaje na WARNING, ale skąd wzięły się sekrety człowiek
    # musi widzieć zawsze — inaczej nieznaleziony `.env` jest nierozróżnialny
    # od niewypełnionej zmiennej.
    logging.getLogger("monday_audit.konfiguracja").setLevel(logging.INFO)
    argumenty = zbuduj_parser().parse_args(argv)
    ustawienia = wczytaj(argumenty.plik_env)
    baza = ustal_baze(argumenty, ustawienia)

    raport = asyncio.run(uruchom(argumenty, ustawienia=ustawienia, baza=baza))
    print(raport.opis())

    if not argumenty.bez_eksportu:
        cel = eksportuj(raport, baza=baza)
        print(f"\n  snapshot do przeglądu: {cel.resolve()}")
    print(f"  baza: {baza.resolve()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
