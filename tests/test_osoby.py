"""Testy pseudonimizacji i granicy PII (etap 3.4), warstwa 1 z 04-test.md.

Najważniejszy test w tym pliku to `test_snapshot_nie_zawiera_ani_jednego_pii`.
04-test.md nazywa go wprost: skan payloadu wzorcem e-maila i nazwiskami
z mapowania. Jeśli kiedykolwiek zacznie przechodzić „przypadkiem" — bo ktoś
osłabił dane wejściowe — cały projekt traci swoją główną gwarancję.

Dane testowe są zmyślone. Prawdziwe imiona nie wchodzą do repo.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from monday_audit.baza import MapowanieOsob, polacz, zastosuj_migracje
from monday_audit.klient import MondayClient
from monday_audit.osoby import (
    DLUGOSC_HASHA,
    Osoba,
    PseudonimizacjaError,
    WpisPII,
    policz_hash,
    policz_podejrzenia_pii,
    waliduj_brak_pii,
    zbierz_osoby,
    zredaguj_pii,
)

TOKEN = "tajny-token-klienta"
SOL = b"sol-testowa-dluga-na-tyle-ze-przechodzi"
KLIENT = "cxlabs"

# Zmyślone dane. Nazwiska dobrane tak, żeby były dłuższe niż MIN_TOKEN_SKANU.
LUDZIE: list[dict[str, Any]] = [
    {
        "id": "101",
        "name": "Zdzisława Wąchockańska",
        "email": "zdzislawa@przyklad.test",
        "kind": "admin",
        "status": "ACTIVE",
        "is_deleted": False,
        "is_email_confirmed": True,
        "created_at": "2024-01-15T10:00:00Z",
        "became_active_at": "2024-01-16T09:00:00Z",
        "last_activity": "2026-07-30T20:25:49Z",
        "title": "Dyrektor operacyjny",
        "teams": [{"id": "1", "name": "Zarząd"}],
    },
    {
        "id": "102",
        "name": "Bonifacy Krzeptowski",
        "email": "bonifacy@przyklad.test",
        "kind": "guest",
        "status": "PENDING",
        "is_deleted": False,
        "is_email_confirmed": False,
        "created_at": "2025-06-01T08:30:00Z",
        "became_active_at": None,
        "last_activity": None,
        "title": None,
        "teams": [],
    },
]


class MapowanieTestowe:
    def __init__(self) -> None:
        self.wpisy: list[WpisPII] = []

    def zapisz_wiele(self, wpisy: Any) -> int:
        self.wpisy = list(wpisy)
        return len(self.wpisy)


def odpowiedz_users(ludzie: list[dict[str, Any]], *, koszt: int = 1753) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "users": ludzie,
                "complexity": {"query": koszt, "after": 9_000_000, "reset_in_x_seconds": 60},
            }
        },
    )


@pytest.fixture
async def zbuduj() -> AsyncIterator[Callable[..., MondayClient]]:
    klienci: list[MondayClient] = []

    def fabryka(uchwyt: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> MondayClient:
        egzemplarz = MondayClient(
            TOKEN, _RejestrCichy(), transport=httpx.MockTransport(uchwyt), **kwargs
        )
        klienci.append(egzemplarz)
        return egzemplarz

    yield fabryka

    for egzemplarz in klienci:
        await egzemplarz.zamknij()


class _RejestrCichy:
    def zapisz(self, **kwargs: Any) -> None:
        pass


def uchwyt_stronicowany(ludzie: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    """Pierwsza strona z ludźmi, druga pusta — tak kończy się paginacja."""
    strony = {1: ludzie}

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        numer = json.loads(zapytanie.content)["variables"]["p"]
        return odpowiedz_users(strony.get(numer, []))

    return uchwyt


# ── TEST ANTYPRZECIEKOWY (04-test.md, „Bezpieczeństwo") ──────────────────


async def test_snapshot_nie_zawiera_ani_jednego_pii(zbuduj: Any) -> None:
    klient = zbuduj(uchwyt_stronicowany(LUDZIE))
    mapowanie = MapowanieTestowe()

    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=mapowanie)
    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)

    assert "@" not in payload
    for czlowiek in LUDZIE:
        assert czlowiek["name"] not in payload
        assert czlowiek["email"] not in payload
        for czesc in str(czlowiek["name"]).split():
            assert czesc not in payload, f"fragment imienia {czesc} wyciekł do snapshotu"
        assert czlowiek["id"] not in payload, "surowy id monday też nie należy do snapshotu"


def test_walidator_lapie_email_w_payloadzie() -> None:
    with pytest.raises(PseudonimizacjaError, match="adresu e-mail"):
        waliduj_brak_pii('{"title": "kontakt: ktos@firma.test"}', [])


def test_walidator_lapie_pelne_imie_w_nazwie_zespolu() -> None:
    """Realny scenariusz i jednoznaczny wyciek: zespół nazwany całym imieniem."""
    payload = '{"zespoly": ["Zespół Bonifacy Krzeptowski"]}'

    with pytest.raises(PseudonimizacjaError, match="pełnych imion"):
        waliduj_brak_pii(payload, [WpisPII("h1", "Bonifacy Krzeptowski", "b@t.test")])


def test_walidator_nie_przerywa_na_pojedynczym_tokenie() -> None:
    """Zmierzone na CXLABS: skan tokenowy dał 54 fałszywki z 3 tokenów.

    Konta serwisowe mają w `name` nazwę firmy albo produktu, a te słowa
    naturalnie występują w nazwach zespołów pisanych przez klienta.
    Przerywanie audytu na tym byłoby blokadą bez powodu.
    """
    waliduj_brak_pii(
        '{"zespoly": ["CXLABS Demo", "Zespół Krzeptowski"]}',
        [WpisPII("h1", "CXLABS", None), WpisPII("h2", "Bonifacy Krzeptowski", None)],
    )


def test_nazwa_jednowyrazowa_nie_jest_twardym_wyciekiem() -> None:
    """Konto serwisowe „CXLABS" to nie imię i nazwisko."""
    waliduj_brak_pii('{"zespoly": ["CXLABS Main"]}', [WpisPII("h1", "CXLABS", None)])


