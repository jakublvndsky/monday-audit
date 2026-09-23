"""Co WOLNO zapisać na dysk po runie — i w jakiej postaci (plan, faza 5c).

Decyzja Kuby z 2026-09-23: przechowywanie ograniczone do minimum, tak żeby nie
trzymać niczyich danych i nie musieć ich prawnie zabezpieczać. Ten moduł jest
JEDYNYM miejscem, przez które uwagi i statystyki trafiają do trwałej bazy.
Snapshot i tabela mapowania nie trafiają tam wcale — żyją w bazie w pamięci
przez czas runu (`cli_analiza`).

## Pseudonim to nadal dana osobowa

`user_hash` wygląda anonimowo, ale jest liczony STAŁĄ solą (`policz_hash`),
więc mając dostęp do konta, da się go odwrócić. Dane pseudonimizowane zostają
danymi osobowymi. Dlatego maskowanie przed zapisem robi więcej niż maskowanie
trace'ów: usuwa też pseudonimy, a nie tylko maile i telefony.

Trzy reguły, każda z własnym powodem:

1. **pseudonim → `[OSOBA]`**, lista pseudonimów → `[OSOBY: n]`. Liczba zostaje,
   bo „13 gości bez aktywności" jest ustaleniem; kto konkretnie — już nie.
2. **data → liczba dni przed runem.** „Ostatnio aktywny 2026-06-09" na koncie
   z dziewiętnastoma miejscami wskazuje jedną osobę tak samo skutecznie jak
   hasz. „105 dni przed runem" mówi to, co audytowi potrzebne, i nic więcej.
3. **dane kontaktowe → jak w trace'ach** (`maskowanie.zamaskuj`).

Czego te reguły NIE łapią: imienia i nazwiska wpisanego przez klienta w nazwę
tablicy (O50). Wzorzec nie ma czego się uchwycić.

## Statystyki: tylko liczby

Z obrazu konta zostają wyłącznie wartości liczbowe i logiczne, plus krótka
lista kluczy tekstowych, bez których liczby tracą sens (`produkt`, `tier`).
Nazwy tablic, workspace'ów i etykiet etapów odpadają w całości. Reguła „tylko
liczby" jest prosta celowo: lista rzeczy do wycięcia starzałaby się z każdym
nowym polem, a lista rzeczy dozwolonych — nie.

## Zawodzi zamknięte

Po maskowaniu całość jest sprawdzana jeszcze raz: gdyby w zapisie został
pseudonim albo adres, `PrzechowanieError` przerywa zapis. Raport i tak wychodzi
(`cli_analiza` wypisuje go przed zapisem) — tracimy wtedy wiersz w bazie, a nie
wynik audytu. W tę stronę błąd jest tani; w drugą oznaczałby dane osoby na
dysku, czyli dokładnie to, czego ta faza ma nie dopuścić.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from monday_audit.maskowanie import zamaskuj
from monday_audit.osoby import DLUGOSC_HASHA, WZORZEC_EMAILA

# Pseudonim z `policz_hash`: dokładnie 16 znaków szesnastkowych. Identyfikatory
# monday to 9-10 cyfr, więc granice słów wystarczą, żeby ich nie pomylić.
WZORZEC_PSEUDONIMU = re.compile(rf"\b[0-9a-f]{{{DLUGOSC_HASHA}}}\b")

# Data albo data z czasem w ISO 8601, jak ją oddaje monday i jak ją przepisuje
# model do dowodu.
WZORZEC_DATY = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)

ZNACZNIK_OSOBY = "[OSOBA]"

# Klucze tekstowe, które statystyki MOGĄ zachować. Bez `produkt` lista rollupów
# byłaby listą liczb bez podpisu. Nic tu nie identyfikuje osoby ani tablicy.
DOZWOLONE_TEKSTY = frozenset({"produkt", "tier", "period"})


class PrzechowanieError(RuntimeError):
    """W zapisie po maskowaniu został identyfikator osoby — zapis przerwany."""


def _dni_przed(data: str, teraz: datetime) -> str:
    try:
        chwila = datetime.fromisoformat(data.replace("Z", "+00:00"))
    except ValueError:
        return "[DATA]"
    if chwila.tzinfo is None:
        chwila = chwila.replace(tzinfo=UTC)
    return f"[{(teraz - chwila).days} dni przed runem]"


def _tekst_do_zapisu(tekst: str, teraz: datetime) -> str:
    tekst = WZORZEC_DATY.sub(lambda m: _dni_przed(m.group(0), teraz), tekst)
    return WZORZEC_PSEUDONIMU.sub(ZNACZNIK_OSOBY, tekst)


def _wartosc_do_zapisu(wartosc: Any, teraz: datetime) -> Any:
    if isinstance(wartosc, str):
        return _tekst_do_zapisu(wartosc, teraz)
    if isinstance(wartosc, list):
        pseudonimy = [w for w in wartosc if isinstance(w, str) and WZORZEC_PSEUDONIMU.fullmatch(w)]
        if wartosc and len(pseudonimy) == len(wartosc):
            # Lista samych pseudonimów → sama liczba. „13 osób" jest
            # ustaleniem audytu; które to osoby — już nie.
            return f"[OSOBY: {len(wartosc)}]"
        return [_wartosc_do_zapisu(w, teraz) for w in wartosc]
    if isinstance(wartosc, dict):
        return {k: _wartosc_do_zapisu(v, teraz) for k, v in wartosc.items()}
    return wartosc


def uwaga_do_zapisu(uwaga: dict[str, Any], *, teraz: datetime | None = None) -> dict[str, Any]:
    """Uwaga krytyczna → postać, którą wolno trzymać na dysku.

    Kolejność ma znaczenie: najpierw nasze reguły (pseudonimy, daty), potem
    `zamaskuj`. Odwrotnie `zamaskuj` mógłby wziąć fragment daty za telefon.
    """
    teraz = teraz or datetime.now(UTC)
    wstepna = {
        "klasa_id": uwaga.get("klasa_id"),
        "zrodlo": uwaga.get("zrodlo") or "model",
        "opis": _wartosc_do_zapisu(str(uwaga.get("opis") or ""), teraz),
        "rekomendacja": _wartosc_do_zapisu(str(uwaga.get("rekomendacja") or ""), teraz),
        "dowod": _wartosc_do_zapisu(uwaga.get("dowod") or {}, teraz),
    }
    zapis: dict[str, Any] = zamaskuj(wstepna).dane
    sprawdz_zapis(zapis)
    return zapis


def statystyki_do_zapisu(obraz: Any) -> Any:
    """Obraz konta → same liczby. Nazwy tablic, workspace'ów i etykiet odpadają.

    Pusta lista i pusty słownik po odsianiu też odpadają, żeby zapis nie był
    szkieletem pustych kluczy, który udaje, że coś zawiera.
    """
    if isinstance(obraz, bool | int | float):
        return obraz
    if isinstance(obraz, dict):
        wynik = {}
        for klucz, wartosc in obraz.items():
            if isinstance(wartosc, str):
                if klucz in DOZWOLONE_TEKSTY:
                    wynik[klucz] = wartosc
                continue
            odsiane = statystyki_do_zapisu(wartosc)
            if odsiane not in (None, {}, []):
                wynik[klucz] = odsiane
        return wynik
    if isinstance(obraz, list):
        odsiane_lista = [statystyki_do_zapisu(w) for w in obraz]
        return [w for w in odsiane_lista if w not in (None, {}, [])]
    return None


def sprawdz_zapis(dane: Any) -> None:
    """Twarda bramka przed dyskiem: pseudonim albo adres w zapisie przerywa zapis.

    Komunikat NIE zawiera znalezionej wartości — błąd o przecieku, który sam
    wypisuje to, co przeciekło, nie jest zabezpieczeniem.
    """
    tekst = json.dumps(dane, ensure_ascii=False)
    if WZORZEC_PSEUDONIMU.search(tekst):
        raise PrzechowanieError("w zapisie został pseudonim osoby — zapis przerwany")
    if WZORZEC_EMAILA.search(tekst):
        raise PrzechowanieError("w zapisie został adres e-mail — zapis przerwany")


def zapisz_uwagi(
    con: sqlite3.Connection,
    run_id: str,
    uwagi: Sequence[dict[str, Any]],
    *,
    teraz: datetime | None = None,
) -> int:
    """Uwagi po maskowaniu do `uwagi_zapisane`. Zwraca, ile zapisano.

    Wszystkie albo żadna: gdyby jedna uwaga nie przeszła bramki, zapis połowy
    wyglądałby na komplet, a to jest gorsze od jawnego braku.
    """
    zapisy = [uwaga_do_zapisu(u, teraz=teraz) for u in uwagi]
    with con:
        con.executemany(
            "INSERT INTO uwagi_zapisane (run_id, klasa_id, zrodlo, opis, rekomendacja, dowod) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    str(z["klasa_id"]),
                    str(z["zrodlo"]),
                    str(z["opis"]),
                    str(z["rekomendacja"]),
                    json.dumps(z["dowod"], ensure_ascii=False),
                )
                for z in zapisy
            ],
        )
    return len(zapisy)


def zapisz_statystyki(con: sqlite3.Connection, run_id: str, obraz: dict[str, Any]) -> None:
    """Liczbowy odcisk obrazu konta — do porównań między runami."""
    dane = statystyki_do_zapisu(obraz) or {}
    sprawdz_zapis(dane)
    with con:
        con.execute(
            "INSERT INTO statystyki_runow (run_id, dane) VALUES (?, ?)",
            (run_id, json.dumps(dane, ensure_ascii=False, sort_keys=True)),
        )
