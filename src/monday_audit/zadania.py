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

import json
import logging
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from monday_audit.klient import Postep

logger = logging.getLogger(__name__)

STAN_W_KOLEJCE = "w_kolejce"
STAN_ZBIERAM = "zbieram"
# Dane zebrane, agent jeszcze nie ruszył — czekamy na decyzję człowieka
# o zakresie i na jego zgodę na koszt. Jedyny stan, w którym nic się nie
# dzieje i to jest w porządku.
STAN_CZEKA_NA_ZGODE = "czeka_na_zgode"
STAN_ANALIZUJE = "analizuje"
STAN_GOTOWE = "gotowe"
STAN_BLAD = "blad"

# Ile godzin ważna jest zgoda na zakres i koszt. Po tym czasie snapshot jest
# na tyle stary, że kwota policzona z niego przestaje być obietnicą — konto
# żyje, tablice się zmieniają, hipotez może być więcej. Zbieramy ponownie.
GODZIN_WAZNOSCI_ZGODY = 12

# Po ilu minutach zadanie „w toku" uznajemy za osierocone. Run trwa ~17 minut,
# więc 40 to margines na wolniejsze konto — ale nie tyle, żeby klient czekał
# godzinami.
#
# Powód z pomiaru: pierwsze uruchomienie na żywo padło z `RuntimeError` PRZED
# startem zadania, a wiersz `w_kolejce` już istniał. Bez tego limitu klient
# byłby zablokowany komunikatem „audyt już trwa" NA ZAWSZE, bo nic nigdy nie
# zmieniłoby stanu. Blokada z powodu naszego błędu, której nie da się zdjąć,
# jest gorsza od braku blokady.
MINUT_NA_OSIEROCENIE = 40

# Hamulec kosztu ZDJĘTY 2026-08-25 (decyzja Kuby). Stałych `ODSTEP_DNI`
# i `SUFIT_AUDYTOW` już nie ma — zostawienie ich jako martwych sugerowałoby,
# że limit gdzieś nadal działa.
#
# Historia dla kogoś, kto będzie chciał je przywrócić: powstały, gdy run agenta
# szedł na NASZYM kluczu (~1,71 USD), więc cztery audyty to było ~7 USD naszych
# pieniędzy. Po przejściu na klucz klienta (O36) ten argument zniknął, a drugi
# — dzienny limit monday — nie przeszedł pomiaru: 132 wywołania na audyt wobec
# 10 000 dziennie na planie `pro` to 75 audytów.
#
# Gdyby trzeba było wrócić do limitowania, właściwym miejscem jest `account_id`
# konta monday, nie `client_id`: `cxlabs` i `acme` dzielą `account_id=27690228`,
# więc liczenie per klient i tak mierzyło coś innego, niż zakładało.

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
    snapshot_id: int | None = None
    zgoda_do: str | None = None

    @property
    def trwa(self) -> bool:
        """Czy front ma dalej odpytywać.

        `czeka_na_zgode` NIE trwa: nic się nie liczy, a front ma przestać
        odpytywać i pokazać ekran wyboru. Wrzucenie tego stanu do „trwa"
        dałoby kręcący się w nieskończoność pasek postępu przy zadaniu,
        które czeka na kliknięcie.
        """
        return self.stan in (STAN_W_KOLEJCE, STAN_ZBIERAM, STAN_ANALIZUJE)

    @property
    def czeka_na_zgode(self) -> bool:
        return self.stan == STAN_CZEKA_NA_ZGODE


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