def test_licznik_podejrzen_zglasza_czastkowe_trafienia() -> None:
    """Nie przerywa, ale nie milczy — liczba idzie do snapshotu i logu."""
    payload = '{"zespoly": ["Zespół Krzeptowski"], "title": "Bonifacy od wszystkiego"}'

    assert policz_podejrzenia_pii(payload, [WpisPII("h1", "Bonifacy Krzeptowski", None)]) == 2


def test_licznik_podejrzen_ignoruje_krotkie_tokeny() -> None:
    """Imię „Ola" w słowie „Solaris" nie może generować podejrzenia."""
    assert policz_podejrzenia_pii('{"zespoly": ["Solaris"]}', [WpisPII("h1", "Ola", None)]) == 0


def test_walidator_komunikat_nie_zawiera_wartosci() -> None:
    """Błąd o wycieku PII, który loguje PII, nie jest zabezpieczeniem."""
    with pytest.raises(PseudonimizacjaError) as blad:
        waliduj_brak_pii('{"a": "ktos@firma.test"}', [WpisPII("h1", "Kazimierz Krzeptowski", None)])

    assert "ktos@firma.test" not in str(blad.value)
    assert "Krzeptowski" not in str(blad.value)


async def test_pii_leci_do_mapowania_kompletne(zbuduj: Any) -> None:
    klient = zbuduj(uchwyt_stronicowany(LUDZIE))
    mapowanie = MapowanieTestowe()

    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=mapowanie)

    assert wynik.zapisanych_mapowan == len(LUDZIE)
    assert {w.email for w in mapowanie.wpisy} == {c["email"] for c in LUDZIE}
    assert {w.imie_nazwisko for w in mapowanie.wpisy} == {c["name"] for c in LUDZIE}
    # Hash łączy jedno z drugim i tylko on jest w snapshocie.
    assert {w.user_hash for w in mapowanie.wpisy} == {o.user_hash for o in wynik.osoby}


# ── sól i hash ───────────────────────────────────────────────────────────


def test_hash_jest_stabilny_miedzy_runami() -> None:
    """Bez tego snapshot #1 i #4 tego samego klienta są nieporównywalne (D7)."""
    assert policz_hash(KLIENT, "101", SOL) == policz_hash(KLIENT, 101, SOL)


def test_inna_sol_daje_inny_hash() -> None:
    assert policz_hash(KLIENT, "101", SOL) != policz_hash(KLIENT, "101", b"inna-sol-tez-dluga-dosc")


def test_inny_klient_daje_inny_hash() -> None:
    """Dwa konta obsłużone tą samą solą nie mogą dać wspólnych pseudonimów."""
    assert policz_hash("klient-a", "101", SOL) != policz_hash("klient-b", "101", SOL)


