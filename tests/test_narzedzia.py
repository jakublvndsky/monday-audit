"""Testy narzędzi agenta (etap 3.10), warstwa 1 z 04-test.md.

Trzy testy pilnują warunków odbioru z `03-build.md` 3.10:

- `test_agent_nie_ma_technicznej_mozliwosci_zapisu` — nie ma ścieżki zapisu
- `test_wyczerpany_budzet_daje_komunikat_nie_wyjatek` — budżety działają
- `test_probka_kolumn_nie_zwraca_ani_jednej_wartosci` — wyjścia przycięte,
  a granica PII nietknięta

Ten ostatni jest tu najważniejszy. Próbka itemów to jedyny wyjątek od D5,
więc jeśli kiedykolwiek zacznie zwracać wartości kolumn, treść klienta wejdzie
do kontekstu modelu i cała granica przestanie istnieć.
"""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.detektory import Hipoteza
from monday_audit.klient import MondayClient, ZapytanieError
from monday_audit.narzedzia import (
    LIMIT_TABLIC_W_ODPOWIEDZI,
    LIMIT_WPISOW_LOGU,
    Budzet,
    Narzedzia,
    NarzedzieError,
)
from monday_audit.osoby import policz_hash
from monday_audit.przebieg import zapisz_snapshot

KLIENT = "cxlabs"
SOL = b"sol-testowa-dluga-na-tyle-ze-przechodzi"
RUN_AT = "2026-08-01T18:00:00+00:00"


class _RejestrCichy:
    def zapisz(self, **kwargs: Any) -> None:
        pass


def payload(
    *,
    tablice: list[dict[str, Any]] | None = None,
    uzytkownicy: list[dict[str, Any]] | None = None,
    aktywnosc: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "meta": {"run_at": RUN_AT, "okno_od": "2026-05-03T18:00:00+00:00", "okno_dni": 90},
        "konto": {"konto": {"id": "27690228"}, "plan": {"tier": "enterprise"}},
        "uzytkownicy": {
            "uzytkownicy": uzytkownicy or [],
            "podsumowanie": {"razem": len(uzytkownicy or []), "zajmujacych_miejsce": 1},
            "discovery": {"last_activity_dostepne": True},
        },
        "tablice": {
            "tablice": tablice or [],
            "podsumowanie": {"tablic": len(tablice or [])},
            "discovery": {"po_typie": {"board": len(tablice or [])}},
        },
        "aktywnosc": {
            "aktywnosc_tablic": aktywnosc or [],
            "podsumowanie": {"tablic_z_logami": len(aktywnosc or [])},
            "discovery": {"logi_dostepne": True},
        },
        "automatyzacje": {
            "statystyki_automatyzacji": [
                {"automation_id": "a1", "success": 2, "failure": 1, "exhausted": 0}
            ],
            "uruchomienia": {"razem": 100, "sukces": 99, "bledow": 1},
            "podsumowanie": {"tablic_sondowanych": 10},
            "discovery": {"lista_automatyzacji_dostepna": False},
        },
    }


def tablica(board_id: str, **nadpisz: Any) -> dict[str, Any]:
    baza = {
        "board_id": board_id,
        "nazwa": f"Tablica {board_id}",
        "typ": "board",
        "state": "active",
        "items_count": 10,
        "workspace_id": "ws1",
        "owners": [],
        "subscribers": [],
        "kolumny": [{"id": "c1", "title": "Status", "type": "status"}],
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
    }
    return {**baza, **nadpisz}


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


@pytest.fixture
async def zbuduj(con: sqlite3.Connection) -> AsyncIterator[Callable[..., Any]]:
    """Fabryka narzędzi, która sama domyka klientów po teście.

    Ręczne `await klient.zamknij()` w każdym teście osobno było wyciekiem
    czekającym na pierwszy test, który się wywali przed tą linią.
    """
    klienci: list[MondayClient] = []

    def fabryka(
        dane: dict[str, Any],
        *,
        uchwyt: Callable[[httpx.Request], httpx.Response] | None = None,
        budzet: int = 5,
    ) -> Any:
        snapshot_id = zapisz_snapshot(con, client_id=KLIENT, payload=dane, run_at=RUN_AT)
        klient = None
        if uchwyt:
            klient = MondayClient(
                "tajny-token", _RejestrCichy(), transport=httpx.MockTransport(uchwyt)
            )
            klienci.append(klient)
        zestaw = Narzedzia(
            con=con, snapshot_id=snapshot_id, client_id=KLIENT, sol=SOL, klient=klient
        )
        hipoteza = Hipoteza(klasa_id="BOARD_OVERCOMPLEX", obiekt_id="b1", budzet_wywolan=budzet)
        return zestaw.dla_hipotezy(hipoteza)

    yield fabryka

    for klient in klienci:
        await klient.zamknij()


