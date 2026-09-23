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


# ── krok 2: analiza_konta (6-2) ──────────────────────────────────────────

import sqlite3  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from monday_audit.baza import polacz, zastosuj_migracje  # noqa: E402
from monday_audit.detektory import Hipoteza  # noqa: E402
from monday_audit.przebieg import zapisz_snapshot  # noqa: E402
from monday_audit.usluga import AnalizaError, analiza_konta  # noqa: E402

NAZWISKO = "Zdzisława Wąchockańska"
PSEUDONIM = "1dcfeabe7fa5d9a7"

ZOMBIE = Hipoteza(
    klasa_id="ZOMBIE_ACCOUNT",
    obiekt_id=PSEUDONIM,
    fakty={
        "user_hash": PSEUDONIM,
        "kind": "member",
        "status": "ACTIVE",
        "last_activity": "2026-06-09T13:01:12Z",
        "obecnosc_w_logach": False,
        "plan_tier": "enterprise",
    },
    budzet_wywolan=0,
)
GHOST = Hipoteza(klasa_id="BOARD_GHOST", obiekt_id="b1", fakty={"wpisow": 0}, budzet_wywolan=2)


@pytest.fixture
def trwala(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    con = polacz(tmp_path / "portal.db")
    zastosuj_migracje(con)
    yield con
    con.close()


@pytest.fixture
def swiat(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Atrapy tam, gdzie zaczyna się świat zewnętrzny: monday i model."""
    wywolania: dict[str, Any] = {"model": 0, "kolejnosc": []}

    async def collector(*, con: Any, client_id: str, **_: Any) -> Any:
        con.execute(
            "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
            "VALUES (?, ?, ?, 'zdzislawa@klient.test')",
            (client_id, PSEUDONIM, NAZWISKO),
        )
        sid = zapisz_snapshot(
            con, client_id=client_id, payload={"meta": {}}, run_at="2026-09-23T00:00:00Z"
        )
        con.commit()
        return SimpleNamespace(snapshot_id=sid, wywolan=40)

    async def model(*_: Any, **__: Any) -> dict[str, Any]:
        wywolania["model"] += 1
        wywolania["kolejnosc"].append("model")
        return wywolania.get(
            "odpowiedz",
            {
                "uwagi": [],
                "pominiete": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "b1", "powod": "świeża"}],
                "zuzycie": {"koszt_usd": 0.4},
                "wywolania_narzedzi": [],
            },
        )

    monkeypatch.setattr(usluga, "wykonaj_run", collector)
    monkeypatch.setattr(usluga, "uruchom_detektory", lambda *_: ([ZOMBIE, GHOST], {}))
    monkeypatch.setattr(usluga, "zbadaj_konto", model)
    return wywolania


def _analiza(trwala: sqlite3.Connection, **zmiany: Any) -> Any:
    parametry: dict[str, Any] = {
        "klucz_anthropic": "",
        "client_id": "cxlabs",
        "sol": b"s" * 32,
        "trwala": trwala,
        **zmiany,
    }
    return analiza_konta("klucz-monday", **parametry)


async def test_kontrakt_rozdziela_zamaskowane_od_raportu_z_nazwiskami(
    trwala: sqlite3.Connection, swiat: dict[str, Any]
) -> None:
    """Sedno 5c w kontrakcie: `uwagi` wolno pokazać i przechować, `raport_html`
    niesie nazwiska i NIE wchodzi do `do_json` — portal ma go oddać człowiekowi,
    a nie przepuścić przez swoje API i logi."""
    wynik = await _analiza(trwala)

    assert NAZWISKO in (wynik.raport_html or "")
    dokument = json.dumps(wynik.do_json(), ensure_ascii=False)
    for slad_osoby in (NAZWISKO, PSEUDONIM, "2026-06-09", "zdzislawa@"):
        assert slad_osoby not in dokument
    assert wynik.do_json()["ma_raport"] is True
    assert wynik.uwagi[0]["dowod"]["user_hash"] == "[OSOBA]"
    assert wynik.wywolan_monday == 40


async def test_nic_o_osobie_w_bazie_portalu(
    trwala: sqlite3.Connection, swiat: dict[str, Any]
) -> None:
    await _analiza(trwala)

    zrzut = "\n".join(
        str(dict(w))
        for t in [
            r["name"] for r in trwala.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for w in trwala.execute(f'SELECT * FROM "{t}"')  # noqa: S608
    )
    for slad_osoby in (NAZWISKO, PSEUDONIM, "zdzislawa@"):
        assert slad_osoby not in zrzut
    assert trwala.execute("SELECT COUNT(*) FROM osoby_mapowanie").fetchone()[0] == 0


async def test_szacunek_idzie_do_wolajacego_przed_modelem(
    trwala: sqlite3.Connection, swiat: dict[str, Any]
) -> None:
    """Ekran portalu ma móc pokazać koszt, ZANIM ruszy model."""
    await _analiza(
        trwala, przed_sesja=lambda p: swiat["kolejnosc"].append(("szacunek", p.do_modelu))
    )

    assert swiat["kolejnosc"] == [("szacunek", 1), "model"]


async def test_tylko_szacunek_nie_woła_modelu_i_nie_zaklada_runu(
    trwala: sqlite3.Connection, swiat: dict[str, Any]
) -> None:
    wynik = await _analiza(trwala, tylko_szacunek=True)

    assert swiat["model"] == 0
    assert wynik.run_id is None
    assert wynik.szacunek.koszt_usd > 0
    assert trwala.execute("SELECT COUNT(*) FROM runy").fetchone()[0] == 0


async def test_odpowiedz_bez_struktury_wraca_w_bledzie_a_run_jest_przerwany(
    trwala: sqlite3.Connection, swiat: dict[str, Any]
) -> None:
    """Za sesję już zapłacono — surowa odpowiedź nie może zniknąć, ale też nie
    trafia na dysk. Wraca w wyjątku, w pamięci."""
    swiat["odpowiedz"] = {"cos_innego": 1, "zuzycie": {}}

    with pytest.raises(AnalizaError) as blad:
        await _analiza(trwala, run_id="r-zly")

    assert blad.value.odpowiedz_modelu["cos_innego"] == 1
    status = trwala.execute("SELECT status FROM runy WHERE run_id = 'r-zly'").fetchone()[0]
    assert status == "przerwany"


async def test_padniety_zapis_nie_zabiera_wyniku(
    trwala: sqlite3.Connection, swiat: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Biblioteczna wersja zasady „najpierw wynik, potem zapis": zapis, który
    padł, wraca jako `blad_zapisu`, a nie wyjątek zabierający opłacony wynik."""

    def zepsuty(*_: Any, **__: Any) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(usluga, "zapisz_uwagi", zepsuty)

    wynik = await _analiza(trwala)

    assert wynik.blad_zapisu == "zapis minimalny padł: OperationalError"
    assert len(wynik.uwagi) == 1
    assert wynik.raport_html is not None
