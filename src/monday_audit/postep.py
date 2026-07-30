"""Wskaźnik postępu collectora na konsolę (uzupełnienie 3.2).

Warstwa prezentacji, świadomie oddzielona od `klient.py`. Klient produkuje
dane (`Postep`), ten moduł je wypisuje. W etapie 5 ten sam strumień pójdzie
do FastAPI i nic w kliencie nie musi się zmienić.

Piszemy na **stderr**, nie na stdout: snapshot i raport z runu idą na stdout,
a wskaźnik postępu nie może się z nimi zmieszać, gdy ktoś przekieruje wynik
do pliku.
"""

from __future__ import annotations

import sys
from typing import TextIO

from monday_audit.klient import Postep


class LicznikKonsolowy:
    """Jedna linia postępu, nadpisywana w miejscu.

    Poza terminalem (przekierowanie do pliku, systemd, CI) nadpisywanie
    w miejscu daje śmieci, więc tam wypisujemy zwykłe linie — ale tylko
    co `co_ile` kroków, żeby log ze 130 wywołań nie miał 130 linii.
    """

    def __init__(
        self,
        strumien: TextIO | None = None,
        *,
        w_miejscu: bool | None = None,
        co_ile: int = 10,
    ) -> None:
        self._strumien = strumien if strumien is not None else sys.stderr
        self._w_miejscu = (
            w_miejscu if w_miejscu is not None else bool(getattr(self._strumien, "isatty", bool)())
        )
        self._co_ile = max(1, co_ile)
        self._szerokosc = 0
        self._krokow = 0

    def __call__(self, postep: Postep) -> None:
        self._krokow += 1
        tekst = postep.opis()

        if self._w_miejscu:
            # Dopełnienie do poprzedniej szerokości wyciera ogon dłuższej linii.
            self._strumien.write(f"\r{tekst.ljust(self._szerokosc)}")
            self._szerokosc = max(self._szerokosc, len(tekst))
            self._strumien.flush()
            return

        # Pauza na reset complexity to zdarzenie, nie rutyna — zawsze w logu,
        # bo bez niej wygląda, jakby run stanął bez powodu.
        if postep.czekanie_s or self._krokow % self._co_ile == 0:
            self._strumien.write(f"{tekst}\n")
            self._strumien.flush()

    def zakoncz(self, podsumowanie: str | None = None) -> None:
        """Domyka linię postępu. Wołaj raz, po zakończeniu zbierania."""
        if self._w_miejscu and self._szerokosc:
            self._strumien.write("\r" + " " * self._szerokosc + "\r")
        if podsumowanie:
            self._strumien.write(f"{podsumowanie}\n")
        self._strumien.flush()
        self._szerokosc = 0
