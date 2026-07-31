"""Testy collectora tablic (etap 3.5), warstwa 1 z 04-test.md.

Dwa testy są tu ważniejsze od pozostałych:
`test_zapytanie_nie_pyta_o_itemy` (granica D5) i
`test_owners_i_subscribers_to_hashe` (granica PII).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from monday_audit.klient import MondayClient
from monday_audit.konto import Zakres
from monday_audit.osoby import PseudonimizacjaError, policz_hash
from monday_audit.tablice import (
    LIMIT_STRONY,
    Tablica,
    zbierz_tablice,
    zbuduj_zapytanie,
)

TOKEN = "tajny-token-klienta"
SOL = b"sol-testowa-dluga-na-tyle-ze-przechodzi"
KLIENT = "cxlabs"


def tablica_surowa(
    board_id: str = "5097387646",
    *,
    state: str = "active",
    owners: list[str] | None = None,
    subscribers: list[str] | None = None,
    kolumn: int = 7,
    items_count: int | None = 28,
    nazwa: str = "Lista pomysłów Agentów AI",
) -> dict[str, Any]:
    return {
        "id": board_id,
        "name": nazwa,
        "state": state,
        "board_kind": "public",
        "items_count": items_count,
        "created_at": "2024-03-01T09:00:00Z",
        "updated_at": "2026-07-29T18:00:00Z",
        "workspace": {"id": "6576039", "name": "monday AI Agents"},
        # `is not None`, nie `or`: pusta lista właścicieli to sensowny przypadek
        # testowy (tablica bez opiekuna), a `[] or [...]` po cichu ją podmienia.
        "owners": [{"id": o} for o in (owners if owners is not None else ["101"])],
        "subscribers": [
            {"id": s} for s in (subscribers if subscribers is not None else ["101", "102"])
        ],
        "columns": [
            {"id": f"kol{n}", "title": f"Kolumna {n}", "type": "text"} for n in range(kolumn)
        ],
    }


def odpowiedz(tablice: list[dict[str, Any]], *, koszt: int = 128_111) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "boards": tablice,
                "complexity": {"query": koszt, "after": 9_000_000, "reset_in_x_seconds": 60},
            }
        },
    )


def uchwyt_jedna_strona(
    tablice: list[dict[str, Any]],
) -> Callable[[httpx.Request], httpx.Response]:
    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        numer = json.loads(zapytanie.content)["variables"]["p"]
        return odpowiedz(tablice if numer == 1 else [])

    return uchwyt


class _RejestrCichy:
    def zapisz(self, **kwargs: Any) -> None:
        pass


@pytest.fixture
async def zbuduj() -> AsyncIterator[Callable[..., MondayClient]]:
    klienci: list[MondayClient] = []

    def fabryka(uchwyt: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> MondayClient:
        egzemplarz = MondayClient(
            TOKEN, _RejestrCichy(), transport=httpx.MockTransport(uchwyt), **kwargs
        )
        klienci.append(egzemplarz)
        return egzemplarz

    yield fabryka

    for egzemplarz in klienci:
        await egzemplarz.zamknij()


# ── granica D5: żadnych itemów ───────────────────────────────────────────


@pytest.mark.parametrize(
    "zakres",
    [Zakres.cale_konto(), Zakres.workspace("6576039"), Zakres.tablice("5097387646")],
)
def test_zapytanie_nie_pyta_o_itemy(zakres: Zakres) -> None:
    """`items_count` to granica (D5). Zejście na itemy to objętość i PII."""
    gql = zbuduj_zapytanie(zakres)

    assert "items_count" in gql
    assert "items_page" not in gql
    assert "items {" not in gql
    assert "column_values" not in gql
    assert "updates" not in gql


def test_zapytanie_pyta_o_kolumny_w_calosci() -> None:
    """Bez tytułów i typów kolumn nie ma BOARD_OVERCOMPLEX ani DUPLICATE_STRUCTURE."""
    gql = zbuduj_zapytanie(Zakres.cale_konto())

    assert "columns { id title type }" in gql


# ── granica PII: hashe, nie identyfikatory ───────────────────────────────


async def test_owners_i_subscribers_to_hashe(zbuduj: Any) -> None:
    klient = zbuduj(uchwyt_jedna_strona([tablica_surowa(owners=["101"], subscribers=["102"])]))

    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)
    tablica = wynik.tablice[0]

    assert tablica.owners == (policz_hash(KLIENT, "101", SOL),)
    assert tablica.subscribers == (policz_hash(KLIENT, "102", SOL),)

    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)
    assert '"101"' not in payload, "surowy identyfikator osoby nie należy do snapshotu"
    assert '"102"' not in payload


async def test_hashe_zgadzaja_sie_z_tymi_z_34(zbuduj: Any) -> None:
    """Ta sama sól i ten sam client_id — inaczej detektory nie połączą osoby
    z jej tablicami."""
    klient = zbuduj(uchwyt_jedna_strona([tablica_surowa(owners=["101"])]))

    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert wynik.tablice[0].owners[0] == policz_hash(KLIENT, 101, SOL)


async def test_email_w_tytule_kolumny_przerywa(zbuduj: Any) -> None:
    """Tytuły kolumn pisze klient — adres w nich to wyciek jak każdy inny."""
    surowa = tablica_surowa()
    surowa["columns"] = [{"id": "k1", "title": "kontakt: ktos@firma.test", "type": "text"}]
    klient = zbuduj(uchwyt_jedna_strona([surowa]))

    with pytest.raises(PseudonimizacjaError, match="adresu e-mail"):
        await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)


# ── zakres ───────────────────────────────────────────────────────────────


async def test_zakres_tablic_filtruje_po_ids(zbuduj: Any) -> None:
    """Najwęższy zakres: dokładnie wskazane tablice i nic poza nimi."""
    wyslane: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        wyslane.append(cialo)
        numer = cialo["variables"]["p"]
        return odpowiedz([tablica_surowa()] if numer == 1 else [])

    klient = zbuduj(uchwyt)
    await zbierz_tablice(klient, Zakres.tablice("5097387646"), client_id=KLIENT, sol=SOL)

    assert "ids: $ids" in wyslane[0]["query"]
    assert wyslane[0]["variables"]["ids"] == ["5097387646"]
    assert "workspace_ids" not in wyslane[0]["query"]


async def test_zakres_workspace_filtruje_po_workspace_ids(zbuduj: Any) -> None:
    wyslane: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        wyslane.append(cialo)
        return odpowiedz([] if cialo["variables"]["p"] > 1 else [tablica_surowa()])

    klient = zbuduj(uchwyt)
    await zbierz_tablice(klient, Zakres.workspace("6576039"), client_id=KLIENT, sol=SOL)

    assert "workspace_ids: $ws" in wyslane[0]["query"]
    assert wyslane[0]["variables"]["ws"] == ["6576039"]


async def test_cale_konto_nie_wysyla_filtra(zbuduj: Any) -> None:
    """Filtr w treści zapytania, nie jako null w zmiennych — cicho zignorowany
    filtr znaczyłby audyt całego konta zamiast wskazanej tablicy."""
    wyslane: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        wyslane.append(cialo)
        return odpowiedz([] if cialo["variables"]["p"] > 1 else [tablica_surowa()])

    klient = zbuduj(uchwyt)
    await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert "workspace_ids" not in wyslane[0]["query"]
    assert "ids:" not in wyslane[0]["query"]
    assert set(wyslane[0]["variables"]) == {"p", "limit", "state"}


# ── stany i kosz (OTWARTE.md O10) ────────────────────────────────────────


async def test_kosz_jest_liczony_ale_nie_listowany(zbuduj: Any) -> None:
    """Na CXLABS kosz to 38% tablic — wliczenie go zawyża każdą metrykę."""
    klient = zbuduj(
        uchwyt_jedna_strona(
            [
                tablica_surowa("1", state="active"),
                tablica_surowa("2", state="archived"),
                tablica_surowa("3", state="deleted"),
                tablica_surowa("4", state="deleted"),
            ]
        )
    )

    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert [t.state for t in wynik.tablice] == ["active", "archived"]
    assert wynik.usunietych == 2
    assert wynik.podsumowanie()["usunietych_pominietych"] == 2


async def test_zbieraj_usuniete_odwraca_decyzje(zbuduj: Any) -> None:
    klient = zbuduj(
        uchwyt_jedna_strona(
            [tablica_surowa("1", state="active"), tablica_surowa("2", state="deleted")]
        )
    )

    wynik = await zbierz_tablice(
        klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL, zbieraj_usuniete=True
    )

    assert len(wynik.tablice) == 2
    assert wynik.usunietych == 0


async def test_zapytanie_prosi_o_wszystkie_stany(zbuduj: Any) -> None:
    """Archiwizacja jest sygnałem (3.5), więc `state: active` by nie wystarczył."""
    wyslane: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        wyslane.append(cialo)
        return odpowiedz([] if cialo["variables"]["p"] > 1 else [tablica_surowa()])

    klient = zbuduj(uchwyt)
    await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert wyslane[0]["variables"]["state"] == "all"


# ── paginacja i podsumowanie ─────────────────────────────────────────────


async def test_paginacja_po_25_domyslnie(zbuduj: Any) -> None:
    wyslane: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        wyslane.append(cialo)
        return odpowiedz([] if cialo["variables"]["p"] > 1 else [tablica_surowa()])

    klient = zbuduj(uchwyt)
    await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert wyslane[0]["variables"]["limit"] == LIMIT_STRONY == 25


async def test_paginacja_zbiera_wiele_stron(zbuduj: Any) -> None:
    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        numer = json.loads(zapytanie.content)["variables"]["p"]
        if numer <= 2:
            return odpowiedz([tablica_surowa(f"{numer}a"), tablica_surowa(f"{numer}b")])
        return odpowiedz([])

    klient = zbuduj(uchwyt)
    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert len(wynik.tablice) == 4
    assert klient.liczba_wywolan == 3


async def test_podsumowanie_liczy_to_co_potrzebuja_detektory(zbuduj: Any) -> None:
    klient = zbuduj(
        uchwyt_jedna_strona(
            [
                tablica_surowa("1", kolumn=7, items_count=28),
                tablica_surowa("2", kolumn=40, items_count=0, owners=[]),
                tablica_surowa("3", state="archived", kolumn=3, items_count=5),
            ]
        )
    )

    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)
    podsumowanie = wynik.podsumowanie()

    assert podsumowanie["razem"] == 3
    assert podsumowanie["po_state"] == {"active": 2, "archived": 1}
    assert podsumowanie["itemow_suma"] == 33
    assert podsumowanie["tablic_bez_itemow"] == 1
    assert podsumowanie["kolumn_suma"] == 50
    assert podsumowanie["kolumn_max"] == 40
    assert podsumowanie["tablic_bez_wlasciciela"] == 1
    assert podsumowanie["workspace_ow"] == 1


async def test_brak_items_count_jest_zglaszany(zbuduj: Any) -> None:
    """Blokuje ocenę objętości, czyli O4 i BOARD_GHOST — nie może być ciszą."""
    klient = zbuduj(uchwyt_jedna_strona([tablica_surowa(items_count=None)]))

    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert wynik.discovery["items_count_dostepne"] is False
    assert wynik.discovery["tablic_bez_items_count"] == 1


async def test_snapshot_ma_dokladnie_dozwolone_pola(zbuduj: Any) -> None:
    klient = zbuduj(uchwyt_jedna_strona([tablica_surowa()]))

    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert set(wynik.do_snapshotu()["tablice"][0]) == {
        "board_id",
        "nazwa",
        "state",
        "board_kind",
        "items_count",
        "workspace_id",
        "workspace_nazwa",
        "owners",
        "subscribers",
        "kolumny",
        "created_at",
        "updated_at",
    }


async def test_nowe_pole_z_api_nie_wchodzi_do_snapshotu(zbuduj: Any) -> None:
    surowa = {**tablica_surowa(), "description": "opis z API", "permissions": "everyone"}
    klient = zbuduj(uchwyt_jedna_strona([surowa]))

    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)
    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)

    assert "opis z API" not in payload
    assert "permissions" not in payload


async def test_tablica_bez_workspace_nie_wywala(zbuduj: Any) -> None:
    """Tablice w głównym obszarze potrafią wracać bez `workspace`."""
    surowa = {**tablica_surowa(), "workspace": None}
    klient = zbuduj(uchwyt_jedna_strona([surowa]))

    wynik = await zbierz_tablice(klient, Zakres.cale_konto(), client_id=KLIENT, sol=SOL)

    assert wynik.tablice[0].workspace_id is None
    assert wynik.podsumowanie()["workspace_ow"] == 0


def test_tablica_jest_niemutowalna() -> None:
    tablica = Tablica(
        board_id="1",
        nazwa="X",
        state="active",
        board_kind="public",
        items_count=0,
        workspace_id=None,
        workspace_nazwa=None,
        owners=(),
        subscribers=(),
        kolumny=(),
        created_at=None,
        updated_at=None,
    )

    with pytest.raises((AttributeError, TypeError)):
        tablica.nazwa = "inna"  # type: ignore[misc]
