"""Reset haseł: czy nowe hasło faktycznie UNIEWAŻNIA stare.

Ten plik istnieje z powodu zmierzonej usterki, nie z chęci pokrycia. Do
2026-08-10 `--dodaj-klienta cxlabs` wywołane drugi raz **nie zmieniało hasła** —
zakładało drugie konto, a stare hasło nadal wpuszczało. Na kopii bazy demo klient
`cxlabs` miał konta id 3 i 7, oba działające, bo `zaloguj` bierze konto klienta
przez `fetchone()` bez `ORDER BY`.

To był guardrail, w który się wierzyło: „wydałem nowe hasło" wyglądało na
odebranie starego dostępu i nie odbierało go. Czwarty taki przypadek w tym
projekcie — po `--read-only` w MCP, `can_use_tool` i kluczu API.

Dlatego testy niżej sprawdzają **skutek**, nie wywołanie: czy stare hasło
przestaje działać, a nowe zaczyna. Test, który sprawdza tylko „reset zwrócił
hasło", przechodziłby także przed poprawką.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.dostep import (
    MAKS_ZADAN_RESETU,
    ROLA_KLIENT,
    ROLA_ZESPOL,
    DostepError,
    konto_klienta,
    policz_wazne_sesje,
    poproszono_o_reset,
    utworz_konto,
    wygeneruj_haslo,
    zaloguj,
    zresetuj_haslo,
    zuzyj_token_resetu,
)

HASLO_STARE = "stare55-haslo66-testowe77-dlugie88"
EMAIL = "kuba@cxlabs.digital"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "dostep.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


def _wpusci(con: sqlite3.Connection, haslo: str, **kto: str) -> bool:
    """Czy tym hasłem da się wejść. Sedno wszystkich testów w tym pliku."""
    try:
        zaloguj(con, haslo=haslo, ip=None, **kto)
    except DostepError:
        return False
    return True


# ── reset unieważnia stare hasło ─────────────────────────────────────────


def test_reset_kliencki_uniewaznia_stare_haslo(con: sqlite3.Connection) -> None:
    """DOKŁADNIE ten scenariusz, który przed poprawką zawodził."""
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_STARE, client_id="acme")
    assert _wpusci(con, HASLO_STARE, client_id="acme"), "konto nie działa od początku"

    konto_id = konto_klienta(con, "acme")
    assert konto_id is not None
    wynik = zresetuj_haslo(con, konto_id=konto_id)

    assert not _wpusci(con, HASLO_STARE, client_id="acme"), "stare hasło nadal wpuszcza"
    assert _wpusci(con, wynik.haslo, client_id="acme"), "nowe hasło nie wpuszcza"


def test_reset_nie_tworzy_drugiego_konta(con: sqlite3.Connection) -> None:
    """Reset nadpisuje wiersz. Drugie konto to była właśnie usterka."""
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_STARE, client_id="acme")
    konto_id = konto_klienta(con, "acme")
    assert konto_id is not None

    zresetuj_haslo(con, konto_id=konto_id)

    ile = con.execute(
        "SELECT COUNT(*) n FROM konta_dostepu WHERE client_id = 'acme' AND aktywne = 1"
    ).fetchone()["n"]
    assert ile == 1, f"po resecie {ile} aktywnych kont — reset zdublował konto"
    assert konto_klienta(con, "acme") == konto_id, "reset podmienił konto na inne"


def test_reset_zespolu_dziala_tak_samo(con: sqlite3.Connection) -> None:
    """Jedna funkcja dla obu rol — inaczej jedna z nich zostałaby bez poprawki."""
    utworz_konto(con, rola=ROLA_ZESPOL, haslo=HASLO_STARE, email=EMAIL)
    wiersz = con.execute("SELECT id FROM konta_dostepu WHERE email = ?", (EMAIL,)).fetchone()

    wynik = zresetuj_haslo(con, konto_id=int(wiersz["id"]))

    assert not _wpusci(con, HASLO_STARE, email=EMAIL)
    assert _wpusci(con, wynik.haslo, email=EMAIL)


def test_reset_nieistniejacego_konta_odmawia(con: sqlite3.Connection) -> None:
    with pytest.raises(DostepError, match="nie ma aktywnego konta"):
        zresetuj_haslo(con, konto_id=9999)


# ── duplikaty: zamknięta droga ───────────────────────────────────────────


def test_dodanie_drugiego_konta_klienta_odmawia(con: sqlite3.Connection) -> None:
    """Komunikat MÓWI, co zrobić — `IntegrityError` z indeksu by nie powiedział.

    Indeks z migracji 007 i tak by tego nie wpuścił, ale wtedy operator widzi
    „UNIQUE constraint failed" i nie wie, że właściwą drogą jest reset.
    """
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_STARE, client_id="acme")

    with pytest.raises(DostepError, match="już aktywne konto"):
        utworz_konto(con, rola=ROLA_KLIENT, haslo="inne99-haslo88-zupelnie77", client_id="acme")


def test_schemat_nie_wpusci_duplikatu_omijajac_kod(con: sqlite3.Connection) -> None:
    """Sprawdzenie w `utworz_konto` to pierwsza linia, indeks to druga.

    Bez indeksu każda inna droga zapisu (skrypt, migracja, ręczny SQL) mogłaby
    znowu utworzyć dwa aktywne konta — a to stan, w którym `zaloguj` wpuszcza
    dowolne z nich.
    """
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_STARE, client_id="acme")

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO konta_dostepu (rola, client_id, hash_hasla, sol_hasla, utworzono, "
            "aktywne) VALUES ('klient', 'acme', 'x', 'y', '2026-08-10T00:00:00+00:00', 1)"
        )


def test_dezaktywowane_konta_moga_sie_powtarzac(con: sqlite3.Connection) -> None:
    """Indeks jest CZĘŚCIOWY i to jest celowe.

    Historia dostępów ma prawo mieć wiele wierszy na klienta — inaczej nie dałoby
    się zachować śladu, kto miał dostęp i od kiedy.
    """
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_STARE, client_id="acme")
    con.execute("UPDATE konta_dostepu SET aktywne = 0 WHERE client_id = 'acme'")
    con.commit()

    # Dwa nieaktywne wiersze tego samego klienta — schemat na to pozwala.
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_STARE, client_id="acme")
    con.execute("UPDATE konta_dostepu SET aktywne = 0 WHERE client_id = 'acme'")
    con.commit()

    ile = con.execute("SELECT COUNT(*) n FROM konta_dostepu WHERE client_id = 'acme'").fetchone()[
        "n"
    ]
    assert ile == 2


# ── sesje po resecie: decyzja, nie przeoczenie ───────────────────────────


def test_reset_nie_wylogowuje_i_mowi_o_tym(con: sqlite3.Connection) -> None:
    """Decyzja Kuby: reset wydaje nowe hasło, nie przerywa pracy.

    Konsekwencja jest niewygodna i dlatego MUSI być widoczna: reset nie odcina
    dostępu natychmiast. `wazne_sesje` jest w wyniku właśnie po to, żeby interfejs
    mógł to napisać — bez tego ktoś klika „reset" i uznaje, że odciął dostęp.

    Gdyby ktoś kiedyś zmienił to na wylogowywanie, ten test padnie i każe świadomie
    zdecydować, a nie zmienić przez przypadek.
    """
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_STARE, client_id="acme")
    zaloguj(con, haslo=HASLO_STARE, client_id="acme", ip=None)
    konto_id = konto_klienta(con, "acme")
    assert konto_id is not None
    assert policz_wazne_sesje(con, konto_id) == 1

    wynik = zresetuj_haslo(con, konto_id=konto_id)

    assert wynik.wazne_sesje == 1, "wynik resetu nie mówi o otwartej sesji"
    assert policz_wazne_sesje(con, konto_id) == 1, "reset usunął sesję — to zmiana decyzji"


def test_wygenerowane_haslo_da_sie_podac_przez_telefon() -> None:
    """Format sylabowy, ten sam co przy zakładaniu konta — nie `x8#Kq!2v`."""
    haslo = wygeneruj_haslo()

    czlony = haslo.split("-")
    assert len(czlony) == 4
    assert all(c[:3].isalpha() and c[3:].isdigit() for c in czlony), haslo


# ── „nie pamiętam hasła": droga BEZ sesji ────────────────────────────────
#
# Powód istnienia: reset z panelu (007) wymaga sesji, a kto zgubił hasło, sesji
# nie ma. Zgłoszone przez Kubę wprost — „dalej nie mogę zresetować hasła" — bo
# poprzednia wersja dała „zmień hasło, gdy je znasz", nie „nie pamiętam hasła".


def test_token_wymienia_sie_na_nowe_haslo(con: sqlite3.Connection) -> None:
    """Cała ścieżka: żądanie → token → nowe hasło → stare nie działa."""
    utworz_konto(con, rola=ROLA_ZESPOL, haslo=HASLO_STARE, email=EMAIL)

    token = poproszono_o_reset(con, email=EMAIL, ip=None)
    assert token is not None, "nie wydał tokenu dla istniejącego konta"

    wynik = zuzyj_token_resetu(con, token)

    assert not _wpusci(con, HASLO_STARE, email=EMAIL), "stare hasło nadal wpuszcza"
    assert _wpusci(con, wynik.haslo, email=EMAIL), "nowe hasło nie wpuszcza"


def test_token_dziala_dokladnie_raz(con: sqlite3.Connection) -> None:
    """Mail bywa przekazywany i cytowany w odpowiedzi.

    Link, który działa dwa razy, daje dostęp każdemu, kto kiedykolwiek zobaczy
    tę wiadomość — także po tym, jak właściciel już go użył.
    """
    utworz_konto(con, rola=ROLA_ZESPOL, haslo=HASLO_STARE, email=EMAIL)
    token = poproszono_o_reset(con, email=EMAIL, ip=None)
    assert token is not None
    zuzyj_token_resetu(con, token)

    with pytest.raises(DostepError, match="nieważny lub został już użyty"):
        zuzyj_token_resetu(con, token)


def test_wygasly_token_nie_dziala(con: sqlite3.Connection) -> None:
    """Link leży w skrzynce latami; ważność liczy się w minutach."""
    utworz_konto(con, rola=ROLA_ZESPOL, haslo=HASLO_STARE, email=EMAIL)
    token = poproszono_o_reset(con, email=EMAIL, ip=None)
    assert token is not None
    con.execute("UPDATE reset_tokeny SET wazny_do = '2020-01-01T00:00:00+00:00'")
    con.commit()

    with pytest.raises(DostepError, match="nieważny lub został już użyty"):
        zuzyj_token_resetu(con, token)


def test_nowe_zadanie_uniewaznia_poprzedni_link(con: sqlite3.Connection) -> None:
    """Pięć kliknięć „nie pamiętam" nie zostawia pięciu ważnych linków."""
    utworz_konto(con, rola=ROLA_ZESPOL, haslo=HASLO_STARE, email=EMAIL)
    pierwszy = poproszono_o_reset(con, email=EMAIL, ip=None)
    drugi = poproszono_o_reset(con, email=EMAIL, ip=None)
    assert pierwszy is not None and drugi is not None

    with pytest.raises(DostepError):
        zuzyj_token_resetu(con, pierwszy)
    assert zuzyj_token_resetu(con, drugi).haslo, "najnowszy link musi działać"


