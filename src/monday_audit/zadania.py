"""Audyt jako zadanie w tle, z postępem widocznym dla odbiorcy.

Run trwa ~17 minut (collector plus agent), więc żądanie HTTP go nie utrzyma.
`POST /api/audyt` zwraca identyfikator natychmiast, run leci osobno, a front
odpytuje o stan.

## Postęp bez zmiany w collectorze

`postep.py` przewidział to w 3.2, w swoim własnym docstringu: „w etapie 5 ten
sam strumień pójdzie do FastAPI i nic w kliencie nie musi się zmienić". Tak jest:
`MondayClient` woła `Callable[[Postep], None]` po każdym kroku, więc dopisujemy
**drugą implementację** tego wywołania — zapisującą do tabeli `zadania` zamiast
na konsolę. W collectorze zero zmian.

## KLUCZ KLIENTA NIE WCHODZI DO BAZY

Najważniejsza rzecz w tym module. Klucz przychodzi argumentem do `uruchom_audyt`,
żyje w zmiennej lokalnej i ginie razem z zadaniem. W `zadania` ląduje wyłącznie
stan: etap, procent, `run_id`, błąd.

Dotyczy to też **komunikatów błędów**: wyjątek z `MondayClient` może nieść
fragment odpowiedzi API, więc przed zapisem przepuszczamy go przez `_bez_sekretow`.
Bez tego wystarczyłby jeden nieszczęśliwy komunikat, żeby klucz wylądował
w kolumnie `blad` — i został tam na zawsze.

## Hamulec kosztu jest w bazie, nie w interfejsie

Klient klika „Wygeneruj audyt" i wydaje NASZE pieniądze (~1,71 USD za run
agenta) oraz wywołania ze swojego limitu monday. `wolno_odpalic()` liczy runy
w tabeli, więc odświeżenie strony ani `curl` tego nie obchodzą.
"""

from __future__ import annotations

import logging
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from monday_audit.klient import Postep

logger = logging.getLogger(__name__)

STAN_W_KOLEJCE = "w_kolejce"
STAN_ZBIERAM = "zbieram"
STAN_ANALIZUJE = "analizuje"
STAN_GOTOWE = "gotowe"
STAN_BLAD = "blad"

# Hamulec kosztu. Odstęp między audytami jednego klienta i sufit na klienta.
# Liczby wzięte z realnego kosztu: run agenta to ~1,71 USD i ~227 wywołań
# z dziennego limitu klienta. Cztery audyty na klienta to ~7 USD — tyle
# jesteśmy gotowi wydać, zanim ktoś z nas spojrzy, co się dzieje.
ODSTEP_DNI = 7
SUFIT_AUDYTOW = 4

# Wzorce, które NIE MOGĄ trafić do kolumny `blad`. Token monday to JWT;
# wychwytujemy też długie ciągi wyglądające na sekret.
_SEKRETY = (
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}[.A-Za-z0-9_-]*"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
)


class ZadanieError(RuntimeError):
    """Nie da się odpalić audytu — limit, brak klienta, zły zakres."""


@dataclass(frozen=True, slots=True)
class StanZadania:
    """Stan runu do pokazania odbiorcy. Bez ani jednego pola z sekretem."""

    id: str
    client_id: str
    stan: str
    etap: str | None
    postep: int | None
    run_id: str | None
    blad: str | None
    zaczeto: str
    skonczono: str | None

    @property
    def trwa(self) -> bool:
        return self.stan in (STAN_W_KOLEJCE, STAN_ZBIERAM, STAN_ANALIZUJE)


def _bez_sekretow(tekst: str) -> str:
    """Usuwa z komunikatu wszystko, co wygląda na klucz.

    Wołane PRZED każdym zapisem do `zadania.blad`. Komunikat błędu jest
    najbardziej niedocenianą drogą wycieku: nikt go nie planuje, a idzie
    prosto do bazy i na ekran.
    """
    wynik = tekst
    for wzorzec in _SEKRETY:
        wynik = wzorzec.sub("[USUNIĘTY SEKRET]", wynik)
    return wynik[:500]


def wolno_odpalic(con: sqlite3.Connection, client_id: str) -> tuple[bool, str]:
    """Czy ten klient może teraz odpalić audyt. Zwraca `(wolno, powód)`.

    Sprawdzenie jest TUTAJ, a nie w interfejsie: przycisk wyszarzony w JS
    powstrzymuje klikanie, ale nie powstrzymuje `curl`-a.
    """
    wiersz = con.execute(
        "SELECT COUNT(*) razem, MAX(zaczeto) ostatni FROM zadania "
        "WHERE client_id = ? AND stan != ?",
        (client_id, STAN_BLAD),
    ).fetchone()
    razem = int(wiersz["razem"] or 0)
    if razem >= SUFIT_AUDYTOW:
        return False, (
            f"wykorzystany limit {SUFIT_AUDYTOW} audytów dla tego konta — "
            f"napisz do nas, jeśli potrzebujesz kolejnego"
        )

    w_toku = con.execute(
        "SELECT id FROM zadania WHERE client_id = ? AND stan IN (?, ?, ?) LIMIT 1",
        (client_id, STAN_W_KOLEJCE, STAN_ZBIERAM, STAN_ANALIZUJE),
    ).fetchone()
    if w_toku is not None:
        return False, "audyt tego konta już trwa — odśwież stronę, żeby zobaczyć postęp"

    if wiersz["ostatni"]:
        mozliwy = datetime.fromisoformat(str(wiersz["ostatni"])) + timedelta(days=ODSTEP_DNI)
        if datetime.now(tz=UTC) < mozliwy:
            return False, f"kolejny audyt będzie możliwy od {mozliwy.date().isoformat()}"

    return True, ""