def _zwolnij_osierocone(con: sqlite3.Connection, client_id: str) -> None:
    """Zadania „w toku", które przestały zgłaszać postęp, dostają stan `blad`.

    Proces mógł padnąć, serwer się zrestartować albo — jak przy pierwszym
    uruchomieniu na żywo — endpoint wywalić się PO utworzeniu wiersza.
    Bez tego klient jest zablokowany komunikatem „audyt już trwa" na zawsze,
    bo nic nigdy nie zmieni stanu.

    Oznaczamy `blad`, a nie kasujemy: ślad nieudanej próby jest informacją,
    a `wolno_odpalic` i tak nie liczy błędów do sufitu.
    """
    granica = (datetime.now(tz=UTC) - timedelta(minutes=MINUT_NA_OSIEROCENIE)).isoformat()
    # Zamykamy ewentualną otwartą transakcję ODCZYTU przed zapisem. Bez tego
    # SQLite musi podnieść blokadę z read na write, a takie podniesienie
    # odrzuca NATYCHMIAST przy równoległych żądaniach — `busy_timeout` nie
    # pomaga, bo czekanie groziłoby zakleszczeniem. Zmierzone na 16 równoległych
    # żądaniach z frontu.
    con.rollback()
    teraz = datetime.now(tz=UTC).isoformat()
    with con:
        # `czeka_na_zgode` NIE JEST na tej liście i to jest sedno.
        #
        # Ten warunek porównuje `zaczeto` — moment założenia zadania — a nie
        # ostatni zgłoszony postęp. Zadanie czekające na decyzję klienta nie
        # zgłasza postępu z definicji, więc trafiłoby tu po 40 minutach
        # i zostało oznaczone jako błąd. Zgoda ważna dwanaście godzin byłaby
        # wtedy fikcją: klient wracałby do audytu, którego już nie ma.
        #
        # To ten sam rodzaj usterki, który w tym projekcie wychodził już
        # kilka razy — mechanizm, w który się wierzy, zamiast go zmierzyć.
        # Dlatego pilnuje tego test podmieniający `zaczeto` w bazie, a nie
        # komentarz.
        con.execute(
            "UPDATE zadania SET stan = ?, blad = ?, skonczono = ? "
            "WHERE client_id = ? AND stan IN (?, ?, ?) AND zaczeto < ?",
            (
                STAN_BLAD,
                "zadanie przerwane — nie zgłosiło postępu w wyznaczonym czasie",
                teraz,
                client_id,
                STAN_W_KOLEJCE,
                STAN_ZBIERAM,
                STAN_ANALIZUJE,
                granica,
            ),
        )
        # Zgoda ma własny termin i własny komunikat. Wygasa po `zgoda_do`,
        # nie po 40 minutach, a klient ma się dowiedzieć, że dane się
        # zestarzały — nie że „coś padło".
        con.execute(
            "UPDATE zadania SET stan = ?, blad = ?, skonczono = ? "
            "WHERE client_id = ? AND stan = ? AND zgoda_do IS NOT NULL AND zgoda_do < ?",
            (
                STAN_BLAD,
                "dane zebrane do tego audytu się zestarzały — zbierz je ponownie",
                teraz,
                client_id,
                STAN_CZEKA_NA_ZGODE,
                teraz,
            ),
        )


