"""Szacowany koszt runu z historii analiz, nie z długości tekstu (faza 5b-3).

Pierwsza wersja estymatora liczyła tokeny z długości zadania i zaniżyła koszt
dwunastokrotnie (0,05 USD wobec 0,63). Po tamtym runie jej szacunek wyszedł
0,06 USD — czyli się nie nauczyła, choć miała. Ten plik pilnuje dwóch rzeczy,
których tamtej wersji zabrakło:

- **że run faktycznie zasila następny szacunek** — to jest test na samo sedno,
- **że szacunek myli się w górę, a nie w dół** — bo klient, który zapłacił
  dziesięć razy więcej, niż usłyszał, ma pretensje, a ten, który zapłacił
  mniej, nie ma.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.koszt import (
    HIPOTEZ_W_POMIARZE,
    KOSZT_POMIARU_USD,
    POMIAR_STARTOWY,
    HistoriaAnaliz,
    Szacunek,
    historia_analiz,
    oszacuj,
    porownaj,
    zapisz_zuzycie_analizy,
)


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """PRAWDZIWA baza z migracjami i włączonymi kluczami obcymi.

    Pierwsza wersja tego pliku stawiała schemat `zuzycie_hipotez` w pamięci,
    przepisany ręcznie — BEZ `REFERENCES runy (run_id)`. Testy były zielone,
    a pierwszy prawdziwy run padł na `FOREIGN KEY constraint failed` po
    opłaconej sesji modelu. Schemat przepisany do testu to drugie źródło prawdy,
    które rozjechało się z pierwszym przy pierwszej okazji.
    """
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


def _run(con: sqlite3.Connection, run_id: str) -> None:
    """Wiersz w `runy` — bez niego klucz obcy odrzuci zapis zużycia."""
    con.execute(
        "INSERT OR IGNORE INTO runy (run_id, client_id, status, started_at) "
        "VALUES (?, 'test', 'w_toku', '2026-09-23T00:00:00Z')",
        (run_id,),
    )
    con.commit()


def _analiza(con: sqlite3.Connection, run_id: str, *, koszt: float | None, hipotez: int) -> None:
    _run(con, run_id)
    zapisz_zuzycie_analizy(con, run_id, {"koszt_usd": koszt}, ile_hipotez=hipotez)


# ── sedno: run zasila następny szacunek ──────────────────────────────────


def test_run_analizy_zmienia_nastepny_szacunek(con: Any) -> None:
    """DOKŁADNIE to, czego pierwsza wersja nie robiła. Po runie za 0,63 USD
    jej szacunek przesunął się z 0,05 na 0,06 — czyli prawie wcale."""
    przed = oszacuj(16, historia_analiz(con))

    _analiza(con, "a1", koszt=3.20, hipotez=16)
    po = oszacuj(16, historia_analiz(con))

    assert po.koszt_usd == pytest.approx(3.20)
    assert po.koszt_usd != przed.koszt_usd


def test_pomiar_startowy_to_ta_architektura_a_nie_stara(con: Any) -> None:
    """Pusta baza (np. serwer przed pierwszą analizą) dostaje pomiar Z TEJ
    architektury. Poprzednia stawka awaryjna pochodziła z sesji per hipoteza
    i zaniżała dziesięciokrotnie."""
    szacunek = oszacuj(HIPOTEZ_W_POMIARZE, historia_analiz(con))

    assert szacunek.koszt_usd == pytest.approx(KOSZT_POMIARU_USD, abs=0.001)
    assert not szacunek.z_pomiaru_w_bazie
    assert "BRAK ANALIZ W BAZIE" in szacunek.opis()


def test_wiersze_starej_sciezki_nie_psuja_stawki(con: Any) -> None:
    """Sesja per hipoteza ma inną strukturę kosztu — każda płaci za własny
    prompt i kontekst. Wliczenie jej zepsułoby szacunek tak samo, jak zepsuła
    go stawka mieszana w pierwszej wersji."""
    _run(con, "stary")
    con.execute(
        "INSERT INTO zuzycie_hipotez (run_id, klasa_id, tokens_in, koszt_usd, zapisano) "
        "VALUES ('stary', 'BOARD_GHOST', 1000, 50.0, 'teraz')"
    )
    con.commit()

    assert historia_analiz(con) == POMIAR_STARTOWY


def test_stawka_liczy_sie_z_sumy_a_nie_ze_sredniej_runow(con: Any) -> None:
    """Średnia po runach ważyłaby tak samo run na 3 hipotezy i na 60."""
    _analiza(con, "maly", koszt=0.30, hipotez=3)
    _analiza(con, "duzy", koszt=6.00, hipotez=60)

    historia = historia_analiz(con)

    assert historia.usd_na_hipoteze == pytest.approx(6.30 / 63)
    assert historia.runow == 2


def test_runy_z_subskrypcji_nie_zanizaja_stawki(con: Any) -> None:
    """`koszt_usd` zerowy albo pusty to run z subskrypcji (D17) — wycena
    teoretyczna, nie faktura. Szacunek zaniżony jest gorszy od żadnego."""
    _analiza(con, "platny", koszt=1.60, hipotez=16)
    _analiza(con, "subskrypcja", koszt=0.0, hipotez=16)
    _analiza(con, "bez_kwoty", koszt=None, hipotez=16)

    historia = historia_analiz(con)

    assert historia.runow == 1
    assert historia.usd_na_hipoteze == pytest.approx(0.10)


# ── szacunek myli się w górę ─────────────────────────────────────────────


def test_mniejszy_run_nie_jest_szacowany_proporcjonalnie_taniej() -> None:
    """Sesja ma koszt stały (prompt, obraz konta, narzut SDK). Run na 4 hipotezy
    nie kosztuje ćwierci runu na 16 — dlatego poniżej liczby odniesienia nie
    skalujemy w dół. Szacunek wychodzi zawyżony i MÓWI, że jest zawyżony."""
    historia = HistoriaAnaliz(usd_na_hipoteze=0.04, hipotez_odniesienia=16, runow=1)

    szacunek = oszacuj(4, historia)

    assert szacunek.koszt_usd == pytest.approx(0.04 * 16)
    assert "liczone jak dla 16" in szacunek.opis()


def test_wiekszy_run_skaluje_sie_liniowo() -> None:
    """Powyżej odniesienia liniowo — przy dodatnim koszcie stałym to górne
    oszacowanie, czyli błąd w bezpieczną stronę."""
    historia = HistoriaAnaliz(usd_na_hipoteze=0.04, hipotez_odniesienia=16, runow=1)

    assert oszacuj(40, historia).koszt_usd == pytest.approx(1.60)


def test_szacunek_jest_gornym_ograniczeniem_przy_koszcie_stalym() -> None:
    """Własność, dla której wzór jest taki, a nie inny. Jeżeli prawdziwy koszt
    to `stały + zmienny × n`, szacunek z jednego runu nie zaniży ŻADNEGO
    innego runu — ani mniejszego, ani większego."""
    staly, zmienny, n_pomiaru = 0.30, 0.02, 16
    koszt_pomiaru = staly + zmienny * n_pomiaru
    historia = HistoriaAnaliz(koszt_pomiaru / n_pomiaru, n_pomiaru, 1)

    for n in (1, 4, 16, 17, 50, 200):
        prawdziwy = staly + zmienny * n
        assert oszacuj(n, historia).koszt_usd >= prawdziwy - 1e-9, n


def test_tani_run_w_bazie_nie_obniza_szacunku_ponizej_pomiaru(con: Any) -> None:
    """ZMIERZONE 2026-09-23: te same 16 hipotez kosztowały 0,63 USD z obrazem
    konta i narzędziami, a 0,30 bez nich. Po tańszym runie sama historia dałaby
    ~0,30 — i run z `--wejscie` przekroczyłby szacunek dwukrotnie."""
    _analiza(con, "analiza-20260923T103802Z", koszt=0.3035235, hipotez=16)

    szacunek = oszacuj(16, historia_analiz(con))

    assert szacunek.koszt_usd == pytest.approx(KOSZT_POMIARU_USD, abs=0.001)
    assert szacunek.z_pomiaru_w_bazie
    # Szacunek wyższy od tego, co mówi baza, MÓWI dlaczego.
    assert "nie schodzi poniżej pomiaru startowego" in szacunek.opis()


def test_podloga_dziala_tez_dla_malego_runu() -> None:
    """Historia z runów na 3 hipotezy dawałaby odniesienie 3 — a koszt stały
    sesji nie maleje z liczbą hipotez."""
    historia = HistoriaAnaliz(usd_na_hipoteze=0.05, hipotez_odniesienia=3, runow=4)

    assert oszacuj(3, historia).koszt_usd == pytest.approx(KOSZT_POMIARU_USD, abs=0.001)


def test_drozsza_historia_wygrywa_z_podloga(con: Any) -> None:
    """Podłoga działa tylko w dół. Run droższy od pomiaru podnosi szacunek."""
    _analiza(con, "drogi", koszt=3.20, hipotez=16)

    szacunek = oszacuj(16, historia_analiz(con))

    assert szacunek.koszt_usd == pytest.approx(3.20)
    assert "nie schodzi" not in szacunek.opis()


def test_zero_hipotez_to_zero_kosztu() -> None:
    """Wszystko poszło szablonami — model nie jest wołany. Przy koncie, na
    którym wychodzą wyłącznie martwe konta, tak właśnie będzie."""
    szacunek = oszacuj(0)

    assert szacunek.koszt_usd == 0.0
    assert "szablon" in szacunek.opis()


# ── konfrontacja z rachunkiem ────────────────────────────────────────────


def test_porownanie_nazywa_kierunek_i_skale_bledu() -> None:
    """Szacunek, którego nikt nie konfrontuje z rachunkiem, po kilku runach
    staje się ozdobą."""
    opis = porownaj(Szacunek(16, 0.50, "historia", True), {"koszt_usd": 0.75})

    assert "drożej" in opis
    assert "0.25" in opis
    # Względem SZACUNKU: 0,25 z 0,50. Pierwsza wersja dzieliła przez rachunek
    # i dawała tu 33%.
    assert "50% względem szacunku" in opis


def test_procent_bledu_nie_przekracza_stu_przy_przeszacowaniu() -> None:
    """ZMIERZONE na `analiza-20260923T103802Z`: szacunek 0,63, rachunek 0,30,
    a opis mówił „taniej o 108%" — czegoś, co nie może się zdarzyć."""
    opis = porownaj(Szacunek(16, 0.63, "pomiar", False), {"koszt_usd": 0.30})

    assert "taniej" in opis
    assert "52% względem szacunku" in opis


