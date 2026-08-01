"""Testy rozpoznania konta i zakresu (etap 3.3), warstwa 1 z 04-test.md.

Kryterium z 03-build.md: „metadane konta w snapshocie, walidacja admina
działa" — przy czym kształt walidacji rozstrzyga `OTWARTE.md` O8, nie
literalny zapis 3.3.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from monday_audit.klient import MondayClient, ZapytanieError
from monday_audit.konto import (
    ZAPYTANIE_KONTO,
    Konto,
    Zakres,
    ZakresError,
    budzet_z_planu,
    rozpoznaj_konto,
)

TOKEN = "tajny-token-klienta"


class RejestrTestowy:
    def __init__(self) -> None:
        self.wpisy: list[dict[str, Any]] = []

    def zapisz(self, **kwargs: Any) -> None:
        self.wpisy.append(kwargs)


def odpowiedz(
    *,
    is_admin: bool = True,
    is_guest: bool = False,
    plan: dict[str, Any] | None = None,
    tier_konta: str | None = None,
) -> httpx.Response:
    # API oddaje `kind`, nie flagi (O17). Parametry zostają boolowskie, bo
    # w treści testów czyta się je lepiej niż literały „admin"/„guest",
    # a mapowanie jest 1:1 (zmierzone na 95 rekordach CXLABS).
    rodzaj = "admin" if is_admin else "guest" if is_guest else "member"
    return httpx.Response(
        200,
        json={
            "data": {
                "me": {
                    "kind": rodzaj,
                    "account": {
                        "id": "12345",
                        "name": "CXLABS",
                        "slug": "cxlabsdigital",
                        "tier": tier_konta,
                        "plan": plan,
                    },
                },
                "complexity": {"query": 6, "after": 9_999_994, "reset_in_x_seconds": 60},
            }
        },
    )


PLAN_PRO = {"tier": "pro", "period": "monthly", "max_users": 25}


@pytest.fixture
async def zbuduj() -> AsyncIterator[Callable[..., MondayClient]]:
    klienci: list[MondayClient] = []

    def fabryka(uchwyt: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> MondayClient:
        egzemplarz = MondayClient(
            TOKEN, RejestrTestowy(), transport=httpx.MockTransport(uchwyt), **kwargs
        )
        klienci.append(egzemplarz)
        return egzemplarz

    yield fabryka

    for egzemplarz in klienci:
        await egzemplarz.zamknij()


# ── granica PII ──────────────────────────────────────────────────────────


def test_zapytanie_nie_pyta_o_imie_ani_o_id_osoby() -> None:
    """PII nie wchodzi do snapshotu od 3.4, tylko od pierwszego zapisu.

    `account { name }` zostaje — to nazwa firmy. `me { name }` i `me { id }`
    nie mają tu być, bo przy audycie klienta to konkretna osoba.
    """
    me = ZAPYTANIE_KONTO.split("account")[0]

    assert "name" not in me, "me { name } to imię i nazwisko — zakaz twardy"
    assert "id" not in me, "me { id } to identyfikator osoby, pseudonimizacja jest w 3.4"
    assert "email" not in ZAPYTANIE_KONTO
    assert "account { id name slug" in ZAPYTANIE_KONTO


async def test_snapshot_nie_zawiera_danych_osoby(zbuduj: Any) -> None:
    klient = zbuduj(lambda _: odpowiedz(plan=PLAN_PRO))
    konto = await rozpoznaj_konto(klient, Zakres.cale_konto())

    payload = json.dumps(konto.do_snapshotu(), ensure_ascii=False)

    assert "@" not in payload, "żadnych adresów e-mail w snapshocie"
    assert set(konto.do_snapshotu()) == {
        "konto",
        "plan",
        "uprawnienia",
        "zakres",
        "pokrycie_pelne",
        "zastrzezenia",
    }


# ── walidacja zakresu (OTWARTE.md O8) ────────────────────────────────────


async def test_cale_konto_wymaga_admina(zbuduj: Any) -> None:
    klient = zbuduj(lambda _: odpowiedz(is_admin=False, plan=PLAN_PRO))

    with pytest.raises(ZakresError, match="nie ma uprawnień admina"):
        await rozpoznaj_konto(klient, Zakres.cale_konto())


async def test_cale_konto_przechodzi_przy_adminie(zbuduj: Any) -> None:
    klient = zbuduj(lambda _: odpowiedz(is_admin=True, plan=PLAN_PRO))

    konto = await rozpoznaj_konto(klient, Zakres.cale_konto())

    assert konto.pokrycie_pelne is True
    assert konto.zastrzezenia == ()


async def test_zakres_workspace_nie_wymaga_admina(zbuduj: Any) -> None:
    """Ścieżka dla tokena Kuby: audyt workspace'u bez uprawnień admina."""
    klient = zbuduj(lambda _: odpowiedz(is_admin=False, plan=PLAN_PRO))

    konto = await rozpoznaj_konto(klient, Zakres.workspace(5513646, "4218568"))

    assert konto.pokrycie_pelne is False
    assert konto.zakres.workspace_ids == ("5513646", "4218568")
    assert any("bez uprawnień admina" in z for z in konto.zastrzezenia)
    assert any("zawężony do 2 workspace'ów" in z for z in konto.zastrzezenia)


