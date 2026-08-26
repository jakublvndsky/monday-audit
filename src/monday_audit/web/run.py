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
import sqlite3
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from monday_audit.agent import zbadaj_hipotezy
from monday_audit.baza import RejestrWywolan, polacz
from monday_audit.cennik import stawki_dla, wersja_uzytych
from monday_audit.cli import zbuduj_zakres
from monday_audit.detektory import uruchom_detektory
from monday_audit.klient import MondayClient
from monday_audit.konfiguracja import (
    ROZLICZENIE_KLUCZ_KLIENTA,
    klucz_anthropic,
    sol_z_ustawien,
    wczytaj,
)
from monday_audit.kontrakt import (
    waliduj,
    zapisz_findingi,
    zapisz_hipotezy_odrzucone,
    zapisz_odrzucone,
)
from monday_audit.narzedzia import Narzedzia
from monday_audit.przebieg import wykonaj_run, zapisz_zuzycie
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.wybor_zakresu import (
    identyfikatory_tablic,
    klasy_milczace,
    odsiej_hipotezy,
    wczytaj_payload,
    zapisz_pominiete,
)
from monday_audit.zadania import (
    STAN_ANALIZUJE,
    STAN_BLAD,
    STAN_GOTOWE,
    PostepDoBazy,
    czekaj_na_zgode,
    wczytaj_stan,
    zapisz_stan,
)

logger = logging.getLogger(__name__)


def _odmiana_sygnalow(ile: int) -> str:
    """Polska odmiana dla licznika sygnałów w tekście postępu.

    Bez tego pasek pokazywał „analizuję 24 sygnał" albo „1 sygnałów" — drobiazg,
    który podważa zaufanie do liczb obok.
    """
    if ile == 1:
        return "sygnał"
    if ile % 10 in (2, 3, 4) and ile % 100 not in (12, 13, 14):
        return "sygnały"
    return "sygnałów"


class RunError(RuntimeError):
    """Audyt nie da się dokończyć — brakuje stanu, którego wymaga faza druga."""


def _run_collectora(con: sqlite3.Connection, snapshot_id: int) -> str:
    """`run_id` collectora, który zapisał ten snapshot.

    Faza druga zaczyna się w innym wywołaniu niż pierwsza, więc nazwy runu
    nie da się przekazać w pamięci — ale w `runy` już jest.
    """
    wiersz = con.execute(
        "SELECT run_id FROM runy WHERE snapshot_id = ? AND run_id NOT LIKE '%-agent' "
        "ORDER BY started_at DESC LIMIT 1",
        (snapshot_id,),
    ).fetchone()
    if wiersz is None:
        raise RunError(f"snapshot {snapshot_id} nie ma runu collectora")
    return str(wiersz["run_id"])


def uruchom_audyt_w_tle(
    baza: Path,
    zadanie_id: str,
    client_id: str,
    klucz_api: str,
    zakres_typ: str,
    workspace_id: str | None,
    klucz_modelu_klienta: str | None = None,
    board_ids: list[str] | None = None,
) -> None:
    """Wejście dla executora. Synchroniczne, bo `run_in_executor` tak woła.

    FAZA PIERWSZA: zbiera dane w ZAWĘŻONYM zakresie i zatrzymuje się na
    potwierdzeniu kosztu. Agent rusza dopiero z `uruchom_analize_w_tle`.

    Zakres przychodzi z ekranu podglądu, który klient widział PRZED kliknięciem
    „Zbierz dane" — więc zbieranie jest już zawężone i nie trwa trzech minut.
    """
    _w_tle(
        baza,
        zadanie_id,
        _zbierz(
            baza,
            zadanie_id,
            client_id,
            klucz_api,
            zakres_typ,
            workspace_id,
            klucz_modelu_klienta,
            board_ids or [],
        ),
    )


def uruchom_analize_w_tle(
    baza: Path,
    zadanie_id: str,
    client_id: str,
    klucz_api: str,
    klucz_modelu_klienta: str | None,
    board_ids: frozenset[str] | None,
) -> None:
    """FAZA DRUGA: agent na hipotezach, które przeszły wybór zakresu.

    Klucze przychodzą argumentem ponownie, bo faza pierwsza ich nie zapisała
    i nie zapisze — token klienta nie ma kolumny w bazie (D12).
    """
    _w_tle(
        baza,
        zadanie_id,
        _analizuj(baza, zadanie_id, client_id, klucz_api, klucz_modelu_klienta, board_ids),
    )


def _w_tle(baza: Path, zadanie_id: str, praca: Coroutine[Any, Any, None]) -> None:
    """Odpala korutynę i zamienia każdy wyjątek na stan `blad` w bazie.

    Wspólne dla obu faz: zadanie, które padło bez zapisania stanu, blokowałoby
    klienta do wygaśnięcia reapera.
    """
    try:
        asyncio.run(praca)
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


