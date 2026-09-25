"""Raport uwag krytycznych Z NAZWISKAMI — oddawany jednorazowo (faza 5c, układ z fazy 7).

Decyzja Kuby z 2026-09-23 (wariant A z D):

* **A** — raport z nazwiskami powstaje W TRAKCIE runu i jest oddany raz.
  Na serwerze nie zostaje jego kopia. Mapowanie osób żyje w bazie w pamięci,
  więc po zamknięciu procesu takiego raportu nie da się już złożyć.
* **D** — to, co przechowujemy (`uwagi_zapisane`), jest zamaskowane.

## Układ (faza 7, projekt z Claude Design, decyzje Kuby z 2026-09-25)

Raport główny to cztery kategorie z rubryki (Workspace, Tablice, Użytkownicy,
Agenci AI). Kliknięcie PRZENOSI do widoku kategorii — w tym samym pliku,
bo dokument ma być jednym plikiem do pobrania. Kategoria bez klasy
z detektorem jest „jeszcze nie mierzona", nie „0 problemów".

Plik jest **wersją dla klienta**: nie niesie kosztu, odrzuconych hipotez ani
identyfikatorów sygnałów — te zostają w `WynikAnalizy` i w widoku zespołu
portalu. Usunięte, nie ukryte: czego nie ma w HTML-u, tego nikt nie wyjmie.

## Dlaczego osobny moduł, a nie `raport.py`

`raport.py` składa raport starej ścieżki (findingi z wagą i kwotą, snapshot
w trwałej bazie). Wspólne zostają: środowisko Jinja z autoescapingiem, marka
i `Deanonimizacja`, jedyne miejsce, które zamienia pseudonimy na nazwiska.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from monday_audit.deanonimizacja import Deanonimizacja
from monday_audit.raport import KATALOG_SZABLONOW, srodowisko, zasob_data_uri
from monday_audit.rubryka import PoleDowodu, Rubryka

if TYPE_CHECKING:
    from monday_audit.analiza import PozaSufitem

logger = logging.getLogger(__name__)

SZABLON_UWAG = "raport_uwag.html.j2"
LOGO_JASNE = "cxlabs-white.png"

# Ile pozycji listy pokazać, zanim pojawi się „i N kolejnych". Grupa duplikatów
# na pełnym CXLABS ma 91 tablic — dowód z 91 nazwami przestaje być dowodem.
POZYCJI_W_LISCIE = 3
# Zdanie „największy problem" na kafelku kategorii — jedno, krótkie.
DLUGOSC_ZDANIA = 180

ZNACZNIK_NIE_ZMIERZONE = "nie_zmierzone"
_DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$")


# ── dowód jako „chipy" ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Chip:
    """Jedno pole dowodu gotowe do pokazania: etykieta i wartość albo powód braku."""

    etykieta: str
    wartosc: str | None = None
    # „Nie zmierzone" to NIE zero ani puste pole — pokazywane osobno, z powodem.
    powod: str | None = None

    @property
    def niezmierzone(self) -> bool:
        return self.powod is not None


def _liczba(wartosc: Any) -> str:
    if isinstance(wartosc, bool):
        return "tak" if wartosc else "nie"
    if isinstance(wartosc, int):
        return f"{wartosc:,}".replace(",", " ")
    if isinstance(wartosc, float):
        return f"{wartosc:,.2f}".replace(",", " ").replace(".", ",")
    return str(wartosc)


def _procent(wartosc: Any) -> str:
    if isinstance(wartosc, (int, float)) and not isinstance(wartosc, bool) and 0 <= wartosc <= 1:
        return f"{round(wartosc * 100)}%"
    return _auto(wartosc, run_at=None, nazwy={})


def _data(wartosc: Any, run_at: str | None) -> str:
    """„2026-05-29 (118 dni przed analizą)" — względne, bo tak czyta się raport."""
    tekst = str(wartosc)
    if not _DATA_ISO.match(tekst):
        return tekst
    dzien = tekst[:10]
    if not run_at:
        return dzien
    try:
        roznica = (datetime.fromisoformat(run_at[:10]) - datetime.fromisoformat(dzien)).days
    except ValueError:
        return dzien
    if roznica <= 0:
        return f"{dzien} (dzień analizy)"
    return f"{dzien} ({roznica} dni przed analizą)"


def _skroc(pozycje: Sequence[str]) -> str:
    if not pozycje:
        return "brak"
    widoczne = list(pozycje[:POZYCJI_W_LISCIE])
    reszta = len(pozycje) - len(widoczne)
    return ", ".join(widoczne) + (f" i {reszta} kolejnych" if reszta else "")


