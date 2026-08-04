"""Testy walidacji kontraktu wyjściowego (D8, etap 3.11), warstwa 1.

Najważniejszy test w tym pliku to `test_finding_bez_dowodu_odpada`. Zakaz
twardy z CLAUDE.md mówi „finding bez pola `dowod` nie przechodzi walidacji.
Bez wyjątków" — jeśli ten test kiedykolwiek zacznie przechodzić przez pomyłkę,
cały projekt traci swoją główną gwarancję.

Drugi w kolejności: `test_kwota_przy_ryzyku_odpada`. Wymyślona kwota w raporcie
podważa wiarygodność u pierwszego klienta, który ją sprawdzi.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.cennik import Stawka
from monday_audit.kontrakt import (
    REGULA_BRAK_POLA,
    REGULA_DOWOD_NIEPELNY,
    REGULA_DOWOD_PUSTY,
    REGULA_KLASA_DO_WERYFIKACJI,
    REGULA_KLASA_NIEZNANA,
    REGULA_KWOTA_BEZ_PODSTAWY,
    REGULA_KWOTA_PRZY_RYZYKU,
    REGULA_NIEZGODNA_Z_RUBRYKA,
    REGULA_PUSTY_TEKST,
    REGULA_SLOWNIK,
    KontraktError,
    waliduj,
    zapisz_findingi,
    zapisz_odrzucone,
)
from monday_audit.rubryka import wczytaj_rubryke

RUBRYKA = wczytaj_rubryke()

# Stawka, którą run mógłby dostać z `stawki_klienta`. Bez niej kwota w findingu
# jest odrzucana — i to jest sens `REGULA_KWOTA_BEZ_PODSTAWY`.
STAWKI = {
    "koszt_licencji_mies": Stawka(
        pozycja="koszt_licencji_mies",
        wartosc=100.0,
        waluta="PLN",
        jednostka="miesiac",
        zrodlo="faktura 07/2026",
        wiarygodnosc="od_klienta",
        pobrano_at="2026-08-04T10:00:00+00:00",
        wazna_do=None,
        per_klient=True,
    )
}
RUN_AT = "2026-08-03T10:00:00+00:00"


def finding(klasa_id: str = "AUTOMATION_DEAD", **nadpisz: Any) -> dict[str, Any]:
    """Poprawny finding dla podanej klasy — pola wagi i wyceny Z RUBRYKI.

    Dla klasy nieistniejącej w rubryce budujemy kształt na podstawie znanej
    i podmieniamy `klasa_id` — inaczej nie dałoby się przetestować przypadku
    „agent wymyślił klasę".
    """
    klasa = RUBRYKA.po_id.get(klasa_id) or RUBRYKA.po_id["AUTOMATION_DEAD"]
    dowod = {pole.rstrip("[]"): "wartosc" for pole in klasa.dowod}
    baza: dict[str, Any] = {
        "klasa_id": klasa_id,  # celowo `klasa_id`, nie `klasa.id`
        "waga": klasa.waga,
        "wysilek_naprawy": klasa.wysilek_naprawy,
        "typ_wyceny": klasa.typ_wyceny,
        "kwota_pln": 1200.0 if klasa.typ_wyceny == "oszczednosc_bezposrednia" else None,
        "opis": "Automatyzacja kończy się błędem w co trzecim uruchomieniu.",
        "rekomendacja": "Poprawić warunek wejściowy albo wyłączyć automatyzację.",
        "dowod": dowod,
        "pewnosc": "wysoka",
    }
    return {**baza, **nadpisz}


def odpowiedz(
    findings: list[dict[str, Any]] | None = None,
    hipotezy_odrzucone: list[dict[str, Any]] | None = None,
    **nadpisz: Any,
) -> dict[str, Any]:
    baza: dict[str, Any] = {
        "run_id": "r1",
        "snapshot_id": 5,
        "rubric_version": RUBRYKA.wersja,
        "findings": findings if findings is not None else [finding()],
        "hipotezy_odrzucone": (
            hipotezy_odrzucone
            if hipotezy_odrzucone is not None
            else [{"klasa_id": "BOARD_GHOST", "board_id": 111, "powod": "archiwum roczne"}]
        ),
        "zuzycie": {"wywolania": 3, "tokens_in": 1000, "tokens_out": 200},
    }
    return {**baza, **nadpisz}


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    polaczenie.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) "
        "VALUES ('r1', 'cxlabs', 'w_toku', ?)",
        (RUN_AT,),
    )
    polaczenie.execute(
        "INSERT INTO snapshots (id, client_id, run_at, collector_ver, payload) "
        "VALUES (5, 'cxlabs', ?, '0.1.0', '{}')",
        (RUN_AT,),
    )
    polaczenie.commit()
    yield polaczenie
    polaczenie.close()


# ── zakaz twardy: dowód ──────────────────────────────────────────────────


def test_poprawny_finding_przechodzi() -> None:
    wynik = waliduj(odpowiedz(), RUBRYKA)

    assert len(wynik.przyjete) == 1
    assert wynik.odrzucone == []
    assert wynik.odsetek_odrzuconych == 0.0


@pytest.mark.parametrize("puste", [{}, None, "brak", []])
def test_finding_bez_dowodu_odpada(puste: Any) -> None:
    """ZAKAZ TWARDY z CLAUDE.md. Bez wyjątków.

    Pusty obiekt jest tak samo zły jak brak pola: obie sytuacje znaczą,
    że agent nie wskazał ani jednego faktu ze snapshotu.
    """
    wynik = waliduj(odpowiedz([finding(dowod=puste)]), RUBRYKA)

    assert wynik.przyjete == []
    assert wynik.odrzucone[0].regula == REGULA_DOWOD_PUSTY


def test_niepelny_dowod_odpada_wzgledem_definicji_klasy() -> None:
    """Wymagania dowodu są w rubryce, nie w kodzie.

    PROCESS_BYPASS wymaga siedmiu pól, AUTOMATION_DEAD pięciu. Zaszycie tego
    w kodzie dałoby dwa źródła prawdy, które rozjadą się przy pierwszej
    zmianie rubryki.
    """
    klasa = RUBRYKA.po_id["PROCESS_BYPASS"]
    tylko_jedno = {klasa.dowod[0].rstrip("[]"): "wartosc"}

    wynik = waliduj(odpowiedz([finding("PROCESS_BYPASS", dowod=tylko_jedno)]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_DOWOD_NIEPELNY
    # Komunikat wymienia, czego brakuje — inaczej poprawa promptu jest zgadywaniem.
    assert klasa.dowod[-1].rstrip("[]") in wynik.odrzucone[0].powod


def test_dowod_z_pustymi_wartosciami_odpada() -> None:
    """Klucz obecny, wartość pusta — to nadal brak faktu."""
    dowod = {p.rstrip("[]"): "" for p in RUBRYKA.po_id["AUTOMATION_DEAD"].dowod}

    wynik = waliduj(odpowiedz([finding(dowod=dowod)]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_DOWOD_NIEPELNY


def test_zero_i_false_sa_poprawnymi_wartosciami_dowodu() -> None:
    """`exhausted: 0` i `obecnosc_w_logach: false` to FAKTY, nie braki.

    Naiwne sprawdzenie „czy wartość jest prawdziwa" odrzuciłoby dokładnie te
    findingi, na których najbardziej zależy — konto bez śladu w logach
    i automatyzację bez wyczerpań.
    """
    klasa = RUBRYKA.po_id["ZOMBIE_ACCOUNT"]
    dowod = {p.rstrip("[]"): 0 for p in klasa.dowod}
    dowod["obecnosc_w_logach"] = False

    wynik = waliduj(odpowiedz([finding("ZOMBIE_ACCOUNT", dowod=dowod)]), RUBRYKA, STAWKI)

    assert len(wynik.przyjete) == 1, [o.powod for o in wynik.odrzucone]


# ── wycena ───────────────────────────────────────────────────────────────


def test_kwota_przy_ryzyku_odpada() -> None:
    """Wymyślona kwota podważa cały raport u pierwszego klienta, który ją sprawdzi."""
    assert RUBRYKA.po_id["AUTOMATION_DEAD"].typ_wyceny == "ryzyko"

    wynik = waliduj(odpowiedz([finding("AUTOMATION_DEAD", kwota_pln=5000)]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_KWOTA_PRZY_RYZYKU


def test_kwota_przy_oszczednosci_przechodzi() -> None:
    assert RUBRYKA.po_id["ZOMBIE_ACCOUNT"].typ_wyceny == "oszczednosc_bezposrednia"

    wynik = waliduj(odpowiedz([finding("ZOMBIE_ACCOUNT", kwota_pln=1200.0)]), RUBRYKA, STAWKI)

    assert len(wynik.przyjete) == 1, [o.powod for o in wynik.odrzucone]


def test_kwota_ujemna_odpada() -> None:
    wynik = waliduj(odpowiedz([finding("ZOMBIE_ACCOUNT", kwota_pln=-1)]), RUBRYKA)

    assert wynik.odrzucone[0].regula


# ── klasa i słowniki ─────────────────────────────────────────────────────


def test_nieznana_klasa_odpada() -> None:
    wynik = waliduj(odpowiedz([finding(klasa_id="WYMYSLONA_KLASA")]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_KLASA_NIEZNANA
    assert wynik.odrzucone[0].klasa_id == "WYMYSLONA_KLASA"


def test_klasa_do_weryfikacji_odpada() -> None:
    """AI_UNUSED czeka na rozstrzygnięcie O2 — nie wolno jej raportować."""
    wynik = waliduj(odpowiedz([finding(klasa_id="AI_UNUSED")]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_KLASA_DO_WERYFIKACJI


def test_pewnosc_poza_slownikiem_odpada() -> None:
    wynik = waliduj(odpowiedz([finding(pewnosc="raczej_tak")]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_SLOWNIK


def test_waga_inna_niz_w_rubryce_odpada() -> None:
    """Waga NIE należy do agenta — jest decyzją biznesową z etapu 1.

    Agent, który podnosi wagę własnego findingu, przesuwa go na pierwszy slajd
    raportu. Kolejność raportu zastępuje health score, więc to nie kosmetyka.
    """
    wynik = waliduj(odpowiedz([finding("AUTOMATION_DEAD", waga="krytyczna")]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_NIEZGODNA_Z_RUBRYKA


def test_pusty_opis_odpada() -> None:
    wynik = waliduj(odpowiedz([finding(opis="   ")]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_PUSTY_TEKST


def test_brak_pola_kontraktu_odpada() -> None:
    niepelny = finding()
    del niepelny["kwota_pln"]

    wynik = waliduj(odpowiedz([niepelny]), RUBRYKA)

    assert wynik.odrzucone[0].regula == REGULA_BRAK_POLA
    assert "kwota_pln" in wynik.odrzucone[0].powod


# ── hipotezy_odrzucone: obowiązkowe ──────────────────────────────────────


def test_brak_hipotez_odrzuconych_przerywa_calosc() -> None:
    """D8: pole obowiązkowe. To błąd kontraktu, nie jednego findingu."""
    bez = odpowiedz()
    del bez["hipotezy_odrzucone"]

    with pytest.raises(KontraktError, match="hipotezy_odrzucone"):
        waliduj(bez, RUBRYKA)


def test_puste_hipotezy_odrzucone_daja_ostrzezenie(caplog: pytest.LogCaptureFixture) -> None:
    """Agent potwierdzający wszystko jest bezużyteczny — ale to nie błąd kontraktu.

    Świadomie NIE przerywamy: pusta lista może być prawdą przy jednej hipotezie.
    Musi być natomiast widoczna, bo to główny sygnał, że agent się nie zastanawia.
    """
    import logging

    with caplog.at_level(logging.WARNING):
        wynik = waliduj(odpowiedz(hipotezy_odrzucone=[]), RUBRYKA)

    assert wynik.hipotezy_odrzucone == []
    assert "ANI JEDNEJ" in caplog.text


def test_brak_listy_findings_przerywa() -> None:
    with pytest.raises(KontraktError, match="findings"):
        waliduj({"hipotezy_odrzucone": []}, RUBRYKA)


# ── metryka jakości i zapis ──────────────────────────────────────────────


def test_odsetek_odrzuconych_to_metryka_etapu_4() -> None:
    wynik = waliduj(
        odpowiedz([finding(), finding(dowod={}), finding(klasa_id="AI_UNUSED"), finding()]),
        RUBRYKA,
    )

    assert len(wynik.przyjete) == 2
    assert len(wynik.odrzucone) == 2
    assert wynik.odsetek_odrzuconych == 0.5


def test_odrzucone_zapisuja_sie_z_trescia(con: sqlite3.Connection) -> None:
    """Licznik mówi „pięć odpadło" i nic więcej. Do poprawy promptu trzeba treści."""
    wynik = waliduj(odpowiedz([finding(dowod={})]), RUBRYKA)

    ile = zapisz_odrzucone(con, wynik.odrzucone, run_id="r1", snapshot_id=5)

    assert ile == 1
    wiersz = con.execute("SELECT * FROM findings_odrzucone").fetchone()
    assert wiersz["regula"] == REGULA_DOWOD_PUSTY
    assert wiersz["klasa_id"] == "AUTOMATION_DEAD"
    # Treść findingu jest w bazie, więc eval z etapu 4 ma na czym pracować.
    assert json.loads(wiersz["finding"])["opis"]


