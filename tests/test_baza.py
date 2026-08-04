"""Testy schematu i runnera migracji (etap 3.1).

Kryterium z 03-build.md: migracje aplikują się od zera i są idempotentne.
Pozostałe testy pilnują własności, które schemat ma gwarantować mechanicznie,
a nie na słowo — uzasadnienia przy każdej z nich są w 001_schemat.sql.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import (
    KATALOG_MIGRACJI,
    MigracjaError,
    polacz,
    zastosuj_migracje,
    znajdz_migracje,
)

CZAS = "2026-07-30T10:00:00+00:00"

WSTAW_FINDING = (
    "INSERT INTO findings (run_id, snapshot_id, klasa_id, rubric_ver, waga, wysilek, "
    "typ_wyceny, widocznosc, opis, rekomendacja, dowod, pewnosc) "
    "VALUES ('r1', 1, 'BOARD_GHOST', '0.1', ?, 'niski', 'ryzyko', 'klient', "
    "'opis', 'rekomendacja', ?, 'wysoka')"
)


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Świeża baza z zastosowanymi migracjami."""
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


@pytest.fixture
def con_z_runem(con: sqlite3.Connection) -> sqlite3.Connection:
    """Baza z jednym snapshotem i jednym runem, gotowa na findingi."""
    con.execute(
        "INSERT INTO snapshots (id, client_id, run_at, collector_ver, payload) "
        "VALUES (1, 'cxlabs', ?, '0.1.0', '{\"boards\": []}')",
        (CZAS,),
    )
    con.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
        "rubric_ver, prompt_hash) "
        "VALUES ('r1', 'cxlabs', 1, 'w_toku', ?, 'claude-sonnet-5', '0.1', 'abc123')",
        (CZAS,),
    )
    con.commit()
    return con


# ── migracje ─────────────────────────────────────────────────────────────


def test_migracje_aplikuja_sie_od_zera(tmp_path: Path) -> None:
    """Numery migracji, nie ich liczba — kolejność jest częścią kontraktu.

    002 doszła w 3.11: tabela `findings_odrzucone`, bo `runy.odrzuconych_walidacja`
    to sam licznik, a D8 nazywa odsetek odrzuconych główną metryką etapu 4.
    003 doszła przy cenniku: stawki jako dane z pochodzeniem i datą ważności,
    bo liczby wklejone w markdown nikt nie odświeża.
    004 dodała `runy.cennik_ver` — skoro stawki odświeżają się same, run musi
    zapisać, na których liczył (D7).
    """
    con = polacz(tmp_path / "nowa.db")
    assert zastosuj_migracje(con) == [1, 2, 3, 4]
    con.close()


def test_wszystkie_tabele_powstaly(con: sqlite3.Connection) -> None:
    tabele = {w["name"] for w in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "_migracje",
        "snapshots",
        "runy",
        "findings",
        "cennik",
        "findings_odrzucone",
        "hipotezy_odrzucone",
        "stawki_klienta",
        "osoby_mapowanie",
        "wywolania",
    } <= tabele


