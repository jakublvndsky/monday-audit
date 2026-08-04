"""Deanonimizacja raportu (3.12) — granica, nie funkcja pomocnicza.

Te testy pilnują dwóch rzeczy naraz: że hashe DAJĄ się rozwinąć (bez tego
raport jest niewykonalny dla klienta) i że **żaden surowy hash nie zostaje**
w dokumencie. Drugie jest ważniejsze i dlatego stoi tu, obok testów PII,
a nie w testach renderera.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.deanonimizacja import WZORZEC_HASHA, Deanonimizacja

# Prawdziwy kształt: 16 znaków hex (`osoby.DLUGOSC_HASHA`).
HASH_ANNY = "05677b1ab370bae1"
HASH_JANA = "86ee1a3be3e79c41"
HASH_BEZ_NAZWY = "df4edd59f3def215"
HASH_OBCY = "deadbeefdeadbeef"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    polaczenie.executemany(
        "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
        "VALUES (?, ?, ?, ?)",
        [
            ("cxlabs", HASH_ANNY, "Anna Górniak", "anna@klient.test"),
            ("cxlabs", HASH_JANA, "Jan Kowalski", None),
            # monday nie zawsze oddaje nazwę — `osoby_mapowanie` dopuszcza NULL
            ("cxlabs", HASH_BEZ_NAZWY, None, "kontakt@klient.test"),
            ("inny-klient", HASH_OBCY, "Nie Ten Klient", None),
        ],
    )
    polaczenie.commit()
    yield polaczenie
    polaczenie.close()


# ── rozwijanie ───────────────────────────────────────────────────────────


def test_znany_hash_dostaje_imie(con: sqlite3.Connection) -> None:
    assert Deanonimizacja(con, "cxlabs").nazwa(HASH_ANNY) == "Anna Górniak"


def test_bez_nazwy_wchodzi_email(con: sqlite3.Connection) -> None:
    """Puste mapowanie to nie nazwa — inaczej raport mówi „konto  jest martwe"."""
    assert Deanonimizacja(con, "cxlabs").nazwa(HASH_BEZ_NAZWY) == "kontakt@klient.test"


def test_email_dokladany_tylko_na_zadanie(con: sqlite3.Connection) -> None:
    """Wersja wewnętrzna może chcieć adresu; domyślnie samo nazwisko."""
    assert Deanonimizacja(con, "cxlabs").nazwa(HASH_ANNY) == "Anna Górniak"
    assert (
        Deanonimizacja(con, "cxlabs", z_emailem=True).nazwa(HASH_ANNY)
        == "Anna Górniak (anna@klient.test)"
    )


def test_mapowanie_innego_klienta_jest_niewidoczne(con: sqlite3.Connection) -> None:
    """Sól jest per klient (D11), więc hash z cudzego mapowania jest nieznany."""
    rozwiniete = Deanonimizacja(con, "cxlabs").nazwa(HASH_OBCY)

    assert "Nie Ten Klient" not in rozwiniete
    assert rozwiniete.startswith("[nieznane konto")


# ── hash w wolnym tekście ────────────────────────────────────────────────


def test_hash_w_srodku_zdania(con: sqlite3.Connection) -> None:
    """Zmierzone na prawdziwym runie: agent wpisuje hash w `opis`."""
    deanon = Deanonimizacja(con, "cxlabs")

    wynik = deanon.tekst(f"Konto administratora (hash {HASH_ANNY}) ma status ACTIVE")

    assert wynik == "Konto administratora (Anna Górniak) ma status ACTIVE"


def test_slowo_hash_jest_zjadane(con: sqlite3.Connection) -> None:
    """Bez tego w dokumencie dla klienta zostaje „(hash Anna Górniak)"."""
    deanon = Deanonimizacja(con, "cxlabs")

    assert "hash" not in deanon.tekst(f"hash {HASH_ANNY}").lower()
    assert "hash" not in deanon.tekst(f"Hashe {HASH_ANNY} i {HASH_JANA}").lower()


def test_kilka_hashy_w_jednym_zdaniu(con: sqlite3.Connection) -> None:
    deanon = Deanonimizacja(con, "cxlabs")

    wynik = deanon.tekst(f"Konta {HASH_ANNY} i {HASH_JANA} są martwe")

    assert wynik == "Konta Anna Górniak i Jan Kowalski są martwe"


def test_identyfikator_monday_zostaje_nietkniety(con: sqlite3.Connection) -> None:
    """`board_id` jest liczbowy, więc nie może wpaść pod wzorzec hasha."""
    deanon = Deanonimizacja(con, "cxlabs")

    assert deanon.tekst("Tablica 5097387646 ma 21 kolumn") == "Tablica 5097387646 ma 21 kolumn"
    assert deanon.nieznane == ()