def odpowiedz(dane: dict[str, Any], koszt: int = 444) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                **dane,
                "complexity": {"query": koszt, "after": 9_000_000, "reset_in_x_seconds": 60},
            }
        },
    )


# ── warunek odbioru: brak możliwości zapisu ──────────────────────────────


def test_agent_nie_ma_technicznej_mozliwosci_zapisu() -> None:
    """Warunek odbioru 3.10, sprawdzony na dwa sposoby.

    Po pierwsze: żadna stała GraphQL w module nie jest mutacją. Sprawdzamy
    STAŁE, nie cały plik — docstring tłumaczy, dlaczego klient mutacje odrzuca,
    i słowo `mutation` ma prawo tam wystąpić.

    Po drugie: nawet gdyby ktoś mutację wstawił, `MondayClient` odrzuci ją
    przed wysłaniem — to sprawdza osobny test.
    """
    from monday_audit import narzedzia

    zapytania = [
        (nazwa, wartosc)
        for nazwa, wartosc in vars(narzedzia).items()
        if isinstance(wartosc, str) and "{" in wartosc and "query" in wartosc
    ]
    assert zapytania, "test straciłby sens, gdyby nie było już żadnego zapytania"
    for nazwa, zapytanie in zapytania:
        assert "mutation" not in zapytanie.lower(), f"{nazwa} zawiera mutację"
        assert "subscription" not in zapytanie.lower(), f"{nazwa} zawiera subskrypcję"

    # Wszystkie publiczne narzędzia — żadne nie ma w nazwie czasownika zapisu.
    zakazane = ("utworz", "zmien", "usun", "zapisz", "create", "update", "delete", "archive")
    for nazwa, _ in inspect.getmembers(narzedzia.NarzedziaHipotezy, inspect.isfunction):
        if nazwa.startswith("_"):
            continue
        assert not any(z in nazwa.lower() for z in zakazane), f"{nazwa} brzmi jak zapis"


def test_klient_odrzuca_mutacje_zanim_wyjdzie_z_procesu() -> None:
    """Druga warstwa: `odebranie możliwości`, nie filtrowanie (D6)."""
    from monday_audit.klient import przygotuj_zapytanie

    for zapytanie in (
        'mutation { create_board(board_name: "X", board_kind: public) { id } }',
        "  MUTATION { delete_item(item_id: 1) { id } }",
        "subscription { events { id } }",
    ):
        with pytest.raises(ZapytanieError, match="wyłącznie czyta"):
            przygotuj_zapytanie(zapytanie)


# ── warunek odbioru: budżety ─────────────────────────────────────────────


def test_budzet_liczy_tylko_wejscia_do_api(zbuduj: Any) -> None:
    """Odczyt snapshotu nie zużywa dziennego limitu klienta, więc go nie liczymy."""
    narzedzia = zbuduj(payload(tablice=[tablica("b1")]), budzet=2)

    for _ in range(5):
        narzedzia.pobierz_inwentarz("tablice")
        narzedzia.zapytaj_snapshot("tablica", "b1")

    assert narzedzia.budzet.zuzyte == 0
    assert narzedzia.budzet.zostalo == 2