def test_hash_nie_zawiera_wejscia() -> None:
    haszyk = policz_hash(KLIENT, "101", SOL)
    assert len(haszyk) == DLUGOSC_HASHA
    assert "101" not in haszyk


# Odczyt soli ze środowiska przeniósł się do `konfiguracja` (D12), więc jego
# testy stoją w `test_konfiguracja.py`. Tutaj zostaje to, co jest regułą granicy
# PII, a nie regułą configu: minimalna długość i typ wyjątku.


# ── zawartość snapshotu ──────────────────────────────────────────────────


async def test_snapshot_ma_dokladnie_dozwolone_pola(zbuduj: Any) -> None:
    klient = zbuduj(uchwyt_stronicowany(LUDZIE))

    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=MapowanieTestowe())

    assert set(wynik.do_snapshotu()["uzytkownicy"][0]) == {
        "user_hash",
        "title",
        "zespoly",
        "kind",
        "status",
        "is_deleted",
        "is_email_confirmed",
        "created_at",
        "became_active_at",
        "last_activity",
    }


async def test_nowe_pole_z_api_nie_wycieka(zbuduj: Any) -> None:
    """Snapshot budowany z listy dozwolonych, nie przez usuwanie zabronionych."""
    czlowiek = {**LUDZIE[0], "phone": "+48 600 700 800", "nowe_pole_api": "cokolwiek"}
    klient = zbuduj(uchwyt_stronicowany([czlowiek]))

    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=MapowanieTestowe())
    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)

    assert "600 700 800" not in payload
    assert "nowe_pole_api" not in payload


async def test_podsumowanie_liczy_to_co_potrzebuja_detektory(zbuduj: Any) -> None:
    klient = zbuduj(uchwyt_stronicowany(LUDZIE))

    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=MapowanieTestowe())

    assert wynik.podsumowanie() == {
        "razem": 2,
        # Jeden admin zajmuje płatne miejsce, gość NIE. Na tym stoi wycena
        # w ZOMBIE_ACCOUNT — `razem` nie jest liczbą ludzi ani licencji.
        "zajmujacych_miejsce": 1,
        "adminow": 1,
        "gosci": 1,
        "agentow": 0,
        "tylko_podglad": 0,
        "aktywnych": 1,
        "nieaktywnych": 0,
        "oczekujacych": 1,
        "usunietych": 0,
        "z_potwierdzonym_mailem": 1,
        "bez_last_activity": 1,
        "bez_became_active_at": 1,
        "bez_title": 1,
        "bez_zespolu": 1,
    }


async def test_agenci_i_podglad_nie_zajmuja_miejsca(zbuduj: Any) -> None:
    """Zmierzone na CXLABS: 36 z 95 rekordów to konta agentów AI, 28 to view_only.

    Flagi `is_admin`/`is_guest` pokazywały jedno i drugie jako zwykłego
    członka, więc ZOMBIE_ACCOUNT liczyłby „nieaktywnych użytkowników" po
    wszystkich rekordach i zawyżył wynik czterokrotnie.
    """
    ludzie = [
        {**LUDZIE[0], "id": "201", "kind": "personal_agent_member"},
        {**LUDZIE[0], "id": "202", "kind": "view_only"},
        {**LUDZIE[0], "id": "203", "kind": "member"},
    ]
    klient = zbuduj(uchwyt_stronicowany(ludzie))

    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=MapowanieTestowe())
    podsumowanie = wynik.podsumowanie()

    assert podsumowanie["razem"] == 3
    assert podsumowanie["zajmujacych_miejsce"] == 1
    assert podsumowanie["agentow"] == 1
    assert podsumowanie["tylko_podglad"] == 1


async def test_nieznany_rodzaj_jest_policzony_a_nie_wciśniety(zbuduj: Any) -> None:
    """Zbiór wartości `kind` nie jest zamknięty — API nie deklaruje enuma.

    Cicha zamiana nieznanego rodzaju na „member" wliczyłaby go do płatnych
    miejsc i klient dostałby rachunek za coś, czego nie rozumiemy.
    """
    ludzie = [{**LUDZIE[0], "id": "301", "kind": "wymyslony_przez_monday"}]
    klient = zbuduj(uchwyt_stronicowany(ludzie))

    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=MapowanieTestowe())

    assert wynik.discovery["nieznane_rodzaje"] == ["wymyslony_przez_monday"]
    assert wynik.discovery["po_rodzaju"] == {"wymyslony_przez_monday": 1}
    assert wynik.podsumowanie()["zajmujacych_miejsce"] == 0