def test_widocznosc_i_trop_biora_sie_z_rubryki_nie_od_agenta(con: sqlite3.Connection) -> None:
    """Agent nie może oznaczyć findingu wewnętrznego jako klientowskiego."""
    podszywka = finding(widocznosc="klient", trop="cokolwiek")
    wynik = waliduj(odpowiedz([podszywka]), RUBRYKA)

    zapisz_findingi(con, wynik.przyjete, run_id="r1", snapshot_id=5, rubryka=RUBRYKA)

    wiersz = con.execute("SELECT widocznosc, waga, rubric_ver FROM findings").fetchone()
    assert wiersz["widocznosc"] == RUBRYKA.po_id["AUTOMATION_DEAD"].widocznosc
    assert wiersz["waga"] == RUBRYKA.po_id["AUTOMATION_DEAD"].waga
    # Wersja rubryki przy każdym findingu — bez tego nie da się przepuścić
    # starego snapshotu przez nową rubrykę i porównać (D7).
    assert wiersz["rubric_ver"] == RUBRYKA.wersja


def test_zapis_findingu_wymusza_obiekt_dowodu(con: sqlite3.Connection) -> None:
    """Druga warstwa: CHECK w schemacie, niezależny od walidacji z D8."""
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO findings (run_id, snapshot_id, klasa_id, rubric_ver, waga, wysilek, "
            "typ_wyceny, widocznosc, opis, rekomendacja, dowod, pewnosc) "
            "VALUES ('r1', 5, 'X', '0.2', 'wysoka', 'niski', 'ryzyko', 'klient', 'o', 'r', "
            "'\"nie-obiekt\"', 'wysoka')"
        )


