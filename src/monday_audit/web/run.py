"""Wykonanie audytu w tle: collector → agent → walidacja (D16).

## Klucz klienta żyje TYLKO tutaj, w pamięci

`klucz_api` przychodzi argumentem, idzie do `MondayClient` i ginie razem
z funkcją. **Nie jest zapisywany, logowany ani wkładany do żadnej struktury,
która gdzieś trafia** — patrz D11 i migracja 006, w której świadomie nie ma
kolumny na token klienta.

Trzy miejsca, w których łatwo go zgubić, i co z nimi robimy:

1. **komunikat błędu** — wyjątek z API może nieść fragment odpowiedzi.
   `zadania.zapisz_stan` przepuszcza `blad` przez `_bez_sekretow`.
2. **log** — nie wypisujemy `klucz_api` nigdzie, także nie jego długości
   ani prefiksu; prefiks tokenu też jest informacją.
3. **argv** — funkcja jest wołana w wątku tego samego procesu, więc klucz
   nigdy nie idzie przez linię komend, którą widzi `ps` (D12).

## Dlaczego wątek, a nie osobny proces

O6 mówi: worker jako proces jednorazowy, nie demon, przy 2 GB dzielonych.
Tu jednak audyt startuje Z ŻĄDANIA, więc proces serwera i tak żyje. Wątek
w executorze trzyma jeden run naraz per klient (pilnuje tego `wolno_odpalic`),
a `asyncio` nie blokuje pozostałych żądań, bo cała robota siedzi w I/O.

Gdy dojdzie deploy (etap 5), to jest miejsce do podmiany na kolejkę — sygnatura
funkcji tego nie zmieni.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from monday_audit.agent import zbadaj_hipotezy
from monday_audit.baza import RejestrWywolan, polacz
from monday_audit.cennik import stawki_dla, wersja_uzytych
from monday_audit.cli import zbuduj_zakres
from monday_audit.detektory import uruchom_detektory
from monday_audit.klient import MondayClient
from monday_audit.konfiguracja import klucz_anthropic, sol_z_ustawien, wczytaj
from monday_audit.kontrakt import (
    waliduj,
    zapisz_findingi,
    zapisz_hipotezy_odrzucone,
    zapisz_odrzucone,
)
from monday_audit.narzedzia import Narzedzia
from monday_audit.przebieg import wykonaj_run, zapisz_zuzycie
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.zadania import (
    STAN_ANALIZUJE,
    STAN_BLAD,
    STAN_GOTOWE,
    PostepDoBazy,
    zapisz_stan,
)

logger = logging.getLogger(__name__)


def uruchom_audyt_w_tle(
    baza: Path,
    zadanie_id: str,
    client_id: str,
    klucz_api: str,
    zakres_typ: str,
    workspace_id: str | None,
) -> None:
    """Wejście dla executora. Synchroniczne, bo `run_in_executor` tak woła."""
    try:
        asyncio.run(_audyt(baza, zadanie_id, client_id, klucz_api, zakres_typ, workspace_id))
    except Exception as blad:
        # Komunikat idzie przez `_bez_sekretow` w `zapisz_stan`. Typ wyjątku
        # zostawiamy, bo pomaga w diagnozie i nie niesie treści.
        con = polacz(baza)
        try:
            zapisz_stan(
                con,
                zadanie_id,
                stan=STAN_BLAD,
                etap="audyt przerwany",
                blad=f"{type(blad).__name__}: {blad}",
            )
        finally:
            con.close()
        logger.exception("zadanie %s padło", zadanie_id)


async def _audyt(
    baza: Path,
    zadanie_id: str,
    client_id: str,
    klucz_api: str,
    zakres_typ: str,
    workspace_id: str | None,
) -> None:
    ustawienia = wczytaj()
    # Brak klucza Anthropic przerywa PRZED wywołaniami monday — nie chcemy
    # zużyć limitu klienta na dane, których nie zdążymy przeanalizować.
    klucz_modelu = klucz_anthropic(ustawienia)
    sol = sol_z_ustawien(ustawienia)
    rubryka = wczytaj_rubryke()

    con = polacz(baza)
    try:
        licznik = PostepDoBazy(con, zadanie_id)
        zakres = zbuduj_zakres(zakres_typ, [workspace_id] if workspace_id else [])

        # ── collector ────────────────────────────────────────────────
        zapisz_stan(con, zadanie_id, stan="zbieram", etap="rozpoznaję konto", postep=1)
        raport_runu = await wykonaj_run(
            token=klucz_api,  # jedyne miejsce, gdzie klucz opuszcza tę funkcję
            con=con,
            client_id=client_id,
            zakres=zakres,
            sol=sol,
            postep=licznik,
        )
        zapisz_stan(
            con,
            zadanie_id,
            stan=STAN_ANALIZUJE,
            etap=f"zebrano {raport_runu.wywolan} wywołań, szukam anomalii",
            postep=60,
            run_id=raport_runu.run_id,
        )

        # ── detektory i agent ────────────────────────────────────────
        hipotezy, _ = uruchom_detektory(con, raport_runu.snapshot_id, rubryka)
        if not hipotezy:
            zapisz_stan(
                con,
                zadanie_id,
                stan=STAN_GOTOWE,
                etap="brak anomalii do zbadania — konto wygląda zdrowo",
                postep=100,
            )
            return

        run_agenta = f"{raport_runu.run_id}-agent"
        con.execute(
            "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
            "rubric_ver) VALUES (?, ?, ?, 'w_toku', ?, ?, ?)",
            (
                run_agenta,
                client_id,
                raport_runu.snapshot_id,
                # DRUGA instancja tej samej usterki co przy `finished_at`:
                # `started_at` dostawało `raport_runu.run_id`, czyli identyfikator
                # zamiast daty. Kolumna jest `TEXT`, więc nic nie protestowało,
                # a panel sortuje wersje audytu WŁAŚNIE po `started_at` — run
                # z panelu wylądowałby w losowym miejscu listy.
                #
                # Obie przeżyły, bo żaden run z panelu nie doszedł jeszcze do
                # zapisu (`runy` z sufiksem `-agent`: 0 wierszy). Zmierzone.
                datetime.now(tz=UTC).isoformat(),
                "claude-sonnet-5",
                rubryka.wersja,
            ),
        )
        con.commit()

        potrzebne = {z for h in hipotezy for z in rubryka.po_id[h.klasa_id].zmienne_od_klienta}
        stawki = stawki_dla(con, potrzebne, client_id=client_id)
        if stawki:
            con.execute(
                "UPDATE runy SET cennik_ver = ? WHERE run_id = ?",
                (wersja_uzytych(stawki), run_agenta),
            )
            con.commit()

        async with MondayClient(klucz_api, RejestrWywolan(con, run_agenta)) as klient:
            zestaw = Narzedzia(
                con=con,
                snapshot_id=raport_runu.snapshot_id,
                client_id=client_id,
                sol=sol,
                klient=klient,
            )
            zapisz_stan(
                con,
                zadanie_id,
                etap=f"badam {len(hipotezy)} anomalii",
                postep=62,
            )
            odpowiedz = await zbadaj_hipotezy(
                hipotezy,
                zestaw=zestaw,
                rubryka=rubryka,
                run_id=run_agenta,
                klucz_api=klucz_modelu,
                stawki=stawki,
            )

        # ── walidacja ────────────────────────────────────────────────
        zapisz_stan(con, zadanie_id, etap="sprawdzam znaleziska", postep=95)
        wynik = waliduj(odpowiedz, rubryka, stawki)
        zapisz_findingi(
            con,
            wynik.przyjete,
            run_id=run_agenta,
            snapshot_id=raport_runu.snapshot_id,
            rubryka=rubryka,
        )
        zapisz_odrzucone(
            con, wynik.odrzucone, run_id=run_agenta, snapshot_id=raport_runu.snapshot_id
        )
        zapisz_hipotezy_odrzucone(con, wynik.hipotezy_odrzucone, run_id=run_agenta)
        con.execute(
            "UPDATE runy SET status = 'zakonczony', finished_at = ?, findingow = ?, "
            "odrzuconych_walidacja = ?, hipotez_zbadanych = ?, hipotez_odrzuconych = ?, "
            "rozliczenie = ? WHERE run_id = ?",
            (
                # ZMIERZONA USTERKA (2026-08-10): tu stało `raport_runu.run_id`,
                # czyli IDENTYFIKATOR runu collectora wpisywany do `finished_at`.
                # Kolumna jest `TEXT`, więc SQLite przyjmował to bez szemrania,
                # a `finished_at` niosłoby napis w miejscu daty. Nie wyszło
                # wcześniej, bo żaden run Z PANELU nie doszedł jeszcze do końca —
                # CLI ma własny, poprawny `UPDATE`.
                datetime.now(tz=UTC).isoformat(),
                len(wynik.przyjete),
                len(wynik.odrzucone),
                len(hipotezy),
                len(wynik.hipotezy_odrzucone),
                ustawienia.agent_rozliczenie,
                run_agenta,
            ),
        )
        # TA SAMA funkcja co w `cli_agent`. Do 2026-08-11 ta ścieżka nie zapisywała
        # zużycia WCALE — tylko `koszt_usd` — więc pierwszy pełny audyt z panelu
        # ma `tokens_in = NULL` i nie wiadomo, z czego składa się jego 7,09 USD.
        zapisz_zuzycie(con, run_agenta, odpowiedz["zuzycie"], odpowiedz.get("per_hipoteza"))
        con.commit()

        zapisz_stan(
            con,
            zadanie_id,
            stan=STAN_GOTOWE,
            etap=f"gotowe: {len(wynik.przyjete)} znalezisk",
            postep=100,
            run_id=run_agenta,
        )
    finally:
        con.close()