# ── struktura `dowod` ────────────────────────────────────────────────────


def test_lista_hashy_pod_kluczem_hash(con: sqlite3.Connection) -> None:
    """`guest_hash[]` z rubryki: klucz jest jeden, wartości wiele."""
    deanon = Deanonimizacja(con, "cxlabs")

    wynik = deanon.wartosc({"guest_hash": [HASH_ANNY, HASH_JANA], "liczba_guest": 12})

    assert wynik == {"guest_hash": ["Anna Górniak", "Jan Kowalski"], "liczba_guest": 12}


def test_zagniezdzona_struktura(con: sqlite3.Connection) -> None:
    """`kandydaci[]` to lista słowników — rekurencja musi tam wejść."""
    deanon = Deanonimizacja(con, "cxlabs")

    wynik = deanon.wartosc(
        {
            "kandydaci": [
                {"board_id": "5097205810", "nazwa": "Onboarding", "top_kontrybutor_hash": HASH_ANNY}
            ]
        }
    )

    assert wynik["kandydaci"][0]["top_kontrybutor_hash"] == "Anna Górniak"
    assert wynik["kandydaci"][0]["nazwa"] == "Onboarding"


def test_hash_pod_kluczem_niebedacym_hashem_tez_wychodzi(con: sqlite3.Connection) -> None:
    """Dowód niesie też wolny tekst, np. `powody_bledow`.

    Gdyby podmiana działała tylko na kluczach `*hash*`, surowy hash zostałby
    w takim polu — a to dokładnie ten przypadek, którego test granicy ma nie
    przepuścić.
    """
    deanon = Deanonimizacja(con, "cxlabs")

    wynik = deanon.wartosc({"powody_bledow": f"brak uprawnień u {HASH_ANNY}"})

    assert wynik == {"powody_bledow": "brak uprawnień u Anna Górniak"}


def test_liczby_i_bool_nie_sa_ruszane(con: sqlite3.Connection) -> None:
    deanon = Deanonimizacja(con, "cxlabs")

    assert deanon.wartosc({"udzial": 0.9905, "probka_pelna": True, "brak": None}) == {
        "udzial": 0.9905,
        "probka_pelna": True,
        "brak": None,
    }


# ── granica: żaden surowy hash nie zostaje ───────────────────────────────


def test_nieznany_hash_jest_oznaczony_i_policzony(
    con: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Cicha podmiana ukryłaby rozjechane mapowanie. Ostrzeżenie z licznikiem."""
    deanon = Deanonimizacja(con, "cxlabs")

    wynik = deanon.wartosc({"user_hash": HASH_OBCY})

    assert wynik == {"user_hash": "[nieznane konto deadbeef…]"}
    assert deanon.nieznane == (HASH_OBCY,)

    with caplog.at_level(logging.WARNING):
        deanon.podsumuj()
    assert "deadbeef" in caplog.text
    assert "osoby_mapowanie" in caplog.text


def test_po_deanonimizacji_nie_zostaje_ani_jeden_surowy_hash(con: sqlite3.Connection) -> None:
    """Główny test tego modułu.

    Surowy hash w dokumencie dla klienta to usterka: rekomendacja „zwolnij to
    konto" bez nazwiska jest niewykonalna. Sprawdzamy wzorcem po całej
    strukturze, a nie po pojedynczych polach — łatwo dodać nowy klucz
    i zapomnieć o podmianie.
    """
    deanon = Deanonimizacja(con, "cxlabs")
    dowod = {
        "user_hash": HASH_ANNY,
        "guest_hash": [HASH_JANA, HASH_OBCY],
        "opis_wewnetrzny": f"kontrybutor {HASH_BEZ_NAZWY} i obcy {HASH_OBCY}",
        "kandydaci": [{"top_kontrybutor_hash": HASH_JANA, "board_id": "5097205810"}],
    }

    wynik = str(deanon.wartosc(dowod))

    assert WZORZEC_HASHA.search(wynik) is None, f"surowy hash przeszedł: {wynik}"


def test_prefiks_nieznanego_hasha_jest_krotszy_niz_hash(con: sqlite3.Connection) -> None:
    """Osiem znaków starcza do odnalezienia wpisu, a nie jest hashem.

    Dzięki temu test powyżej przechodzi także dla kont nieznanych — inaczej
    oznaczenie nierozwiązanego hasha samo łamałoby granicę.
    """
    oznaczenie = Deanonimizacja(con, "cxlabs").nazwa(HASH_OBCY)

    assert WZORZEC_HASHA.search(oznaczenie) is None
    assert HASH_OBCY[:8] in oznaczenie
