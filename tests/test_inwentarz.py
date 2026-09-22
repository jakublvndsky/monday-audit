"""Inwentarz konta — liczenie kafelków (plan, faza 2a).

Testy pilnują trzech rzeczy, z których każda już raz w tym projekcie zawiodła
albo była blisko: rozdzielenia rodzajów kont, widoczności rodzaju nieznanego
i paginacji, która milczy po pierwszej stronie.
"""

from __future__ import annotations

from typing import Any

import pytest

from monday_audit.inwentarz import (
    _PYTANIE_TABLIC,
    _PYTANIE_UZYTKOWNIKOW,
    LIMIT_TABLIC,
    LIMIT_UZYTKOWNIKOW,
    Inwentarz,
    policz_produkty,
    policz_rodzaje,
    zbuduj_inwentarz,
)
from monday_audit.konto import Konto, Zakres
from monday_audit.osoby import (
    RODZAJ_ADMIN,
    RODZAJ_AGENT,
    RODZAJ_CZLONEK,
    RODZAJ_GOSC,
    RODZAJ_PODGLAD,
)
from monday_audit.podglad_zakresu import RejestrPodgladu, WorkspaceDoWyboru


def _osoba(kind: str | None, *, usuniety: bool = False) -> dict[str, Any]:
    return {"id": "1", "kind": kind, "status": "active", "is_deleted": usuniety}


# ── liczenie rodzajów ────────────────────────────────────────────────────


def test_rodzaje_licza_sie_osobno() -> None:
    """Agent i gość NIE są użytkownikami — mieszanie ich zawyżało ZOMBIE_ACCOUNT."""
    po_rodzajach, nieznane = policz_rodzaje(
        [
            _osoba(RODZAJ_ADMIN),
            _osoba(RODZAJ_CZLONEK),
            _osoba(RODZAJ_CZLONEK),
            _osoba(RODZAJ_GOSC),
            _osoba(RODZAJ_AGENT),
            _osoba(RODZAJ_AGENT),
            _osoba(RODZAJ_PODGLAD),
        ]
    )

    assert po_rodzajach[RODZAJ_ADMIN] == 1
    assert po_rodzajach[RODZAJ_CZLONEK] == 2
    assert po_rodzajach[RODZAJ_GOSC] == 1
    assert po_rodzajach[RODZAJ_AGENT] == 2
    assert po_rodzajach[RODZAJ_PODGLAD] == 1
    assert nieznane == ()


def test_usunieci_nie_wchodza_do_liczb() -> None:
    """Kafelek mówi o koncie, jakim jest dzisiaj."""
    po_rodzajach, _ = policz_rodzaje(
        [_osoba(RODZAJ_CZLONEK), _osoba(RODZAJ_CZLONEK, usuniety=True)]
    )

    assert po_rodzajach[RODZAJ_CZLONEK] == 1


def test_nieznany_rodzaj_jest_widoczny() -> None:
    """API nie deklaruje zamkniętej listy rodzajów (O17), więc nowy ma krzyczeć,
    a nie wpaść po cichu do jednego z istniejących kafelków."""
    po_rodzajach, nieznane = policz_rodzaje([_osoba("nowy_rodzaj_monday"), _osoba(None)])

    assert nieznane == ("nieznany", "nowy_rodzaj_monday")
    assert po_rodzajach["nowy_rodzaj_monday"] == 1


# ── produkty workspace'ów ────────────────────────────────────────────────


def test_produkty_grupuja_sie_po_rodzaju() -> None:
    wynik = policz_produkty(
        (
            WorkspaceDoWyboru("1", "CRM demo", produkt_id="42", produkt_kind="crm"),
            WorkspaceDoWyboru("2", "Serwis", produkt_id="43", produkt_kind="service"),
            WorkspaceDoWyboru("3", "Drugi CRM", produkt_id="42", produkt_kind="crm"),
        )
    )

    assert wynik == {"crm": 2, "service": 1}


def test_workspace_bez_produktu_ma_wlasna_kategorie() -> None:
    """`bez_produktu` to normalny stan konta, nie błąd odczytu — i nie wolno go
    zlepiać z żadnym produktem."""
    wynik = policz_produkty(
        (
            WorkspaceDoWyboru("1", "Luźny", produkt_id=None, produkt_kind=None),
            WorkspaceDoWyboru("2", "CRM", produkt_id="42", produkt_kind="crm"),
        )
    )

    assert wynik == {"bez_produktu": 1, "crm": 1}


# ── całość, z paginacją ──────────────────────────────────────────────────


class _KlientAtrapa:
    """Oddaje strony po kolei i zapisuje, o co go pytano."""

    def __init__(self, tablice: list[dict[str, Any]], uzytkownicy: list[dict[str, Any]]) -> None:
        self._tablice = tablice
        self._uzytkownicy = uzytkownicy
        self.rejestr = RejestrPodgladu()
        self.zapytania: list[str] = []

    async def query(
        self,
        gql: str,
        variables: dict[str, Any] | None = None,
        *,
        etykieta: str | None = None,
        wersja_api: str | None = None,
    ) -> dict[str, Any]:
        self.zapytania.append(etykieta or "?")
        self.rejestr.zapisz(narzedzie=etykieta or "?")
        zmienne = variables or {}
        strona = int(zmienne.get("p", 1))
        limit = int(zmienne.get("limit", 100))
        od = (strona - 1) * limit

        if gql == _PYTANIE_TABLIC:
            return {"boards": self._tablice[od : od + limit]}
        if gql == _PYTANIE_UZYTKOWNIKOW:
            return {"users": self._uzytkownicy[od : od + limit]}
        return {
            "workspaces": [
                {"id": "1", "name": "Jedyny", "account_product": {"id": "9", "kind": "crm"}}
            ]
        }


