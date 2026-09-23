"""Raport uwag krytycznych Z NAZWISKAMI — oddawany jednorazowo (plan, faza 5c).

Decyzja Kuby z 2026-09-23 (wariant A z D):

* **A** — raport z nazwiskami powstaje W TRAKCIE runu i jest oddany raz.
  Na serwerze nie zostaje jego kopia. Mapowanie osób żyje w bazie w pamięci
  (`cli_analiza`), więc po zamknięciu procesu takiego raportu nie da się już
  złożyć — i o to chodzi.
* **D** — to, co przechowujemy (`uwagi_zapisane`), jest zamaskowane, ale
  zostaje użyteczne: uwaga o osobie niesie atrybuty (rodzaj konta, dni bez
  aktywności, plan), po których klient odnajdzie konto w panelu monday.

## Dlaczego osobny moduł, a nie `raport.py`

`raport.py` składa raport starej ścieżki: findingi z wagą, wysiłkiem i kwotą,
czytane z tabeli `findings` powiązanej ze snapshotem. Nowa ścieżka nie ma ani
jednego z tych pól ani snapshotu w trwałej bazie. Wspólne zostają: środowisko
Jinja z autoescapingiem (`raport.srodowisko`), marka i makro dowodu — oraz
`Deanonimizacja`, jedyne miejsce, które zamienia pseudonimy na nazwiska.

Wygląd jest tymczasowy. Grupowanie i PDF to faza 7.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from monday_audit.deanonimizacja import Deanonimizacja
from monday_audit.raport import KATALOG_SZABLONOW, LOGO, srodowisko, zasob_data_uri
from monday_audit.rubryka import Rubryka

logger = logging.getLogger(__name__)

SZABLON_UWAG = "raport_uwag.html.j2"


@dataclass(frozen=True, slots=True)
class UwagaWRaporcie:
    klasa_id: str
    nazwa_klasy: str
    zrodlo: str
    opis: str
    rekomendacja: str
    dowod: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RaportUwag:
    client_id: str
    run_id: str
    run_at: str
    uwagi: tuple[UwagaWRaporcie, ...]
    pominietych: int
    zastrzezenia: tuple[str, ...]
    nieznane_hashe: int = 0


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
) -> RaportUwag:
    """Uwagi przyjęte przez walidację → raport z nazwiskami.

    `con` to połączenie, w którym leży `osoby_mapowanie` — w trybie pamięci
    baza RAM-owa runu. Wołać PRZED jej zamknięciem.
    """
    deanon = Deanonimizacja(con, client_id)
    uwagi = tuple(
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
            dowod=deanon.wartosc(u["dowod"]),
        )
        for u in przyjete
    )
    deanon.podsumuj()
    return RaportUwag(
        client_id=client_id,
        run_id=run_id,
        run_at=run_at,
        uwagi=uwagi,
        pominietych=pominietych,
        zastrzezenia=zastrzezenia,
        nieznane_hashe=len(deanon.nieznane),
    )


def wyrenderuj_uwagi(raport: RaportUwag, *, katalog: Path = KATALOG_SZABLONOW) -> str:
    return (
        srodowisko(katalog)
        .get_template(SZABLON_UWAG)
        .render(r=raport, logo=zasob_data_uri(LOGO, katalog=katalog / "zasoby"))
    )


def oddaj_raport(raport: RaportUwag, sciezka: Path) -> Path:
    """Raport do pliku wskazanego przez operatora — TYLKO na wyraźne żądanie.

    Prawa `600` od chwili utworzenia, nie po zapisie: plik niesie nazwiska
    pracowników klienta, więc nie może istnieć ani chwili z prawami domyślnymi.
    """
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    deskryptor = os.open(sciezka, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(deskryptor, "w", encoding="utf-8") as plik:
        plik.write(wyrenderuj_uwagi(raport))
    sciezka.chmod(0o600)  # także gdy plik istniał wcześniej z innymi prawami
    return sciezka
