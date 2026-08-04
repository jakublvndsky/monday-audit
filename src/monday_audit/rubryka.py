"""Wczytanie i walidacja `rubryka_znalezisk.yaml` (etap 3.9).

Rubryka jest jednocześnie specyfikacją, skillem agenta i podstawą evali —
więc jej wczytanie nie jest `yaml.safe_load` i tyle. Plik jest edytowany
ręcznie przez człowieka, a błąd w nim nie może objawić się dopiero jako
finding bez wagi w raporcie u klienta.

**Walidacja jest twarda i dzieje się raz, na starcie.** Sprawdzamy to, czego
nie da się naprawić później:

- każda klasa ma niepustą listę `dowod` — bez tego finding tej klasy nie
  przejdzie walidacji kontraktu (zakaz twardy z CLAUDE.md), więc detektor
  wzbudzałby hipotezy, które z definicji nie mogą się domknąć
- `waga`, `wysilek_naprawy`, `typ_wyceny` i `widocznosc` należą do słowników
  zadeklarowanych w tym samym pliku — literówka w wadze psuje kolejność
  raportu, a ta zastępuje health score
- `budzet_wywolan` jest liczbą całkowitą >= 0 i nie przekracza bezpiecznika
  globalnego, bo suma budżetów klas to realny sufit runu agenta
- identyfikatory klas są unikalne — duplikat cicho przesłaniałby definicję
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SCIEZKA_RUBRYKI = Path("rubryka_znalezisk.yaml")

# Klasy z tym statusem nie mają detektora. `03-build.md` 3.9 mówi wprost:
# „dla każdej klasy z rubryki (poza `status: do_weryfikacji`)". Powód jest
# w rubryce przy AI_UNUSED i w OTWARTE.md O2 — nie wiemy, czy API oddaje
# zużycie kredytów AI, więc sygnał byłby zbudowany na domysle.
STATUS_DO_WERYFIKACJI = "do_weryfikacji"

# Typy wyceny ze słownika rubryki. Nazwane, bo od nich zależy, czy finding
# może nieść kwotę — a wymyślona kwota podważa cały raport.
TYP_OSZCZEDNOSC = "oszczednosc_bezposrednia"
TYP_RYZYKO = "ryzyko"


class RubrykaError(RuntimeError):
    """Rubryka jest niespójna. Run nie startuje."""


@dataclass(frozen=True, slots=True)
class Klasa:
    """Jedna klasa znaleziska. Pola dokładnie te, których używa kod."""

    id: str
    nazwa: str
    sygnal: str
    budzet_wywolan: int
    waga: str
    wysilek_naprawy: str
    typ_wyceny: str
    widocznosc: str
    dowod: tuple[str, ...]
    warunki_odrzucenia: tuple[str, ...]
    rola_agenta: str
    status: str | None
    # Wzór wyceny i zmienne, które trzeba dostać od klienta. Do 2026-08-04
    # rubryka ich NIE wczytywała, więc agent nigdy nie dostał wzoru i nie miał
    # czym policzyć kwoty — wszystkie findingi wychodziły z `kwota_pln: null`
    # także tam, gdzie kwota była przewidziana.
    wzor: str | None
    zmienne_od_klienta: tuple[str, ...]

    @property
    def ma_detektor(self) -> bool:
        return self.status != STATUS_DO_WERYFIKACJI

    @property
    def ma_wycene(self) -> bool:
        """Czy dla tej klasy wolno w ogóle podać kwotę."""
        return self.typ_wyceny == TYP_OSZCZEDNOSC and bool(self.wzor)


@dataclass(frozen=True, slots=True)
class Rubryka:
    wersja: str
    klasy: tuple[Klasa, ...]
    maks_wywolan_na_run: int

    def __post_init__(self) -> None:
        if not self.klasy:
            raise RubrykaError("rubryka bez klas")

    @property
    def po_id(self) -> dict[str, Klasa]:
        return {k.id: k for k in self.klasy}

    def do_detekcji(self) -> tuple[Klasa, ...]:
        """Klasy, dla których 3.9 ma zbudować sygnał wzbudzający."""
        return tuple(k for k in self.klasy if k.ma_detektor)

    def budzet(self, klasa_id: str) -> int:
        try:
            return self.po_id[klasa_id].budzet_wywolan
        except KeyError:
            raise RubrykaError(f"nieznana klasa {klasa_id}") from None

    def suma_budzetow(self) -> int:
        """Ile wywołań zamówiłby agent, gdyby każda klasa wzbudziła się raz.

        Nie jest to prognoza kosztu runu (hipotez może być wiele na klasę),
        tylko dolna granica sensowności bezpiecznika globalnego.
        """
        return sum(k.budzet_wywolan for k in self.do_detekcji())


def _wymagane(surowa: dict[str, Any], pole: str, gdzie: str) -> Any:
    if pole not in surowa or surowa[pole] in (None, "", [], {}):
        raise RubrykaError(f"{gdzie}: brak wymaganego pola `{pole}`")
    return surowa[pole]


def _ze_slownika(wartosc: Any, dozwolone: list[str], pole: str, gdzie: str) -> str:
    tekst = str(wartosc)
    if tekst not in dozwolone:
        raise RubrykaError(f"{gdzie}: `{pole}` = {tekst!r} nie jest w słowniku {dozwolone}")
    return tekst


def _klasa(surowa: dict[str, Any], slowniki: dict[str, list[str]], maks: int) -> Klasa:
    identyfikator = str(_wymagane(surowa, "id", "klasa bez id"))
    gdzie = f"klasa {identyfikator}"

    dowod = _wymagane(surowa, "dowod", gdzie)
    if not isinstance(dowod, list):
        raise RubrykaError(f"{gdzie}: `dowod` musi być listą")

    budzet = surowa.get("budzet_wywolan")
    if not isinstance(budzet, int) or isinstance(budzet, bool) or budzet < 0:
        raise RubrykaError(f"{gdzie}: `budzet_wywolan` musi być liczbą całkowitą >= 0")
    if budzet > maks:
        raise RubrykaError(
            f"{gdzie}: `budzet_wywolan` {budzet} przekracza bezpiecznik globalny {maks}"
        )

    # Spójność wyceny. Klasa obiecująca oszczędność bez wzoru to kwota,
    # której nikt nie umie policzyć; wzór przy `ryzyko` to zaproszenie
    # do podania kwoty tam, gdzie rubryka jej zabrania.
    typ = str(surowa.get("typ_wyceny") or "")
    ma_wzor = bool(surowa.get("wzor"))
    if typ == TYP_OSZCZEDNOSC and not ma_wzor:
        raise RubrykaError(
            f"{gdzie}: `typ_wyceny: {TYP_OSZCZEDNOSC}` wymaga `wzor` — inaczej nie ma "
            f"z czego policzyć kwoty"
        )
    if typ == TYP_RYZYKO and ma_wzor:
        raise RubrykaError(
            f"{gdzie}: `typ_wyceny: {TYP_RYZYKO}` nie może mieć `wzor` — ta klasa "
            f"świadomie nie podaje kwoty"
        )

    return Klasa(
        id=identyfikator,
        nazwa=str(_wymagane(surowa, "nazwa", gdzie)),
        sygnal=str(_wymagane(surowa, "sygnal", gdzie)),
        budzet_wywolan=budzet,
        waga=_ze_slownika(_wymagane(surowa, "waga", gdzie), slowniki["waga"], "waga", gdzie),
        wysilek_naprawy=_ze_slownika(
            _wymagane(surowa, "wysilek_naprawy", gdzie),
            slowniki["wysilek_naprawy"],
            "wysilek_naprawy",
            gdzie,
        ),
        typ_wyceny=_ze_slownika(
            _wymagane(surowa, "typ_wyceny", gdzie),
            slowniki["typ_wyceny"],
            "typ_wyceny",
            gdzie,
        ),
        widocznosc=_ze_slownika(
            _wymagane(surowa, "widocznosc", gdzie),
            slowniki["widocznosc"],
            "widocznosc",
            gdzie,
        ),
        dowod=tuple(str(d) for d in dowod),
        warunki_odrzucenia=tuple(str(w) for w in (surowa.get("warunki_odrzucenia") or [])),
        rola_agenta=str(surowa.get("rola_agenta") or "brak"),
        status=str(surowa["status"]) if surowa.get("status") else None,
        wzor=str(surowa["wzor"]) if surowa.get("wzor") else None,
        zmienne_od_klienta=tuple(str(z) for z in (surowa.get("zmienne_od_klienta") or [])),
    )


def wczytaj_rubryke(sciezka: Path = SCIEZKA_RUBRYKI) -> Rubryka:
    """Czyta i waliduje rubrykę. Każdy błąd przerywa, żaden nie jest ostrzeżeniem."""
    if not sciezka.is_file():
        raise RubrykaError(f"nie ma pliku rubryki: {sciezka.resolve()}")

    surowa = yaml.safe_load(sciezka.read_text(encoding="utf-8"))
    if not isinstance(surowa, dict):
        raise RubrykaError(f"{sciezka}: korzeń YAML-a nie jest mapą")

    wersja = str(_wymagane(surowa, "wersja", str(sciezka)))
    slowniki = surowa.get("slowniki") or {}
    for nazwa in ("waga", "wysilek_naprawy", "typ_wyceny", "widocznosc"):
        if not isinstance(slowniki.get(nazwa), list):
            raise RubrykaError(f"{sciezka}: brak słownika `{nazwa}`")

    reguly = surowa.get("reguly") or {}
    bezpiecznik = (reguly.get("bezpiecznik_globalny") or {}).get("max_wywolan_na_run")
    if not isinstance(bezpiecznik, int) or bezpiecznik <= 0:
        raise RubrykaError(
            f"{sciezka}: `reguly.bezpiecznik_globalny.max_wywolan_na_run` musi być liczbą > 0"
        )

    surowe_klasy = _wymagane(surowa, "klasy", str(sciezka))
    if not isinstance(surowe_klasy, list):
        raise RubrykaError(f"{sciezka}: `klasy` musi być listą")

    klasy = tuple(_klasa(k, slowniki, bezpiecznik) for k in surowe_klasy)

    identyfikatory = [k.id for k in klasy]
    duplikaty = sorted({i for i in identyfikatory if identyfikatory.count(i) > 1})
    if duplikaty:
        raise RubrykaError(f"{sciezka}: zduplikowane id klas: {', '.join(duplikaty)}")

    rubryka = Rubryka(wersja=wersja, klasy=klasy, maks_wywolan_na_run=bezpiecznik)
    logger.info(
        "rubryka %s: %d klas, %d z detektorem, suma budżetów %d z %d",
        rubryka.wersja,
        len(rubryka.klasy),
        len(rubryka.do_detekcji()),
        rubryka.suma_budzetow(),
        rubryka.maks_wywolan_na_run,
    )
    return rubryka
