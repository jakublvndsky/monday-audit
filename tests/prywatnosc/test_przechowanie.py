"""Co wolno zapisać na dysk po runie (plan, faza 5c).

Ten plik pilnuje jednej rzeczy z trzech stron: że **na dysk nie trafia nic,
co wskazuje konkretną osobę**. Pseudonim, data jej aktywności, adres — każde
z osobna wystarczy, żeby zapis przestał być „statystyką" i stał się daną
osobową, którą trzeba prawnie zabezpieczyć.

Przykłady są wzięte z prawdziwej analizy `analiza-20260923T085706Z`
(CXLABS, snapshot 1) — z pseudonimami zmienionymi na wymyślone.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.prywatnosc.przechowanie import (
    PrzechowanieError,
    sprawdz_zapis,
    statystyki_do_zapisu,
    uwaga_do_zapisu,
    zapisz_statystyki,
    zapisz_uwagi,
)

TERAZ = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

# Kształt uwagi ZOMBIE_ACCOUNT z prawdziwego runu, pseudonim wymyślony.
ZOMBIE = {
    "klasa_id": "ZOMBIE_ACCOUNT",
    "zrodlo": "szablon",
    "opis": (
        "Konto członka zespołu zajmuje płatne miejsce i nie wykazuje aktywności. "
        "Ostatnia aktywność: 2026-06-09T13:01:12Z."
    ),
    "rekomendacja": "Potwierdzić u właściciela konta, czy ta osoba nadal pracuje.",
    "dowod": {
        "user_hash": "1dcfeabe7fa5d9a7",
        "kind": "member",
        "last_activity": "2026-06-09T13:01:12Z",
        "obecnosc_w_logach": False,
        "plan_tier": "enterprise",
    },
}

# Kształt uwagi GUEST_SPRAWL — lista pseudonimów gości.
GOSCIE = {
    "klasa_id": "GUEST_SPRAWL",
    "opis": "Konto ma 13 gości wobec 8 członków.",
    "rekomendacja": "Przejrzeć listę gości.",
    "dowod": {
        "liczba_guest": 13,
        "liczba_members": 8,
        "guest_hash": ["0240dd46f87f3dd2", "0242d176469cbe1d", "320d5fda5f6f5b75"],
    },
}


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    polaczenie.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) "
        "VALUES ('r1', 'cxlabs', 'w_toku', '2026-09-23T00:00:00Z')"
    )
    polaczenie.commit()
    yield polaczenie
    polaczenie.close()


# ── pseudonim to nadal dana osobowa ──────────────────────────────────────


def test_pseudonim_w_dowodzie_staje_sie_znacznikiem() -> None:
    """Hasz liczony stałą solą da się odwrócić, mając dostęp do konta —
    wygląda anonimowo, ale nim nie jest."""
    zapis = uwaga_do_zapisu(ZOMBIE, teraz=TERAZ)

    assert zapis["dowod"]["user_hash"] == "[OSOBA]"
    assert "1dcfeabe7fa5d9a7" not in json.dumps(zapis)


def test_lista_pseudonimow_staje_sie_liczba() -> None:
    """„13 gości" jest ustaleniem audytu; KTÓRE to osoby — już nie."""
    zapis = uwaga_do_zapisu(GOSCIE, teraz=TERAZ)

    assert zapis["dowod"]["guest_hash"] == "[OSOBY: 3]"
    assert zapis["dowod"]["liczba_guest"] == 13


def test_liczby_i_fakty_audytu_zostaja() -> None:
    """Sedno prośby Kuby: uwagi zapisane Z LICZBAMI. Maskowanie ma zabierać
    tożsamość, a nie treść ustalenia."""
    zapis = uwaga_do_zapisu(ZOMBIE, teraz=TERAZ)

    assert zapis["dowod"]["kind"] == "member"
    assert zapis["dowod"]["obecnosc_w_logach"] is False
    assert zapis["dowod"]["plan_tier"] == "enterprise"
    assert zapis["klasa_id"] == "ZOMBIE_ACCOUNT"