def _tablice(wartosc: Any, nazwy: dict[str, str]) -> str:
    """ID tablic → nazwy ze snapshotu. Model przepisuje ID, człowiek czyta nazwy."""
    identyfikatory = wartosc if isinstance(wartosc, list) else [wartosc]
    return _skroc([nazwy.get(str(i), str(i)) for i in identyfikatory if i is not None])


def _aktywnosc(wartosc: Any) -> str:
    """Mapa tablica → wpisy w oknie. `None` to „bez próbki logu", nie zero."""
    if not isinstance(wartosc, dict) or not wartosc:
        return _auto(wartosc, run_at=None, nazwy={})
    wpisy = list(wartosc.values())
    aktywnych = sum(1 for w in wpisy if isinstance(w, (int, float)) and w > 0)
    bez_probki = sum(1 for w in wpisy if w is None)
    tekst = f"aktywnych {aktywnych} z {len(wpisy)}"
    return tekst + (f", bez próbki logu {bez_probki}" if bez_probki else "")


def _zakres_dat(wartosc: Any, run_at: str | None) -> str:
    if isinstance(wartosc, dict):
        daty = sorted(str(v)[:10] for v in wartosc.values() if v and _DATA_ISO.match(str(v)))
        if not daty:
            return _auto(wartosc, run_at=run_at, nazwy={})
        if daty[0] == daty[-1]:
            return _data(daty[0], run_at)
        return f"od {daty[0]} do {_data(daty[-1], run_at)}"
    return _data(wartosc, run_at)


def _mapa_liczb(wartosc: Any) -> str:
    if not isinstance(wartosc, dict):
        return _auto(wartosc, run_at=None, nazwy={})
    pary = sorted(wartosc.items(), key=lambda p: -p[1] if isinstance(p[1], (int, float)) else 0)
    return _skroc([f"{k}: {_liczba(v)}" for k, v in pary])


def _auto(wartosc: Any, *, run_at: str | None, nazwy: dict[str, str]) -> str:
    """Format zgadnięty z typu — dla pól spoza słownika w rubryce."""
    if wartosc is None:
        return "brak"
    if isinstance(wartosc, (bool, int, float)):
        return _liczba(wartosc)
    if isinstance(wartosc, str):
        return _data(wartosc, run_at) if _DATA_ISO.match(wartosc) else wartosc
    if isinstance(wartosc, list):
        return _skroc([_auto(v, run_at=run_at, nazwy=nazwy) for v in wartosc])
    if isinstance(wartosc, dict):
        return _skroc([f"{k}: {_auto(v, run_at=run_at, nazwy=nazwy)}" for k, v in wartosc.items()])
    return json.dumps(wartosc, ensure_ascii=False)


def chipy_dowodu(
    dowod: dict[str, Any],
    pola: dict[str, PoleDowodu] | Any,
    *,
    run_at: str | None,
    nazwy_tablic: dict[str, str] | None = None,
) -> tuple[Chip, ...]:
    """Dowód (już po deanonimizacji) → chipy w kolejności pól dowodu."""
    nazwy = nazwy_tablic or {}
    wynik: list[Chip] = []
    for klucz, wartosc in dowod.items():
        opis = pola.get(klucz) or PoleDowodu(etykieta=str(klucz).replace("_", " "))
        if opis.format == "pomin":
            continue
        if (
            isinstance(wartosc, dict)
            and set(wartosc) == {ZNACZNIK_NIE_ZMIERZONE}
            and isinstance(wartosc[ZNACZNIK_NIE_ZMIERZONE], str)
        ):
            wynik.append(Chip(opis.etykieta, powod=wartosc[ZNACZNIK_NIE_ZMIERZONE]))
            continue
        formatery = {
            "liczba": lambda v: _liczba(v) if v is not None else "brak",
            "procent": _procent,
            "data": lambda v: _data(v, run_at) if v else "brak",
            "tablice": lambda v: _tablice(v, nazwy),
            "lista": lambda v: (
                _skroc([str(x) for x in v])
                if isinstance(v, list)
                else _auto(v, run_at=run_at, nazwy=nazwy)
            ),
            "aktywnosc": _aktywnosc,
            "zakres_dat": lambda v: _zakres_dat(v, run_at),
            "mapa_liczb": _mapa_liczb,
        }
        formater = formatery.get(opis.format)
        tekst = formater(wartosc) if formater else _auto(wartosc, run_at=run_at, nazwy=nazwy)
        wynik.append(Chip(opis.etykieta, wartosc=tekst))
    return tuple(wynik)


