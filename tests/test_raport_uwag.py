"""Raport uwag z nazwiskami (faza 5c, wariant A).

Dwie rzeczy są tu sednem: pseudonim ma się zamienić w nazwisko — inaczej
rekomendacja „dezaktywuj konto" jest niewykonalna — a treść klienta ma być
escapowana, bo nazwa z `<script>` nie może stać się skryptem.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.raport_uwag import oddaj_raport, wyrenderuj_uwagi, zbuduj_raport_uwag
from monday_audit.rubryka import wczytaj_rubryke

PSEUDONIM = "1dcfeabe7fa5d9a7"
NAZWISKO = "Zdzisława Wąchockańska"


@pytest.fixture
def con() -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(":memory:")
    zastosuj_migracje(polaczenie)
    polaczenie.execute(
        "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
        "VALUES ('cxlabs', ?, ?, NULL)",
        (PSEUDONIM, NAZWISKO),
    )
    yield polaczenie
    polaczenie.close()


def _uwaga(**zmiany: object) -> dict[str, object]:
    return {
        "klasa_id": "ZOMBIE_ACCOUNT",
        "zrodlo": "szablon",
        "opis": f"Konto {PSEUDONIM} nie wykazuje aktywności od 105 dni.",
        "rekomendacja": "Potwierdzić u właściciela konta.",
        "dowod": {"user_hash": PSEUDONIM, "kind": "admin", "last_activity": "2026-06-09"},
        **zmiany,
    }


def _raport(con: sqlite3.Connection, *uwagi: dict[str, object]) -> str:
    raport = zbuduj_raport_uwag(
        list(uwagi),
        con=con,
        client_id="cxlabs",
        run_id="r1",
        run_at="2026-09-23T12:00:00Z",
        rubryka=wczytaj_rubryke(),
        pominietych=3,
    )
    return wyrenderuj_uwagi(raport)


def test_pseudonim_w_dowodzie_i_opisie_staje_sie_nazwiskiem(con: sqlite3.Connection) -> None:
    html = _raport(con, _uwaga())

    assert NAZWISKO in html
    assert PSEUDONIM not in html


def test_nieznany_pseudonim_jest_oznaczony_a_nie_zgubiony(con: sqlite3.Connection) -> None:
    html = _raport(con, _uwaga(dowod={"user_hash": "ffffffffffffffff"}, opis="konto"))

    assert "nieznane konto" in html
    assert "ffffffffffffffff" not in html


def test_tresc_klienta_jest_escapowana(con: sqlite3.Connection) -> None:
    html = _raport(con, _uwaga(opis="Tablica <script>alert(1)</script>"))

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_plik_od_razu_z_prawami_600(con: sqlite3.Connection, tmp_path: Path) -> None:
    raport = zbuduj_raport_uwag(
        [_uwaga()],
        con=con,
        client_id="cxlabs",
        run_id="r1",
        run_at="2026-09-23T12:00:00Z",
        rubryka=wczytaj_rubryke(),
    )

    sciezka = oddaj_raport(raport, tmp_path / "a" / "raport.html")

    assert sciezka.stat().st_mode & 0o777 == 0o600
    assert NAZWISKO in sciezka.read_text(encoding="utf-8")


def test_uwagi_sa_grupowane_po_klasie_a_nie_wyliczane(con: sqlite3.Connection) -> None:
    """Prośba Kuby po pierwszym raporcie: „wszystko rozbite na osobne itemy".
    Trzy martwe konta to jedna sekcja z trzema wierszami, a nie trzy karty."""
    auto = {
        "klasa_id": "AUTOMATION_DEAD",
        "opis": "Automatyzacja pada.",
        "rekomendacja": "Naprawić trigger.",
        "dowod": {"automation_id": "1", "failure": 3},
    }
    raport = zbuduj_raport_uwag(
        [_uwaga(), _uwaga(), _uwaga(), auto],
        con=con,
        client_id="cxlabs",
        run_id="r1",
        run_at="2026-09-23T12:00:00Z",
        rubryka=wczytaj_rubryke(),
    )

    grupy = raport.grupy
    assert [(g.klasa_id, len(g.uwagi)) for g in grupy] == [
        ("ZOMBIE_ACCOUNT", 3),
        ("AUTOMATION_DEAD", 1),
    ]
    assert grupy[0].kolumny == ("user_hash", "kind", "last_activity")
    # Ta sama rekomendacja trzy razy to jedna rekomendacja.
    assert grupy[0].wspolna_rekomendacja == "Potwierdzić u właściciela konta."

    html = wyrenderuj_uwagi(raport)
    assert html.count('class="grupa"') == 2
    assert html.count("Potwierdzić u właściciela konta.") == 1


def test_istniejacy_plik_z_szerszymi_prawami_nie_dostaje_nazwisk_przed_zawezeniem(
    con: sqlite3.Connection, tmp_path: Path
) -> None:
    """`os.open` ustawia prawa tylko przy tworzeniu — plik 644 dostałby
    nazwiska, zanim prawa się zawężą."""
    import os

    sciezka = tmp_path / "raport.html"
    sciezka.write_text("stary", encoding="utf-8")
    sciezka.chmod(0o644)
    widziane: list[int] = []
    prawdziwy = os.fdopen

    def podgladaj(fd: int, *a: object, **k: object) -> object:
        widziane.append(os.fstat(fd).st_mode & 0o777)
        return prawdziwy(fd, *a, **k)  # type: ignore[call-overload]

    raport = zbuduj_raport_uwag(
        [_uwaga()],
        con=con,
        client_id="cxlabs",
        run_id="r1",
        run_at="2026-09-23T12:00:00Z",
        rubryka=wczytaj_rubryke(),
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "fdopen", podgladaj)
        oddaj_raport(raport, sciezka)

    assert widziane == [0o600], "prawa zawężone PRZED zapisem"
    assert sciezka.stat().st_mode & 0o777 == 0o600