async def _zbierz(
    baza: Path,
    zadanie_id: str,
    client_id: str,
    klucz_api: str,
    zakres_typ: str,
    workspace_id: str | None,
    klucz_modelu_klienta: str | None = None,
    board_ids: list[str] | None = None,
) -> None:
    """Collector i detektory. Kończy się na `czeka_na_zgode`, nie na agencie.

    ## Zakres jest ZAWĘŻONY już tutaj, i to jest zmiana z 2026-08-25

    Wcześniej collector zbierał całe konto, a zawężenie działało tylko na
    hipotezach — z rozumowaniem, że snapshot ma być kompletny. Rozumowanie było
    poprawne, ale kolejność zła: klient musiał czekać trzy minuty, ŻEBY MÓC
    wskazać workspace. Teraz wskazuje go na ekranie podglądu (6 s), więc
    zbieranie startuje już zawężone i jest krótsze.

    Cena tej decyzji, zapisana jawnie: snapshot zawężony do jednego workspace'u
    NIE zawiera pozostałych, więc `DUPLICATE_STRUCTURE` nie porówna tablicy
    z tablicą z innego workspace'u. To znika z audytu i raport musi to napisać —
    `_uwagi_o_zakresie` w `przebieg.py` produkuje takie zastrzeżenia dla każdego
    zawężonego zakresu.
    """
    ustawienia = wczytaj()
    # ── KLUCZA MODELU TU JUŻ NIE SPRAWDZAMY ────────────────────────
    #
    # Do 2026-08-25 brak klucza Anthropic przerywał PRZED wywołaniami monday,
    # z dobrym uzasadnieniem: nie zużywać limitu klienta na dane, których nie
    # ma czym przeanalizować.
    #
    # Ale kolejność ekranów się zmieniła i to uzasadnienie przestało pasować:
    # klucz modelu klient podaje DOPIERO przy zatwierdzaniu zakresu, gdy widzi
    # dokładne widełki. Sprawdzanie go tutaj przerywałoby zbieranie, na które
    # się właśnie zgodził — a bramka (O36) stoi teraz w `POST /zgoda`, przed
    # jedynym krokiem, który faktycznie woła model.
    if klucz_modelu_klienta:
        # Bez prefiksu, bez długości — prefiks tokenu też jest informacją (D11).
        logger.info("run %s ma już klucz KLIENTA — koszt nie obciąży CXLABS", zadanie_id)
    sol = sol_z_ustawien(ustawienia)
    rubryka = wczytaj_rubryke()

    con = polacz(baza)
    try:
        licznik = PostepDoBazy(con, zadanie_id)
        # Tryb `tablice` jest NAJWĘŻSZY i wygrywa, gdy klient wskazał tablice.
        # `Zakres.__post_init__` odrzuca podanie obu list naraz, więc wybór musi
        # być jednoznaczny już tutaj — nie w collectorze.
        if zakres_typ == "tablice" and board_ids:
            zakres = zbuduj_zakres("tablice", list(board_ids))
        else:
            zakres = zbuduj_zakres(zakres_typ, [workspace_id] if workspace_id else [])
        logger.info("zbieranie %s w zakresie: %s", zadanie_id, zakres.opis())

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
            # Bez liczby wywołań API: klient nie wie, co to jest, a „zebrano 132
            # wywołań" czyta się jak usterka. ZGŁOSZONE (Kuba, 2026-08-25):
            # „jakieś były liczby niestworzone".
            etap="szukam nieprawidłowości w zebranych danych",
            postep=60,
            run_id=raport_runu.run_id,
        )

        # ── detektory ────────────────────────────────────────────────
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

        # ── PAUZA: wybór zakresu i zgoda na koszt ────────────────────
        #
        # Tu kończy się faza pierwsza. Agent nie ruszy, dopóki człowiek nie
        # zatwierdzi zakresu — bo od tego zależy rachunek, a rachunek idzie
        # na klucz klienta.
        #
        # `snapshot_id` zapisujemy do zadania, bo faza druga musi wiedzieć,
        # KTÓRY snapshot został zatwierdzony. Bez tego szłaby po „ostatnim
        # snapshocie klienta", a ten mógł w międzyczasie powstać z innego
        # zbierania.
        czekaj_na_zgode(
            con,
            zadanie_id,
            snapshot_id=raport_runu.snapshot_id,
            hipotez=len(hipotezy),
        )
        logger.info(
            "zadanie %s czeka na zgodę — %d hipotez ze snapshotu %d",
            zadanie_id,
            len(hipotezy),
            raport_runu.snapshot_id,
        )
    finally:
        con.close()