# ── uwagi, grupy, kategorie, pokrycie ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UwagaWRaporcie:
    klasa_id: str
    nazwa_klasy: str
    zrodlo: str
    opis: str
    rekomendacja: str
    dowod: dict[str, Any]
    chipy: tuple[Chip, ...] = ()


@dataclass(frozen=True, slots=True)
class GrupaUwag:
    """Uwagi jednej klasy — sekcja raportu zamiast N osobnych kart.

    Prośba Kuby z 2026-09-23 po pierwszym raporcie: „wszystko rozbite na
    osobne itemy". Osiem martwych kont to JEDEN problem z ośmioma wierszami.
    """

    klasa_id: str
    nazwa_klasy: str
    uwagi: tuple[UwagaWRaporcie, ...]
    # Pola dowodu w kolejności pierwszego wystąpienia.
    kolumny: tuple[str, ...]
    # Rekomendacje bez powtórzeń. Jedna → pokazana raz nad grupą (decyzja
    # 2026-09-25: bez rekomendacji grupowej pisanej przez model).
    rekomendacje: tuple[str, ...]

    @property
    def wspolna_rekomendacja(self) -> str | None:
        return self.rekomendacje[0] if len(self.rekomendacje) == 1 else None


def _grupy(uwagi: Sequence[UwagaWRaporcie]) -> tuple[GrupaUwag, ...]:
    """Najliczniejsze grupy najpierw — tam jest najwięcej do zrobienia."""
    po_klasie: dict[str, list[UwagaWRaporcie]] = {}
    for uwaga in uwagi:
        po_klasie.setdefault(uwaga.klasa_id, []).append(uwaga)
    grupy = [
        GrupaUwag(
            klasa_id=klasa_id,
            nazwa_klasy=lista[0].nazwa_klasy,
            uwagi=tuple(lista),
            kolumny=tuple(dict.fromkeys(k for u in lista for k in u.dowod)),
            rekomendacje=tuple(dict.fromkeys(u.rekomendacja for u in lista)),
        )
        for klasa_id, lista in po_klasie.items()
    ]
    return tuple(sorted(grupy, key=lambda g: -len(g.uwagi)))


def _pierwsze_zdanie(tekst: str) -> str:
    """Zdanie na kafelek kategorii — deterministycznie, bez modelu (decyzja 2026-09-25)."""
    zdanie = re.split(r"(?<=[.!?])\s+", tekst.strip(), maxsplit=1)[0]
    if len(zdanie) <= DLUGOSC_ZDANIA:
        return zdanie
    return zdanie[: DLUGOSC_ZDANIA - 1].rsplit(" ", 1)[0].rstrip(",;:—– ") + "…"


@dataclass(frozen=True, slots=True)
class KategoriaRaportu:
    """Kafelek raportu głównego i jego widok pogłębiony."""

    id: str
    nazwa: str
    # Tekst zamiast liczby, gdy kategorii nie da się dziś zmierzyć.
    niezmierzona: str | None
    grupy: tuple[GrupaUwag, ...]
    udzial: int = 0

    @property
    def uwag(self) -> int:
        return sum(len(g.uwagi) for g in self.grupy)

    @property
    def zdanie(self) -> str:
        """„Największy problem": pierwsze zdanie pierwszej uwagi najliczniejszej grupy."""
        if not self.grupy:
            return "Audyt nie znalazł w tej kategorii uwag krytycznych."
        return _pierwsze_zdanie(self.grupy[0].uwagi[0].opis)


@dataclass(frozen=True, slots=True)
class Pokrycie:
    """Czego raport nie sprawdził — liczbą, gdy się da, tekstem, gdy nie."""

    temat: str
    tekst: str
    zbadanych: int | None = None
    wszystkich: int | None = None
    kategoria: str | None = None

    @property
    def niezmierzone(self) -> bool:
        return self.zbadanych is None

    @property
    def procent(self) -> int:
        if not self.wszystkich or self.zbadanych is None:
            return 0
        return round(self.zbadanych / self.wszystkich * 100)


