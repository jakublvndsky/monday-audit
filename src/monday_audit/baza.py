"""Dostęp do SQLite i runner migracji (etap 3.1).

Bez ORM-a — `sqlite3` ze standardu i ręczne zapytania (03-build.md, 3.1).
Migracje to numerowane pliki `.sql` w `monday_audit/migracje/`, stosowane
po kolei i odnotowywane w tabeli `_migracje`.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

KATALOG_MIGRACJI = Path(__file__).parent / "migracje"

# Rejestr zastosowanych migracji. Tworzony przez runner, nie przez migrację —
# inaczej migracja 001 musiałaby odnotować samą siebie w tabeli, której
# jeszcze nie ma.
_SCHEMAT_REJESTRU = """
CREATE TABLE IF NOT EXISTS _migracje (
    numer          INTEGER PRIMARY KEY,
    nazwa          TEXT NOT NULL,
    suma_kontrolna TEXT NOT NULL,
    zastosowana_at TEXT NOT NULL
) STRICT
"""


class MigracjaError(RuntimeError):
    """Błąd w zestawie migracji albo w ich stosowaniu."""


def polacz(sciezka: Path | str) -> sqlite3.Connection:
    """Otwiera połączenie z wymuszonymi kluczami obcymi i transakcyjnym DDL."""
    con = sqlite3.connect(sciezka)
    con.row_factory = sqlite3.Row

    # KOLEJNOŚĆ TYCH DWÓCH LINII JEST ISTOTNA.
    # `PRAGMA foreign_keys` jest po cichu ignorowana wewnątrz transakcji,
    # a przy autocommit=False transakcja otwiera się przed pierwszym
    # poleceniem. Najpierw pragma, dopiero potem tryb.
    con.execute("PRAGMA foreign_keys = ON")

    # Domyślny tryb sqlite3 otwiera transakcję tylko przed DML. CREATE TABLE
    # trafiałoby poza nią i migracja przestałaby być niepodzielna.
    con.autocommit = False

    return con


def _numer(plik: Path) -> int:
    przedrostek = plik.name.split("_", 1)[0]
    if not przedrostek.isdigit():
        raise MigracjaError(f"Nazwa migracji musi zaczynać się od numeru: {plik.name}")
    return int(przedrostek)


def znajdz_migracje(katalog: Path | None = None) -> list[Path]:
    """Zwraca pliki migracji posortowane po nazwie, czyli po numerze."""
    katalog = katalog or KATALOG_MIGRACJI
    if not katalog.is_dir():
        raise MigracjaError(f"Katalog migracji nie istnieje: {katalog}")

    pliki = sorted(katalog.glob("*.sql"))
    numery = [_numer(p) for p in pliki]
    duble = sorted({n for n in numery if numery.count(n) > 1})
    if duble:
        raise MigracjaError(f"Zduplikowane numery migracji w {katalog}: {duble}")
    return pliki


def _instrukcje(sql: str) -> Iterator[str]:
    """Dzieli skrypt na pojedyncze instrukcje.

    Nie po średnikach: średnik w ciele `CREATE TRIGGER` nie kończy instrukcji.
    `sqlite3.complete_statement` o tym wie, naiwny split nie.
    """
    bufor = ""
    for linia in sql.splitlines(keepends=True):
        bufor += linia
        if sqlite3.complete_statement(bufor):
            yield bufor
            bufor = ""

    ogon = [w for w in (x.strip() for x in bufor.splitlines()) if w and not w.startswith("--")]
    if ogon:
        raise MigracjaError(f"Niedomknięta instrukcja na końcu pliku: {ogon[0][:80]}")


def zastosuj_migracje(con: sqlite3.Connection, katalog: Path | None = None) -> list[int]:
    """Stosuje brakujące migracje po kolei. Zwraca numery tych zastosowanych.

    Idempotentne: powtórne wywołanie na tej samej bazie nie robi nic.
    Każda migracja idzie w jednej transakcji razem z wpisem do `_migracje`,
    więc nie da się zastosować migracji bez jej odnotowania ani odwrotnie.
    """
    con.execute(_SCHEMAT_REJESTRU)
    con.commit()

    zapisane = {
        int(w["numer"]): str(w["suma_kontrolna"])
        for w in con.execute("SELECT numer, suma_kontrolna FROM _migracje")
    }
    zastosowane: list[int] = []

    for plik in znajdz_migracje(katalog):
        numer = _numer(plik)
        tresc = plik.read_text(encoding="utf-8")
        suma = hashlib.sha256(tresc.encode("utf-8")).hexdigest()

        if numer in zapisane:
            # Zastosowana migracja jest niezmienna. Gdyby ktoś ją edytował,
            # baza i plik rozjeżdżają się po cichu — a etap 5 wymaga
            # odtwarzalności audytu sprzed miesięcy.
            if zapisane[numer] != suma:
                raise MigracjaError(
                    f"Migracja {plik.name} zmieniła się po zastosowaniu "
                    f"(suma kontrolna się nie zgadza). Dopisz nową migrację "
                    f"zamiast edytować zastosowaną."
                )
            logger.debug("migracja %s już zastosowana, pomijam", numer)
            continue

        try:
            for instrukcja in _instrukcje(tresc):
                con.execute(instrukcja)
            con.execute(
                "INSERT INTO _migracje (numer, nazwa, suma_kontrolna, zastosowana_at) "
                "VALUES (?, ?, ?, ?)",
                (numer, plik.name, suma, datetime.now(tz=UTC).isoformat()),
            )
        except Exception:
            con.rollback()
            raise
        con.commit()

        zastosowane.append(numer)
        logger.info("zastosowano migrację %s (%s)", numer, plik.name)

    return zastosowane


class RejestrWywolan:
    """Zapis każdego wywołania do tabeli `wywolania` — obserwowalność D10.

    Obsługuje oba rodzaje wywołań: GraphQL collectora (3.2) i narzędzia
    agenta (3.10). Zamiast Langfuse tabela, na której SQL odpowie na pięć
    pytań z 06-operate.md.

    **Wymaga istniejącego wiersza w `runy`.** `wywolania.run_id` to
    NOT NULL REFERENCES, a `polacz()` włącza klucze obce — więc run trzeba
    otworzyć przed pierwszym zapytaniem do monday, także tym walidującym
    `is_admin` z 3.3.

    Zapis jest synchroniczny, wołany z kodu async. Świadomie: to jeden
    lokalny INSERT bez sieci, a `aiosqlite` byłoby nową zależnością.

    Każdy wpis idzie z własnym commitem, żeby dane obserwowalności przetrwały
    przerwany run. Konsekwencja do zapamiętania: nie wołaj `zapisz()`
    z wnętrza otwartej transakcji zapisu — commit rejestru domknąłby także ją.
    """

    def __init__(self, con: sqlite3.Connection, run_id: str) -> None:
        self._con = con
        self._run_id = run_id

    def zapisz(
        self,
        *,
        narzedzie: str,
        latency_ms: int | None = None,
        complexity: int | None = None,
        hipoteza_id: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        model: str | None = None,
    ) -> None:
        self._con.execute(
            "INSERT INTO wywolania (run_id, hipoteza_id, narzedzie, tokens_in, tokens_out, "
            "latency_ms, complexity, model, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._run_id,
                hipoteza_id,
                narzedzie,
                tokens_in,
                tokens_out,
                latency_ms,
                complexity,
                model,
                datetime.now(tz=UTC).isoformat(),
            ),
        )
        self._con.commit()


@dataclass(frozen=True, slots=True)
class WpisOdczytany:
    """Jedna linia PII odczytana z `osoby_mapowanie`."""

    user_hash: str
    imie_nazwisko: str | None
    email: str | None


class WpisMapowania(Protocol):
    """Kształt wpisu PII, którego oczekuje `MapowanieOsob`.

    Protokół, a nie import z `osoby.py`: baza jest warstwą niżej i nie ma
    powodu wiedzieć, jak collector nazywa swoje struktury.
    """

    # Właściwości, nie pola: `WpisPII` jest niemutowalny (frozen dataclass),
    # a protokół z gołymi adnotacjami wymagałby atrybutów zapisywalnych.
    @property
    def user_hash(self) -> str: ...

    @property
    def imie_nazwisko(self) -> str | None: ...

    @property
    def email(self) -> str | None: ...


class MapowanieOsob:
    """MAGAZYN PII — tabela `osoby_mapowanie` (etap 3.4).

    **Agent nie ma żadnego narzędzia czytającego tę tabelę** (D6, CLAUDE.md).

    Metoda `wczytaj()` istnieje dla DWÓCH wywołujących i żadnego więcej:
    walidacji antyprzeciekowej w 3.8 (trzeba znać nazwiska, żeby sprawdzić,
    że ich nie ma w snapshocie) oraz renderera w 3.12 (deanonimizacja
    raportu). **Nie owijaj jej w narzędzie agenta i nie dopisuj kolejnych
    czytników „na potrzeby debugowania"** — to jedyna warstwa, która oddziela
    raport od danych osobowych klienta.

    Klucz główny to `(client_id, user_hash)`, więc powtórny run tego samego
    klienta nadpisuje własne wpisy zamiast się wywalić. Sól jest per klient,
    a po audycie dostęp jest odbierany (D11) — dlatego `client_id` musi być
    w kluczu, inaczej nie da się skasować mapowań jednego klienta.
    """

    def __init__(self, con: sqlite3.Connection, client_id: str) -> None:
        self._con = con
        self._client_id = client_id

    def zapisz_wiele(self, wpisy: Iterable[WpisMapowania]) -> int:
        """Zapisuje mapowania w JEDNEJ transakcji. Zwraca liczbę wpisów.

        Jeden commit, nie N: albo mapowanie jest kompletne, albo nie ma go
        wcale. Snapshot bez pełnego mapowania jest nierenderowalny — findingi
        wskazywałyby na hashe, których nikt nie umie rozwinąć.
        """
        wiersze = [(self._client_id, w.user_hash, w.imie_nazwisko, w.email) for w in wpisy]
        if not wiersze:
            return 0

        try:
            self._con.executemany(
                "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (client_id, user_hash) DO UPDATE SET "
                "imie_nazwisko = excluded.imie_nazwisko, email = excluded.email",
                wiersze,
            )
        except Exception:
            self._con.rollback()
            raise
        self._con.commit()

        logger.info("zapisano %d mapowań osób dla klienta %s", len(wiersze), self._client_id)
        return len(wiersze)

    def wczytaj(self) -> list[WpisOdczytany]:
        """Odczytuje PII tego klienta. Patrz ostrzeżenie w docstringu klasy."""
        wiersze = self._con.execute(
            "SELECT user_hash, imie_nazwisko, email FROM osoby_mapowanie "
            "WHERE client_id = ? ORDER BY user_hash",
            (self._client_id,),
        ).fetchall()
        return [
            WpisOdczytany(
                user_hash=str(w["user_hash"]),
                imie_nazwisko=w["imie_nazwisko"],
                email=w["email"],
            )
            for w in wiersze
        ]