def test_identyfikator_automatyzacji_nie_jest_mylony_z_pseudonimem() -> None:
    """Identyfikatory monday to 9-10 cyfr, pseudonim — 16 znaków szesnastkowych.
    Maskowanie, które zjada id automatyzacji, zabrałoby uwadze jej adres."""
    uwaga = {
        "klasa_id": "AUTOMATION_DEAD",
        "opis": "Automatyzacja 132931514 pada w 100%.",
        "rekomendacja": "Poprawić krok przypisania.",
        "dowod": {"automation_id": "132931514", "failure": 1},
    }

    zapis = uwaga_do_zapisu(uwaga, teraz=TERAZ)

    assert zapis["dowod"]["automation_id"] == "132931514"
    assert "132931514" in zapis["opis"]


# ── data aktywności wskazuje osobę tak samo jak hasz ─────────────────────


def test_data_w_dowodzie_staje_sie_liczba_dni() -> None:
    """„Ostatnio aktywny 2026-06-09" na koncie z dziewiętnastoma miejscami
    wskazuje jedną osobę. „105 dni przed runem" mówi to, czego audyt potrzebuje."""
    zapis = uwaga_do_zapisu(ZOMBIE, teraz=TERAZ)

    assert zapis["dowod"]["last_activity"] == "[105 dni przed runem]"


def test_data_w_tekscie_tez_znika() -> None:
    zapis = uwaga_do_zapisu(ZOMBIE, teraz=TERAZ)

    assert "2026-06-09" not in zapis["opis"]
    assert "dni przed runem" in zapis["opis"]


# ── dane kontaktowe jak w trace'ach ──────────────────────────────────────


def test_mail_w_uwadze_jest_maskowany() -> None:
    uwaga = {**GOSCIE, "opis": "Zgłasza kontakt@klient.test, tel. +48 501 234 567."}

    zapis = uwaga_do_zapisu(uwaga, teraz=TERAZ)

    assert "[E-MAIL]" in zapis["opis"]
    assert "[TELEFON]" in zapis["opis"]


# ── statystyki: tylko liczby ─────────────────────────────────────────────


def test_statystyki_zachowuja_liczby_i_gubia_nazwy() -> None:
    """Nazwy tablic, workspace'ów i etykiet odpadają w całości — lista rzeczy
    DOZWOLONYCH się nie starzeje, lista rzeczy do wycięcia tak."""
    obraz = {
        "konto": {
            "nazwa": "CXLABS",
            "workspacow": 136,
            "gosci": 13,
            "licencja": {"tier": "enterprise"},
        },
        "itemy": {"najwieksze_tablice": [{"nazwa": "👤 Leads", "board_id": "123", "itemow": 7076}]},
        "zestawienia": [
            {"produkt": "crm", "w_toku": 1290, "etykiety_w_toku": ["Qualified", "New Lead"]}
        ],
    }

    dane = statystyki_do_zapisu(obraz)

    assert dane["konto"] == {"workspacow": 136, "gosci": 13, "licencja": {"tier": "enterprise"}}
    assert dane["itemy"]["najwieksze_tablice"] == [{"itemow": 7076}]
    assert dane["zestawienia"] == [{"produkt": "crm", "w_toku": 1290}]
    assert "Leads" not in json.dumps(dane)
    assert "CXLABS" not in json.dumps(dane)


def test_statystyki_gubia_etykiety_takze_w_kluczach() -> None:
    """Review 2026-09-23: rozkład po grupach to `{"Anna Nowak": 20}` — nazwa
    grupy w KLUCZU, liczba w wartości. Pierwsza wersja sprawdzała tylko wartości
    i zapisywała takie klucze wprost; nazwisko i telefon lądowały na dysku."""
    obraz = {
        "itemy": {
            "razem": 40,
            "per_produkt": {"crm": 40},
            "najwieksze_tablice": [
                {
                    "itemow": 40,
                    "lejek_stopien": 2,
                    "rozklad": {
                        "Anna Nowak": 20,
                        "tel +48 501 234 567": 8,
                        "2026-06-09": 1,
                        "Qualified": 11,
                    },
                }
            ],
        },
        "tablice": {"aktywnosc": {"30d": 5, "365d": 2}, "po_rodzaju": {"public": 3}},
        "po_osobach": {"1dcfeabe7fa5d9a7": 4},
    }

    dane = statystyki_do_zapisu(obraz)

    tekst = json.dumps(dane, ensure_ascii=False)
    for slad_osoby in ("Anna Nowak", "501 234 567", "2026-06-09", "Qualified", "1dcfeabe7fa5d9a7"):
        assert slad_osoby not in tekst
    # Klucze NASZEGO schematu zostają — bez nich liczby nie mają podpisu.
    assert dane["itemy"]["per_produkt"] == {"crm": 40}
    assert dane["itemy"]["najwieksze_tablice"] == [{"itemow": 40, "lejek_stopien": 2}]
    assert dane["tablice"] == {"aktywnosc": {"30d": 5, "365d": 2}, "po_rodzaju": {"public": 3}}
    assert "po_osobach" not in dane