def wolno_odpalic(con: sqlite3.Connection, client_id: str) -> tuple[bool, str]:
    """Czy ten klient może teraz odpalić audyt. Zwraca `(wolno, powód)`.

    Sprawdzenie jest TUTAJ, a nie w interfejsie: przycisk wyszarzony w JS
    powstrzymuje klikanie, ale nie powstrzymuje `curl`-a.
    """
    # Zwolnienie osieroconych MUSI iść przed liczeniem — inaczej zadanie, które
    # nigdy nie wystartowało, blokowałoby przez odstęp 7 dni. Wyszło w teście:
    # zdjęliśmy blokadę „już trwa", a został „kolejny możliwy od…" na to samo
    # martwe zadanie.
    _zwolnij_osierocone(con, client_id)

    # ── SUFIT I ODSTĘP ZDJĘTE (decyzja Kuby, 2026-08-25) ──────────
    #
    # Powód: koszt modelu idzie w całości na klucz Anthropic klienta (O36), więc
    # hamulec nie chroni już NASZYCH pieniędzy — a to było jego głównym
    # uzasadnieniem („cztery audyty na klienta to ~7 USD").
    #
    # Drugie uzasadnienie — dzienny limit wywołań monday — nie utrzymało się
    # w pomiarze: audyt zużywa ~132 wywołania, a plan `pro` daje 10 000 dziennie,
    # czyli mieści się 75 audytów. Nawet na `free` (1 000) siedem. Blokada
    # tygodniowa nie chroniła przed niczym realnym, a blokowała normalną pracę:
    # dwa podejścia tego samego dnia to typowy przypadek, nie nadużycie.
    #
    # ZOSTAJE sprawdzenie „audyt już trwa" i to NIE JEST hamulec kosztu, a
    # ochrona spójności: dwa równoległe runy tego samego klienta piszą do tej
    # samej bazy, a `_analizuj` czyta `snapshot_id` z zadania. Zdjęcie tego dałoby
    # wyścig, nie oszczędność.
    w_toku = con.execute(
        "SELECT id FROM zadania WHERE client_id = ? AND stan IN (?, ?, ?) LIMIT 1",
        (client_id, STAN_W_KOLEJCE, STAN_ZBIERAM, STAN_ANALIZUJE),
    ).fetchone()
    if w_toku is not None:
        return False, "audyt tego konta już trwa — odśwież stronę, żeby zobaczyć postęp"

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
    snapshot_id: int | None = None,
    zgoda_do: str | None = None,
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
        ("snapshot_id", snapshot_id),
        ("zgoda_do", zgoda_do),
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
        snapshot_id=wiersz["snapshot_id"],
        zgoda_do=wiersz["zgoda_do"],
    )


def czekaj_na_zgode(
    con: sqlite3.Connection,
    zadanie_id: str,
    *,
    snapshot_id: int,
    hipotez: int,
) -> str:
    """Zatrzymuje zadanie po zebraniu danych. Zwraca termin ważności zgody.

    Termin liczymy od TERAZ, nie od `run_at` snapshotu: snapshot bywa zapisany
    kilka minut po rozpoczęciu zbierania, a klient patrzy na ekran dopiero
    teraz. Liczenie od `run_at` odbierałoby mu te minuty bez powodu.
    """
    do_kiedy = (datetime.now(tz=UTC) + timedelta(hours=GODZIN_WAZNOSCI_ZGODY)).isoformat()
    zapisz_stan(
        con,
        zadanie_id,
        stan=STAN_CZEKA_NA_ZGODE,
        etap=f"czekam na wybór zakresu — {hipotez} sygnałów do zbadania",
        postep=60,
        snapshot_id=snapshot_id,
        zgoda_do=do_kiedy,
    )
    return do_kiedy


def zgoda_wazna(stan: StanZadania, *, teraz: datetime | None = None) -> bool:
    """Czy zgoda na ten zakres jeszcze obowiązuje.

    Sprawdzane po stronie serwera przy przyjmowaniu zgody, nie tylko przy
    wygaszaniu w tle: między jednym a drugim mija czas, a `curl` nie pyta
    reapera o pozwolenie.
    """
    if not stan.zgoda_do:
        return False
    moment = teraz or datetime.now(tz=UTC)
    try:
        return datetime.fromisoformat(stan.zgoda_do) > moment
    except ValueError:
        logger.warning("zadanie %s ma nieparsowalny `zgoda_do`: %r", stan.id, stan.zgoda_do)
        return False


