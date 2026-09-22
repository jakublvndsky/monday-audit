"""Jedyne wejście modelu w nowej ścieżce (plan, faza 5a).

Stara ścieżka ma bramkę: `przebieg.py` puszcza snapshot przez `zredaguj_pii`
i `waliduj_brak_pii`, zanim cokolwiek zobaczy model. **Nowa ścieżka — inwentarz,
przegląd tablic, itemy, zestawienia — nie miała jej wcale.** Ten moduł jest tą
bramką i jednocześnie jedynym miejscem, w którym powstaje dokument dla modelu.

## Trzy rzeczy naraz, i to nie przypadek

1. **Składa** cztery warstwy agregatów w jeden dokument,
2. **Przycina** go do rozmiaru, który ma sens w kontekście modelu,
3. **Maskuje** — i to jest powód, dla którego punkty 1 i 2 są tutaj, a nie
   rozsypane po CLI. Bramka, którą da się obejść, nie jest bramką. Jedna droga
   do modelu znaczy jedno miejsce do sprawdzenia.

## Co bramka łapie, a czego NIE

Łapie wzorcem: adresy e-mail, telefony, numery kont — czyli to, co mogło wejść
przez treść pisaną przez klienta (nazwa tablicy, nazwa grupy, etykieta etapu).

**Nie łapie imion i nazwisk** i to jest świadoma dziura, nie niedopatrzenie.
Stara ścieżka radziła sobie z nimi, bo miała tabelę mapowania — pobierała
`name` i `email` użytkowników, żeby móc je potem podmienić. Nowa ścieżka
**celowo ich nie pobiera** (faza 2a: zapytanie o użytkowników nie prosi o `name`
ani `email`, bo najtańszym sposobem na niewyciekanie danych jest ich nie
pobrać). Cena tej decyzji jest dokładnie tutaj: tablica nazwana „Jan Kowalski —
projekty" przejdzie, bo nie mamy z czym jej porównać.

To jest rozstrzygnięcie do podjęcia przez człowieka, nie do zamiecenia:
albo przyjmujemy ryzyko, albo wracamy po listę nazwisk i tracimy zysk z 2a.
Zapisane w `docs/OTWARTE.md`.

## Dlaczego maskowanie tu NIE przerywa

Trafienie wzorca jest głośne (ostrzeżenie w logu, licznik w dokumencie), ale
nie wywraca analizy — bo zamaskowana wartość **i tak nie dociera do modelu**,
czyli zakaz z `CLAUDE.md` jest spełniony przez samo maskowanie. Przerywa
wyłącznie `MaskowanieError`, czyli sytuacja, w której warstwa nie umie czegoś
przetworzyć i nie wie, co przepuszcza. Ta sama zasada, co przy wysyłce
trace'ów: zawodzimy zamknięte tam, gdzie nie wiemy, a nie tam, gdzie wiemy.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from monday_audit.maskowanie import zamaskuj

logger = logging.getLogger(__name__)

# Ile tablic wchodzi do dokumentu. Model nie potrzebuje wszystkich 954 —
# potrzebuje największych i najbardziej aktywnych plus agregaty, które i tak
# opisują całość. Reszta to koszt tokenów bez treści.
TABLIC_DO_MODELU = 30
AUTOMATYZACJI_DO_MODELU = 25


@dataclass(frozen=True)
class WejscieAnalizy:
    """Dokument dla modelu plus to, co o nim wiemy."""

    dokument: dict[str, Any]
    trafienia_maskowania: Counter[str] = field(default_factory=Counter)
    pola_z_trafieniami: tuple[str, ...] = ()

    @property
    def czyste(self) -> bool:
        return not self.trafienia_maskowania


def _przytnij_tablice(itemy: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Największe tablice, bo o nich jest co powiedzieć.

    Sortowanie po `itemow`, nie po `pobranych`: tablica z O47 deklaruje 7076
    i oddaje zero, a to właśnie ona jest ciekawa. Gdyby sortować po pobranych,
    zniknęłaby z dokumentu i model nigdy by o niej nie napisał.
    """
    if not itemy:
        return []
    tablice = list(itemy.get("tablice") or [])
    tablice.sort(key=lambda t: int(t.get("itemow") or 0), reverse=True)
    return tablice[:TABLIC_DO_MODELU]