async def test_wyczerpany_budzet_daje_komunikat_nie_wyjatek(zbuduj: Any) -> None:
    """Warunek odbioru: wyjątek przerwałby pętlę i hipoteza zostałaby otwarta."""
    wywolan = {"n": 0}

    def uchwyt(_: httpx.Request) -> httpx.Response:
        wywolan["n"] += 1
        return odpowiedz({"boards": [{"id": "b1", "activity_logs": []}]})

    narzedzia = zbuduj(payload(), uchwyt=uchwyt, budzet=1)

    pierwszy = await narzedzia.log_tablicy("b1", "2026-05-01", "2026-08-01")
    drugi = await narzedzia.log_tablicy("b1", "2026-05-01", "2026-08-01")

    assert pierwszy.komunikat is None
    assert drugi.komunikat is not None and "wyczerpany" in drugi.komunikat
    assert drugi.dane == {}
    assert wywolan["n"] == 1, "po wyczerpaniu budżetu NIE wolno wołać API"


def test_budzet_nie_schodzi_ponizej_zera() -> None:
    budzet = Budzet(limit=1)

    assert budzet.wez() is True
    assert budzet.wez() is False
    assert budzet.zostalo == 0
    assert budzet.wyczerpany is True


# ── warunek odbioru: przycięte wyjścia i granica PII ─────────────────────


async def test_probka_kolumn_nie_zwraca_ani_jednej_wartosci(zbuduj: Any) -> None:
    """NAJWAŻNIEJSZY test tego modułu.

    Próbka itemów to jedyny wyjątek od D5. Wartości pobieramy, żeby policzyć
    wypełnienie, i natychmiast wyrzucamy. Gdyby kiedykolwiek wyszły w wyniku,
    treść klienta wejdzie do kontekstu modelu.
    """
    tajne = "Kowalski jan@firma.test +48 600 700 800"

    def uchwyt(_: httpx.Request) -> httpx.Response:
        return odpowiedz(
            {
                "boards": [
                    {
                        "id": "b1",
                        "columns": [
                            {"id": "name", "title": "Item", "type": "name"},
                            {"id": "c1", "title": "Status", "type": "status"},
                            {"id": "c2", "title": "Notatka", "type": "long_text"},
                        ],
                        "items_page": {
                            "cursor": None,
                            "items": [
                                {
                                    "id": "1",
                                    "column_values": [
                                        {"id": "c1", "text": "Gotowe"},
                                        {"id": "c2", "text": tajne},
                                    ],
                                },
                                {
                                    "id": "2",
                                    "column_values": [
                                        {"id": "c1", "text": "W toku"},
                                        {"id": "c2", "text": ""},
                                    ],
                                },
                            ],
                        },
                    }
                ]
            }
        )

    narzedzia = zbuduj(payload(), uchwyt=uchwyt)
    wynik = await narzedzia.probka_kolumn("b1")
    tekst = repr(wynik.do_modelu())

    assert tajne not in tekst
    assert "Kowalski" not in tekst
    assert "jan@firma.test" not in tekst
    assert "600 700 800" not in tekst
    assert "Gotowe" not in tekst, "nawet niewinna wartość statusu nie ma tu wychodzić"
    # A liczby są.
    assert wynik.dane["rozmiar_probki"] == 2
    assert {k["title"]: k["wypelnionych"] for k in wynik.dane["kolumny"]} == {
        "Status": 2,
        "Notatka": 1,
    }


async def test_kolumna_tytulu_nie_jest_martwa(zbuduj: Any) -> None:
    """Zmierzone na tablicy 5097454411: typ `name` dawał 0/15 przy 15 tytułach.

    Tytuł itemu siedzi w `items { name }`, nie w `column_values`. Bez wyłączenia
    tego typu KAŻDA tablica dostawałaby jedną fałszywą martwą kolumnę — czyli
    dokładnie ten rodzaj fałszywki, którą klient sprawdzi pierwszą.
    """

    def uchwyt(_: httpx.Request) -> httpx.Response:
        return odpowiedz(
            {
                "boards": [
                    {
                        "id": "b1",
                        "columns": [
                            {"id": "name", "title": "Item", "type": "name"},
                            {"id": "c1", "title": "Pusta", "type": "text"},
                        ],
                        "items_page": {
                            "cursor": None,
                            "items": [{"id": "1", "column_values": [{"id": "c1", "text": ""}]}],
                        },
                    }
                ]
            }
        )

    narzedzia = zbuduj(payload(), uchwyt=uchwyt)
    wynik = await narzedzia.probka_kolumn("b1")

    assert wynik.dane["kolumny_martwe"] == ["Pusta"]
    assert wynik.dane["kolumn_pominietych"] == 1
    assert "Item" not in wynik.dane["kolumny_martwe"]


