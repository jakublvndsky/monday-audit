"""Ewaluacja etapu 4: rozbicie kosztu i raport HTML.

Testy pilnują trzech rzeczy, z których każda ma konkretną historię:

1. **Dwie metryki odrzuceń nie mogą się mieszać.** Pomyliłem je w pierwszej wersji
   modułu: pokazywałem „hipotezy bez znaleziska" (0,686) pod nazwą „odsetek
   odrzuconych na walidacji", którego próg etapu 4 wynosi 0,15. Wyszło ze ZRZUTU
   raportu — liczba świeciła na czerwono i nie zgadzała się z niczym w bazie.
2. **Brak danych mówi „brak", nie zero.** Zero w tabeli wygląda jak wynik pomiaru.
3. **Suma rozbicia = koszt runu.** Bez tego rozbicie jest fikcją, a decyzja
   o tańszym modelu stanie na złej liczbie.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.ewaluacja import PROGI, udzial_w_rachunku, wyrenderuj, zbierz_zuzycie
from monday_audit.przebieg import zapisz_zuzycie
from monday_audit.rubryka import wczytaj_rubryke

RUBRYKA = wczytaj_rubryke()
RUN = "r-ewal"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "e.db")
    zastosuj_migracje(polaczenie)
    polaczenie.execute(
        "INSERT INTO snapshots (id, client_id, run_at, collector_ver, payload) "
        "VALUES (1, 'cxlabs', '2026-08-11T00:00:00+00:00', '0.1.0', '{}')"
    )
    polaczenie.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
        "hipotez_zbadanych, hipotez_odrzuconych, findingow, odrzuconych_walidacja) "
        "VALUES (?, 'cxlabs', 1, 'zakonczony', '2026-08-11T00:00:00+00:00', "
        "'claude-sonnet-5', 10, 4, 5, 1)",
        (RUN,),
    )
    polaczenie.commit()
    yield polaczenie
    polaczenie.close()


def _zapisz(con: sqlite3.Connection) -> None:
    zapisz_zuzycie(
        con,
        RUN,
        {
            "tokens_in": 2_000,
            "tokens_out": 500,
            "tokens_cache_read": 80_000,
            "tokens_cache_write": 5_000,
            "koszt_usd": 3.0,
        },
        [
            {
                "klasa_id": "BOARD_GHOST",
                "obiekt_id": "1",
                "tokens_in": 1_500,
                "tokens_out": 400,
                "tokens_cache_read": 60_000,
                "tokens_cache_write": 5_000,
                "koszt_usd": 2.4,
                "sekund": 90.0,
                "wywolan_narzedzi": 4,
                "byl_finding": True,
            },
            {
                "klasa_id": "ZOMBIE_ACCOUNT",
                "obiekt_id": "h1",
                "tokens_in": 500,
                "tokens_out": 100,
                "tokens_cache_read": 20_000,
                "tokens_cache_write": 0,
                "koszt_usd": 0.6,
                "sekund": 30.0,
                "wywolan_narzedzi": 0,
                "byl_finding": False,
            },
        ],
    )


def test_dwie_metryki_odrzucen_sie_nie_mieszaja(con: sqlite3.Connection) -> None:
    """USTERKA Z PIERWSZEJ WERSJI: jedna liczba pokazywana pod nazwą drugiej.

    Run w fixture: 10 hipotez, 4 obalone przez agenta, 5 findingów przyjętych,
    1 odrzucony na walidacji. To DAJE DWIE RÓŻNE liczby i tylko druga ma próg.
    """
    z = zbierz_zuzycie(con, RUN, RUBRYKA)

    # Agent obalił 4 z 10 — nie jest to wada, detektory wzbudzają szeroko.
    assert z.odsetek_obalonych == pytest.approx(0.4)
    # Walidacja odrzuciła 1 z 6 ZGŁOSZONYCH (5 przyjętych + 1 odrzucony).
    assert z.odsetek_walidacji == pytest.approx(round(1 / 6, 3))
    # Gdyby ktoś znowu je pomylił, ta asercja pada.
    assert z.odsetek_obalonych != z.odsetek_walidacji


def test_prog_walidacji_jest_ze_specyfikacji() -> None:
    """Próg w kodzie, nie w szablonie — inaczej nie da się go sprawdzić testem."""
    assert PROGI["odrzucenia"] == 0.15
    assert PROGI["falszywki"] == 0.1, "fałszywki są ważniejsze od trafności (04-test.md)"


def test_udzial_w_rachunku_sumuje_sie_do_stu(con: sqlite3.Connection) -> None:
    """Bez tego rozbicie gubi koszt, a router modelu stanie na złej liczbie."""
    _zapisz(con)

    udzialy = udzial_w_rachunku(zbierz_zuzycie(con, RUN, RUBRYKA))

    assert sum(udzialy.values()) == pytest.approx(100.0, abs=0.2)
    # BOARD_GHOST to 2,4 z 3,0 USD — 80% rachunku, więc tam warto eksperymentować.
    assert udzialy["BOARD_GHOST"] == pytest.approx(80.0)


def test_suma_per_hipoteza_zgadza_sie_z_kosztem_runu(con: sqlite3.Connection) -> None:
    """Rozbicie musi się zgadzać z sumą do groszy, inaczej jest fikcją."""
    _zapisz(con)
    z = zbierz_zuzycie(con, RUN, RUBRYKA)

    assert sum(k.koszt_usd for k in z.klasy) == pytest.approx(z.koszt_usd)


def test_rola_agenta_brak_jest_oznaczona(con: sqlite3.Connection) -> None:
    """Klasy, gdzie detektor już orzekł, to pierwsi kandydaci na tańszy model.

    `ZOMBIE_ACCOUNT` ma w rubryce `rola_agenta: brak` i budżet 0 — agent nie ma tam
    nic do ustalenia. Raport musi to pokazać, bo to jest wskazówka, gdzie ciąć.
    """
    _zapisz(con)
    z = zbierz_zuzycie(con, RUN, RUBRYKA)

    po_id = {k.klasa_id: k for k in z.klasy}
    assert po_id["ZOMBIE_ACCOUNT"].rola_agenta_brak is True
    assert po_id["BOARD_GHOST"].rola_agenta_brak is False


def test_run_bez_rozbicia_mowi_brak_a_nie_zero(con: sqlite3.Connection) -> None:
    """Runy sprzed migracji 010 nie mają rozbicia — i NIE dzielimy sumy po równo.

    Twój run z 2026-08-11 jest właśnie taki: 7,09 USD i 86 hipotez, ale zero
    wierszy per hipoteza. Podzielenie 7,09/86 dałoby liczbę wyglądającą na pomiar.
    """
    z = zbierz_zuzycie(con, RUN, RUBRYKA)  # bez `_zapisz`

    assert z.ma_rozbicie is False
    assert z.klasy == ()
    html = wyrenderuj(z)
    assert "Brak rozbicia per hipoteza" in html
    assert "nie dzielimy po równo" in html


def test_raport_mowi_brak_miary_bez_zlotego_zestawu(con: sqlite3.Connection) -> None:
    """Zero w tabeli jakości wyglądałoby jak wynik pomiaru.

    Trafność i fałszywki liczą się wobec ręcznego złotego zestawu, nie wobec
    wyniku agenta — ocena własnego wyniku to racjonalizacja (04-test.md).
    """
    _zapisz(con)

    html = wyrenderuj(zbierz_zuzycie(con, RUN, RUBRYKA))

    assert "Brak miary jakości" in html
    assert "racjonalizacja, nie ewaluacja" in html
    assert html.count("brak miary") >= 3, "trafność, fałszywki i powtarzalność"


def test_porownanie_oznacza_utrate_znalezisk(con: sqlite3.Connection) -> None:
    """Taniej przy MNIEJSZYM udziale znalezisk to najgroźniejszy wynik.

    Wygląda na oszczędność, a jest utratą produktu — raport musi to nazwać.

    Od 2026-08-12 porównujemy UDZIAŁ (findingi/hipotezy), nie liczbę bezwzględną.
    Zobaczone na zrzucie: porównanie 8 hipotez z 86 pokazywało „−20 znalezisk" jako
    regresję, choć to po prostu inny zakres. Suma nie skaluje się z liczbą hipotez.
    """
    _zapisz(con)
    z = zbierz_zuzycie(con, RUN, RUBRYKA)

    con.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, "
        "hipotez_zbadanych, hipotez_odrzuconych, findingow, odrzuconych_walidacja, "
        "koszt_usd, sekund_agenta) VALUES ('r-base', 'cxlabs', 1, 'zakonczony', "
        "'2026-08-10T00:00:00+00:00', 10, 2, 9, 1, 7.0, 3600)"
    )
    con.commit()
    baseline = zbierz_zuzycie(con, "r-base", RUBRYKA)

    html = wyrenderuj(z, poprzedni=baseline)

    # Oba runy mają 10 hipotez, więc zakres jest ten sam: 5/10 wobec 9/10 to
    # realna regresja udziału, nie różnica zakresu.
    assert "sprawdź trafność" in html
    assert "nie rozstrzyga niczego" in html
    # Przy TYM SAMYM zakresie nie ostrzegamy o nieporównywalności sum.
    assert "Runy mają różną liczbę hipotez" not in html


def test_porownanie_ostrzega_przy_roznym_zakresie(con: sqlite3.Connection) -> None:
    """Sumy dwóch runów o różnej liczbie hipotez NIE SĄ porównywalne.

    ZOBACZONE NA ZRZUCIE, nie w liczbach: raport pokazywał „−6,27 USD" i „−20
    znalezisk" przy porównaniu 8 hipotez z 86, jakby to był wynik eksperymentu.
    To był inny zakres — a taka tabela sugeruje sukces tam, gdzie go nie ma.
    """
    _zapisz(con)
    z = zbierz_zuzycie(con, RUN, RUBRYKA)  # 10 hipotez w fixture

    con.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, "
        "hipotez_zbadanych, hipotez_odrzuconych, findingow, odrzuconych_walidacja, "
        "koszt_usd, sekund_agenta) VALUES ('r-duzy', 'cxlabs', 1, 'zakonczony', "
        "'2026-08-10T00:00:00+00:00', 86, 30, 27, 9, 7.09, 3720)"
    )
    con.commit()

    html = wyrenderuj(z, poprzedni=zbierz_zuzycie(con, "r-duzy", RUBRYKA))

    assert "Runy mają różną liczbę hipotez" in html
    assert "nieporównywalna przy różnym zakresie" in html
    # Miara, która się skaluje, MUSI być w tabeli.
    assert "koszt / hipotezę" in html


def test_raport_nie_wypuszcza_pii(con: sqlite3.Connection) -> None:
    """Dokument wewnętrzny, ale obowiązuje go ta sama granica co raport (D14).

    Łatwo o tym zapomnieć właśnie dlatego, że jest „tylko dla nas".
    """
    _zapisz(con)
    con.execute(
        "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
        "VALUES ('cxlabs', 'h1', 'Anna Górniak', 'anna@klient.test')"
    )
    con.commit()

    html = wyrenderuj(zbierz_zuzycie(con, RUN, RUBRYKA))

    assert "Anna Górniak" not in html
    assert "anna@klient.test" not in html
    # Wzorzec ADRESU, nie samo `@` — arkusz marki ma komentarz o `@import`, więc
    # szukanie znaku dawało fałszywy alarm (pierwsza wersja tego testu).
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", html) is None, "adres e-mail w treści"