@dataclass(frozen=True, slots=True)
class RaportUwag:
    client_id: str
    run_id: str
    run_at: str
    uwagi: tuple[UwagaWRaporcie, ...]
    pominietych: int
    zastrzezenia: tuple[str, ...]
    nieznane_hashe: int = 0
    konto_nazwa: str | None = None
    plan: str | None = None
    kategorie: tuple[KategoriaRaportu, ...] = ()
    pokrycie: tuple[Pokrycie, ...] = field(default_factory=tuple)

    @property
    def grupy(self) -> tuple[GrupaUwag, ...]:
        return _grupy(self.uwagi)

    @property
    def zmierzone(self) -> tuple[KategoriaRaportu, ...]:
        return tuple(k for k in self.kategorie if not k.niezmierzona)

    def pokrycie_kategorii(self, kategoria: str) -> tuple[Pokrycie, ...]:
        return tuple(p for p in self.pokrycie if p.kategoria == kategoria)


def _payload(con: sqlite3.Connection, snapshot_id: int | None) -> dict[str, Any]:
    if snapshot_id is None:
        return {}
    wiersz = con.execute("SELECT payload FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return json.loads(wiersz["payload"]) if wiersz else {}


def _nazwy_tablic(payload: dict[str, Any], deanon: Deanonimizacja) -> dict[str, str]:
    """Nazwy tablic ze snapshotu — po redakcji osób, więc rozwinięte deanonimizacją."""
    return {
        str(t["board_id"]): deanon.tekst(str(t.get("nazwa") or t["board_id"]))
        for t in ((payload.get("tablice") or {}).get("tablice") or [])
        if t.get("board_id") is not None
    }


def _kategorie(uwagi: Sequence[UwagaWRaporcie], rubryka: Rubryka) -> tuple[KategoriaRaportu, ...]:
    kategoria_klasy = {k.id: k.kategoria for k in rubryka.klasy}
    razem = len(uwagi) or 1
    wynik = []
    for kategoria in rubryka.kategorie:
        # Mierzona = ma choć jedną klasę z detektorem. Tekst z rubryki wygrywa:
        # kategoria z klasą, której API nie zasila (agenci), nadal nie jest mierzona.
        mierzona = not kategoria.niezmierzona and any(
            k.kategoria == kategoria.id and k.ma_detektor for k in rubryka.klasy
        )
        swoje = [u for u in uwagi if kategoria_klasy.get(u.klasa_id) == kategoria.id]
        wynik.append(
            KategoriaRaportu(
                id=kategoria.id,
                nazwa=kategoria.nazwa,
                niezmierzona=None
                if mierzona
                else (kategoria.niezmierzona or "jeszcze nie mierzone"),
                grupy=_grupy(swoje),
                udzial=round(len(swoje) / razem * 100),
            )
        )
    return tuple(wynik)


def _pokrycie(
    payload: dict[str, Any],
    uwagi: Sequence[UwagaWRaporcie],
    kategorie: Sequence[KategoriaRaportu],
    poza_sufitem: Sequence[PozaSufitem],
    rubryka: Rubryka,
) -> tuple[Pokrycie, ...]:
    wynik: list[Pokrycie] = []
    for p in poza_sufitem:
        klasa = rubryka.po_id.get(p.klasa_id)
        wynik.append(
            Pokrycie(
                temat=klasa.nazwa if klasa else p.klasa_id,
                tekst=(
                    f"AI zbadało {p.zbadanych} z {p.wszystkich} przypadków ({p.kryterium}). "
                    f"Pozostałych {p.pominietych} nie oceniało — ich brak w raporcie NIE "
                    "znaczy, że są w porządku."
                ),
                zbadanych=p.zbadanych,
                wszystkich=p.wszystkich,
                kategoria=klasa.kategoria if klasa else None,
            )
        )
    podsumowanie = (payload.get("aktywnosc") or {}).get("podsumowanie") or {}
    zbadanych = podsumowanie.get("tablic_zbadanych")
    pominietych = podsumowanie.get("tablic_pominietych")
    if isinstance(zbadanych, int) and isinstance(pominietych, int) and pominietych:
        wynik.append(
            Pokrycie(
                temat="Logi aktywności",
                tekst=(
                    f"Pobrano dla {zbadanych} z {zbadanych + pominietych} tablic. Ocena "
                    "aktywności i porzucenia tablic opiera się tylko na tej próbie."
                ),
                zbadanych=zbadanych,
                wszystkich=zbadanych + pominietych,
                kategoria="tablice",
            )
        )
    # „Nie zmierzone" z dowodów — raz na etykietę, z powodem, który podał detektor.
    kategoria_klasy = {k.id: k.kategoria for k in rubryka.klasy}
    widziane: set[str] = set()
    for uwaga in uwagi:
        for chip in uwaga.chipy:
            if chip.niezmierzone and chip.etykieta not in widziane:
                widziane.add(chip.etykieta)
                wynik.append(
                    Pokrycie(
                        temat=chip.etykieta[:1].upper() + chip.etykieta[1:],
                        tekst=str(chip.powod),
                        kategoria=kategoria_klasy.get(uwaga.klasa_id),
                    )
                )
    wynik += [
        Pokrycie(temat=k.nazwa, tekst=k.niezmierzona or "", kategoria=k.id)
        for k in kategorie
        if k.niezmierzona
    ]
    return tuple(wynik)


def zbuduj_raport_uwag(
    przyjete: list[dict[str, Any]],
    *,
    con: sqlite3.Connection,
    client_id: str,
    run_id: str,
    run_at: str,
    rubryka: Rubryka,
    pominietych: int = 0,
    zastrzezenia: tuple[str, ...] = (),
    snapshot_id: int | None = None,
    poza_sufitem: Sequence[PozaSufitem] = (),
) -> RaportUwag:
    """Uwagi przyjęte przez walidację → raport z nazwiskami.

    `con` to połączenie, w którym leży `osoby_mapowanie` (i snapshot) — w trybie
    pamięci baza RAM-owa runu. Wołać PRZED jej zamknięciem.
    """
    deanon = Deanonimizacja(con, client_id)
    payload = _payload(con, snapshot_id)
    nazwy = _nazwy_tablic(payload, deanon)
    uwagi = []
    for u in przyjete:
        dowod = deanon.wartosc(u["dowod"])
        uwagi.append(
            UwagaWRaporcie(
                klasa_id=str(u["klasa_id"]),
                nazwa_klasy=(
                    rubryka.po_id[u["klasa_id"]].nazwa
                    if u["klasa_id"] in rubryka.po_id
                    else str(u["klasa_id"])
                ),
                zrodlo=str(u.get("zrodlo") or "model"),
                opis=deanon.tekst(str(u["opis"])),
                rekomendacja=deanon.tekst(str(u["rekomendacja"])),
                dowod=dowod,
                chipy=chipy_dowodu(
                    dowod if isinstance(dowod, dict) else {"dowod": dowod},
                    rubryka.pola_dowodu,
                    run_at=run_at,
                    nazwy_tablic=nazwy,
                ),
            )
        )
    # Zastrzeżenia też: obraz konta jest redagowany przed modelem, więc niosą
    # `[OSOBA:…]` — bez tego czytelnik raportu widziałby surowy hasz.
    zastrzezenia = tuple(deanon.tekst(z) for z in zastrzezenia)
    deanon.podsumuj()
    kategorie = _kategorie(uwagi, rubryka)
    konto = (payload.get("konto") or {}).get("konto") or {}
    plan = (payload.get("konto") or {}).get("plan") or {}
    return RaportUwag(
        client_id=client_id,
        run_id=run_id,
        run_at=run_at,
        uwagi=tuple(uwagi),
        pominietych=pominietych,
        zastrzezenia=zastrzezenia,
        nieznane_hashe=len(deanon.nieznane),
        konto_nazwa=konto.get("nazwa") or None,
        plan=plan.get("tier") or None,
        kategorie=kategorie,
        pokrycie=_pokrycie(payload, uwagi, kategorie, poza_sufitem, rubryka),
    )


def wyrenderuj_uwagi(raport: RaportUwag, *, katalog: Path = KATALOG_SZABLONOW) -> str:
    return (
        srodowisko(katalog)
        .get_template(SZABLON_UWAG)
        # Biały znak — raport otwiera ciemny pasek marki.
        .render(r=raport, logo=zasob_data_uri(LOGO_JASNE, katalog=katalog / "zasoby"))
    )


def oddaj_raport(raport: RaportUwag, sciezka: Path) -> Path:
    """Raport do pliku wskazanego przez operatora — TYLKO na wyraźne żądanie."""
    return zapisz_html(wyrenderuj_uwagi(raport), sciezka)


def zapisz_html(html: str, sciezka: Path) -> Path:
    """Gotowy HTML raportu do pliku, z prawami `600` od chwili utworzenia.

    Plik niesie nazwiska pracowników klienta, więc nie może istnieć ani chwili
    z prawami domyślnymi.
    """
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    deskryptor = os.open(sciezka, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # `os.open` ustawia prawa tylko przy TWORZENIU. Plik, który już istniał
    # z 644, dostałby nazwiska przed zawężeniem praw — stąd `fchmod` na
    # deskryptorze PRZED zapisem (review 2026-09-23).
    os.fchmod(deskryptor, 0o600)
    with os.fdopen(deskryptor, "w", encoding="utf-8") as plik:
        plik.write(html)
    return sciezka