async def test_niepelna_probka_jest_oznaczona_jako_urwana(zbuduj: Any) -> None:
    """Kursor niepusty = itemów jest więcej. Agent nie może opisać tego jako całości."""

    def uchwyt(_: httpx.Request) -> httpx.Response:
        return odpowiedz(
            {
                "boards": [
                    {
                        "id": "b1",
                        "columns": [{"id": "c1", "title": "K", "type": "text"}],
                        "items_page": {
                            "cursor": "jest-wiecej",
                            "items": [{"id": "1", "column_values": [{"id": "c1", "text": "x"}]}],
                        },
                    }
                ]
            }
        )

    narzedzia = zbuduj(payload(), uchwyt=uchwyt)
    wynik = await narzedzia.probka_kolumn("b1")

    assert wynik.urwane is True
    assert wynik.dane["probka_pelna"] is False
    assert "URWANY" in wynik.do_modelu()["UWAGA"]


async def test_log_pseudonimizuje_autorow(zbuduj: Any) -> None:
    """Do kontekstu modelu nie wchodzi żaden identyfikator osoby (D6)."""

    def uchwyt(_: httpx.Request) -> httpx.Response:
        return odpowiedz(
            {
                "boards": [
                    {
                        "id": "b1",
                        "activity_logs": [
                            {
                                "id": "l1",
                                "event": "update_column_value",
                                "entity": "pulse",
                                "created_at": "17830789794688296",
                                "user_id": "101",
                            }
                        ],
                    }
                ]
            }
        )

    narzedzia = zbuduj(payload(), uchwyt=uchwyt)
    wynik = await narzedzia.log_tablicy("b1", "2026-05-01", "2026-08-01")
    tekst = repr(wynik.do_modelu())

    assert '"101"' not in tekst and "'101'" not in tekst
    assert policz_hash(KLIENT, "101", SOL) in tekst
    assert wynik.dane["zdarzenia"][0]["klasa"] == "operacyjne"
    assert list(wynik.dane["po_dniu"]) == ["2026-07-03"]


async def test_log_nie_pyta_o_tresc_klienta(zbuduj: Any) -> None:
    """Pole `data` zawiera wartości kolumn i nazwy itemów (D5)."""
    zapytania: list[str] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        import json as _json

        zapytania.append(_json.loads(zapytanie.content)["query"])
        return odpowiedz({"boards": [{"id": "b1", "activity_logs": []}]})

    narzedzia = zbuduj(payload(), uchwyt=uchwyt)
    await narzedzia.log_tablicy("b1", "2026-05-01", "2026-08-01")

    assert zapytania and "data" not in zapytania[0]


def test_lista_tablic_jest_przycieta(zbuduj: Any) -> None:
    duzo = [tablica(str(n), subscribers=["h1"]) for n in range(LIMIT_TABLIC_W_ODPOWIEDZI + 10)]
    narzedzia = zbuduj(payload(tablice=duzo))

    wynik = narzedzia.zapytaj_snapshot("tablice_osoby", "h1")

    assert wynik.dane["tablic"] == LIMIT_TABLIC_W_ODPOWIEDZI + 10
    assert len(wynik.dane["tablice"]) == LIMIT_TABLIC_W_ODPOWIEDZI
    assert wynik.urwane is True


def test_inwentarz_nie_zwraca_pelnych_list(zbuduj: Any) -> None:
    """Sto pięć tablic w kontekście modelu to nie wiedza, to szum."""
    duzo = [tablica(str(n)) for n in range(105)]
    narzedzia = zbuduj(payload(tablice=duzo))

    wynik = narzedzia.pobierz_inwentarz("tablice")

    assert "tablice" not in wynik.dane, "pełna lista nie ma tu prawa być"
    assert wynik.dane["podsumowanie"]["tablic"] == 105


# ── uczciwość odpowiedzi ─────────────────────────────────────────────────


def test_brak_probki_logu_nie_jest_brakiem_aktywnosci(zbuduj: Any) -> None:
    """Dwie różne rzeczy, których agent nie może pomylić."""
    narzedzia = zbuduj(payload(tablice=[tablica("b1")], aktywnosc=[]))

    wynik = narzedzia.zapytaj_snapshot("aktywnosc_tablicy", "b1")

    assert wynik.komunikat is not None
    assert "nie sprawdzał" in wynik.komunikat


