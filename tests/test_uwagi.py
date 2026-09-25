"""Uwaga krytyczna: jedna kategoria, dowód nadal obowiązkowy (faza 5b-1).

Najważniejszy test w tym pliku to ten, który pilnuje, że uproszczenie **nie
rozluźniło** zakazu twardego. Z findingu znika pięć pól oceniających, ale
`dowod` nie jest jednym z nich — i gdyby po drodze przestał być egzekwowany,
cała wiarygodność raportu padłaby po cichu.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from monday_audit.dowod import KontraktError
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.uwagi import POLA_UWAGI, waliduj_uwagi

RUBRYKA = wczytaj_rubryke()

# `ZOMBIE_ACCOUNT` — klasa z dowodem, którego wymagania są krótkie i stabilne.
#
# `last_activity` z DATĄ, nie `None`. Puste pole dowodu jest odrzucane i to jest
# poprawne: „nie wiem, kiedy ostatnio pracował" nie jest dowodem, że nie pracuje.
# Wyjątek „cisza jest dowodem" obejmuje wyłącznie pola ROZKŁADU, nie daty.
DOWOD_ZOMBIE = {
    "user_hash": "a1b2c3",
    "kind": "member",
    "status": "ACTIVE",
    "last_activity": "2025-01-15T10:00:00Z",
    "obecnosc_w_logach": False,
    "plan_tier": "enterprise",
}


def _uwaga(**nadpisz: Any) -> dict[str, Any]:
    baza = {
        "klasa_id": "ZOMBIE_ACCOUNT",
        "opis": "Konto zajmuje płatne miejsce i nie wykazuje aktywności.",
        "rekomendacja": "Potwierdzić u właściciela, czy osoba nadal pracuje.",
        "dowod": dict(DOWOD_ZOMBIE),
    }
    baza.update(nadpisz)
    return baza


# ── zakaz twardy przeżył uproszczenie ────────────────────────────────────


def test_uwaga_bez_dowodu_nie_przechodzi() -> None:
    """Zakaz twardy z CLAUDE.md. Uproszczenie zabrało pięć pól oceniających,
    ale nie to."""
    wynik = waliduj_uwagi({"uwagi": [_uwaga(dowod={})]}, RUBRYKA)

    assert not wynik.przyjete
    assert wynik.odrzucone[0].regula == "dowod pusty albo nie jest obiektem"


def test_dowod_niepelny_nie_przechodzi() -> None:
    """Wymagania dowodu bierzemy Z RUBRYKI, nie ze stałej listy — dlatego
    uwaga z połową pól odpada, choć strukturalnie jest poprawna."""
    niepelny = {"user_hash": "a1b2c3"}

    wynik = waliduj_uwagi({"uwagi": [_uwaga(dowod=niepelny)]}, RUBRYKA)

    assert wynik.odrzucone[0].regula == "dowod nie pokrywa pol wymaganych przez klase"


def test_regula_dowodu_jest_wspoldzielona_ze_stara_sciezka() -> None:
    """Nie kopia, tylko ta sama funkcja. Kopia rozjechałaby się przy pierwszej
    zmianie — dokładnie jak literał `personal_agent_member` (O44)."""
    import inspect

    from monday_audit import kontrakt, uwagi

    assert uwagi.sprawdz_dowod is kontrakt.sprawdz_dowod
    assert "sprawdz_dowod" in inspect.getsource(uwagi._sprawdz_uwage)


# ── pola oceniające faktycznie zniknęły ──────────────────────────────────


def test_uwaga_nie_potrzebuje_wagi_ani_kwoty() -> None:
    """Sedno uproszczenia: cztery pola zamiast dziewięciu."""
    assert POLA_UWAGI == ("klasa_id", "opis", "rekomendacja", "dowod")

    wynik = waliduj_uwagi({"uwagi": [_uwaga()]}, RUBRYKA)

    assert len(wynik.przyjete) == 1


def test_dodatkowe_pola_nie_przeszkadzaja() -> None:
    """Model może dopisać coś od siebie; walidacja pilnuje minimum, nie
    maksimum. Odrzucanie nadmiaru karałoby za dokładność."""
    wynik = waliduj_uwagi({"uwagi": [_uwaga(zrodlo="detektor")]}, RUBRYKA)

    assert len(wynik.przyjete) == 1


# ── pochodzenie zostaje, bo bez niego nie ma czego sprawdzać ─────────────


def test_nieznana_klasa_odpada() -> None:
    """`klasa_id` przestało być kategorią w raporcie, ale zostało
    POCHODZENIEM — mówi, jakich faktów wymaga dowód."""
    wynik = waliduj_uwagi({"uwagi": [_uwaga(klasa_id="WYMYSLONA")]}, RUBRYKA)

    assert wynik.odrzucone[0].regula == "klasa_id nie istnieje w rubryce"


# ── struktura ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pole", ["opis", "rekomendacja"])
def test_pusty_tekst_odpada(pole: str) -> None:
    wynik = waliduj_uwagi({"uwagi": [_uwaga(**{pole: "   "})]}, RUBRYKA)

    assert wynik.odrzucone[0].regula == "opis albo rekomendacja puste"


@pytest.mark.parametrize(
    "odpowiedz",
    ["nie obiekt", {"cos": []}, {"uwagi": "nie lista"}],
)
def test_zly_korzen_przerywa_zamiast_dawac_zero_uwag(odpowiedz: Any) -> None:
    """Jedna zła uwaga to normalny wynik. Odpowiedź bez struktury znaczy, że
    sesja poszła nie tak — i to trzeba ZOBACZYĆ, a nie policzyć jako „czysto"."""
    with pytest.raises(KontraktError):
        waliduj_uwagi(odpowiedz, RUBRYKA)