def porzuc_zadanie(con: sqlite3.Connection, zadanie_id: str) -> bool:
    """Klient rezygnuje z zebranych danych i chce zbierać od nowa.

    Stan `blad`, a NIE usunięcie wiersza — z dwóch powodów, oba istotne:

    1. **Hamulec kosztu nie liczy błędów** (`wolno_odpalic` pomija `STAN_BLAD`),
       więc porzucone zadanie nie liczy się jako „w toku". Bez tego klient
       widziałby „audyt tego konta już trwa" przy audycie, z którego właśnie
       zrezygnował — i nie miałby jak się odblokować.
    2. **Ślad zostaje.** Wiemy, że dane zebrano i porzucono; usunięty wiersz
       kłamałby, że nic się nie stało, choć limit monday został zużyty.

    Snapshotu NIE ruszamy: jest niemutowalny (D7), a panel pokazuje go dalej
    jako zebrane dane. Zwraca `False`, gdy nie było czego porzucać.
    """
    with con:
        kursor = con.execute(
            "UPDATE zadania SET stan = ?, blad = ?, skonczono = ? WHERE id = ? AND stan = ?",
            (
                STAN_BLAD,
                "porzucone — klient wybrał zbieranie danych od nowa",
                datetime.now(tz=UTC).isoformat(),
                zadanie_id,
                STAN_CZEKA_NA_ZGODE,
            ),
        )
    if not kursor.rowcount:
        logger.debug("zadanie %s nie czekało na zgodę — nie ma czego porzucać", zadanie_id)
        return False
    logger.info("zadanie %s porzucone na życzenie klienta", zadanie_id)
    return True


def zapisz_wybor(con: sqlite3.Connection, zadanie_id: str, wybor: dict[str, object]) -> None:
    """Zapisuje zatwierdzony zakres. Bez sekretów — to tylko identyfikatory.

    Zapisujemy, bo raport ma powiedzieć, ile tablic objął audyt, a po fakcie
    nie da się tego odtworzyć: snapshot jest pełny, więc sam nie zdradza,
    co klient wybrał.
    """
    with con:
        con.execute(
            "UPDATE zadania SET wybor = ? WHERE id = ?",
            (json.dumps(wybor, ensure_ascii=False, sort_keys=True), zadanie_id),
        )


def wczytaj_wybor(con: sqlite3.Connection, zadanie_id: str) -> dict[str, Any] | None:
    """Zatwierdzony zakres albo `None`, gdy zadanie szło bez zawężenia."""
    wiersz = con.execute("SELECT wybor FROM zadania WHERE id = ?", (zadanie_id,)).fetchone()
    if wiersz is None or not wiersz["wybor"]:
        return None
    wynik: dict[str, Any] = json.loads(wiersz["wybor"])
    return wynik


# Co robi collector, powiedziane po polsku. Klucze to etykiety zapytań
# z `klient.py` (`_narzedzie`), więc muszą się z nimi zgadzać — nieznana
# etykieta daje ogólne „czytam dane z monday", nie surowy `graphql:cokolwiek`.
_ETAPY_ZBIERANIA = {
    "graphql:konto": "sprawdzam konto i plan",
    "graphql:users": "czytam listę osób",
    "graphql:boards": "czytam tablice i kolumny",
    "graphql:boards_podglad": "czytam tablice",
    "graphql:workspaces": "czytam listę obszarów",
    "graphql:triggery": "czytam automatyzacje",
    "graphql:sonda_runow": "sprawdzam uruchomienia automatyzacji",
    "graphql:sonda_agentow": "sprawdzam agenty AI",
    "graphql:logi": "czytam dziennik aktywności",
    "graphql:log_tablicy": "czytam dziennik aktywności",
    "graphql:probka_kolumn": "sprawdzam wypełnienie kolumn",
}


def _etap_zbierania(narzedzie: str) -> str:
    return _ETAPY_ZBIERANIA.get(narzedzie, "czytam dane z monday")


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
        # `postep.opis()` jest DLA KONSOLI: „graphql:boards: 12/400 wywołań,
        # complexity 128 000". ZGŁOSZONE (Kuba, 2026-08-25): „w interfejsie widać
        # graphql:*, dla klienta to nieistotne totalnie".
        #
        # Klient dostaje etap po polsku, bez nazw zapytań, complexity i budżetu.
        # Pełny opis techniczny zostaje w logu procesu — tam jest dla nas.
        zapisz_stan(
            self._con,
            self._zadanie,
            stan=STAN_ZBIERAM,
            etap=_etap_zbierania(postep.narzedzie),
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
