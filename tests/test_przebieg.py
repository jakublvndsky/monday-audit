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
from monday_audit.przebieg import (
    collector_ver,
    otworz_run,
    przerwij_run,
    wykonaj_run,
    zapisz_snapshot,
    zapisz_zuzycie,
)

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
        elif "agents (" in gql or "agent_runs (" in gql:
            # Realny dzisiejszy stan: pola nie ma w wersji przypiętej (O20).
            # Sonda MUSI to przełknąć i nie przerwać runu.
            return httpx.Response(
                200,
                json={"errors": [{"message": 'Cannot query field "agents" on type "Query".'}]},
            )
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
        "agenci",
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
    # Run jest OZNACZONY jako przerwany, nie zostawiony w `w_toku`.
    # Poprzednia wersja tego testu wymagała `w_toku` i uzasadniała to tak:
    # „widać, że coś się urwało". Nie widać — po kilku nieudanych próbach
    # `w_toku` znaczy tylko „nie wiadomo", a etap 6 opiera na tym polu
    # monitoring. Sprawdzone na bazie produkcyjnej: pięć runów wisiało
    # w `w_toku` bez żadnego śladu, dlaczego.
    wiersz = con.execute("SELECT status, snapshot_id, finished_at FROM runy").fetchone()
    assert wiersz["status"] == "przerwany"
    assert wiersz["finished_at"] is not None, "bez tego nie policzysz, jak długo run żył"
    assert wiersz["snapshot_id"] is None


async def test_zawezony_zakres_dopisuje_uwagi(con: sqlite3.Connection) -> None:
    """Dwa zapytania są z natury na poziomie konta — trzeba to powiedzieć.

    Test pilnuje SENSU, nie brzmienia: teksty przeszły na język klienta
    (2026-08-25), bo niosły `users`, `board_id` i odnośnik „OTWARTE.md O12" —
    nazwy z API i nasz plik wewnętrzny. Wiązanie testu z dosłownym zdaniem
    zamieniłoby każdą poprawkę językową w czerwony test.
    """
    raport = await uruchom(con, zakres=Zakres.workspace("6576039"))

    assert any("osób" in z and "całe konto" in z for z in raport.zastrzezenia)
    assert any("automatyzacji" in z for z in raport.zastrzezenia)
    # Żadnych nazw z API ani odnośników do naszych dokumentów w tekście,
    # który czyta klient.
    zlepek = " ".join(raport.zastrzezenia)
    for przeciek in ("OTWARTE.md", "board_id", "`users`", "Int32", "--wszystkie-logi"):
        assert przeciek not in zlepek, f"techniczny przeciek w tekście dla klienta: {przeciek}"


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


async def test_domkniety_run_nie_da_sie_przerwac(con: sqlite3.Connection) -> None:
    """`przerwij_run` rusza WYŁĄCZNIE wiersz w `w_toku`.

    Gdyby ruszał każdy, błąd w kodzie po zapisie snapshotu — na przykład
    w eksporcie do pliku — przepisałby zakończony run na `przerwany` i skasował
    znacznik końca. Snapshot by został, a jego run wyglądałby na nieudany.
    """
    raport = await uruchom(con, api())
    przerwij_run(con, run_id=raport.run_id, powod="próba po domknięciu")

    wiersz = con.execute("SELECT status FROM runy WHERE run_id = ?", (raport.run_id,)).fetchone()
    assert wiersz["status"] == "zakonczony"


# ── zużycie modelu: jedno miejsce zapisu ─────────────────────────────────
#
# ZMIERZONA USTERKA (2026-08-11). Zapis zużycia stał w dwóch miejscach i zapisywał
# różne rzeczy: `cli_agent` dwie liczby z czterech, `web/run` — ścieżka panelowa —
# ŻADNEJ. Pierwszy pełny audyt z panelu (86 hipotez, 7,09 USD) ma więc
# `tokens_in = NULL` i nie da się powiedzieć, z czego ten koszt się składa.


def _zuzycie() -> dict[str, object]:
    return {
        "tokens_in": 1000,
        "tokens_out": 200,
        "tokens_cache_read": 50_000,
        "tokens_cache_write": 3_000,
        "koszt_usd": 1.234567,
    }


def _per_hipoteza() -> list[dict[str, object]]:
    return [
        {
            "klasa_id": "ZOMBIE_ACCOUNT",
            "obiekt_id": "abc123",
            "tokens_in": 600,
            "tokens_out": 120,
            "tokens_cache_read": 30_000,
            "tokens_cache_write": 3_000,
            "koszt_usd": 0.8,
            "sekund": 41.5,
            "wywolan_narzedzi": 0,
            "byl_finding": True,
        },
        {
            "klasa_id": "BOARD_GHOST",
            "obiekt_id": "5097387646",
            "tokens_in": 400,
            "tokens_out": 80,
            "tokens_cache_read": 20_000,
            "tokens_cache_write": 0,
            "koszt_usd": 0.434567,
            "sekund": 22.25,
            "wywolan_narzedzi": 3,
            "byl_finding": False,
        },
    ]


