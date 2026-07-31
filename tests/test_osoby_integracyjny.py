"""Test antyprzeciekowy PII na prawdziwych danych — warstwa 2 z 04-test.md.

**WYŁĄCZNIE konto CXLABS. Nigdy konto klienta.**

    uv run pytest -m integracyjny

To jest pozycja z listy BRAMY w 03-build.md („Test antyprzeciekowy PII
przechodzi"), wykonana na kształcie danych, jaki naprawdę zwraca monday —
z polskimi znakami, pustymi polami i użytkownikami bez `last_activity`.

DWIE ZASADY TEGO PLIKU:

1. **Używa soli testowej, nie produkcyjnej.** Mapowanie idzie do bazy
   tymczasowej i ginie z testem, więc nie ma powodu dotykać `SOL_PSEUDONIMIZACJI`.
2. **Asercje liczą, a nie porównują wartości.** `assert imie not in payload`
   wypisałoby imię do logu pytest przy niepowodzeniu — czyli sam test byłby
   wyciekiem. Dlatego wszędzie liczniki.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import MapowanieOsob, polacz, zastosuj_migracje
from monday_audit.klient import MondayClient
from monday_audit.osoby import WZORZEC_EMAILA, zbierz_osoby

# Sekrety wstawia fixture `zrodlo_sekretow` z conftest.py — przez
# `konfiguracja.wczytaj()`, czyli tą samą drogą co program (D12).
# Brak sekretów pomija te testy, nie wywraca ich.
pytestmark = pytest.mark.integracyjny

# Sól wyłącznie na potrzeby testu. Produkcyjna nie jest tu potrzebna i nie ma
# jej dotykać — hashe z tego testu i tak lecą do bazy tymczasowej.
SOL_TESTOWA = b"sol-tylko-do-testu-integracyjnego-nie-produkcyjna"
KLIENT = "cxlabs-test"


class RejestrCichy:
    def zapisz(self, **kwargs: object) -> None:
        pass


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "integracyjny.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


async def test_prawdziwi_uzytkownicy_bez_pii_w_snapshocie(con: sqlite3.Connection) -> None:
    token = os.environ["MONDAY_TOKEN"]
    mapowanie = MapowanieOsob(con, KLIENT)

    async with MondayClient(token, RejestrCichy(), budzet_wywolan=10) as klient:
        wynik = await zbierz_osoby(klient, client_id=KLIENT, sol=SOL_TESTOWA, mapowanie=mapowanie)

    assert wynik.osoby, "konto CXLABS ma użytkowników — pusty wynik znaczy zepsutą paginację"
    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)

    # 1. Żadnego adresu e-mail.
    assert WZORZEC_EMAILA.search(payload) is None

    # 2. Żadnego pełnego imienia i nazwiska ani adresu z tabeli mapowania.
    #    Liczymy trafienia zamiast je porównywać, żeby komunikat pytest
    #    nie wypisał PII do logu.
    wiersze = con.execute(
        "SELECT imie_nazwisko, email FROM osoby_mapowanie WHERE client_id = ?", (KLIENT,)
    ).fetchall()
    maly = payload.lower()
    wyciekle = 0
    for wiersz in wiersze:
        imie = (wiersz["imie_nazwisko"] or "").strip()
        if len(imie.split()) >= 2 and imie.lower() in maly:
            wyciekle += 1
        if wiersz["email"] and wiersz["email"].lower() in maly:
            wyciekle += 1
    assert wyciekle == 0, f"{wyciekle} fragmentów PII wyciekło do snapshotu"

    # 3. Podejrzenia z treści pisanej przez klienta są ZARAPORTOWANE, nie
    #    przemilczane — na CXLABS to konta serwisowe i słowa ze stanowisk.
    assert "podejrzenia_pii_w_tekstach" in wynik.discovery

    # 4. Mapowanie kompletne — bez tego findingi wskazują na hashe,
    #    których renderer nie umie rozwinąć (3.12).
    assert len(wiersze) == len(wynik.osoby)
    assert wynik.zapisanych_mapowan == len(wynik.osoby)


async def test_discovery_last_activity_na_zywych_danych(con: sqlite3.Connection) -> None:
    """Potwierdzone 2026-07-30: pole zwraca ISO-8601, ale nie u wszystkich.

    `null` znaczy „nie wiem", nie „nieaktywny od zawsze" — dlatego liczba
    braków musi być w snapshocie, żeby ZOMBIE_ACCOUNT jej nie przemilczał.
    """
    token = os.environ["MONDAY_TOKEN"]

    async with MondayClient(token, RejestrCichy(), budzet_wywolan=10) as klient:
        wynik = await zbierz_osoby(
            klient, client_id=KLIENT, sol=SOL_TESTOWA, mapowanie=MapowanieOsob(con, KLIENT)
        )

    discovery = wynik.discovery
    assert discovery["last_activity_dostepne"] is True
    assert discovery["last_activity_razem"] == len(wynik.osoby)

    podsumowanie = wynik.podsumowanie()
    assert podsumowanie["razem"] == len(wynik.osoby)
    assert podsumowanie["bez_last_activity"] == (
        discovery["last_activity_razem"] - discovery["last_activity_wypelnione"]
    )

    # Timestampy są w formacie, który da się porównać leksykograficznie
    # (ISO-8601 ze strefą) — na tym opiera się okno 90 dni w ZOMBIE_ACCOUNT.
    znaczniki = [o.last_activity for o in wynik.osoby if o.last_activity]
    assert all(z.startswith("20") and ("T" in z) for z in znaczniki)