def test_zerowy_szacunek_nie_dzieli_przez_zero() -> None:
    opis = porownaj(Szacunek(0, 0.0, "szablony", True), {"koszt_usd": 0.05})

    assert "szacowano 0 USD" in opis
    assert "%" not in opis


def test_brak_kosztu_nie_udaje_zera() -> None:
    """Run z subskrypcji nie ma faktury. „0 USD" byłoby kłamstwem."""
    opis = porownaj(Szacunek(16, 0.50, "historia", True), {"koszt_usd": 0.0})

    assert "NIEZNANY" in opis


# ── zapis ────────────────────────────────────────────────────────────────


def test_zapis_bez_wiersza_w_runy_pada_na_kluczu_obcym(con: Any) -> None:
    """Dokładnie ta awaria zabrała wynik pierwszego prawdziwego runu. Test
    istnieje po to, żeby ograniczenie było ZNANE, a nie odkrywane na płatnym
    przebiegu — `cli_analiza` musi założyć wiersz w `runy` przed sesją."""
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        zapisz_zuzycie_analizy(con, "bez-runu", {"koszt_usd": 0.1}, ile_hipotez=1)


def test_liczba_hipotez_jest_obowiazkowa() -> None:
    """Bez niej wiersz nie zasila historii i estymator nie uczy się z runu —
    czyli usterka, którą ta wersja naprawia. Wartość domyślna pozwoliłaby
    o tym zapomnieć bez żadnego sygnału."""
    import inspect

    parametr = inspect.signature(zapisz_zuzycie_analizy).parameters["ile_hipotez"]

    assert parametr.default is inspect.Parameter.empty
