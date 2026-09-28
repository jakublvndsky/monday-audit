"""Collector workspace'ów (faza 9): lista z rodzajem i stanem, ostatni wpis logu."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from monday_audit.zbieranie.klient import MondayClient
from monday_audit.zbieranie.konto import Zakres
from monday_audit.zbieranie.workspace import TABLIC_NA_ZAPYTANIE, zbierz_workspace

TOKEN = "tajny-token-klienta"


class _RejestrCichy:
    def zapisz(self, **kwargs: Any) -> None:
        pass


def _odpowiedz(dane: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                **dane,
                "complexity": {"query": 1, "after": 9_000_000, "reset_in_x_seconds": 60},
            }
        },
    )


@pytest.fixture
async def klient_z() -> AsyncIterator[Callable[..., tuple[MondayClient, list[dict[str, Any]]]]]:
    klienci: list[MondayClient] = []

    def fabryka(
        workspace_y: list[dict[str, Any]], wpisy: dict[str, str | None]
    ) -> tuple[MondayClient, list[dict[str, Any]]]:
        zapytania: list[dict[str, Any]] = []

        def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
            cialo = json.loads(zapytanie.content)
            zapytania.append(cialo)
            if "workspaces (" in cialo["query"]:
                strona = cialo["variables"]["p"]
                return _odpowiedz({"workspaces": workspace_y if strona == 1 else []})
            ids = cialo["variables"]["ids"]
            return _odpowiedz(
                {
                    "boards": [
                        {
                            "id": i,
                            "activity_logs": [{"created_at": wpisy[i]}] if wpisy.get(i) else [],
                        }
                        for i in ids
                    ]
                }
            )

        k = MondayClient(TOKEN, _RejestrCichy(), transport=httpx.MockTransport(uchwyt))
        klienci.append(k)
        return k, zapytania

    yield fabryka
    for k in klienci:
        await k.zamknij()


WS = [
    {
        "id": 1,
        "name": "Sprzedaż",
        "kind": "open",
        "state": "active",
        "account_product": {"kind": "crm"},
    },
    {"id": 2, "name": "Demo", "kind": "closed", "state": "active", "account_product": None},
]


async def test_lista_z_rodzajem_stanem_i_produktem(klient_z: Any) -> None:
    klient, _ = klient_z(WS, {})

    wynik = await zbierz_workspace(klient, Zakres.cale_konto(), [])

    assert [w.do_snapshotu() for w in wynik.workspace_y] == [
        {
            "workspace_id": "1",
            "nazwa": "Sprzedaż",
            "rodzaj": "open",
            "stan": "active",
            "produkt": "crm",
        },
        {
            "workspace_id": "2",
            "nazwa": "Demo",
            "rodzaj": "closed",
            "stan": "active",
            "produkt": None,
        },
    ]
    assert wynik.objete is True


async def test_ostatni_wpis_po_50_tablic_w_zapytaniu(klient_z: Any) -> None:
    """ZMIERZONE: 289 tablic w 6 wywołaniach. Brak wpisów to `None`, nie brak klucza."""
    ids = [str(n) for n in range(TABLIC_NA_ZAPYTANIE + 7)]
    klient, zapytania = klient_z(WS, {"0": "17830789794688296"})

    wynik = await zbierz_workspace(klient, Zakres.cale_konto(), ids)

    o_logi = [z for z in zapytania if "activity_logs" in z["query"]]
    assert [len(z["variables"]["ids"]) for z in o_logi] == [TABLIC_NA_ZAPYTANIE, 7]
    assert wynik.ostatni_wpis["0"] is not None and wynik.ostatni_wpis["0"].startswith("2026-")
    assert wynik.ostatni_wpis["1"] is None
    assert len(wynik.ostatni_wpis) == len(ids)


async def test_zakres_workspace_zaweza_a_tablice_nie_obejmuje(klient_z: Any) -> None:
    klient, zapytania = klient_z(WS, {})

    wycinek = await zbierz_workspace(klient, Zakres.workspace("2"), [])
    assert [w.workspace_id for w in wycinek.workspace_y] == ["2"]

    przed = len(zapytania)
    tablice = await zbierz_workspace(klient, Zakres.tablice("5"), ["5"])
    assert tablice.objete is False and tablice.workspace_y == ()
    assert len(zapytania) == przed, "zakres `tablice` nie odpytuje workspace'ów"
