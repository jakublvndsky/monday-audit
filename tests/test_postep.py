"""Testy wskaźnika postępu (uzupełnienie 3.2), warstwa 1 z 04-test.md."""

from __future__ import annotations

import io

from monday_audit.klient import Postep
from monday_audit.postep import LicznikKonsolowy


def krok(**nadpisz: object) -> Postep:
    dane: dict[str, object] = {
        "narzedzie": "graphql:boards",
        "wywolania": 1,
        "budzet": 400,
        "complexity_suma": 1000,
    }
    dane.update(nadpisz)
    return Postep(**dane)  # type: ignore[arg-type]


def test_w_terminalu_nadpisuje_jedna_linie() -> None:
    strumien = io.StringIO()
    licznik = LicznikKonsolowy(strumien, w_miejscu=True)

    licznik(krok(wywolania=1))
    licznik(krok(wywolania=2))

    wynik = strumien.getvalue()
    assert wynik.count("\r") == 2
    assert "\n" not in wynik, "w terminalu postęp nie przewija ekranu"


def test_poza_terminalem_wypisuje_linie_co_ile() -> None:
    """Log ze 130 wywołań nie może mieć 130 linii postępu."""
    strumien = io.StringIO()
    licznik = LicznikKonsolowy(strumien, w_miejscu=False, co_ile=3)

    for numer in range(1, 7):
        licznik(krok(wywolania=numer))

    linie = strumien.getvalue().splitlines()
    assert len(linie) == 2
    assert "3/400" in linie[0]
    assert "6/400" in linie[1]


def test_pauza_zawsze_trafia_do_logu() -> None:
    """Bez tego wpisu run wygląda, jakby stanął bez powodu."""
    strumien = io.StringIO()
    licznik = LicznikKonsolowy(strumien, w_miejscu=False, co_ile=100)

    licznik(krok(wywolania=1, czekanie_s=43.0))

    assert "PAUZA 43 s" in strumien.getvalue()


def test_krotsza_linia_wyciera_ogon_dluzszej() -> None:
    strumien = io.StringIO()
    licznik = LicznikKonsolowy(strumien, w_miejscu=True)

    licznik(krok(wywolania=1, strona=100, zebrane=2500))
    dluga = len(strumien.getvalue())
    licznik(krok(wywolania=2))

    ostatnia = strumien.getvalue()[dluga:]
    assert ostatnia.endswith(" "), "krótsza linia musi dopełnić do poprzedniej szerokości"


def test_zakoncz_czysci_linie_i_wypisuje_podsumowanie() -> None:
    strumien = io.StringIO()
    licznik = LicznikKonsolowy(strumien, w_miejscu=True)

    licznik(krok())
    licznik.zakoncz("zebrano 3171 tablic")

    assert strumien.getvalue().endswith("zebrano 3171 tablic\n")


def test_domyslnie_wykrywa_terminal() -> None:
    """StringIO nie jest terminalem, więc tryb w miejscu ma być wyłączony."""
    licznik = LicznikKonsolowy(io.StringIO(), co_ile=1)
    licznik(krok())
    assert not licznik._w_miejscu
