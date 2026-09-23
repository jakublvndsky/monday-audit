"""Szacowany koszt runu, liczony z pomiaru a nie z cennika (faza 5b-3).

Najważniejsze w tym pliku nie jest to, czy szacunek trafia — bo nie trafi,
dopóki historia pochodzi z innej architektury. Najważniejsze jest, żeby
**każda liczba mówiła, na czym stoi**: czy to pomiar, ile go było, i o ile
szacunek się pomylił. Szacunek bez tej informacji wygląda tak samo jak pomiar.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from monday_audit.koszt import (
    STAWKA_AWARYJNA_USD_ZA_TOKEN,
    ZNAKOW_NA_TOKEN,
    StawkaTokenow,
    Szacunek,
    oszacuj,
    porownaj,
    stawka_z_historii,
    zapisz_zuzycie_analizy,
)

SCHEMAT = """
CREATE TABLE zuzycie_hipotez (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    klasa_id TEXT NOT NULL,
    obiekt_id TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_cache_read INTEGER NOT NULL DEFAULT 0,
    tokens_cache_write INTEGER NOT NULL DEFAULT 0,
    koszt_usd REAL,
    sekund REAL,
    wywolan_narzedzi INTEGER NOT NULL DEFAULT 0,
    byl_finding INTEGER NOT NULL DEFAULT 0,
    zapisano TEXT NOT NULL
);
"""


@pytest.fixture
def con() -> Any:
    polaczenie = sqlite3.connect(":memory:")
    polaczenie.row_factory = sqlite3.Row
    polaczenie.executescript(SCHEMAT)
    yield polaczenie
    polaczenie.close()


def _wiersz(con: Any, *, koszt: float | None, tokenow: int) -> None:
    con.execute(
        "INSERT INTO zuzycie_hipotez (run_id, klasa_id, tokens_in, koszt_usd, zapisano) "
        "VALUES ('r', 'X', ?, ?, 'teraz')",
        (tokenow, koszt),
    )
    con.commit()


# ── stawka z historii ────────────────────────────────────────────────────


def test_pusta_historia_daje_stawke_awaryjna(con: Any) -> None:
    """Pierwszy run w świeżej bazie ma dostać JAKĄŚ liczbę, ale oznaczoną."""
    stawka = stawka_z_historii(con)

    assert not stawka.z_pomiaru
    assert stawka.usd_za_token == STAWKA_AWARYJNA_USD_ZA_TOKEN


def test_stawka_liczy_sie_z_sumy_a_nie_ze_sredniej_wierszy(con: Any) -> None:
    """Średnia po wierszach ważyłaby tak samo run za 0,01 USD i za 1 USD."""
    _wiersz(con, koszt=1.0, tokenow=1000)
    _wiersz(con, koszt=9.0, tokenow=9000)

    stawka = stawka_z_historii(con)

    assert stawka.z_pomiaru
    assert stawka.usd_za_token == pytest.approx(10.0 / 10_000)


def test_runy_z_subskrypcji_nie_zanizaja_stawki(con: Any) -> None:
    """`koszt_usd` zerowy albo pusty znaczy „run poszedł z subskrypcji" (D17),
    czyli kwota jest teoretyczna, nie fakturą. Wliczenie go zaniżyłoby stawkę,
    a szacunek optymistyczny jest gorszy od żadnego."""
    _wiersz(con, koszt=1.0, tokenow=1000)
    _wiersz(con, koszt=0.0, tokenow=50_000)
    _wiersz(con, koszt=None, tokenow=50_000)

    stawka = stawka_z_historii(con)

    assert stawka.usd_za_token == pytest.approx(1.0 / 1000)
    assert stawka.wierszy == 1


# ── szacunek ─────────────────────────────────────────────────────────────


def test_szacunek_rosnie_z_dlugoscia_zadania() -> None:
    stawka = StawkaTokenow(usd_za_token=0.001, tokenow=1000, wierszy=1)

    maly = oszacuj("x" * 100, ile_hipotez=1, stawka=stawka)
    duzy = oszacuj("x" * 10_000, ile_hipotez=1, stawka=stawka)

    assert duzy.koszt_usd > maly.koszt_usd
    assert maly.tokenow_wejscia == int(100 / ZNAKOW_NA_TOKEN)


def test_prompt_liczy_sie_pelna_stawka_czyli_przeszacowujemy() -> None:
    """Prompt jest prefiksem cache'u, więc przy drugim runie kosztuje ułamek.
    NIE modelujemy tego rabatu — błąd w stronę wyższej kwoty jest bezpieczny,
    bo klient, który zapłacił mniej niż usłyszał, nie ma pretensji."""
    stawka = StawkaTokenow(usd_za_token=0.001, tokenow=1000, wierszy=1)

    bez = oszacuj("zadanie", ile_hipotez=1, stawka=stawka)
    z_promptem = oszacuj("zadanie", ile_hipotez=1, prompt="p" * 3300, stawka=stawka)

    assert z_promptem.tokenow_wejscia - bez.tokenow_wejscia == pytest.approx(1000, abs=2)


def test_wyjscie_skaluje_sie_liczba_hipotez() -> None:
    stawka = StawkaTokenow(usd_za_token=0.001, tokenow=1000, wierszy=1)

    jedna = oszacuj("z", ile_hipotez=1, stawka=stawka)
    dziesiec = oszacuj("z", ile_hipotez=10, stawka=stawka)

    assert dziesiec.tokenow_wyjscia == 10 * jedna.tokenow_wyjscia


def test_opis_mowi_czy_stawka_jest_z_pomiaru() -> None:
    """Szacunek bez tej informacji wygląda dokładnie tak samo jak pomiar."""
    bez_pomiaru = oszacuj("z", ile_hipotez=1, stawka=StawkaTokenow(0.001, 0, 0))
    z_pomiarem = oszacuj("z", ile_hipotez=1, stawka=StawkaTokenow(0.001, 5000, 12))

    assert "BEZ POMIARU" in bez_pomiaru.opis()
    assert "12 wierszy historii" in z_pomiarem.opis()


# ── konfrontacja z rachunkiem ────────────────────────────────────────────


def test_porownanie_nazywa_kierunek_i_skale_bledu() -> None:
    """Szacunek, którego nikt nie konfrontuje z rachunkiem, po kilku runach
    staje się ozdobą — a wtedy lepiej go nie pokazywać wcale."""
    szacunek = Szacunek(1000, 500, 0.50, "historia", True)

    opis = porownaj(szacunek, {"koszt_usd": 0.75})

    assert "drożej" in opis
    assert "0.25" in opis
    assert "33%" in opis


def test_brak_kosztu_nie_udaje_zera() -> None:
    """Run z subskrypcji nie ma faktury. „0 USD" byłoby kłamstwem, a nie
    dobrą wiadomością."""
    szacunek = Szacunek(1000, 500, 0.50, "historia", True)

    opis = porownaj(szacunek, {"koszt_usd": 0.0})

    assert "NIEZNANY" in opis


# ── zapis zużycia sesji ──────────────────────────────────────────────────


def test_sesja_zapisuje_sie_jako_jeden_wiersz(con: Any) -> None:
    """Bez migracji: tabela przyjmuje `klasa_id` jako tekst, więc sesja
    zbiorcza siada pod ANALIZA_KONTA i zasila następny szacunek."""
    zapisz_zuzycie_analizy(
        con,
        "analiza-1",
        {"tokens_in": 100, "tokens_out": 200, "koszt_usd": 0.4},
        ile_uwag=3,
        wywolan_narzedzi=2,
        sekund=12.5,
    )

    wiersz = con.execute("SELECT * FROM zuzycie_hipotez").fetchone()

    assert wiersz["klasa_id"] == "ANALIZA_KONTA"
    assert wiersz["koszt_usd"] == 0.4
    assert wiersz["byl_finding"] == 1


def test_zapisana_sesja_zasila_nastepny_szacunek(con: Any) -> None:
    """Sedno projektu: estymator uczy się z własnej historii. Pierwszy szacunek
    dla nowej architektury będzie zły, drugi policzy się z pierwszego."""
    zapisz_zuzycie_analizy(con, "a", {"tokens_in": 1000, "tokens_out": 1000, "koszt_usd": 2.0})

    stawka = stawka_z_historii(con)

    assert stawka.z_pomiaru
    assert stawka.usd_za_token == pytest.approx(2.0 / 2000)