async def test_brak_last_activity_zostaje_nullem(zbuduj: Any) -> None:
    """`null` znaczy „nie wiem", nie „nieaktywny od zawsze" — ZOMBIE_ACCOUNT
    nie może liczyć tych kont jako martwych bez sygnału z 3.7."""
    klient = zbuduj(uchwyt_stronicowany(LUDZIE))

    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=MapowanieTestowe())

    bez = [o for o in wynik.osoby if o.last_activity is None]
    assert len(bez) == 1
    assert wynik.discovery == {
        "last_activity_dostepne": True,
        "last_activity_wypelnione": 1,
        "last_activity_razem": 2,
        "po_rodzaju": {"admin": 1, "guest": 1},
        "nieznane_rodzaje": [],
        "bez_rodzaju": 0,
        "is_verified_porzucone": "brak w API 2026-10; is_email_confirmed to inne pole",
        "podejrzenia_pii_w_tekstach": 0,
    }


async def test_uzytkownik_bez_id_przerywa(zbuduj: Any) -> None:
    klient = zbuduj(uchwyt_stronicowany([{"name": "Ktoś Bezidowy", "email": "a@b.test"}]))

    with pytest.raises(PseudonimizacjaError, match="bez `id`"):
        await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=MapowanieTestowe())


async def test_paginacja_zbiera_wszystkie_strony(zbuduj: Any) -> None:
    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        numer = json.loads(zapytanie.content)["variables"]["p"]
        if numer == 1:
            return odpowiedz_users([LUDZIE[0]])
        if numer == 2:
            return odpowiedz_users([LUDZIE[1]])
        return odpowiedz_users([])

    klient = zbuduj(uchwyt)
    wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL, mapowanie=MapowanieTestowe())

    assert len(wynik.osoby) == 2
    assert klient.liczba_wywolan == 3


# ── tabela osoby_mapowanie ───────────────────────────────────────────────


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


def test_mapowanie_pisze_do_tabeli(con: sqlite3.Connection) -> None:
    mapowanie = MapowanieOsob(con, KLIENT)

    zapisanych = mapowanie.zapisz_wiele(
        [
            WpisPII(user_hash="abc123", imie_nazwisko="Zdzisława Wąchockańska", email="z@t.test"),
            WpisPII(user_hash="def456", imie_nazwisko="Bonifacy Krzeptowski", email="b@t.test"),
        ]
    )

    assert zapisanych == 2
    wiersze = con.execute(
        "SELECT client_id, user_hash, imie_nazwisko, email FROM osoby_mapowanie ORDER BY user_hash"
    ).fetchall()
    assert [w["user_hash"] for w in wiersze] == ["abc123", "def456"]
    assert wiersze[0]["client_id"] == KLIENT


def test_powtorny_run_nadpisuje_mapowanie(con: sqlite3.Connection) -> None:
    """Drugi audyt tego samego klienta nie może się wywalić na kluczu głównym."""
    mapowanie = MapowanieOsob(con, KLIENT)
    mapowanie.zapisz_wiele([WpisPII("abc123", "Stare Nazwisko", "stary@t.test")])

    mapowanie.zapisz_wiele([WpisPII("abc123", "Nowe Nazwisko", "nowy@t.test")])

    wiersze = con.execute("SELECT imie_nazwisko, email FROM osoby_mapowanie").fetchall()
    assert len(wiersze) == 1
    assert wiersze[0]["imie_nazwisko"] == "Nowe Nazwisko"


def test_mapowanie_rozdziela_klientow(con: sqlite3.Connection) -> None:
    """Po audycie dostęp jest odbierany (D11) — kasowanie musi być per klient."""
    MapowanieOsob(con, "klient-a").zapisz_wiele([WpisPII("hash-a", "Ktoś A", "a@t.test")])
    MapowanieOsob(con, "klient-b").zapisz_wiele([WpisPII("hash-b", "Ktoś B", "b@t.test")])

    con.execute("DELETE FROM osoby_mapowanie WHERE client_id = 'klient-a'")
    con.commit()

    pozostale = con.execute("SELECT client_id FROM osoby_mapowanie").fetchall()
    assert [w["client_id"] for w in pozostale] == ["klient-b"]


def test_puste_mapowanie_nie_rusza_bazy(con: sqlite3.Connection) -> None:
    assert MapowanieOsob(con, KLIENT).zapisz_wiele([]) == 0
    assert con.execute("SELECT count(*) FROM osoby_mapowanie").fetchone()[0] == 0


