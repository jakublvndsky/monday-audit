"""Testy zapisu snapshotu i przebiegu (etap 3.8), warstwa 1 z 04-test.md.

Najważniejsze tutaj: run otwiera się PRZED pierwszym wywołaniem (inaczej
`wywolania.run_id` nie ma do czego wskazywać), walidacja PII idzie PRZED
zapisem (snapshot jest niemutowalny), a snapshot faktycznie ląduje w bazie
razem z domkniętym runem.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.klient import Postep
from monday_audit.konto import Zakres
from monday_audit.osoby import PseudonimizacjaError
from monday_audit.przebieg import collector_ver, otworz_run, wykonaj_run, zapisz_snapshot

TOKEN = "tajny-token-klienta"
SOL = b"sol-testowa-dluga-na-tyle-ze-przechodzi"
KLIENT = "cxlabs"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


def _complexity() -> dict[str, int]:
    return {"query": 100, "after": 9_000_000, "reset_in_x_seconds": 60}


LUDZIE = [
    {
        "id": "101",
        "name": "Zdzisława Wąchockańska",
        "email": "z@przyklad.test",
        "kind": "admin",
        "status": "ACTIVE",
        "is_deleted": False,
        "is_email_confirmed": True,
        "created_at": "2024-01-15T10:00:00Z",
        "became_active_at": "2024-01-16T09:00:00Z",
        "last_activity": "2026-07-30T20:00:00Z",
        "title": "Dyrektor",
        "teams": [],
    }
]

TABLICA = {
    "id": "5097387646",
    "name": "Lista pomysłów",
    "state": "active",
    "board_kind": "public",
    "items_count": 28,
    "created_at": "2024-03-01T09:00:00Z",
    "updated_at": "2026-07-29T18:00:00Z",
    "workspace": {"id": "6576039", "name": "monday AI Agents"},
    "owners": [{"id": "101"}],
    "subscribers": [{"id": "101"}],
    "columns": [{"id": "k1", "title": "Status", "type": "status"}],
}


def api(
    *,
    ludzie: list[dict[str, Any]] | None = None,
    tablice: list[dict[str, Any]] | None = None,
) -> Any:
    """Atrapa całego API monday, rozdzielana po treści zapytania."""
    ludzie = LUDZIE if ludzie is None else ludzie
    tablice = [TABLICA] if tablice is None else tablice

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        gql = cialo["query"]
        zmienne = cialo.get("variables") or {}
        strona = zmienne.get("p", 1)

        if "me {" in gql:
            dane: dict[str, Any] = {
                "me": {
                    "kind": "admin",
                    "account": {
                        "id": "12345",
                        "name": "CXLABS",
                        "slug": "cxlabsdigital",
                        "tier": "enterprise",
                        "plan": None,
                    },
                }
            }
        elif "users (" in gql:
            dane = {"users": ludzie if strona == 1 else []}
        elif "activity_logs" in gql:
            dane = {
                "boards": [
                    {
                        "id": zmienne["ids"][0],
                        "activity_logs": [
                            {
                                "id": "log-1",
                                "event": "update_column_value",
                                "entity": "pulse",
                                "created_at": "17830789794688296",
                                "user_id": "101",
                            }
                        ],
                    }
                ]
            }
        elif "boards (" in gql:
            dane = {"boards": tablice if strona == 1 else []}
        elif "account_trigger_statistics" in gql:
            dane = {
                "account_trigger_statistics": {
                    "id": "x",
                    "success": 1226,
                    "failure": 11,
                    "total": 1237,
                }
            }
        elif "account_triggers_statistics_by_entity_id" in gql:
            dane = {
                "account_triggers_statistics_by_entity_id": {
                    "id": "y",
                    "automation_statistics": {},
                    "workflow_statistics": {},
                }
            }
        elif "trigger_events" in gql:
            dane = {"trigger_events": {"triggerEvents": []}}
        else:  # pragma: no cover
            raise AssertionError(f"nieobsłużone zapytanie: {gql[:120]}")

        return httpx.Response(200, json={"data": {**dane, "complexity": _complexity()}})

    return uchwyt


async def uruchom(con: sqlite3.Connection, uchwyt: Any = None, **kwargs: Any) -> Any:
    return await wykonaj_run(
        token=TOKEN,
        con=con,
        client_id=KLIENT,
        zakres=kwargs.pop("zakres", Zakres.tablice("5097387646")),
        sol=SOL,
        transport=httpx.MockTransport(uchwyt or api()),
        **kwargs,
    )


# ── kolejność operacji ───────────────────────────────────────────────────


def test_run_powstaje_przed_pierwszym_wywolaniem(con: sqlite3.Connection) -> None:
    """`wywolania.run_id` to NOT NULL REFERENCES — bez runu nie ma logowania."""
    rejestr = otworz_run(con, run_id="r1", client_id=KLIENT)

    rejestr.zapisz(narzedzie="graphql:konto", complexity=6)

    wiersz = con.execute("SELECT status, started_at FROM runy WHERE run_id = 'r1'").fetchone()
    assert wiersz["status"] == "w_toku"
    assert wiersz["started_at"]
    assert con.execute("SELECT count(*) FROM wywolania").fetchone()[0] == 1


def test_snapshot_zapisuje_sie_z_wersja_collectora(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz_snapshot(
        con, client_id=KLIENT, payload={"meta": {"x": 1}}, run_at="2026-07-30T21:00:00+00:00"
    )

    wiersz = con.execute(
        "SELECT client_id, collector_ver, payload FROM snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    assert wiersz["client_id"] == KLIENT
    assert wiersz["collector_ver"] == collector_ver()
    assert json.loads(wiersz["payload"]) == {"meta": {"x": 1}}


def test_snapshot_jest_niemutowalny_po_zapisie(con: sqlite3.Connection) -> None:
    """Dlatego walidacja PII musi iść PRZED insertem (D7)."""
    snapshot_id = zapisz_snapshot(
        con, client_id=KLIENT, payload={"a": 1}, run_at="2026-07-30T21:00:00+00:00"
    )

    with pytest.raises(sqlite3.IntegrityError, match="niemutowalny"):
        con.execute("UPDATE snapshots SET payload = '{}' WHERE id = ?", (snapshot_id,))
        con.commit()


def test_collector_ver_jest_konkretna_wersja() -> None:
    """Jeden z czterech elementów pinowania z etapu 5."""
    wersja = collector_ver()

    assert wersja != "nieznana"
    assert wersja[0].isdigit()


# ── pełny przebieg ───────────────────────────────────────────────────────


async def test_przebieg_zapisuje_snapshot_i_domyka_run(con: sqlite3.Connection) -> None:
    raport = await uruchom(con)

    wiersz = con.execute(
        "SELECT snapshot_id, status, wywolania_monday, complexity_suma, finished_at "
        "FROM runy WHERE run_id = ?",
        (raport.run_id,),
    ).fetchone()

    assert wiersz["status"] == "zakonczony"
    assert wiersz["snapshot_id"] == raport.snapshot_id
    assert wiersz["wywolania_monday"] == raport.wywolan > 0
    assert wiersz["complexity_suma"] == raport.complexity > 0
    assert wiersz["finished_at"]


async def test_payload_ma_wszystkie_sekcje(con: sqlite3.Connection) -> None:
    raport = await uruchom(con)

    payload = json.loads(
        con.execute("SELECT payload FROM snapshots WHERE id = ?", (raport.snapshot_id,)).fetchone()[
            "payload"
        ]
    )

    assert set(payload) == {
        "meta",
        "konto",
        "uzytkownicy",
        "tablice",
        "automatyzacje",
        "aktywnosc",
    }
    assert payload["meta"]["client_id"] == KLIENT
    assert payload["meta"]["collector_ver"] == collector_ver()
    assert payload["meta"]["okno_dni"] == 90


async def test_imie_w_nazwie_tablicy_jest_redagowane(con: sqlite3.Connection) -> None:
    """Klient nazwał tablicę imieniem osoby — podmieniamy na pseudonim.

    Wycięcie nazwy zabrałoby sygnał (fakt, że tablica jest nazwana po kimś,
    jest informacją audytową), a zostawienie jej wpuściłoby PII do snapshotu.
    """
    zatruta = api(tablice=[{**TABLICA, "name": "Zdzisława Wąchockańska prywatna"}])

    raport = await uruchom(con, zatruta)

    payload = con.execute(
        "SELECT payload FROM snapshots WHERE id = ?", (raport.snapshot_id,)
    ).fetchone()["payload"]

    assert "Zdzisława Wąchockańska" not in payload
    assert "[OSOBA:" in payload
    assert "prywatna" in payload, "reszta nazwy zostaje — to nie jest PII"
    assert raport.liczby["zredagowanych_pii_w_tresci"] == 1
    assert json.loads(payload)["meta"]["zredagowanych_pii"] == 1


async def test_nieznany_email_w_tresci_przerywa_przed_zapisem(con: sqlite3.Connection) -> None:
    """Adres, którego nie ma w mapowaniu, to niespodzianka — run staje.

    Snapshot jest niemutowalny, więc walidacja MUSI iść przed insertem.
    """
    zatruta = api(tablice=[{**TABLICA, "name": "Kontakt ktos-obcy@firma.test"}])

    with pytest.raises(PseudonimizacjaError, match="adresu e-mail"):
        await uruchom(con, zatruta)

    assert con.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
    # Run został otwarty, ale nie domknięty — widać, że coś się urwało.
    wiersz = con.execute("SELECT status, snapshot_id FROM runy").fetchone()
    assert wiersz["status"] == "w_toku"
    assert wiersz["snapshot_id"] is None


async def test_zawezony_zakres_dopisuje_uwagi(con: sqlite3.Connection) -> None:
    """Dwa zapytania są z natury na poziomie konta — trzeba to powiedzieć."""
    raport = await uruchom(con, zakres=Zakres.workspace("6576039"))

    assert any("lista użytkowników" in z for z in raport.zastrzezenia)
    assert any("statystyki uruchomień" in z for z in raport.zastrzezenia)


async def test_pelny_zakres_nie_dopisuje_uwag_o_zawezeniu(con: sqlite3.Connection) -> None:
    raport = await uruchom(con, zakres=Zakres.cale_konto())

    assert not any("zawężony" in z for z in raport.zastrzezenia)


async def test_raport_ma_liczby_i_discovery(con: sqlite3.Connection) -> None:
    raport = await uruchom(con)

    assert raport.liczby["uzytkownikow"] == 1
    assert raport.liczby["tablic"] == 1
    assert raport.liczby["uruchomien_automatyzacji"] == 1237
    assert raport.liczby["mapowan_pii"] == 1
    assert "osoby.last_activity_dostepne" in raport.discovery
    assert "tablice.items_count_dostepne" in raport.discovery
    assert raport.bajtow_payloadu > 0

    opis = raport.opis()
    assert "RAPORT Z RUNU" in opis
    assert raport.run_id in opis


async def test_postep_jest_przekazywany(con: sqlite3.Connection) -> None:
    zdarzenia: list[Postep] = []

    await uruchom(con, postep=zdarzenia.append)

    assert zdarzenia, "wskaźnik postępu musi dostawać zdarzenia z całego przebiegu"
    assert zdarzenia[-1].wywolania > 1


async def test_mapowanie_pii_ma_wpisy_a_snapshot_nie(con: sqlite3.Connection) -> None:
    raport = await uruchom(con)

    payload = con.execute(
        "SELECT payload FROM snapshots WHERE id = ?", (raport.snapshot_id,)
    ).fetchone()["payload"]
    mapowanie = con.execute(
        "SELECT imie_nazwisko, email FROM osoby_mapowanie WHERE client_id = ?", (KLIENT,)
    ).fetchall()

    assert len(mapowanie) == 1
    assert mapowanie[0]["imie_nazwisko"] == "Zdzisława Wąchockańska"
    assert "Zdzisława" not in payload
    assert "@" not in payload
