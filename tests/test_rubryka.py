"""Rubryka: pola, które renderer odsłonił, i reguła kolejności raportu.

Dwa pola były w `rubryka_znalezisk.yaml` i **nie były wczytywane**, każde
z cichym skutkiem:

- `trop_sprzedazowy` → `findings.trop` był NULL we wszystkich 17 wierszach
  bazy produkcyjnej, czyli wersja wewnętrzna raportu nie miała czym się
  różnić od klientowej
- `reguly.kolejnosc_raportu` → nic nie implementowało sortowania, które
  w rubryce zastępuje health score

Testy tutaj pilnują, żeby nie wróciły. To ta sama usterka co przy `wzor`:
pole istnieje, kod go nie czyta, nikt nie zauważa.
"""

from __future__ import annotations

import logging

import pytest

from monday_audit.rubryka import Rubryka, wczytaj_rubryke

RUBRYKA = wczytaj_rubryke()


# ── trop_sprzedazowy ─────────────────────────────────────────────────────


def test_trop_jest_wczytywany_dla_kazdej_klasy() -> None:
    """Wszystkie 12 klas mają trop w pliku — więc wszystkie muszą go mieć w kodzie."""
    bez_tropu = [k.id for k in RUBRYKA.klasy if not k.trop_sprzedazowy]

    assert bez_tropu == []


def test_trop_ma_tresc_z_pliku_a_nie_pustego_stringa() -> None:
    """Konkretna wartość, nie „cokolwiek niepustego" — łapie zły klucz w YAML-u."""
    assert RUBRYKA.po_id["ZOMBIE_ACCOUNT"].trop_sprzedazowy == (
        "porządkowanie licencji — szybka wygrana budująca zaufanie"
    )


def test_trop_wieloliniowy_jest_zwiniety() -> None:
    """`ENGAGEMENT_DROP` ma trop w bloku `|`, czyli z końcowym znakiem nowej linii."""
    trop = RUBRYKA.po_id["ENGAGEMENT_DROP"].trop_sprzedazowy

    assert trop is not None
    assert not trop.endswith("\n")
    assert "warsztat adopcyjny" in trop


# ── kolejność raportu ────────────────────────────────────────────────────


def test_slowniki_zachowuja_kolejnosc_z_pliku() -> None:
    """Pozycja w liście JEST informacją — zbiór by ją zgubił."""
    assert RUBRYKA.kolejnosc_wag == ("krytyczna", "wysoka", "srednia", "niska")
    assert RUBRYKA.kolejnosc_wysilkow == ("niski", "sredni", "wysoki")


def test_waga_bije_wysilek() -> None:
    """Waga jest kryterium pierwszym: `krytyczna`/`wysoki` przed `wysoka`/`niski`."""
    assert RUBRYKA.klucz_kolejnosci("krytyczna", "wysoki") < RUBRYKA.klucz_kolejnosci(
        "wysoka", "niski"
    )


def test_przy_tej_samej_wadze_wygrywa_tanszy() -> None:
    """Sens reguły z rubryki: quick wins na pierwszy slajd."""
    assert RUBRYKA.klucz_kolejnosci("krytyczna", "niski") < RUBRYKA.klucz_kolejnosci(
        "krytyczna", "wysoki"
    )


def test_kolejnosc_raportu_sortuje_findingi() -> None:
    """Pełna reguła na liście udającej wiersze z tabeli `findings`."""
    findingi = [
        {"id": "srednia-niski", "waga": "srednia", "wysilek": "niski"},
        {"id": "krytyczna-wysoki", "waga": "krytyczna", "wysilek": "wysoki"},
        {"id": "krytyczna-niski", "waga": "krytyczna", "wysilek": "niski"},
        {"id": "wysoka-sredni", "waga": "wysoka", "wysilek": "sredni"},
    ]

    kolejnosc = [f["id"] for f in RUBRYKA.kolejnosc_raportu(findingi)]

    assert kolejnosc == ["krytyczna-niski", "krytyczna-wysoki", "wysoka-sredni", "srednia-niski"]


def test_sortowanie_jest_stabilne() -> None:
    """Findingi o tej samej wadze i wysiłku zachowują kolejność z bazy.

    Istotne, bo w tabeli `findings` jest ich po kilka na klasę (7 kont zombie)
    i raport ma je pokazywać w kolejności zapisu, a nie losowej.
    """
    findingi = [{"id": f"k{n}", "waga": "wysoka", "wysilek": "niski"} for n in range(5)]

    assert [f["id"] for f in RUBRYKA.kolejnosc_raportu(findingi)] == ["k0", "k1", "k2", "k3", "k4"]


def test_waga_spoza_slownika_idzie_na_koniec_a_nie_wywala(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stary run musi dać się wyrenderować po zmianie słownika (D7).

    Findingi noszą `rubric_ver`, więc w bazie mogą siedzieć wartości, których
    dzisiejsza rubryka nie zna. Wyjątek zabrałby możliwość obejrzenia takiego
    runu — a to jedyny sposób porównania go z nowym.
    """
    findingi = [
        {"id": "nieznana", "waga": "apokaliptyczna", "wysilek": "niski"},
        {"id": "niska", "waga": "niska", "wysilek": "wysoki"},
    ]

    with caplog.at_level(logging.WARNING):
        kolejnosc = [f["id"] for f in RUBRYKA.kolejnosc_raportu(findingi)]

    assert kolejnosc == ["niska", "nieznana"]
    assert "apokaliptyczna" in caplog.text


# ── walidacja ────────────────────────────────────────────────────────────


def test_rubryka_bez_klas_nie_powstaje() -> None:
    with pytest.raises(Exception, match="bez klas"):
        Rubryka(
            wersja="0.0",
            klasy=(),
            maks_wywolan_na_run=600,
            kolejnosc_wag=("krytyczna",),
            kolejnosc_wysilkow=("niski",),
        )