async def test_gosc_dostaje_wlasne_zastrzezenie(zbuduj: Any) -> None:
    klient = zbuduj(lambda _: odpowiedz(is_admin=False, is_guest=True, plan=PLAN_PRO))

    konto = await rozpoznaj_konto(klient, Zakres.workspace("1"))

    assert any("gościa" in z for z in konto.zastrzezenia)


def test_zakres_workspace_bez_id_jest_bledem() -> None:
    """Zakres bez ani jednego workspace'u nic nie audytuje — to nie jest run."""
    with pytest.raises(ZakresError, match="nic nie audytuje"):
        Zakres(typ="workspace")


def test_cale_konto_nie_przyjmuje_listy_workspace() -> None:
    with pytest.raises(ZakresError, match="nie przyjmuje listy"):
        Zakres(typ="cale_konto", workspace_ids=("1",))


# ── plan i budżet wywołań ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tier", "oczekiwany"),
    [("free", 500), ("pro", 5_000), ("enterprise", 12_500), ("PRO", 5_000)],
)
def test_budzet_z_potwierdzonych_tierow(tier: str, oczekiwany: int) -> None:
    assert budzet_z_planu(tier) == oczekiwany


@pytest.mark.parametrize("tier", [None, "", "basic", "standard", "wymyslony"])
def test_budzet_nieznanego_tieru_jest_none(tier: str | None) -> None:
    """`basic` i `standard` świadomie nieznane — zgadnięcie limitu to założenie."""
    assert budzet_z_planu(tier) is None


async def test_plan_pro_podnosi_budzet_wywolan(zbuduj: Any) -> None:
    klient = zbuduj(lambda _: odpowiedz(plan=PLAN_PRO), budzet_wywolan=400)

    await rozpoznaj_konto(klient, Zakres.cale_konto())

    assert klient.budzet_wywolan == 5_000


async def test_budzet_recznie_podany_jest_nienaruszalny(zbuduj: Any) -> None:
    """Hamulec człowieka wygrywa z planem konta.

    Zmierzone 2026-07-31: `--budzet-wywolan 2` na koncie enterprise kończyło się
    sufitem 12500, bo plan podnosił wartość zaraz po rozpoznaniu konta. Flaga
    bezpieczeństwa, która nie hamowała — a właśnie na nią liczy człowiek, gdy
    boi się kosztu runu na cudzym koncie.
    """
    klient = zbuduj(lambda _: odpowiedz(plan=PLAN_PRO), budzet_wywolan=3)

    await rozpoznaj_konto(klient, Zakres.cale_konto(), dostosuj_budzet=False)

    assert klient.budzet_wywolan == 3


async def test_brak_planu_zostawia_budzet_i_dopisuje_zastrzezenie(zbuduj: Any) -> None:
    """Zmierzone na CXLABS: `account.plan` zwraca null przy tokenie bez admina."""
    klient = zbuduj(lambda _: odpowiedz(is_admin=False, plan=None), budzet_wywolan=400)

    konto = await rozpoznaj_konto(klient, Zakres.workspace("1"))

    assert klient.budzet_wywolan == 400
    assert konto.tier is None
    assert konto.do_snapshotu()["plan"] is None
    assert any("plan konta niedostępny" in z for z in konto.zastrzezenia)