def test_odrzucenie_krzyczy_do_logu(caplog: Any) -> None:
    """Odsetek odrzuconych to metryka jakości, nie szum. Cicha porażka
    walidacji uczy ignorować walidację."""
    with caplog.at_level(logging.WARNING):
        waliduj_uwagi({"uwagi": [_uwaga(dowod={})]}, RUBRYKA)

    assert "uwaga odrzucona" in caplog.text


# ── pominięte to nie porażki ─────────────────────────────────────────────


def test_pominiete_hipotezy_sa_zachowane() -> None:
    """To ta połowa pracy, za którą płacimy, żeby dowiedzieć się, że czegoś
    NIE MA. Bez niej raport nie odróżnia „sprawdzone i czyste" od
    „niesprawdzone"."""
    odpowiedz = {
        "uwagi": [_uwaga()],
        "pominiete": [{"klasa_id": "BOARD_GHOST", "powod": "tablica ma świeże wpisy"}],
    }

    wynik = waliduj_uwagi(odpowiedz, RUBRYKA)

    assert len(wynik.pominiete) == 1
    assert wynik.odsetek_odrzuconych == 0.0


def test_opis_wyniku_liczy_wszystkie_trzy_kubelki() -> None:
    odpowiedz = {
        "uwagi": [_uwaga(), _uwaga(dowod={})],
        "pominiete": [{"klasa_id": "BOARD_GHOST"}],
    }

    opis = waliduj_uwagi(odpowiedz, RUBRYKA).opis()

    assert "1 przyjęte" in opis
    assert "1 odrzucone" in opis
    assert "pominiętych przez model: 1" in opis


# ── pola listowe dowodu (zmierzone 2026-09-23) ───────────────────────────


def _gosc(tablice: object) -> dict[str, object]:
    return {
        "klasa_id": "GUEST_SPRAWL",
        "opis": "13 gości wobec 8 członków.",
        "rekomendacja": "Przejrzeć gości.",
        "dowod": {
            "liczba_guest": 13,
            "liczba_members": 8,
            "guest_hash": ["0240dd46f87f3dd2"],
            "tablice_dostepne": tablice,
        },
    }


@pytest.mark.parametrize(
    "tablice",
    [
        # Dosłownie to, co model wpisał na runie `analiza-20260923T122843Z`.
        "dla wszystkich 11 nieaktywnych gości lista pusta w danych; zweryfikować",
        {"0240dd46f87f3dd2": [], "0242d176469cbe1d": []},
        42,
    ],
)
def test_opis_braku_danych_w_polu_listowym_nie_przechodzi(tablice: object) -> None:
    """Obejście O31: napis niepusty przechodził jako „wypełnione pole". Opis
    braku danych nie jest daną, a mapa samych pustych list to ta sama pustka."""
    from monday_audit.rubryka import wczytaj_rubryke

    wynik = waliduj_uwagi({"uwagi": [_gosc(tablice)]}, wczytaj_rubryke())

    assert not wynik.przyjete
    assert "nie są niepustą listą: tablice_dostepne" in wynik.odrzucone[0].powod


@pytest.mark.parametrize(
    "tablice",
    [["Onboarding klienta"], {"0240dd46f87f3dd2": ["Onboarding klienta"], "0242d176469cbe1d": []}],
)
def test_prawdziwa_lista_albo_mapa_przechodzi(tablice: object) -> None:
    from monday_audit.rubryka import wczytaj_rubryke

    wynik = waliduj_uwagi({"uwagi": [_gosc(tablice)]}, wczytaj_rubryke())

    assert len(wynik.przyjete) == 1
