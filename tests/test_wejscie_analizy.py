"""Bramka i dokument dla modelu w nowej ścieżce (plan, faza 5a).

Najważniejsze testy w tym pliku nie dotyczą składania dokumentu, tylko dwóch
rzeczy, których brak byłby cichy:

- że **zastrzeżenia docierają razem z liczbami**, bo model, który dostaje
  liczbę w jednym miejscu, a jej ograniczenie w drugim, napisze uwagę
  krytyczną opartą na liczbie, o której nie wie, że jest niepełna,
- że **przycięcie nie gubi tablicy z O47** — a gubiłoby, gdyby sortować po
  tym, co faktycznie pobrano.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from monday_audit.wejscie_analizy import TABLIC_DO_MODELU, zbuduj_wejscie

INWENTARZ: dict[str, Any] = {
    "konto_nazwa": "CXLABS",
    "licencja": {"tier": "enterprise", "max_users": 100},
    "workspacow": 136,
    "po_produktach": {"core": 66, "crm": 40},
    "tablic_aktywnych": 1315,
    "uzytkownikow": 100,
    "gosci": 12,
    "agentow_ai": 4,
    "podgladajacych": 0,
    "zastrzezenia": ["gości nie widać w subscribers (O45)"],
}


def _wejscie(**nadpisz: Any) -> Any:
    return zbuduj_wejscie(inwentarz={**INWENTARZ, **nadpisz.pop("inwentarz", {})}, **nadpisz)


# ── zastrzeżenia idą razem z liczbami ────────────────────────────────────


def test_zastrzezenia_ze_wszystkich_warstw_ladują_w_jednej_liscie() -> None:
    """Dziś są rozsypane po czterech obiektach. Model dostaje jedną listę."""
    wynik = _wejscie(
        tablice={"tablic": 1315, "zastrzezenia": ["nieuruchomionych automatyzacji nie widać"]},
        itemy={"itemow_razem": 25159, "zastrzezenia": ["12 tablic nie oddało itemów (O47)"]},
        zestawienia={"zestawienia": [], "zastrzezenia": ["reguła: rozpoznajemy etapy KOŃCOWE"]},
    )

    uwagi = wynik.dokument["zastrzezenia"]

    assert len(uwagi) == 4
    assert any(u.startswith("[inwentarz]") for u in uwagi)
    assert any(u.startswith("[tablice]") for u in uwagi)
    assert any(u.startswith("[itemy]") for u in uwagi)
    assert any(u.startswith("[zestawienia]") for u in uwagi)


def test_zrodlo_zastrzezenia_jest_nazwane() -> None:
    """„12 tablic nie oddało itemów" bez źródła nie mówi, której liczby
    dotyczy — a model ma wskazać dowód, nie zgadywać."""
    wynik = _wejscie(itemy={"itemow_razem": 1, "zastrzezenia": ["coś tam"]})

    assert "[itemy] coś tam" in wynik.dokument["zastrzezenia"]


# ── przycinanie ──────────────────────────────────────────────────────────


def test_przyciecie_sortuje_po_deklarowanych_a_nie_po_pobranych() -> None:
    """Tablica z O47 deklaruje 7076 itemów i oddaje zero — i to właśnie ona
    jest ciekawa. Sortowanie po `pobranych` zepchnęłoby ją na koniec i model
    nigdy by o niej nie napisał."""
    itemy = {
        "itemow_razem": 7176,
        "tablice": [
            {"board_id": "1", "nazwa": "mała", "itemow": 100, "pobranych": 100},
            {"board_id": "2", "nazwa": "👤 Leads", "itemow": 7076, "pobranych": 0},
        ],
    }

    najwieksze = _wejscie(itemy=itemy).dokument["itemy"]["najwieksze_tablice"]

    assert najwieksze[0]["nazwa"] == "👤 Leads"


def test_przyciecie_ma_sufit() -> None:
    itemy = {
        "itemow_razem": 0,
        "tablice": [{"board_id": str(i), "itemow": i} for i in range(100)],
    }

    assert len(_wejscie(itemy=itemy).dokument["itemy"]["najwieksze_tablice"]) == TABLIC_DO_MODELU


# ── bramka PII ───────────────────────────────────────────────────────────


def test_adres_w_nazwie_tablicy_nie_dociera_do_modelu() -> None:
    """Nazwę tablicy pisze klient. Może w niej wpisać cokolwiek — i wpisuje."""
    itemy = {
        "itemow_razem": 5,
        "tablice": [{"board_id": "1", "nazwa": "Kontakt biuro@klient.test", "itemow": 5}],
    }

    wynik = _wejscie(itemy=itemy)

    nazwa = wynik.dokument["itemy"]["najwieksze_tablice"][0]["nazwa"]
    assert nazwa == "Kontakt [E-MAIL]"
    assert not wynik.czyste
    assert wynik.trafienia_maskowania["email"] == 1


def test_adres_w_nazwie_grupy_nie_dociera_do_modelu() -> None:
    """Nazwa grupy i etykieta etapu siedzą w `rozklad` jako KLUCZE. Do review
    2026-09-23 bramka ich nie widziała: mail w nazwie grupy szedł do modelu
    wprost, a licznik trafień pokazywał zero — czyli łamał zakaz twardy
    i jeszcze meldował, że wszystko czyste."""
    itemy = {
        "itemow_razem": 20,
        "tablice": [
            {
                "board_id": "1",
                "nazwa": "Leady",
                "itemow": 20,
                "rozklad": {"Leady od jan.kowalski@firma.test": 12, "tel +48 501 234 567": 8},
            }
        ],
    }

    wynik = _wejscie(itemy=itemy)

    rozklad = wynik.dokument["itemy"]["najwieksze_tablice"][0]["rozklad"]
    assert rozklad == {"Leady od [E-MAIL]": 12, "tel [TELEFON]": 8}
    assert wynik.trafienia_maskowania == {"email": 1, "telefon": 1}
    assert "jan.kowalski" not in json.dumps(wynik.dokument, ensure_ascii=False)


def test_maskowanie_jest_widoczne_w_dokumencie_a_nie_tylko_w_logu() -> None:
    """Model ma wiedzieć, że patrzy na dane po redakcji — inaczej napisze
    uwagę o tablicy „[E-MAIL]" i nie zrozumie, skąd ta nazwa."""
    itemy = {"itemow_razem": 1, "tablice": [{"board_id": "1", "nazwa": "a@b.test", "itemow": 1}]}

    dokument = _wejscie(itemy=itemy).dokument

    assert dokument["maskowanie"]["trafien"] == 1
    assert dokument["maskowanie"]["pola"]


def test_trafienie_krzyczy_do_logu(caplog: Any) -> None:
    itemy = {"itemow_razem": 1, "tablice": [{"board_id": "1", "nazwa": "a@b.test", "itemow": 1}]}

    with caplog.at_level(logging.WARNING):
        _wejscie(itemy=itemy)

    assert "treść pisana przez klienta" in caplog.text
    assert "a@b.test" not in caplog.text


def test_czyste_wejscie_nie_ostrzega(caplog: Any) -> None:
    with caplog.at_level(logging.WARNING):
        wynik = _wejscie()

    assert wynik.czyste
    assert not caplog.text


# ── dokument bez opcjonalnych warstw ─────────────────────────────────────


def test_sam_inwentarz_wystarcza() -> None:
    """Pierwszy ekran user story to same kafelki — analiza ma działać i bez
    itemów, tylko z mniejszą liczbą rzeczy do powiedzenia."""
    dokument = _wejscie().dokument

    assert dokument["konto"]["workspacow"] == 136
    assert "itemy" not in dokument
    assert "tablice" not in dokument