def test_nieistniejacy_adres_nie_dostaje_tokenu(con: sqlite3.Connection) -> None:
    """`None`, a wołający MUSI odpowiedzieć tak samo jak przy istniejącym.

    Rozróżnienie zamieniłoby bramę w wyrocznię: „ten adres @cxlabs.digital jest
    prawdziwy, tamten nie".
    """
    assert poproszono_o_reset(con, email="nie-ma@cxlabs.digital", ip=None) is None


def test_klient_nie_dostanie_tokenu_resetu(con: sqlite3.Connection) -> None:
    """Ta droga jest WYŁĄCZNIE dla zespołu.

    Klient nie ma skrzynki w naszej domenie, więc nie mamy czym potwierdzić, że
    to on prosi — a jego hasło jest jedyną bramą do jego danych osobowych.
    """
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_STARE, client_id="acme")

    # Nawet gdyby ktoś podał `client_id` jako e-mail, konto klienta ma `email`
    # NULL, więc zapytanie go nie znajdzie.
    assert poproszono_o_reset(con, email="acme", ip=None) is None


def test_limit_zadan_resetu_blokuje_zasypywanie(con: sqlite3.Connection) -> None:
    """Endpoint bez sesji jest otwarty na świat.

    Bez limitu da się zasypać czyjąś skrzynkę i sprawdzać adresy hurtowo.
    """
    utworz_konto(con, rola=ROLA_ZESPOL, haslo=HASLO_STARE, email=EMAIL)

    wydane = [poproszono_o_reset(con, email=EMAIL, ip="10.0.0.1") for _ in range(8)]

    assert all(t is not None for t in wydane[:MAKS_ZADAN_RESETU]), "limit zadziałał za wcześnie"
    assert wydane[MAKS_ZADAN_RESETU] is None, "limit nie zadziałał"
