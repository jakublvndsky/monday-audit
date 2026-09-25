"""Raport uwag z nazwiskami (faza 5c, wariant A).

Dwie rzeczy są tu sednem: pseudonim ma się zamienić w nazwisko — inaczej
rekomendacja „dezaktywuj konto" jest niewykonalna — a treść klienta ma być
escapowana, bo nazwa z `<script>` nie może stać się skryptem.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.raport_uwag import RaportUwag, oddaj_raport, wyrenderuj_uwagi, zbuduj_raport_uwag
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


def test_zastrzezenia_tez_dostaja_nazwiska(con: sqlite3.Connection) -> None:
    """Review 2026-09-24: obraz konta jest redagowany przed modelem, więc jego
    zastrzeżenia niosą `[OSOBA:…]` — w raporcie ma stać nazwisko, nie hasz."""
    raport = zbuduj_raport_uwag(
        [_uwaga()],
        con=con,
        client_id="cxlabs",
        run_id="r1",
        run_at="2026-09-23T12:00:00Z",
        rubryka=wczytaj_rubryke(),
        zastrzezenia=(f"[tablice] workspace [OSOBA:{PSEUDONIM}] Leady bez próbki logu",),
    )

    assert raport.zastrzezenia == (f"[tablice] workspace {NAZWISKO} Leady bez próbki logu",)


# ── faza 7: kategorie, pokrycie, dowód jako chipy (2026-09-25) ───────────


def _zbuduj(con: sqlite3.Connection, uwagi: list[dict[str, Any]], **k: Any) -> RaportUwag:
    return zbuduj_raport_uwag(
        uwagi,
        con=con,
        client_id="cxlabs",
        run_id="r1",
        run_at="2026-09-24T12:00:00Z",
        rubryka=wczytaj_rubryke(),
        **k,
    )


def _duplikaty(**dowod: Any) -> dict[str, Any]:
    return {
        "klasa_id": "DUPLICATE_STRUCTURE",
        "opis": "10 kopii jednego szablonu w jednym workspace. Żadna nie jest używana.",
        "rekomendacja": "Zostawić jeden szablon.",
        "dowod": {
            "board_ids": [str(n) for n in range(10)],
            "nakladanie_kolumn": 1.0,
            "nakladanie_subskrybentow": 0.5,
            "daty_utworzenia": {"0": "2026-05-29T10:00:00Z", "1": "2026-05-29T10:12:00Z"},
            "aktywnosc_stron": {"0": 3, "1": 0, "2": None},
            **dowod,
        },
    }


def test_cztery_kategorie_w_kolejnosci_z_rubryki_a_niemierzone_mowia_to_wprost(
    con: sqlite3.Connection,
) -> None:
    """Kategoria bez detektora to „jeszcze nie mierzone", nie „0 problemów"."""
    raport = _zbuduj(con, [_uwaga(), _duplikaty(), _duplikaty()])

    kategorie = {k.id: k for k in raport.kategorie}
    assert list(kategorie) == ["workspace", "tablice", "uzytkownicy", "agenci"]
    assert (kategorie["tablice"].uwag, kategorie["tablice"].udzial) == (2, 67)
    assert (kategorie["uzytkownicy"].uwag, kategorie["uzytkownicy"].udzial) == (1, 33)
    assert kategorie["workspace"].niezmierzona and kategorie["agenci"].niezmierzona
    assert kategorie["tablice"].niezmierzona is None
    # Zdanie na kafelek: pierwsze zdanie uwagi z najliczniejszej grupy.
    assert kategorie["tablice"].zdanie == "10 kopii jednego szablonu w jednym workspace."


def test_dowod_staje_sie_czytelnymi_chipami(con: sqlite3.Connection) -> None:
    """Projekt: „nakładanie kolumn: 100%", „… i 7 kolejnych", daty względem analizy."""
    raport = _zbuduj(con, [_duplikaty()])

    chipy = {c.etykieta: c.wartosc for c in raport.uwagi[0].chipy}
    assert chipy["tablice"] == "0, 1, 2 i 7 kolejnych"
    assert chipy["nakładanie kolumn"] == "100%"
    assert chipy["nakładanie subskrybentów"] == "50%"
    assert chipy["utworzone"] == "2026-05-29 (118 dni przed analizą)"
    assert chipy["aktywność tablic w oknie"] == "aktywnych 1 z 3, bez próbki logu 1"


def test_nazwy_tablic_ze_snapshotu_zamiast_id(con: sqlite3.Connection) -> None:
    """Model przepisuje ID, człowiek czyta nazwy — snapshot jest wtedy w pamięci."""
    import json

    from monday_audit.przebieg import zapisz_snapshot

    payload = {
        "tablice": {"tablice": [{"board_id": "0", "nazwa": f"Leady [OSOBA:{PSEUDONIM}]"}]},
        "konto": {"konto": {"nazwa": "Nordwind"}, "plan": {"tier": "enterprise"}},
        "aktywnosc": {"podsumowanie": {"tablic_zbadanych": 120, "tablic_pominietych": 1945}},
    }
    sid = zapisz_snapshot(con, client_id="cxlabs", payload=json.loads(json.dumps(payload)),
                          run_at="2026-09-24T12:00:00Z")  # fmt: skip

    raport = _zbuduj(con, [_duplikaty(board_ids=["0"])], snapshot_id=sid)

    assert raport.uwagi[0].chipy[0].wartosc == f"Leady {NAZWISKO}"
    assert (raport.konto_nazwa, raport.plan) == ("Nordwind", "enterprise")
    logi = next(p for p in raport.pokrycie if p.temat == "Logi aktywności")
    assert (logi.zbadanych, logi.wszystkich, logi.procent) == (120, 2065, 6)


def test_pokrycie_z_sufitu_i_nie_zmierzone_z_dowodu(con: sqlite3.Connection) -> None:
    from monday_audit.analiza import PozaSufitem

    gosc = {
        "klasa_id": "GUEST_SPRAWL",
        "opis": "13 gości.",
        "rekomendacja": "Przejrzeć gości.",
        "dowod": {
            "liczba_guest": 13,
            "tablice_dostepne": {"nie_zmierzone": "API nie pokazuje (O45)"},
        },
    }

    raport = _zbuduj(
        con, [gosc], poza_sufitem=[PozaSufitem("BOARD_OVERCOMPLEX", 20, 401, "najwięcej kolumn")]
    )

    pokrycie = {p.temat: p for p in raport.pokrycie}
    sufit = pokrycie["Tablica z polami, których nikt nie wypełnia"]
    assert (sufit.procent, sufit.kategoria) == (5, "tablice")
    assert "NIE znaczy, że są w porządku" in sufit.tekst
    goscie = pokrycie["Dostęp gości do tablic"]
    assert goscie.niezmierzone and goscie.kategoria == "uzytkownicy"
    assert {"Workspace", "Agenci AI"} <= set(pokrycie)
    chip = raport.uwagi[0].chipy[1]
    assert (chip.niezmierzone, chip.powod) == (True, "API nie pokazuje (O45)")


def test_plik_to_wersja_klienta_bez_danych_zespolu_i_bez_zasobow(con: sqlite3.Connection) -> None:
    """Koszt, odrzucone hipotezy i identyfikatory runu to widok zespołu w portalu.
    Usunięte, nie ukryte. Do tego zero skryptów, fontów i adresów zewnętrznych (D14)."""
    import re

    html = wyrenderuj_uwagi(_zbuduj(con, [_uwaga(), _duplikaty()], pominietych=4713))

    tekst = re.sub(r"data:[^\"')]+", "", html)  # logo w base64 nie jest treścią
    assert "USD" not in tekst and "4713" not in tekst and "r1" not in tekst
    assert "<script" not in html
    assert "@font-face" not in html
    assert re.search(r"""(src|href)\s*=\s*["']https?://""", html) is None
    # Kategorie mierzone mają widok pogłębiony, niemierzone — tylko wiersz.
    assert 'id="kat-tablice"' in html and 'id="kat-uzytkownicy"' in html
    assert 'id="kat-agenci"' not in html
    assert html.count("jeszcze nie mierzone") == 2