def test_klucze_dowodu_przechodza_przez_te_same_reguly() -> None:
    """Data aktywności i telefon w roli KLUCZA znikały z wartości, ale nie
    z kluczy — ta sama osoba wskazana innym wejściem."""
    uwaga = {
        "klasa_id": "BOARD_GHOST",
        "opis": "o",
        "rekomendacja": "r",
        "dowod": {"rozklad": {"2026-06-09": 1, "tel +48 501 234 567": 2, "1dcfeabe7fa5d9a7": 3}},
    }

    zapis = uwaga_do_zapisu(uwaga, teraz=TERAZ)

    assert zapis["dowod"]["rozklad"] == {
        "[106 dni przed runem]": 1,
        "tel [TELEFON]": 2,
        "[OSOBA]": 3,
    }


def test_bramka_przerywa_gdy_telefon_przetrwal() -> None:
    with pytest.raises(PrzechowanieError, match="numer telefonu") as blad:
        sprawdz_zapis({"rozklad": {"+48 501 234 567": 1}})

    assert "501" not in str(blad.value)


# ── zawodzi zamknięte ────────────────────────────────────────────────────


def test_bramka_przerywa_gdy_pseudonim_przetrwal() -> None:
    """Pseudonim, który przeszedł jakąś inną drogą (np. klucz słownika),
    przerywa zapis. Komunikat nie wypisuje znalezionej wartości."""
    with pytest.raises(PrzechowanieError) as blad:
        sprawdz_zapis({"1dcfeabe7fa5d9a7": 1})

    assert "1dcfeabe7fa5d9a7" not in str(blad.value)


def test_zapis_uwag_jest_wszystko_albo_nic(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zapis połowy wyglądałby na komplet — gorsze od jawnego braku.

    Bramka jest tu wywracana atrapą, a nie spreparowaną uwagą. Pierwsza wersja
    testu przemycała pseudonim w KLUCZU dowodu — i działała tylko dlatego, że
    klucze omijały maskowanie. Po poprawce z review 2026-09-23 nic nie ma jak
    przetrwać reguł, więc awarię trzeba wywołać wprost.
    """
    from monday_audit.prywatnosc import przechowanie

    wywolan = {"ile": 0}
    prawdziwa = przechowanie.sprawdz_zapis

    def druga_pada(dane: object) -> None:
        wywolan["ile"] += 1
        if wywolan["ile"] == 2:
            raise PrzechowanieError("w zapisie został pseudonim osoby — zapis przerwany")
        prawdziwa(dane)

    monkeypatch.setattr(przechowanie, "sprawdz_zapis", druga_pada)

    with pytest.raises(PrzechowanieError):
        zapisz_uwagi(con, "r1", [ZOMBIE, GOSCIE], teraz=TERAZ)

    assert con.execute("SELECT COUNT(*) FROM uwagi_zapisane").fetchone()[0] == 0


# ── do bazy ──────────────────────────────────────────────────────────────


def test_uwagi_trafiaja_do_bazy_zamaskowane(con: sqlite3.Connection) -> None:
    assert zapisz_uwagi(con, "r1", [ZOMBIE, GOSCIE], teraz=TERAZ) == 2

    zapisane = [dict(w) for w in con.execute("SELECT * FROM uwagi_zapisane ORDER BY id")]
    caly = json.dumps(zapisane, ensure_ascii=False)

    assert "1dcfeabe7fa5d9a7" not in caly
    assert "0240dd46f87f3dd2" not in caly
    assert "2026-06-09" not in caly
    assert zapisane[0]["zrodlo"] == "szablon"
    assert zapisane[1]["zrodlo"] == "model"


def test_statystyki_trafiaja_do_bazy(con: sqlite3.Connection) -> None:
    zapisz_statystyki(con, "r1", {"konto": {"nazwa": "CXLABS", "workspacow": 136}})

    dane = json.loads(con.execute("SELECT dane FROM statystyki_runow").fetchone()[0])

    assert dane == {"konto": {"workspacow": 136}}
