"""Wspólna konfiguracja testów.

Jedyne zadanie: wczytać `.env`, żeby testy integracyjne (warstwa 2
z 04-test.md) widziały `MONDAY_TOKEN`. Warstwa 1 nie potrzebuje niczego.
"""

from __future__ import annotations

import os
from pathlib import Path

KORZEN = Path(__file__).resolve().parents[1]


def _wczytaj_env() -> None:
    """Minimalny czytnik `.env` — bez `python-dotenv`, żeby nie dodawać zależności.

    Nie nadpisuje zmiennych już ustawionych w środowisku: token podany jawnie
    w komendzie ma pierwszeństwo nad plikiem.
    """
    plik = KORZEN / ".env"
    if not plik.is_file():
        return

    for linia in plik.read_text(encoding="utf-8").splitlines():
        wpis = linia.strip()
        if not wpis or wpis.startswith("#") or "=" not in wpis:
            continue
        klucz, _, wartosc = wpis.partition("=")
        os.environ.setdefault(klucz.strip(), wartosc.strip().strip("\"'"))


_wczytaj_env()