def test_tablice_osoby_mowi_o_ograniczeniu_zakresu(zbuduj: Any) -> None:
    narzedzia = zbuduj(payload(tablice=[tablica("b1", subscribers=["h1"])]))

    wynik = narzedzia.zapytaj_snapshot("tablice_osoby", "h1")

    assert "poza zakresem" in wynik.dane["uwaga_o_zakresie"]


def test_nieznane_pytanie_jest_bledem_nie_domyslem(zbuduj: Any) -> None:
    """Agent wybiera z listy, nie pisze SQL-a (3.10)."""
    narzedzia = zbuduj(payload())

    with pytest.raises(NarzedzieError, match="nieznane pytanie"):
        narzedzia.zapytaj_snapshot("SELECT * FROM snapshots")


def test_nieznany_zakres_inwentarza_jest_bledem(zbuduj: Any) -> None:
    narzedzia = zbuduj(payload())

    with pytest.raises(NarzedzieError, match="nieznany zakres"):
        narzedzia.pobierz_inwentarz("wszystko")


def test_pytanie_o_obiekt_wymaga_identyfikatora(zbuduj: Any) -> None:
    narzedzia = zbuduj(payload())

    with pytest.raises(NarzedzieError, match="wymaga `obiekt_id`"):
        narzedzia.zapytaj_snapshot("tablica")


def test_obiekt_poza_snapshotem_mowi_o_zakresie(zbuduj: Any) -> None:
    narzedzia = zbuduj(payload(tablice=[tablica("b1")]))

    wynik = narzedzia.zapytaj_snapshot("tablica", "nie-ma-mnie")

    assert wynik.komunikat is not None and "poza zakresem" in wynik.komunikat


def test_narzedzie_na_zywo_bez_klienta_jest_bledem(zbuduj: Any) -> None:
    """Tryb bez monday (np. eval na zamrożonym snapshocie) musi być jawny."""
    narzedzia = zbuduj(payload())

    with pytest.raises(NarzedzieError, match="brak klienta monday"):
        import asyncio

        asyncio.run(narzedzia.probka_kolumn("b1"))


def test_wywolania_sa_zapisywane_do_sladu(zbuduj: Any) -> None:
    """Ślad wywołań jest wejściem dla evali z etapu 4."""
    narzedzia = zbuduj(payload(tablice=[tablica("b1")]))

    narzedzia.pobierz_inwentarz("tablice")
    narzedzia.zapytaj_snapshot("tablica", "b1")

    assert narzedzia.wywolania == ["pobierz_inwentarz:tablice", "zapytaj_snapshot:tablica"]


def test_limit_logu_jest_zgodny_z_dokumentacja() -> None:
    """Sufit w kodzie i w zapytaniu muszą być tą samą liczbą."""
    assert LIMIT_WPISOW_LOGU <= 100, "monday oddaje 100 wpisów na stronę"


def test_tytul_kolumny_z_mailem_przerywa(zbuduj: Any) -> None:
    """Tytuły kolumn pisze klient, a tu idą prosto z API — omijając collector.

    Kolumna nazwana adresem e-mail wsadziłaby PII do kontekstu modelu tą
    ścieżką. Granica PII jest zaimplementowana raz i obowiązuje też narzędzia.
    """
    import asyncio

    from monday_audit.osoby import PseudonimizacjaError

    def uchwyt(_: httpx.Request) -> httpx.Response:
        return odpowiedz(
            {
                "boards": [
                    {
                        "id": "b1",
                        "columns": [
                            {"id": "c1", "title": "kontakt: ktos@firma.test", "type": "text"}
                        ],
                        "items_page": {
                            "cursor": None,
                            "items": [{"id": "1", "column_values": [{"id": "c1", "text": "x"}]}],
                        },
                    }
                ]
            }
        )

    narzedzia = zbuduj(payload(), uchwyt=uchwyt)

    with pytest.raises(PseudonimizacjaError, match="adresu e-mail"):
        asyncio.run(narzedzia.probka_kolumn("b1"))
