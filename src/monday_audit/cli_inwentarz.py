"""Sześć kafelków dla konta monday — pierwszy ekran z user story.

    uv run python -m monday_audit.cli_inwentarz
    uv run python -m monday_audit.cli_inwentarz --json

Token bierze się z konfiguracji procesu (D12), nigdy z argv — `ps` pokazuje
argumenty każdemu na maszynie.

Nic nie zapisuje: to nie jest run, więc nie zakłada wiersza w `runy` ani
snapshotu. `RejestrPodgladu` trzyma liczniki w pamięci.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from monday_audit.inwentarz import zbuduj_inwentarz
from monday_audit.itemy import WynikItemow, zbuduj_itemy
from monday_audit.klient import MondayClient, MondayError
from monday_audit.konfiguracja import wczytaj
from monday_audit.konto import Zakres, rozpoznaj_konto
from monday_audit.podglad_zakresu import RejestrPodgladu
from monday_audit.przeglad_tablic import PrzegladTablic, pobierz_tablice, zbuduj_przeglad
from monday_audit.zestawienia import WynikZestawien, zbuduj_zestawienia

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inwentarz konta monday — sześć kafelków")
    parser.add_argument("--json", action="store_true", help="wypisz surowy JSON zamiast tabelki")
    parser.add_argument(
        "--itemy",
        action="store_true",
        help="dolicz itemy: leady, etapy lejka, przyrost dzienny (punkt 3 wytycznych)",
    )
    parser.add_argument(
        "--tablice",
        action="store_true",
        help="dolicz przegląd tablic: rodzaje, użytkownicy, aktywność, żywe automatyzacje",
    )
    return parser


def _wypisz_przeglad(przeglad: PrzegladTablic) -> None:
    print(f"\n  Tablice — przegląd ({przeglad.tablic} aktywnych)")
    print(f"  {'─' * 46}")
    print("  wg rodzaju:  " + ", ".join(f"{k} {v}" for k, v in przeglad.po_rodzaju.items()))
    print("  wg typu:     " + ", ".join(f"{k} {v}" for k, v in przeglad.po_typie.items()))
    print(
        f"  userów na tablicę: średnio {przeglad.userow_srednio}, mediana {przeglad.userow_mediana}"
    )
    print(
        f"  gości na tablicach: {przeglad.gosci_na_tablicach} "
        f"(na {przeglad.tablic_z_goscmi} tablicach)"
    )
    print("  ostatnia zmiana: " + ", ".join(f"{k} {v}" for k, v in przeglad.aktywnosc.items()))

    ile = len(przeglad.zywe_automatyzacje)
    print(f"\n  Tablice z ŻYWYMI automatyzacjami: {ile} z {przeglad.tablic}")
    print(f"  {'─' * 46}")
    for tablica in przeglad.zywe_automatyzacje[:15]:
        produkt = f" [{tablica.produkt}]" if tablica.produkt else ""
        bledy = f", {tablica.bledow} błędów" if tablica.bledow else ""
        nazwa = tablica.nazwa or tablica.board_id
        print(f"  {tablica.uruchomien:>5} uruchomień{bledy}  {nazwa}{produkt}")
    print(f"  {'─' * 46}")
    # Koszt DROŻSZEJ połowy. Do 2026-09-22 CLI chwaliło się tylko wywołaniami
    # kafelków, a te ~95 tutaj było niewidoczne — koszt, którego nie widać,
    # nie istnieje dla decydującego.
    print(f"  {przeglad.wywolan} wywołań z limitu konta (przegląd tablic)\n")
    for uwaga in przeglad.zastrzezenia:
        print(f"  UWAGA: {uwaga}")


def _wypisz_itemy(itemy: WynikItemow) -> None:
    print(f"\n  Itemy — {itemy.itemow_razem} na koncie")
    print(f"  {'─' * 46}")
    for produkt, ile in itemy.itemow_per_produkt.items():
        print(f"      {produkt:<14}{ile:>7}")
    plan = itemy.plan
    print(
        f"  rozkład policzony dla {len(plan.do_pobrania)} tablic, pominięto {len(plan.pominiete)}"
    )
    print(f"  {'─' * 46}")
    for t in itemy.tablice[:10]:
        stopien = {1: "lejek", 2: "grupy", 3: "—"}[t.lejek.stopien]
        czolo = ", ".join(f"{k} {v}" for k, v in list(t.rozklad.items())[:3])
        opis_ile = f"{t.itemow} itemów"
        if t.pobranych != t.itemow:
            # Rozbieżność `items_count` vs faktycznie pobrane ma być WIDOCZNA
            # przy tablicy, nie tylko w zastrzeżeniach na końcu (O47).
            opis_ile += f", pobrano {t.pobranych}"
        print(f"  {opis_ile:>30}  {t.nazwa or t.board_id}")
        print(f"          {stopien}: {czolo}")
        print(
            f"          przyrost {t.przyrost_dzienny}/dzień "
            f"({t.powstalo_w_oknie} w {t.okno_dni} dni)"
        )
    print(f"  {'─' * 46}")
    print(f"  {itemy.wywolan} wywołań z limitu konta (itemy)\n")
    for uwaga in itemy.zastrzezenia:
        print(f"  UWAGA: {uwaga}")

    _wypisz_zestawienia(zbuduj_zestawienia(itemy.tablice))


def _wypisz_zestawienia(wynik: WynikZestawien) -> None:
    """Rollupy produktowe. Zero wywołań — liczone z tego, co już mamy."""
    if not wynik.zestawienia:
        return
    print("\n  Rollupy produktowe")
    print(f"  {'─' * 46}")
    for z in wynik.zestawienia:
        print(
            f"  {z.produkt.upper()} — {z.tablic} tablic ({z.tablic_z_lejkiem} z lejkiem), "
            f"{z.itemow_deklarowanych} itemów"
        )
        print(
            f"      w toku {z.w_toku}, wygrane {z.wygrane}, odpadło {z.odpadlo}, "
            f"zamknięte {z.zamkniete}, bez etapu {z.bez_etapu}"
        )
        print(
            f"      przyrost {z.przyrost_dzienny}/dzień, "
            f"zamknięć ~{z.zamkniec_dziennie}/dzień (szacunek z ilorazu)"
        )
        print(f"      pokrycie {z.pokrycie:.1%}")
        # Etykiety policzone jako OTWARTE — to jest miejsce, w którym człowiek
        # w dziesięć sekund wyłapie, że wśród szans siedzi „Archiwum".
        if z.etykiety_w_toku:
            widoczne = ", ".join(z.etykiety_w_toku[:8])
            reszta = len(z.etykiety_w_toku) - 8
            print(f"      jako otwarte: {widoczne}" + (f" (+{reszta})" if reszta > 0 else ""))
    print(f"  {'─' * 46}\n")
    for uwaga in wynik.zastrzezenia:
        print(f"  UWAGA: {uwaga}")


async def _wykonaj(jako_json: bool, z_tablicami: bool, z_itemami: bool) -> int:
    ustawienia = wczytaj()
    token = ustawienia.monday_token.get_secret_value() if ustawienia.monday_token else ""
    if not token:
        print("BŁĄD: MONDAY_TOKEN pusty. Wpisz go do .env.", file=sys.stderr)
        return 1

    rejestr = RejestrPodgladu()
    async with MondayClient(token, rejestr) as klient:
        # `cale_konto` celowo: inwentarz bez pełnego zakresu jest inwentarzem
        # czegoś innego niż konto. Tokenem bez admina `rozpoznaj_konto` przerwie
        # i to jest właściwe zachowanie — lepiej brak liczby niż liczba zaniżona.
        konto = await rozpoznaj_konto(klient, Zakres(typ="cale_konto"))
        inwentarz = await zbuduj_inwentarz(klient, konto, rejestr)
        surowe_tablice = await pobierz_tablice(klient) if (z_tablicami or z_itemami) else []
        przeglad = (
            await zbuduj_przeglad(klient, rejestr, inwentarz.workspace_y, tablice=surowe_tablice)
            if z_tablicami
            else None
        )
        if z_itemami:
            # Budżet dla itemów to TO, CO ZOSTAŁO z limitu ustawionego przez
            # `rozpoznaj_konto` (połowa dziennego limitu planu) — a nie osobna
            # liczba wzięta z sufitu.
            zostalo = max(0, klient.budzet_wywolan - klient.liczba_wywolan - 20)
            produkty = {w.workspace_id: w.produkt_kind for w in inwentarz.workspace_y}
            itemy = await zbuduj_itemy(
                klient, rejestr, surowe_tablice, produkty=produkty, budzet=zostalo
            )
        else:
            itemy = None

    if jako_json:
        dokument = inwentarz.do_json()
        if przeglad is not None:
            dokument["tablice"] = przeglad.do_json()
        if itemy is not None:
            dokument["itemy"] = itemy.do_json()
            dokument["zestawienia"] = zbuduj_zestawienia(itemy.tablice).do_json()
        print(json.dumps(dokument, ensure_ascii=False, indent=2))
        return 0

    print(f"\n  {inwentarz.konto_nazwa}")
    print(f"  {'─' * 46}")
    print(f"  workspace'ów      {inwentarz.workspacow:>6}")
    for produkt, ile in inwentarz.po_produktach.items():
        print(f"      {produkt:<14}{ile:>6}")
    print(f"  tablic            {inwentarz.tablic_aktywnych:>6}   (aktywne, type=board)")
    for stan, ile in inwentarz.tablic_po_stanie.items():
        print(f"      {stan:<14}{ile:>6}")
    for typ, ile in inwentarz.tablic_po_typie.items():
        if typ != "board":
            print(f"      {typ:<14}{ile:>6}   (nie liczone jako tablice)")
    print(f"  użytkowników      {inwentarz.uzytkownikow:>6}   (admin + member)")
    print(f"  gości             {inwentarz.gosci:>6}")
    print(f"  agentów AI        {inwentarz.agentow_ai:>6}   (konta agentowe, w tym zewnętrzne)")
    print(f"  podglądających    {inwentarz.podgladajacych:>6}")
    licencja = inwentarz.licencja_tier or "nieznana"
    miejsca = inwentarz.licencja_max_users
    print(f"  licencja          {licencja:>6}" + (f"   (max {miejsca} miejsc)" if miejsca else ""))
    print(f"  {'─' * 46}")
    print(f"  {inwentarz.wywolan} wywołań z limitu konta\n")

    for uwaga in inwentarz.zastrzezenia:
        print(f"  UWAGA: {uwaga}")

    if przeglad is not None:
        _wypisz_przeglad(przeglad)
    if itemy is not None:
        _wypisz_itemy(itemy)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(
            _wykonaj(jako_json=args.json, z_tablicami=args.tablice, z_itemami=args.itemy)
        )
    except MondayError as blad:
        # Treść błędu z API może nieść fragment odpowiedzi, ale nie token —
        # `MondayClient` go nie wkłada do komunikatu.
        print(f"BŁĄD monday: {blad}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