def utworz_zadanie(con: sqlite3.Connection, *, client_id: str, konto_id: int) -> str:
    """Zakłada zadanie i zwraca jego identyfikator. Sprawdza hamulec kosztu."""
    wolno, powod = wolno_odpalic(con, client_id)
    if not wolno:
        raise ZadanieError(powod)

    # `token_urlsafe`, nie licznik: rosnący numer w URL-u zdradza, ile audytów
    # zrobiliśmy wszystkim klientom razem.
    identyfikator = secrets.token_urlsafe(12)
    with con:
        con.execute(
            "INSERT INTO zadania (id, client_id, konto_id, stan, etap, postep, zaczeto) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (
                identyfikator,
                client_id,
                konto_id,
                STAN_W_KOLEJCE,
                "czekam na start",
                datetime.now(tz=UTC).isoformat(),
            ),
        )
    logger.info("zadanie %s utworzone dla klienta %s", identyfikator, client_id)
    return identyfikator


def zapisz_stan(
    con: sqlite3.Connection,
    zadanie_id: str,
    *,
    stan: str | None = None,
    etap: str | None = None,
    postep: int | None = None,
    run_id: str | None = None,
    blad: str | None = None,
) -> None:
    """Aktualizuje stan zadania. `blad` przechodzi przez `_bez_sekretow`."""
    pola: list[str] = []
    wartosci: list[object] = []
    for nazwa, wartosc in (
        ("stan", stan),
        ("etap", etap),
        ("postep", postep),
        ("run_id", run_id),
        ("blad", _bez_sekretow(blad) if blad else None),
    ):
        if wartosc is not None:
            pola.append(f"{nazwa} = ?")
            wartosci.append(wartosc)
    if stan in (STAN_GOTOWE, STAN_BLAD):
        pola.append("skonczono = ?")
        wartosci.append(datetime.now(tz=UTC).isoformat())
    if not pola:
        return
    wartosci.append(zadanie_id)
    with con:
        con.execute(
            f"UPDATE zadania SET {', '.join(pola)} WHERE id = ?",  # noqa: S608 — nazwy pól są literałami
            wartosci,
        )


def wczytaj_stan(con: sqlite3.Connection, zadanie_id: str) -> StanZadania | None:
    wiersz = con.execute("SELECT * FROM zadania WHERE id = ?", (zadanie_id,)).fetchone()
    if wiersz is None:
        return None
    return StanZadania(
        id=str(wiersz["id"]),
        client_id=str(wiersz["client_id"]),
        stan=str(wiersz["stan"]),
        etap=wiersz["etap"],
        postep=wiersz["postep"],
        run_id=wiersz["run_id"],
        blad=wiersz["blad"],
        zaczeto=str(wiersz["zaczeto"]),
        skonczono=wiersz["skonczono"],
    )


class PostepDoBazy:
    """Druga implementacja `Callable[[Postep], None]` — zapis do `zadania`.

    Pierwsza to `postep.LicznikKonsolowy`. Collector nie wie o żadnej z nich:
    dostaje wywoływalny obiekt i tyle (`klient.py`, parametr `postep`).

    **Zapisujemy nie częściej niż co `co_ile` kroków.** Run collectora to ~227
    wywołań; zapis przy każdym to 227 transakcji SQLite na jeden audyt, a front
    i tak odpytuje raz na sekundę.
    """

    def __init__(
        self,
        con: sqlite3.Connection,
        zadanie_id: str,
        *,
        co_ile: int = 5,
    ) -> None:
        self._con = con
        self._zadanie = zadanie_id
        self._co_ile = max(1, co_ile)
        self._krokow = 0

    def __call__(self, postep: Postep) -> None:
        self._krokow += 1
        if self._krokow % self._co_ile:
            return
        # Procent liczymy z `wywolania/budzet`, które `Postep` już niesie —
        # nie z własnego licznika kroków. Collector zna swój budżet (podnosi go
        # po rozpoznaniu planu), więc to jego liczba jest prawdziwa, a nie nasza.
        #
        # Collector zajmuje pierwsze 60% paska, agent pozostałe 40%: collector
        # to minuty, agent kwadrans. Bez tego podziału pasek dobijałby do 100%
        # w połowie czasu i wyglądał na zawieszony.
        udzial = min(60, int(60 * postep.wywolania / max(1, postep.budzet)))
        zapisz_stan(
            self._con,
            self._zadanie,
            stan=STAN_ZBIERAM,
            etap=postep.opis(),
            postep=udzial,
        )

    def etap_agenta(self, numer: int, ile: int, opis: str) -> None:
        """Postęp fazy analitycznej. Wołane z pętli agenta, nie z collectora."""
        zapisz_stan(
            self._con,
            self._zadanie,
            stan=STAN_ANALIZUJE,
            etap=f"badam hipotezę {numer} z {ile}: {opis}",
            postep=60 + min(39, int(39 * numer / max(1, ile))),
        )
