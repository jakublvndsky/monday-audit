"""Stawki: odczyt, walidacja i przeterminowanie.

Jedyne wejście do stawek w całym systemie. Front z etapu 5 podłączy się tutaj,
a nie do tabel — dzięki temu formularz „odśwież cennik" i `cli_cennik` wołają
ten sam kod.

Trzy zasady, każda wyniesiona z konkretnego ryzyka:

1. **Stawka klienta bije publiczną.** Cena Enterprise jest negocjowana, więc
   publiczny cennik jej nie zawiera. Podstawienie ceny z listy jako
   `koszt_licencji_mies` dałoby liczbę pewnie brzmiącą i błędną (O7).
2. **Przedziały rozsądku przy ZAPISIE, nie przy czytaniu.** Scraper, który
   wyciągnie `$9` zamiast `$0.01`, ma odpaść od razu. Strona monday ma na sobie
   oba te napisy, więc to nie jest ryzyko teoretyczne.
3. **Przeterminowanie jest sygnałem, nie ciszą.** Po `wazna_do` stawka nadal
   się zwraca, ale oznaczona — a liczenie kwot na niej jest zabronione.
   Cicho zgniła stawka w raporcie klienta jest groźniejsza od braku kwoty.

Historia jest zachowywana: `zapisz_stawke` DOPISUJE odczyt, nie nadpisuje.
Snapshot sprzed trzech miesięcy musi dać się zinterpretować stawką, która
wtedy obowiązywała (D7).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Ile dni stawka publiczna jest uznawana za świeżą. Cennik monday zmienia się
# rzadziej, ale trzydzieści dni to okno, w którym zmiana zostanie zauważona
# przed następnym audytem, a nie po nim.
WAZNOSC_DNI = 30

# Przedziały rozsądku. Odrzucenie przy zapisie, bo scraper czytający cudzy HTML
# pomyli się kiedyś na pewno — pytanie tylko, czy zauważymy.
#
# `kredyt_ai_usd` w 0,001–1,00: strona monday zawiera zarówno `$0.01`
# (stawka kredytu), jak i `$9` (cena planu per user). Bez sufitu scraper
# mógłby wziąć drugie i policzyć klientowi kwotę 900 razy za dużą.
PRZEDZIALY: dict[str, tuple[float, float]] = {
    "kredyt_ai_usd": (0.001, 1.0),
    "ai_block_kredyty": (1, 100),
    "ai_notetaker_kredyty_godzina": (10, 1000),
    "vibe_kredyty_wiadomosc": (1, 500),
    # Uruchomienie agenta: dolna granica prostego zadania i sufit bardzo
    # złożonego. Zmierzone u źródła 2026-08-04: 10 i 250 kredytów.
    "agent_run_kredyty_min": (1, 100),
    "agent_run_kredyty_max": (10, 2000),
    # Cena licencji per użytkownik miesięcznie — waluty bywają różne, więc
    # przedział jest szeroki, ale zero i tysiące odpadają.
    "koszt_licencji_mies": (1, 1000),
}


class CennikError(RuntimeError):
    """Stawka jest niespójna albo poza przedziałem rozsądku."""


@dataclass(frozen=True, slots=True)
class Stawka:
    """Stawka RAZEM z pochodzeniem i wiekiem, nie sama liczba.

    Kwota w raporcie klienta musi dać się sprawdzić, więc kod liczący nie może
    dostać samego `float` — dostaje wartość i to, na czym ona stoi.
    """

    pozycja: str
    wartosc: float
    waluta: str | None
    jednostka: str
    zrodlo: str
    wiarygodnosc: str
    pobrano_at: str
    wazna_do: str | None
    per_klient: bool = False

    @property
    def przeterminowana(self) -> bool:
        if not self.wazna_do:
            return False
        return self.wazna_do < datetime.now(tz=UTC).isoformat()

    @property
    def dni_od_odswiezenia(self) -> int | None:
        try:
            kiedy = datetime.fromisoformat(self.pobrano_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (datetime.now(tz=UTC) - kiedy).days

    @property
    def wolno_liczyc(self) -> bool:
        """Czy na tej stawce wolno wyliczyć kwotę do raportu.

        Stawka per klient nie przeterminowuje się: człowiek podał ją świadomie
        na ten run. Publiczna po `wazna_do` jest odcięta, bo nikt jej nie
        potwierdził, a raport nie ma prawa podać cichej starej liczby.
        """
        return self.per_klient or not self.przeterminowana

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "pozycja": self.pozycja,
            "wartosc": self.wartosc,
            "waluta": self.waluta,
            "jednostka": self.jednostka,
            "zrodlo": self.zrodlo,
            "wiarygodnosc": self.wiarygodnosc,
            "pobrano_at": self.pobrano_at,
            "przeterminowana": self.przeterminowana,
            "dni_od_odswiezenia": self.dni_od_odswiezenia,
            "per_klient": self.per_klient,
            "wolno_liczyc": self.wolno_liczyc,
        }


def sprawdz_przedzial(pozycja: str, wartosc: float) -> None:
    """Rzuca `CennikError`, gdy wartość jest poza przedziałem rozsądku.

    Wołana przy ZAPISIE. Pozycja bez zadeklarowanego przedziału przechodzi,
    ale z ostrzeżeniem — nowa pozycja powinna dostać przedział, zanim ktoś
    policzy na niej kwotę.
    """
    if wartosc <= 0:
        raise CennikError(f"{pozycja}: stawka musi być dodatnia, jest {wartosc}")
    granice = PRZEDZIALY.get(pozycja)
    if granice is None:
        logger.warning(
            "pozycja %s nie ma przedziału rozsądku w PRZEDZIALY — dopisz go, "
            "zanim ktoś policzy na niej kwotę",
            pozycja,
        )
        return
    dolna, gorna = granice
    if not dolna <= wartosc <= gorna:
        raise CennikError(
            f"{pozycja}: {wartosc} jest poza przedziałem rozsądku {dolna}–{gorna}. "
            f"Najczęstsza przyczyna to zmiana układu strony i wyciągnięcie innej "
            f"liczby — sprawdź `surowy_fragment`, zanim podniesiesz przedział"
        )


def zapisz_stawke(
    con: sqlite3.Connection,
    *,
    pozycja: str,
    wartosc: float,
    jednostka: str,
    sposob: str,
    wiarygodnosc: str,
    waluta: str | None = None,
    zrodlo_url: str | None = None,
    surowy_fragment: str | None = None,
    wazna_dni: int = WAZNOSC_DNI,
) -> int:
    """DOPISUJE odczyt stawki. Nie nadpisuje — historia jest celowa."""
    sprawdz_przedzial(pozycja, wartosc)
    teraz = datetime.now(tz=UTC)
    with con:
        kursor = con.execute(
            "INSERT INTO cennik (pozycja, wartosc, waluta, jednostka, zrodlo_url, sposob, "
            "wiarygodnosc, pobrano_at, wazna_do, surowy_fragment) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pozycja,
                float(wartosc),
                waluta,
                jednostka,
                zrodlo_url,
                sposob,
                wiarygodnosc,
                teraz.isoformat(),
                (teraz + timedelta(days=wazna_dni)).isoformat(),
                (surowy_fragment or "")[:500] or None,
            ),
        )
    logger.info("cennik: %s = %s %s (%s)", pozycja, wartosc, waluta or jednostka, sposob)
    return int(kursor.lastrowid or 0)


def zapisz_stawke_klienta(
    con: sqlite3.Connection,
    *,
    client_id: str,
    pozycja: str,
    wartosc: float,
    waluta: str,
    zrodlo: str,
) -> int:
    """Stawka podana przez CZŁOWIEKA dla konkretnego klienta.

    Scraper tej funkcji nie wywołuje i nie ma prawa: cena Enterprise jest
    negocjowana, a publiczny cennik jej nie zawiera (O7).
    """
    sprawdz_przedzial(pozycja, wartosc)
    with con:
        kursor = con.execute(
            "INSERT INTO stawki_klienta (client_id, pozycja, wartosc, waluta, podano_at, zrodlo) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (client_id, pozycja, float(wartosc), waluta, datetime.now(tz=UTC).isoformat(), zrodlo),
        )
    logger.info("stawka klienta %s: %s = %s %s (%s)", client_id, pozycja, wartosc, waluta, zrodlo)
    return int(kursor.lastrowid or 0)


def aktualna(
    con: sqlite3.Connection, pozycja: str, *, client_id: str | None = None
) -> Stawka | None:
    """Najświeższa stawka. Stawka klienta ma PIERWSZEŃSTWO nad publiczną.

    Zwraca `None`, gdy stawki nie ma wcale — i to jest poprawna odpowiedź,
    nie błąd. Wtedy finding leci bez kwoty, tak jak przewiduje O7.
    """
    if client_id:
        wiersz = con.execute(
            "SELECT * FROM stawki_klienta WHERE client_id = ? AND pozycja = ? "
            "ORDER BY podano_at DESC LIMIT 1",
            (client_id, pozycja),
        ).fetchone()
        if wiersz is not None:
            return Stawka(
                pozycja=pozycja,
                wartosc=float(wiersz["wartosc"]),
                waluta=wiersz["waluta"],
                jednostka="miesiac",
                zrodlo=str(wiersz["zrodlo"]),
                wiarygodnosc="od_klienta",
                pobrano_at=str(wiersz["podano_at"]),
                wazna_do=None,
                per_klient=True,
            )

    wiersz = con.execute(
        "SELECT * FROM cennik WHERE pozycja = ? ORDER BY pobrano_at DESC LIMIT 1",
        (pozycja,),
    ).fetchone()
    if wiersz is None:
        return None
    return Stawka(
        pozycja=pozycja,
        wartosc=float(wiersz["wartosc"]),
        waluta=wiersz["waluta"],
        jednostka=str(wiersz["jednostka"]),
        zrodlo=str(wiersz["zrodlo_url"] or wiersz["sposob"]),
        wiarygodnosc=str(wiersz["wiarygodnosc"]),
        pobrano_at=str(wiersz["pobrano_at"]),
        wazna_do=wiersz["wazna_do"],
    )


def stawki_dla(
    con: sqlite3.Connection, pozycje: Iterable[str], *, client_id: str | None = None
) -> dict[str, Stawka]:
    """Stawki dla podanych pozycji. Brakujących NIE uzupełniamy niczym.

    Pozycja bez stawki po prostu nie trafia do słownika — i wtedy walidacja
    kontraktu odrzuci finding, który mimo to podał kwotę. Cichy fallback na
    „jakąś" wartość jest tu groźniejszy od braku kwoty (O7).
    """
    znalezione = {p: aktualna(con, p, client_id=client_id) for p in pozycje}
    return {p: s for p, s in znalezione.items() if s is not None}


def wersja_uzytych(stawki: Mapping[str, Stawka]) -> str | None:
    """Znacznik pinowania: najświeższy odczyt spośród stawek UŻYTYCH w runie.

    Świadomie nie `wersja_cennika` — run, który nie policzył żadnej kwoty,
    nie ma czego pinować, a wpisanie mu daty odświeżenia cennika sugerowałoby
    wpływ, którego nie było.
    """
    if not stawki:
        return None
    return max(s.pobrano_at for s in stawki.values())


def wersja_cennika(con: sqlite3.Connection) -> str | None:
    """Znacznik najświeższego odczytu — piąty element pinowania obok rubryki.

    Idzie do snapshotu, bo skoro stawki będą się zmieniać same, audyt sprzed
    trzech miesięcy musi zostać czytelny (D7, 05-deploy).
    """
    wiersz = con.execute("SELECT MAX(pobrano_at) AS ostatni FROM cennik").fetchone()
    return str(wiersz["ostatni"]) if wiersz and wiersz["ostatni"] else None


def przeglad(con: sqlite3.Connection, *, client_id: str | None = None) -> list[Stawka]:
    """Wszystkie znane pozycje w najświeższej wersji. Wejście dla `--pokaz`."""
    pozycje = {str(w["pozycja"]) for w in con.execute("SELECT DISTINCT pozycja FROM cennik")}
    if client_id:
        pozycje |= {
            str(w["pozycja"])
            for w in con.execute(
                "SELECT DISTINCT pozycja FROM stawki_klienta WHERE client_id = ?", (client_id,)
            )
        }
    znalezione = [aktualna(con, p, client_id=client_id) for p in sorted(pozycje)]
    return [s for s in znalezione if s is not None]