def test_indeksy_wymagane_przez_31(con: sqlite3.Connection) -> None:
    indeksy = {
        w["name"] for w in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {"idx_snapshots_client_run", "idx_findings_snapshot", "idx_wywolania_run"} <= indeksy


def test_powtorne_wywolanie_nic_nie_robi(con: sqlite3.Connection) -> None:
    assert zastosuj_migracje(con) == []


def test_idempotentne_na_nowym_polaczeniu(tmp_path: Path) -> None:
    sciezka = tmp_path / "b.db"
    pierwsze = polacz(sciezka)
    zastosuj_migracje(pierwsze)
    pierwsze.close()

    drugie = polacz(sciezka)
    assert zastosuj_migracje(drugie) == []
    drugie.close()


def test_edycja_zastosowanej_migracji_jest_wykrywana(tmp_path: Path) -> None:
    katalog = tmp_path / "migracje"
    shutil.copytree(KATALOG_MIGRACJI, katalog)
    con = polacz(tmp_path / "b.db")
    zastosuj_migracje(con, katalog)

    plik = next(katalog.glob("*.sql"))
    plik.write_text(plik.read_text(encoding="utf-8") + "\n-- dopisek\n", encoding="utf-8")

    with pytest.raises(MigracjaError, match="zmieniła się"):
        zastosuj_migracje(con, katalog)
    con.close()


def test_zduplikowany_numer_migracji(tmp_path: Path) -> None:
    katalog = tmp_path / "migracje"
    katalog.mkdir()
    (katalog / "001_a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (katalog / "001_b.sql").write_text("SELECT 1;\n", encoding="utf-8")

    with pytest.raises(MigracjaError, match="Zduplikowane"):
        znajdz_migracje(katalog)


def test_migracja_bez_numeru_w_nazwie(tmp_path: Path) -> None:
    katalog = tmp_path / "migracje"
    katalog.mkdir()
    (katalog / "schemat.sql").write_text("SELECT 1;\n", encoding="utf-8")

    with pytest.raises(MigracjaError, match="numeru"):
        znajdz_migracje(katalog)


# ── więzy schematu ───────────────────────────────────────────────────────


def test_strict_odrzuca_tekst_w_kolumnie_liczbowej(con_z_runem: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        con_z_runem.execute("UPDATE runy SET tokens_in = 'dużo' WHERE run_id = 'r1'")
        con_z_runem.commit()


def test_check_odrzuca_status_spoza_slownika(con: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO runy (run_id, client_id, status, started_at) VALUES (?, ?, ?, ?)",
            ("r-zly", "cxlabs", "wymyslony_status", CZAS),
        )
        con.commit()


def test_klucze_obce_sa_wymuszone(con: sqlite3.Connection) -> None:
    """PRAGMA foreign_keys jest w SQLite domyślnie WYŁĄCZONA — polacz() ją włącza."""
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO wywolania (run_id, narzedzie, at) VALUES (?, ?, ?)",
            ("run-ktory-nie-istnieje", "graphql", CZAS),
        )
        con.commit()


def test_payload_musi_byc_poprawnym_json(con: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO snapshots (client_id, run_at, collector_ver, payload) VALUES (?, ?, ?, ?)",
            ("cxlabs", CZAS, "0.1.0", "to nie jest json"),
        )
        con.commit()


# ── niemutowalność snapshotu (D7) ────────────────────────────────────────


def test_update_na_snapshots_jest_blokowany(con_z_runem: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="niemutowalny"):
        con_z_runem.execute("UPDATE snapshots SET payload = '{}' WHERE id = 1")
        con_z_runem.commit()


def test_delete_na_snapshots_pozostaje_dozwolony(con: sqlite3.Connection) -> None:
    """Usunięcie danych klienta musi być wykonalne — blokujemy tylko UPDATE."""
    con.execute(
        "INSERT INTO snapshots (id, client_id, run_at, collector_ver, payload) "
        "VALUES (7, 'cxlabs', ?, '0.1.0', '{}')",
        (CZAS,),
    )
    con.commit()
    con.execute("DELETE FROM snapshots WHERE id = 7")
    con.commit()
    assert con.execute("SELECT count(*) FROM snapshots WHERE id = 7").fetchone()[0] == 0


# ── dowod i słowniki rubryki (D8) ────────────────────────────────────────


def test_poprawny_finding_przechodzi(con_z_runem: sqlite3.Connection) -> None:
    con_z_runem.execute(WSTAW_FINDING, ("srednia", '{"board_id": 123}'))
    con_z_runem.commit()
    assert con_z_runem.execute("SELECT count(*) FROM findings").fetchone()[0] == 1


def test_dowod_musi_byc_obiektem_json(con_z_runem: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        con_z_runem.execute(WSTAW_FINDING, ("srednia", '["nie obiekt"]'))
        con_z_runem.commit()


def test_slowniki_rubryki_nie_sa_zabetonowane_w_check(con_z_runem: sqlite3.Connection) -> None:
    """Rubryka jest wersjonowana niezależnie od bazy, więc nowa waga musi przejść.

    Zamknięcie słowników w CHECK znaczyłoby, że zmiana rubryki wymaga migracji.
    Tych wartości pilnuje walidacja kontraktu przeciwko wczytanej rubryce (3.11).
    """
    con_z_runem.execute(WSTAW_FINDING, ("waga_z_rubryki_0_2", '{"board_id": 1}'))
    con_z_runem.commit()
    assert con_z_runem.execute("SELECT count(*) FROM findings").fetchone()[0] == 1