def test_osoba_jest_niemutowalna() -> None:
    osoba = Osoba(
        user_hash="abc",
        title=None,
        zespoly=(),
        kind="member",
        status="ACTIVE",
        is_deleted=False,
        is_email_confirmed=False,
        created_at=None,
        became_active_at=None,
        last_activity=None,
    )

    with pytest.raises((AttributeError, TypeError)):
        osoba.user_hash = "inny"  # type: ignore[misc]


# ── redakcja PII w treści pisanej przez klienta ───────────────────────────


def test_redakcja_podmienia_pelne_imie_na_pseudonim() -> None:
    """Zmierzone przy 3.8: klient nazwał obiekt w monday imieniem osoby."""
    dane = {"tablice": [{"nazwa": "Zdzisława Wąchockańska prywatna"}]}

    wynik, sciezki = zredaguj_pii(dane, [WpisPII("abc123", "Zdzisława Wąchockańska", None)])

    assert wynik["tablice"][0]["nazwa"] == "[OSOBA:abc123] prywatna"
    assert sciezki == ["tablice[0].nazwa"]


def test_redakcja_nie_rusza_nazw_jednowyrazowych() -> None:
    """„CXLABS" to konto serwisowe — podmiana zniszczyłaby nazwy zespołów."""
    dane = {"zespoly": ["CXLABS Main"]}

    wynik, sciezki = zredaguj_pii(dane, [WpisPII("abc", "CXLABS", None)])

    assert wynik == dane
    assert sciezki == []


def test_redakcja_podmienia_znany_email() -> None:
    dane = {"title": "pisz na bonifacy@przyklad.test"}

    wynik, _ = zredaguj_pii(dane, [WpisPII("def456", None, "bonifacy@przyklad.test")])

    assert wynik["title"] == "pisz na [EMAIL:def456]"


def test_redakcja_zwraca_sciezki_nie_wartosci() -> None:
    """Raport z runu nie może być wyciekiem — stąd ścieżki, nie treść."""
    dane = {"a": {"b": ["Bonifacy Krzeptowski"]}}

    _, sciezki = zredaguj_pii(dane, [WpisPII("h", "Bonifacy Krzeptowski", None)])

    assert sciezki == ["a.b[0]"]
    assert all("Bonifacy" not in s for s in sciezki)


def test_redakcja_jest_niewrazliwa_na_wielkosc_liter() -> None:
    dane = {"nazwa": "tablica zdzisławy... nie, ZDZISŁAWA WĄCHOCKAŃSKA"}

    wynik, _ = zredaguj_pii(dane, [WpisPII("h", "Zdzisława Wąchockańska", None)])

    assert "ZDZISŁAWA WĄCHOCKAŃSKA" not in wynik["nazwa"]
    assert "[OSOBA:h]" in wynik["nazwa"]


def test_redakcja_bez_wpisow_nic_nie_robi() -> None:
    dane = {"nazwa": "cokolwiek"}
    assert zredaguj_pii(dane, []) == (dane, [])


def test_redakcja_zostawia_liczby_i_bool_w_spokoju() -> None:
    dane = {"ile": 5, "czy": True, "brak": None}
    assert zredaguj_pii(dane, [WpisPII("h", "Jan Kowalski", None)])[0] == dane


def test_redakcja_nie_wchodzi_w_srodek_dluzszego_slowa() -> None:
    """Zmierzone na CXLABS: konto „AI Agent" psuło workspace „monday AI Agents".

    Bez granic słów redakcja zamieniała 105 rekordów na „monday [OSOBA:...]s".
    """
    dane = {"workspace_nazwa": "monday AI Agents"}

    wynik, sciezki = zredaguj_pii(dane, [WpisPII("h", "AI Agent", None)])

    assert wynik == dane
    assert sciezki == []


def test_walidator_tez_nie_lapie_nazwy_w_liczbie_mnogiej() -> None:
    """Walidator musi mieć ten sam warunek, inaczej przerwie run na fałszywce."""
    waliduj_brak_pii('{"workspace_nazwa": "monday AI Agents"}', [WpisPII("h", "AI Agent", None)])


def test_redakcja_dziala_gdy_imie_konczy_slowo() -> None:
    dane = {"nazwa": "tablica: Bonifacy Krzeptowski"}

    wynik, _ = zredaguj_pii(dane, [WpisPII("h", "Bonifacy Krzeptowski", None)])

    assert wynik["nazwa"] == "tablica: [OSOBA:h]"