def _konto() -> Konto:
    return Konto(
        account_id="1",
        nazwa="CXLABS",
        slug="cxlabs",
        is_admin=True,
        is_guest=False,
        tier="enterprise",
        period="yearly",
        max_users=50,
        zakres=Zakres(typ="cale_konto"),
    )


@pytest.mark.asyncio
async def test_paginacja_nie_urywa_sie_na_pierwszej_stronie() -> None:
    """Konto z 250 tablicami ma dać 250, a nie 100.

    To nie jest teoretyczne: `pobierz_workspace` brało samą pierwszą stronę do
    2026-09-22 i przy 120 workspace'ach pokazałoby równe 100 bez ostrzeżenia.
    """
    tablice = [{"id": str(i), "state": "active"} for i in range(250)]
    uzytkownicy = [_osoba(RODZAJ_CZLONEK) for _ in range(LIMIT_UZYTKOWNIKOW + 30)]
    klient = _KlientAtrapa(tablice, uzytkownicy)

    wynik = await zbuduj_inwentarz(klient, _konto(), klient.rejestr)  # type: ignore[arg-type]

    assert wynik.tablic_aktywnych == 250
    assert wynik.uzytkownikow == LIMIT_UZYTKOWNIKOW + 30
    # 250 tablic przy stronie 100 to trzy zapytania, nie jedno.
    assert klient.zapytania.count("inwentarz_tablice") == 3


@pytest.mark.asyncio
async def test_inwentarz_liczy_wywolania_i_niesie_licencje() -> None:
    klient = _KlientAtrapa([{"id": "1", "state": "active"}], [_osoba(RODZAJ_GOSC)])

    wynik = await zbuduj_inwentarz(klient, _konto(), klient.rejestr)  # type: ignore[arg-type]

    assert isinstance(wynik, Inwentarz)
    assert wynik.licencja_tier == "enterprise"
    assert wynik.licencja_max_users == 50
    assert wynik.gosci == 1
    assert wynik.uzytkownikow == 0
    assert wynik.wywolan == len(klient.zapytania)
    assert wynik.po_produktach == {"crm": 1}


@pytest.mark.asyncio
async def test_nieznany_rodzaj_trafia_do_zastrzezen() -> None:
    """Cicha utrata kontroli jest tym, co ją psuje — nieznany rodzaj ma być
    w zastrzeżeniach raportu, nie tylko w logu."""
    klient = _KlientAtrapa([], [_osoba("cos_nowego")])

    wynik = await zbuduj_inwentarz(klient, _konto(), klient.rejestr)  # type: ignore[arg-type]

    assert any("cos_nowego" in u for u in wynik.zastrzezenia)


@pytest.mark.asyncio
async def test_kosz_nie_wchodzi_do_kafelka_tablic() -> None:
    """ZMIERZONE na CXLABS: 3268 obiektów, z czego 1206 w koszu. Kafelek
    pokazujący 3268 byłby prawdziwy i bezużyteczny."""
    tablice = (
        [{"id": f"a{i}", "state": "active"} for i in range(5)]
        + [{"id": f"d{i}", "state": "deleted"} for i in range(9)]
        + [{"id": "arch", "state": "archived"}]
    )
    klient = _KlientAtrapa(tablice, [])

    wynik = await zbuduj_inwentarz(klient, _konto(), klient.rejestr)  # type: ignore[arg-type]

    assert wynik.tablic_aktywnych == 5
    assert wynik.tablic_razem == 15
    assert wynik.tablic_po_stanie == {"active": 5, "archived": 1, "deleted": 9}


@pytest.mark.asyncio
async def test_agenci_zewnetrzni_licza_sie_jako_agenci() -> None:
    """Decyzja Kuby 2026-09-22. Na CXLABS to cztery konta, których kafelek
    wcześniej nie widział — a `pulpit.py` pokazywał je w zakładce „Ludzie"."""
    klient = _KlientAtrapa(
        [],
        [
            _osoba(RODZAJ_AGENT),
            _osoba("external_agent_member"),
            _osoba("external_agent_member"),
            _osoba("external_agent_detached_member"),
        ],
    )

    wynik = await zbuduj_inwentarz(klient, _konto(), klient.rejestr)  # type: ignore[arg-type]

    assert wynik.agentow_ai == 4
    # Rodzaje agentowe są teraz ZNANE, więc nie mają trafiać do zastrzeżeń.
    assert wynik.nieznane_rodzaje == ()
    assert wynik.zastrzezenia == ()


def test_limit_tablic_nie_przekracza_zalecenia_skilla() -> None:
    """Skill `monday-graphql` zaleca ~25 na stronę przy zapytaniach z zagnieżdżeniami.
    To zapytanie ma dwa pola skalarne, więc 100 jest bezpieczne — ale nie 500."""
    assert LIMIT_TABLIC <= 100