def zbuduj_wejscie(
    *,
    inwentarz: dict[str, Any],
    tablice: dict[str, Any] | None = None,
    itemy: dict[str, Any] | None = None,
    zestawienia: dict[str, Any] | None = None,
) -> WejscieAnalizy:
    """Cztery warstwy agregatów → jeden zamaskowany dokument dla modelu.

    Przyjmuje słowniki `do_json()`, a nie obiekty, celowo: ten moduł nie ma
    znać kształtu czterech warstw ani zmieniać się, gdy któraś urośnie o pole.
    Zna tylko to, co sam wyciąga.
    """
    # ── zastrzeżenia w JEDNYM miejscu ──────────────────────────────────────
    #
    # Dziś są rozsypane po czterech obiektach i każdy wypisuje swoje osobno.
    # Model, który dostaje liczby w jednym miejscu, a ich ograniczenia
    # w czterech, napisze uwagę krytyczną opartą na liczbie, o której nie
    # wie, że jest niepełna. Stąd jedna lista i nazwa źródła przy każdej.
    zastrzezenia: list[str] = []
    for nazwa, warstwa in (
        ("inwentarz", inwentarz),
        ("tablice", tablice),
        ("itemy", itemy),
        ("zestawienia", zestawienia),
    ):
        for uwaga in (warstwa or {}).get("zastrzezenia") or []:
            zastrzezenia.append(f"[{nazwa}] {uwaga}")

    surowy: dict[str, Any] = {
        "konto": {
            "nazwa": inwentarz.get("konto_nazwa"),
            "licencja": inwentarz.get("licencja"),
            "workspacow": inwentarz.get("workspacow"),
            "po_produktach": inwentarz.get("po_produktach"),
            "tablic_aktywnych": inwentarz.get("tablic_aktywnych"),
            "uzytkownikow": inwentarz.get("uzytkownikow"),
            "gosci": inwentarz.get("gosci"),
            "agentow_ai": inwentarz.get("agentow_ai"),
            "podgladajacych": inwentarz.get("podgladajacych"),
        },
        "zastrzezenia": zastrzezenia,
    }

    if tablice:
        surowy["tablice"] = {
            "aktywnych": tablice.get("tablic"),
            "po_rodzaju": tablice.get("po_rodzaju"),
            "userow_srednio": tablice.get("userow_srednio"),
            "userow_mediana": tablice.get("userow_mediana"),
            "aktywnosc": tablice.get("aktywnosc"),
            "okno_dni": tablice.get("okno_dni"),
            # Automatyzacje, które FAKTYCZNIE się uruchamiały. O41 mówi, że tych
            # nigdy nieuruchomionych nie widzimy — i to zastrzeżenie idzie wyżej,
            # żeby model nie napisał „macie 23 automatyzacje".
            "zywe_automatyzacje": (tablice.get("zywe_automatyzacje") or [])[
                :AUTOMATYZACJI_DO_MODELU
            ],
            "uruchomien_razem": tablice.get("uruchomien_razem"),
        }

    if itemy:
        surowy["itemy"] = {
            "razem": itemy.get("itemow_razem"),
            "per_produkt": itemy.get("itemow_per_produkt"),
            "najwieksze_tablice": _przytnij_tablice(itemy),
        }

    if zestawienia:
        surowy["zestawienia"] = zestawienia.get("zestawienia")

    # ── BRAMKA ─────────────────────────────────────────────────────────────
    #
    # `wpisy` puste świadomie: nowa ścieżka nie ma tabeli mapowania, bo nie
    # pobiera nazwisk. Zostaje maskowanie wzorcem — patrz docstring modułu.
    zamaskowane = zamaskuj(surowy)
    if not zamaskowane.czyste:
        logger.warning(
            "wejście analizy: %s — treść pisana przez klienta (nazwy tablic, grup, "
            "etykiety etapów) niosła dane kontaktowe; do modelu idą zamaskowane",
            zamaskowane.podsumowanie(),
        )

    dokument = dict(zamaskowane.dane)
    # Licznik JAWNIE w dokumencie, nie tylko w logu. Model ma wiedzieć, że
    # patrzy na dane po redakcji — inaczej napisze uwagę o tablicy „[E-MAIL]"
    # i nie zrozumie, dlaczego tak się nazywa.
    dokument["maskowanie"] = {
        "trafien": zamaskowane.ile,
        "pola": list(zamaskowane.sciezki),
    }

    return WejscieAnalizy(
        dokument=dokument,
        trafienia_maskowania=zamaskowane.trafienia,
        pola_z_trafieniami=zamaskowane.sciezki,
    )