async def _analizuj(
    baza: Path,
    zadanie_id: str,
    client_id: str,
    klucz_api: str,
    klucz_modelu_klienta: str | None,
    board_ids: frozenset[str] | None,
) -> None:
    """Agent na hipotezach po odsianiu, walidacja, zapis. Faza druga.

    `board_ids is None` znaczy „bez zawężenia" — klient zatwierdził całe
    konto. Pusty zbiór znaczyłby „nie wybrano ani jednej tablicy" i wtedy
    zostają wyłącznie hipotezy o koncie.
    """
    ustawienia = wczytaj()
    if klucz_modelu_klienta:
        klucz_modelu = klucz_modelu_klienta
        rozliczenie = ROZLICZENIE_KLUCZ_KLIENTA
        logger.info("analiza %s idzie na kluczu KLIENTA", zadanie_id)
    else:
        klucz_modelu = klucz_anthropic(ustawienia)
        rozliczenie = ustawienia.agent_rozliczenie
    sol = sol_z_ustawien(ustawienia)
    rubryka = wczytaj_rubryke()

    con = polacz(baza)
    try:
        stan = wczytaj_stan(con, zadanie_id)
        if stan is None or stan.snapshot_id is None:
            raise RunError(f"zadanie {zadanie_id} nie ma zatwierdzonego snapshotu")
        snapshot_id = stan.snapshot_id

        # Detektory lecą PONOWNIE, a nie z zapisanej listy. Są deterministyczne
        # i czytają zamrożony snapshot (D7), więc dają ten sam wynik — a nie
        # musimy serializować hipotez do bazy tylko po to, żeby je odczytać.
        wszystkie, _ = uruchom_detektory(con, snapshot_id, rubryka)
        payload = wczytaj_payload(con, snapshot_id)
        hipotezy, pominiete = odsiej_hipotezy(
            wszystkie,
            board_ids=board_ids,
            znane_tablice=identyfikatory_tablic(payload),
        )
        if not hipotezy:
            zapisz_stan(
                con,
                zadanie_id,
                stan=STAN_GOTOWE,
                etap="wybrany zakres nie zawiera nic do zbadania",
                postep=100,
            )
            return

        # `run_id` collectora bierzemy z bazy, a nie z pamięci: faza pierwsza
        # skończyła się w innym procesie roboczym i nic po niej nie zostało
        # poza wierszami w SQLite.
        run_collectora = _run_collectora(con, snapshot_id)
        run_agenta = f"{run_collectora}-agent"
        con.execute(
            "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
            "rubric_ver) VALUES (?, ?, ?, 'w_toku', ?, ?, ?)",
            (
                run_agenta,
                client_id,
                snapshot_id,
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
        # Ślad po hipotezach, których wybór nie objął — w tabeli, którą panel
        # już pokazuje jako „czego nie widać". Zapis MUSI iść po `INSERT INTO
        # runy`, bo `hipotezy_odrzucone.run_id` ma klucz obcy.
        zapisz_pominiete(con, run_id=run_agenta, pominiete=pominiete)
        if pominiete:
            milczace = klasy_milczace(pominiete, rubryka)
            logger.info(
                "zadanie %s: wybór odsiał %d hipotez, klasy milczące: %s",
                zadanie_id,
                len(pominiete),
                ", ".join(milczace) or "brak",
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
                snapshot_id=snapshot_id,
                client_id=client_id,
                sol=sol,
                klient=klient,
            )
            zapisz_stan(
                con,
                zadanie_id,
                etap=f"analizuję {len(hipotezy)} {_odmiana_sygnalow(len(hipotezy))}",
                postep=62,
            )

            # Postęp PO KAŻDEJ hipotezie, nie raz na całą analizę.
            #
            # ZGŁOSZONE (Kuba, 2026-08-25): „patrzysz w to i nie wiesz, kiedy co
            # się stanie, za ile się stanie". Wcześniej był jeden zapis stanu na
            # dziewięć minut, więc ekran nie odróżniał pracy od zawieszenia.
            #
            # Skala 62→94: 95 zajmuje walidacja, 100 gotowe. Dolne 62 zostaje
            # z chwili startu analizy, żeby pasek nie cofał się po zebraniu.
            def melduj(zbadanych: int, wszystkich: int, klasa_id: str) -> None:
                udzial = zbadanych / wszystkich if wszystkich else 0
                zapisz_stan(
                    con,
                    zadanie_id,
                    etap=f"zbadano {zbadanych} z {wszystkich} sygnałów",
                    postep=62 + int(udzial * 32),
                )

            odpowiedz = await zbadaj_hipotezy(
                hipotezy,
                zestaw=zestaw,
                rubryka=rubryka,
                run_id=run_agenta,
                klucz_api=klucz_modelu,
                stawki=stawki,
                postep=melduj,
            )

        # ── walidacja ────────────────────────────────────────────────
        zapisz_stan(con, zadanie_id, etap="sprawdzam znaleziska", postep=95)
        wynik = waliduj(odpowiedz, rubryka, stawki)
        zapisz_findingi(
            con,
            wynik.przyjete,
            run_id=run_agenta,
            snapshot_id=snapshot_id,
            rubryka=rubryka,
        )
        zapisz_odrzucone(con, wynik.odrzucone, run_id=run_agenta, snapshot_id=snapshot_id)
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
                rozliczenie,
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
