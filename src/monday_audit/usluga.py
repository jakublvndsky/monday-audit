"""Funkcje wejściowe pakietu dla portalu (plan, faza 6, wariant A).

Decyzja Kuby z 2026-09-23: kod audytu trafia do repo portalu jako importowany
pakiet. Ekrany, sesje, konta i magazyn klucza pisze portal — ten moduł jest
JEDYNYM miejscem, które portal ma wołać. Wszystko inne w `monday_audit` to
szczegół implementacji, który może się zmienić bez ostrzeżenia.

Dwie funkcje, dwa kroki z user story:

1. `przeglad_konta` — „Analizuj moje środowisko": sześć kafelków (krok 6-1),
2. `analiza_konta` — „Chcę wykonać analizę" (krok 6-2, jeszcze nie ma).

## Umowa, której portal może się trzymać

* **Klucz monday przychodzi parametrem** i żyje tylko w pamięci wywołania —
  nie trafia do logów, argv ani na dysk (D12).
* **Nic nie jest zapisywane.** `przeglad_konta` nie dotyka żadnej bazy.
* **Wynik ma `do_json()`** z polami, które są kontraktem. Typy dla portalu
  generujemy z dataclass (krok 6-4), nie piszemy ręcznie.
* **Błędy po stronie klienta konta** (token bez admina, zły klucz) wracają
  jako `UslugaError` z komunikatem, który można pokazać człowiekowi — bez
  treści odpowiedzi API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from monday_audit.inwentarz import Inwentarz, zbuduj_inwentarz
from monday_audit.klient import LimitDziennyError, MondayClient, MondayError
from monday_audit.konto import Zakres, ZakresError, rozpoznaj_konto
from monday_audit.podglad_zakresu import RejestrPodgladu

logger = logging.getLogger(__name__)


class UslugaError(RuntimeError):
    """Błąd, który portal pokazuje człowiekowi. Komunikat bez danych z API."""


@dataclass(frozen=True, slots=True)
class Kafelek:
    """Jeden kafelek z punktu 2 wytycznych.

    `klucz` jest stały i służy portalowi do wyboru ikony i trasy — w fazie 7
    kliknięcie w kafelek przenosi do pogłębionego raportu tej kategorii.
    `szczegoly` to rozbicie, które kafelek może pokazać pod liczbą.
    """

    klucz: str
    etykieta: str
    wartosc: int | str | None
    szczegoly: dict[str, Any] = field(default_factory=dict)

    def do_json(self) -> dict[str, Any]:
        return {
            "klucz": self.klucz,
            "etykieta": self.etykieta,
            "wartosc": self.wartosc,
            "szczegoly": dict(self.szczegoly),
        }


@dataclass(frozen=True, slots=True)
class PrzegladKonta:
    """Wynik kroku 1. Zero modelu, zero itemów, zero zapisu."""

    konto_nazwa: str
    kafelki: tuple[Kafelek, ...]
    # Ile wywołań z DZIENNEGO limitu konta klienta to kosztowało — portal
    # może to pokazać obok przycisku kroku 2, który kosztuje więcej.
    wywolan: int
    # Czego te liczby nie obejmują. Pokazywać razem z kafelkami, nie w przypisie.
    zastrzezenia: tuple[str, ...] = ()

    def do_json(self) -> dict[str, Any]:
        return {
            "konto_nazwa": self.konto_nazwa,
            "kafelki": [k.do_json() for k in self.kafelki],
            "wywolan": self.wywolan,
            "zastrzezenia": list(self.zastrzezenia),
        }


# Kolejność jak w punkcie 2 wytycznych. Stała, bo portal buduje na niej układ.
KAFELKI = ("workspace", "tablice", "uzytkownicy", "goscie", "agenci_ai", "licencja")


def kafelki_z_inwentarza(inwentarz: Inwentarz) -> tuple[Kafelek, ...]:
    """`Inwentarz` → sześć kafelków. Czysta funkcja, testowalna bez sieci."""
    return (
        Kafelek(
            "workspace",
            "Workspace'y",
            inwentarz.workspacow,
            {"po_produktach": dict(inwentarz.po_produktach)},
        ),
        Kafelek(
            "tablice",
            "Tablice",
            inwentarz.tablic_aktywnych,
            {
                # Kafelek liczy AKTYWNE tablice typu `board`; reszta obok, żeby
                # „3268 obiektów, w tym 1206 w koszu" nie znikło (faza 2a).
                "razem_obiektow": inwentarz.tablic_razem,
                "po_stanie": dict(inwentarz.tablic_po_stanie),
                "po_typie": dict(inwentarz.tablic_po_typie),
            },
        ),
        Kafelek(
            "uzytkownicy",
            "Użytkownicy",
            inwentarz.uzytkownikow,
            {
                "podgladajacych": inwentarz.podgladajacych,
                "po_rodzajach": dict(inwentarz.po_rodzajach),
            },
        ),
        Kafelek("goscie", "Goście", inwentarz.gosci),
        Kafelek("agenci_ai", "Agenci AI", inwentarz.agentow_ai),
        Kafelek(
            "licencja",
            "Licencja",
            inwentarz.licencja_tier,
            {"okres": inwentarz.licencja_period, "max_uzytkownikow": inwentarz.licencja_max_users},
        ),
    )


async def przeglad_konta(klucz_monday: str) -> PrzegladKonta:
    """Krok 1 — „Analizuj moje środowisko". ~36 wywołań na CXLABS, 0 USD.

    Wymaga tokena z uprawnieniami admina: inwentarz liczony bez admina byłby
    cicho niepełny, więc `rozpoznaj_konto` przerywa, a tu wraca to jako
    `UslugaError` z komunikatem dla człowieka.
    """
    if not klucz_monday or not klucz_monday.strip():
        raise UslugaError("brak klucza API monday")

    rejestr = RejestrPodgladu()
    try:
        async with MondayClient(klucz_monday.strip(), rejestr) as klient:
            konto = await rozpoznaj_konto(klient, Zakres(typ="cale_konto"))
            inwentarz = await zbuduj_inwentarz(klient, konto, rejestr)
    except ZakresError:
        raise UslugaError(
            "Ten klucz nie ma uprawnień administratora konta monday — przegląd całego "
            "konta byłby niepełny. Użyj klucza administratora."
        ) from None
    except LimitDziennyError:
        raise UslugaError(
            "Dzienny limit wywołań API tego konta monday jest wyczerpany. Spróbuj jutro."
        ) from None
    except (MondayError, httpx.HTTPError) as blad:
        # Treść błędu z API zostaje w logu po NASZEJ stronie, nie w komunikacie —
        # bywa fragmentem odpowiedzi.
        logger.warning("przegląd konta nie wyszedł: %s", type(blad).__name__)
        raise UslugaError(
            "Nie udało się odczytać konta tym kluczem. Sprawdź, czy klucz jest poprawny."
        ) from None

    return PrzegladKonta(
        konto_nazwa=inwentarz.konto_nazwa,
        kafelki=kafelki_z_inwentarza(inwentarz),
        wywolan=inwentarz.wywolan,
        zastrzezenia=inwentarz.zastrzezenia,
    )


__all__ = ["KAFELKI", "Kafelek", "PrzegladKonta", "UslugaError", "przeglad_konta"]