async def test_nieznany_tier_zostawia_budzet(zbuduj: Any) -> None:
    klient = zbuduj(
        lambda _: odpowiedz(plan={"tier": "basic", "period": "monthly", "max_users": 10}),
        budzet_wywolan=400,
    )

    konto = await rozpoznaj_konto(klient, Zakres.cale_konto())

    assert klient.budzet_wywolan == 400
    assert konto.tier == "basic"
    assert any("nie jest potwierdzony" in z for z in konto.zastrzezenia)


async def test_account_tier_ratuje_budzet_gdy_plan_jest_nullem(zbuduj: Any) -> None:
    """Zmierzone przy 3.6: `account.plan` = null, ale `account.tier` = 'enterprise'.

    Bez tego zapasowego źródła budżet zostawał na wartości domyślnej, mimo
    że plan konta jest znany (`OTWARTE.md` O12).
    """
    klient = zbuduj(
        lambda _: odpowiedz(is_admin=False, plan=None, tier_konta="enterprise"),
        budzet_wywolan=400,
    )

    konto = await rozpoznaj_konto(klient, Zakres.workspace("1"))

    assert konto.tier == "enterprise"
    assert konto.tier_z_pola == "account.tier"
    assert klient.budzet_wywolan == 12_500
    assert not any("plan konta niedostępny" in z for z in konto.zastrzezenia)


async def test_plan_tier_ma_pierwszenstwo_nad_account_tier(zbuduj: Any) -> None:
    klient = zbuduj(lambda _: odpowiedz(plan=PLAN_PRO, tier_konta="enterprise"))

    konto = await rozpoznaj_konto(klient, Zakres.cale_konto(), dostosuj_budzet=False)

    assert konto.tier == "pro"
    assert konto.tier_z_pola == "plan.tier"


async def test_dostosuj_budzet_da_sie_wylaczyc(zbuduj: Any) -> None:
    klient = zbuduj(lambda _: odpowiedz(plan=PLAN_PRO), budzet_wywolan=400)

    await rozpoznaj_konto(klient, Zakres.cale_konto(), dostosuj_budzet=False)

    assert klient.budzet_wywolan == 400


# ── kształt odpowiedzi ───────────────────────────────────────────────────


async def test_metadane_konta_laduja_w_snapshocie(zbuduj: Any) -> None:
    klient = zbuduj(lambda _: odpowiedz(plan=PLAN_PRO))

    fragment = (await rozpoznaj_konto(klient, Zakres.cale_konto())).do_snapshotu()

    assert fragment["konto"] == {"id": "12345", "nazwa": "CXLABS", "slug": "cxlabsdigital"}
    assert fragment["plan"] == {
        "tier": "pro",
        "period": "monthly",
        "max_users": 25,
        "zrodlo_tieru": "plan.tier",
    }
    assert fragment["uprawnienia"] == {"is_admin": True, "is_guest": False}
    assert fragment["zakres"] == {"typ": "cale_konto", "workspace_ids": [], "board_ids": []}


async def test_odpowiedz_bez_me_jest_bledem(zbuduj: Any) -> None:
    klient = zbuduj(
        lambda _: httpx.Response(
            200, json={"data": {"complexity": {"query": 1, "after": 5, "reset_in_x_seconds": 1}}}
        )
    )

    with pytest.raises(ZapytanieError, match="bez pola `me`"):
        await rozpoznaj_konto(klient, Zakres.cale_konto())


async def test_odpowiedz_bez_account_jest_bledem(zbuduj: Any) -> None:
    klient = zbuduj(
        lambda _: httpx.Response(
            200,
            json={
                "data": {
                    "me": {"is_admin": True, "is_guest": False},
                    "complexity": {"query": 1, "after": 5, "reset_in_x_seconds": 1},
                }
            },
        )
    )

    with pytest.raises(ZapytanieError, match=r"bez pola `me\.account`"):
        await rozpoznaj_konto(klient, Zakres.cale_konto())


def test_konto_jest_niemutowalne() -> None:
    konto = Konto(
        account_id="1",
        nazwa="X",
        slug="x",
        is_admin=True,
        is_guest=False,
        tier="pro",
        period="monthly",
        max_users=10,
        zakres=Zakres.cale_konto(),
    )

    with pytest.raises((AttributeError, TypeError)):
        konto.is_admin = False  # type: ignore[misc]