def _run_do_testu(con: sqlite3.Connection, run_id: str = "r-zuzycie") -> str:
    con.execute(
        "INSERT INTO snapshots (id, client_id, run_at, collector_ver, payload) "
        "VALUES (99, 'cxlabs', '2026-08-11T00:00:00+00:00', '0.1.0', '{}')"
    )
    con.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at) "
        "VALUES (?, 'cxlabs', 99, 'w_toku', '2026-08-11T00:00:00+00:00')",
        (run_id,),
    )
    con.commit()
    return run_id


def test_zuzycie_zapisuje_wszystkie_cztery_liczby(tmp_path: Path) -> None:
    """Cztery liczby, nie dwie. Bez tokenów CACHE nie wiadomo, czy caching działa.

    Przy prompt cachingu (D2) większość wejścia idzie przez cache, więc sam
    `tokens_in` pokazywałby wartość bliską zeru i sugerował, że run był tani.
    """
    con = polacz(tmp_path / "z.db")
    zastosuj_migracje(con)
    run_id = _run_do_testu(con)

    zapisz_zuzycie(con, run_id, _zuzycie(), _per_hipoteza())

    w = con.execute(
        "SELECT tokens_in, tokens_out, tokens_cache_read, tokens_cache_write, "
        "koszt_usd, sekund_agenta FROM runy WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert w["tokens_in"] == 1000
    assert w["tokens_out"] == 200
    assert w["tokens_cache_read"] == 50_000, "brak tokenów cache_read"
    assert w["tokens_cache_write"] == 3_000, "brak tokenów cache_write"
    assert w["koszt_usd"] == pytest.approx(1.234567)
    # Czas liczony Z WIERSZY per hipoteza, nie osobnym zegarem — dwa niezależne
    # pomiary dałyby dwie liczby i pytanie, której wierzyć.
    assert w["sekund_agenta"] == pytest.approx(63.75)
    con.close()


def test_rozbicie_per_hipoteza_zgadza_sie_z_suma(tmp_path: Path) -> None:
    """Bez tej zgodności rozbicie jest fikcją, a router modelu stanie na złej liczbie."""
    con = polacz(tmp_path / "z.db")
    zastosuj_migracje(con)
    run_id = _run_do_testu(con)

    zapisz_zuzycie(con, run_id, _zuzycie(), _per_hipoteza())

    w = con.execute(
        "SELECT COUNT(*) n, SUM(koszt_usd) koszt, SUM(sekund) sek FROM zuzycie_hipotez "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert w["n"] == 2
    assert w["koszt"] == pytest.approx(1.234567), "suma per hipoteza ≠ koszt runu"
    assert w["sek"] == pytest.approx(63.75)
    con.close()


def test_hipoteza_bez_findingu_tez_jest_zapisana(tmp_path: Path) -> None:
    """Odrzucona hipoteza KOSZTUJE i to jest istotna liczba.

    Jeśli większość hipotez kończy się odrzuceniem, płacimy głównie za
    dowiadywanie się, że czegoś NIE MA — a to zmienia, co warto optymalizować.
    """
    con = polacz(tmp_path / "z.db")
    zastosuj_migracje(con)
    run_id = _run_do_testu(con)

    zapisz_zuzycie(con, run_id, _zuzycie(), _per_hipoteza())

    wiersze = {
        w["klasa_id"]: w["byl_finding"]
        for w in con.execute(
            "SELECT klasa_id, byl_finding FROM zuzycie_hipotez WHERE run_id = ?", (run_id,)
        )
    }
    assert wiersze == {"ZOMBIE_ACCOUNT": 1, "BOARD_GHOST": 0}
    con.close()


def test_obie_sciezki_zapisuja_tyle_samo() -> None:
    """`cli_agent` i `web/run` MUSZĄ wołać tę samą funkcję.

    To jest ta usterka: dwa miejsca robiące to samo rozjechały się i ścieżka
    panelowa przestała zapisywać zużycie. Test czyta kod, bo różnicy nie widać
    w żadnym pojedynczym wywołaniu — widać ją tylko w porównaniu obu ścieżek.
    """
    korzen = Path(__file__).resolve().parent.parent / "src" / "monday_audit"
    cli = (korzen / "cli_agent.py").read_text(encoding="utf-8")
    web = (korzen / "web" / "run.py").read_text(encoding="utf-8")

    for nazwa, tresc in (("cli_agent", cli), ("web/run", web)):
        assert "zapisz_zuzycie(" in tresc, f"{nazwa} nie zapisuje zużycia wspólną funkcją"
        # Żadna ścieżka nie może zapisywać tokenów własnym UPDATE-em — wtedy znowu
        # rozjechałyby się przy pierwszej zmianie.
        assert "tokens_in = ?" not in tresc, f"{nazwa} ma własny zapis tokenów"
        assert "koszt_usd = ?" not in tresc, f"{nazwa} ma własny zapis kosztu"
