"""Funkcje wejściowe pakietu dla portalu (faza 6, wariant A).

Portal woła WYŁĄCZNIE `monday_audit.usluga`. Testy pilnują trzech rzeczy:
kształtu kontraktu (portal buduje na nim układ), tego, że krok 1 niczego nie
zapisuje, i tego, że błąd dla człowieka nie niesie treści odpowiedzi API.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from monday_audit import usluga
from monday_audit.inwentarz import Inwentarz
from monday_audit.klient import ZapytanieError
from monday_audit.konto import Konto, Zakres, ZakresError
from monday_audit.podglad_zakresu import WorkspaceDoWyboru
from monday_audit.usluga import KAFELKI, UslugaError, kafelki_z_inwentarza, przeglad_konta


def _inwentarz() -> Inwentarz:
    return Inwentarz(
        konto_nazwa="CXLABS",
        licencja_tier="enterprise",
        licencja_period="yearly",
        licencja_max_users=50,
        workspacow=136,
        workspace_y=(),
        po_produktach={"crm": 4, "service": 2},
        tablic_aktywnych=1315,
        tablic_razem=3268,
        tablic_po_stanie={"active": 2016, "deleted": 1206, "archived": 46},
        tablic_po_typie={"board": 1315, "sub_items_board": 701},
        uzytkownikow=19,
        gosci=13,
        agentow_ai=40,
        podgladajacych=2,
        po_rodzajach={"member": 16, "admin": 3, "guest": 13},
        nieznane_rodzaje=(),
        wywolan=36,
        zastrzezenia=("token bez listy agentów (O20)",),
    )


def test_szesc_kafelkow_w_stalej_kolejnosci() -> None:
    """Portal buduje układ na kolejności i kluczach — zmiana to złamanie umowy."""
    kafelki = kafelki_z_inwentarza(_inwentarz())

    assert tuple(k.klucz for k in kafelki) == KAFELKI
    wartosci = {k.klucz: k.wartosc for k in kafelki}
    assert wartosci == {
        "workspace": 136,
        "tablice": 1315,
        "uzytkownicy": 19,
        "goscie": 13,
        "agenci_ai": 40,
        "licencja": "enterprise",
    }


def test_kafelek_tablic_niesie_to_czego_nie_liczy() -> None:
    """3268 obiektów, w tym 1206 w koszu — kafelek pokazuje 1315 aktywnych
    tablic, ale reszta nie może zniknąć (faza 2a)."""
    tablice = next(k for k in kafelki_z_inwentarza(_inwentarz()) if k.klucz == "tablice")

    assert tablice.szczegoly["razem_obiektow"] == 3268
    assert tablice.szczegoly["po_stanie"]["deleted"] == 1206


def test_do_json_jest_serializowalny_i_niesie_zastrzezenia() -> None:
    wynik = usluga.PrzegladKonta(
        konto_nazwa="CXLABS",
        kafelki=kafelki_z_inwentarza(_inwentarz()),
        wywolan=36,
        zastrzezenia=("x",),
    )

    dokument = json.loads(json.dumps(wynik.do_json(), ensure_ascii=False))

    assert [k["klucz"] for k in dokument["kafelki"]] == list(KAFELKI)
    assert dokument["zastrzezenia"] == ["x"]
    assert dokument["wywolan"] == 36


async def test_przeglad_skleja_konto_i_inwentarz(monkeypatch: pytest.MonkeyPatch) -> None:
    konto = Konto(
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
    widziany_zakres: list[str] = []

    async def rozpoznaj(_: Any, zakres: Zakres) -> Konto:
        widziany_zakres.append(zakres.typ)
        return konto

    async def inwentarz(*_: Any) -> Inwentarz:
        return _inwentarz()

    monkeypatch.setattr(usluga, "rozpoznaj_konto", rozpoznaj)
    monkeypatch.setattr(usluga, "zbuduj_inwentarz", inwentarz)

    wynik = await przeglad_konta("klucz-testowy")

    # Zawsze całe konto — przegląd części konta byłby przeglądem czegoś innego.
    assert widziany_zakres == ["cale_konto"]
    assert wynik.konto_nazwa == "CXLABS"
    assert len(wynik.kafelki) == 6
    assert wynik.zastrzezenia == ("token bez listy agentów (O20)",)


async def test_token_bez_admina_daje_komunikat_dla_czlowieka(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def bez_admina(*_: Any) -> Konto:
        raise ZakresError("żądano audytu całego konta, ale token nie ma uprawnień admina")

    monkeypatch.setattr(usluga, "rozpoznaj_konto", bez_admina)

    with pytest.raises(UslugaError, match="uprawnień administratora"):
        await przeglad_konta("klucz-testowy")


async def test_blad_api_nie_przecieka_do_komunikatu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treść błędu z API bywa fragmentem odpowiedzi — portal pokazuje komunikat
    człowiekowi, więc tej treści w nim nie ma."""

    async def zly_klucz(*_: Any) -> Konto:
        raise ZapytanieError("odpowiedź bez pola `me`: {'konto': 'Tablica Jana Kowalskiego'}")

    monkeypatch.setattr(usluga, "rozpoznaj_konto", zly_klucz)

    with pytest.raises(UslugaError) as blad:
        await przeglad_konta("klucz-testowy")

    assert "Kowalskiego" not in str(blad.value)
    assert blad.value.__cause__ is None


async def test_pusty_klucz_odmawia_bez_wywolania() -> None:
    with pytest.raises(UslugaError, match="brak klucza"):
        await przeglad_konta("   ")


def test_workspace_y_z_nazwami_nie_wchodza_do_kontraktu() -> None:
    """Kafelek niesie liczby, nie listę workspace'ów z nazwami — nazwy pisze
    klient i potrafią nieść nazwisko (O50). Pogłębienie to faza 7."""
    inw = _inwentarz()
    z_nazwami = Inwentarz(
        **{
            **{f: getattr(inw, f) for f in inw.__dataclass_fields__},
            "workspace_y": (WorkspaceDoWyboru(workspace_id="1", nazwa="Jan Kowalski CRM"),),
        }
    )

    dokument = json.dumps(
        [k.do_json() for k in kafelki_z_inwentarza(z_nazwami)], ensure_ascii=False
    )

    assert "Kowalski" not in dokument