def test_zuzycie_przechodzi_do_wyniku() -> None:
    wynik = waliduj(odpowiedz(), RUBRYKA)

    assert wynik.zuzycie == {"wywolania": 3, "tokens_in": 1000, "tokens_out": 200}


def test_niezgodna_wersja_rubryki_jest_odnotowana(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        waliduj(odpowiedz(rubric_version="0.1"), RUBRYKA)

    assert "rubric_version" in caplog.text


# ── kwota musi mieć podstawę (REGULA_KWOTA_BEZ_PODSTAWY) ─────────────────


def test_kwota_bez_stawki_odpada() -> None:
    """Prompt też o tym mówi, ale prompt jest warstwą dodatkową (D6).

    Wymyślona kwota to najgorszy możliwy błąd tego raportu, więc sprawdzenie
    jest mechaniczne: run bez stawki nie może wypuścić findingu z kwotą.
    """
    wynik = waliduj(odpowiedz([finding("ZOMBIE_ACCOUNT", kwota_pln=1200.0)]), RUBRYKA, None)

    assert wynik.przyjete == []
    assert wynik.odrzucone[0].regula == REGULA_KWOTA_BEZ_PODSTAWY
    assert "koszt_licencji_mies" in wynik.odrzucone[0].powod


def test_brak_kwoty_bez_stawki_przechodzi() -> None:
    """Bez stawki finding leci BEZ kwoty i to jest poprawne (O7)."""
    wynik = waliduj(odpowiedz([finding("ZOMBIE_ACCOUNT", kwota_pln=None)]), RUBRYKA, None)

    assert len(wynik.przyjete) == 1, [o.powod for o in wynik.odrzucone]


def test_kwota_na_przeterminowanej_stawce_odpada() -> None:
    """Cicho zgniła stawka w raporcie klienta jest groźniejsza od braku kwoty."""
    stara = {
        "koszt_licencji_mies": Stawka(
            pozycja="koszt_licencji_mies",
            wartosc=100.0,
            waluta="PLN",
            jednostka="miesiac",
            zrodlo="cennik",
            wiarygodnosc="zrodlo_pierwotne",
            pobrano_at="2025-01-01T00:00:00+00:00",
            wazna_do="2025-02-01T00:00:00+00:00",
        )
    }

    wynik = waliduj(odpowiedz([finding("ZOMBIE_ACCOUNT", kwota_pln=1200.0)]), RUBRYKA, stara)

    assert wynik.odrzucone[0].regula == REGULA_KWOTA_BEZ_PODSTAWY
    assert "przeterminowane" in wynik.odrzucone[0].powod


def test_kwota_przy_ryzyku_ma_wlasna_regule_nie_ogolna() -> None:
    """Kolejność reguł ma znaczenie — to dwie różne poprawki promptu.

    Gdyby ogólna „brak podstawy" wyprzedzała precyzyjną „kwota przy ryzyku",
    eval z etapu 4 pokazywałby jeden powód zamiast dwóch i nie dałoby się
    odróżnić agenta, który wymyśla kwoty, od takiego, który myli typ wyceny.
    """
    wynik = waliduj(odpowiedz([finding("AUTOMATION_DEAD", kwota_pln=5000.0)]), RUBRYKA, STAWKI)

    assert wynik.odrzucone[0].regula == REGULA_KWOTA_PRZY_RYZYKU


def test_drugi_wzor_wyceny_przechodzi_ze_stawka() -> None:
    """`PLAN_MISMATCH` ma inny wzór niż `ZOMBIE_ACCOUNT` i też musi przejść.

    Na żywym snapshocie ta klasa jest odrzucana warunkiem z rubryki (najnowsze
    konto młodsze niż 60 dni — konto rośnie), więc jej wzór nie da się
    sprawdzić runem. Bez tego testu druga ścieżka wyceny zostawałaby
    niesprawdzona aż do pierwszego klienta, u którego się wzbudzi.
    """
    klasa = RUBRYKA.po_id["PLAN_MISMATCH"]
    assert klasa.zmienne_od_klienta == ("koszt_licencji_mies",)

    # 10 miejsc nadwyżki * 100 PLN * 12 miesięcy
    wynik = waliduj(odpowiedz([finding("PLAN_MISMATCH", kwota_pln=12000.0)]), RUBRYKA, STAWKI)

    assert len(wynik.przyjete) == 1, [o.powod for o in wynik.odrzucone]
    assert wynik.przyjete[0]["kwota_pln"] == 12000.0
