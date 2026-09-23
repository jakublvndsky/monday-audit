"""Testy collectora automatyzacji (etap 3.6), warstwa 1 z 04-test.md.

Najważniejszy test: `test_sufit_sond_odnotowuje_pominiete`. Cichy limit
wyglądałby w raporcie jak „sprawdziliśmy wszystko".
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from monday_audit.automatyzacje import (
    MAKS_SOND,
    ROZMIAR_STRONY,
    WynikAutomatyzacji,
    sonduj_tablice,
    statystyki_konta,
    zbierz_automatyzacje,
)
from monday_audit.klient import MondayClient
from monday_audit.osoby import PseudonimizacjaError

TOKEN = "tajny-token-klienta"


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


def odpowiedz(dane: dict[str, Any], *, koszt: int = 50) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                **dane,
                "complexity": {"query": koszt, "after": 9_000_000, "reset_in_x_seconds": 60},
            }
        },
    )


STATYSTYKI = {
    "account_trigger_statistics": {
        "id": "account-trigger-statistics",
        "success": 1226,
        "failure": 11,
        "total": 1237,
    }
}

BLEDY = {
    "account_triggers_statistics_by_entity_id": {
        "id": "account-triggers-by-entity-id",
        "automation_statistics": {
            "156134268": {"total": 1, "Brak wyników": 1},
            "156134682": {"total": 2, "Brak plików do odczytu": 2},
        },
        "workflow_statistics": {},
    }
}

SUKCESY = {
    "account_triggers_statistics_by_entity_id": {
        "id": "account-triggers-by-entity-id",
        "automation_statistics": {"156134268": {"total": 40}},
        "workflow_statistics": {},
    }
}


def zdarzenie(stan: str = "success", *, testowe: bool = False) -> dict[str, Any]:
    return {
        "eventKind": "column_changed",
        "eventState": stan,
        "triggerStartedAt": "2026-07-29T10:00:00Z",
        "entityKind": "automation",
        "is_test_run": testowe,
    }


def router(
    zdarzenia_per_tablica: dict[str, list[dict[str, Any]]] | None = None,
    *,
    historia: dict[int, list[dict[str, Any]]] | None = None,
    kroki: dict[str, list[dict[str, Any]]] | None = None,
) -> Any:
    """Rozdziela odpowiedzi po treści zapytania, tak jak prawdziwe API."""
    zdarzenia_per_tablica = zdarzenia_per_tablica or {}
    historia = historia or {}
    kroki = kroki or {}

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        gql = cialo["query"]

        if "account_trigger_statistics" in gql:
            return odpowiedz(STATYSTYKI)
        if "account_triggers_statistics_by_entity_id" in gql:
            status = cialo["variables"]["status"]
            return odpowiedz(BLEDY if status == "failure" else SUKCESY)
        if "block_events" in gql:
            uuid = cialo["variables"]["uuid"]
            return odpowiedz({"block_events": {"blockEvents": kroki.get(uuid, [])}})
        if "trigger_events" in gql and "automationIds" in cialo["variables"]["f"]:
            (aid,) = cialo["variables"]["f"]["automationIds"]
            return odpowiedz({"trigger_events": {"triggerEvents": historia.get(aid, [])}})
        if "trigger_events" in gql:
            board = cialo["variables"]["f"].get("boardId")
            return odpowiedz(
                {"trigger_events": {"triggerEvents": zdarzenia_per_tablica.get(board, [])}}
            )
        raise AssertionError(f"nieoczekiwane zapytanie: {gql[:80]}")

    return uchwyt


# ── poziom konta: trzy wywołania, niezależnie od wielkości ───────────────


async def test_statystyki_konta_to_jedno_wywolanie(zbuduj: Any) -> None:
    """3.6 ostrzega przed krokiem liniowym per tablica. Poziom konta go nie ma."""
    klient = zbuduj(router())

    liczby, discovery = await statystyki_konta(klient)

    assert liczby == {"sukces": 1226, "bledow": 11, "razem": 1237}
    assert discovery["uruchomienia_dostepne"] is True
    assert klient.liczba_wywolan == 1


async def test_bez_tablic_kosztuje_cztery_wywolania_plus_przebiegi(zbuduj: Any) -> None:
    """Koszt poziomu konta jest STAŁY, niezależny od wielkości konta.

    Cztery, nie trzy: statystyki konta plus jedno zapytanie na każdy z trzech
    stanów uruchomień. Wcześniej pytaliśmy tylko o `failure` i `success`,
    a liczby sukcesów wyrzucaliśmy — przez co AUTOMATION_DEAD nie mógł policzyć
    udziału błędów ani zobaczyć automatyzacji zatrzymanej limitem.

    Od O52 dochodzi historia każdej automatyzacji z błędem — tu dwóch, bez
    uruchomień nieudanych w oknie, więc bez drugiego zapytania o kroki.
    """
    klient = zbuduj(router())

    wynik = await zbierz_automatyzacje(klient)

    assert klient.liczba_wywolan == 4 + 2
    assert wynik.uruchomien_razem == 1237
    assert wynik.sondy == ()


async def test_o1_rozstrzygniete_uruchomienia_sa_dostepne(zbuduj: Any) -> None:
    """O1 pytało, czy API zwraca liczbę uruchomień. Zwraca — na poziomie konta."""
    klient = zbuduj(router())

    wynik = await zbierz_automatyzacje(klient)

    assert wynik.discovery["uruchomienia_dostepne"] is True
    assert wynik.do_snapshotu()["uruchomienia"] == {
        "sukces": 1226,
        "bledow": 11,
        "razem": 1237,
    }


async def test_brak_statystyk_jest_odnotowany_a_nie_przemilczany(zbuduj: Any) -> None:
    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        if "account_trigger_statistics" in cialo["query"]:
            return odpowiedz({"account_trigger_statistics": None})
        if "account_triggers_statistics_by_entity_id" in cialo["query"]:
            return odpowiedz(SUKCESY)
        return odpowiedz({"trigger_events": {"triggerEvents": []}})

    klient = zbuduj(uchwyt)
    wynik = await zbierz_automatyzacje(klient)

    assert wynik.uruchomien_razem is None
    assert wynik.discovery["uruchomienia_dostepne"] is False


async def test_automatyzacje_z_bledami_maja_powody(zbuduj: Any) -> None:
    klient = zbuduj(router())

    wynik = await zbierz_automatyzacje(klient)

    assert [a["automation_id"] for a in wynik.automatyzacje_z_bledami] == [
        "156134268",
        "156134682",
    ]
    assert wynik.automatyzacje_z_bledami[1]["total"] == 2
    assert wynik.automatyzacje_z_bledami[1]["powody"] == {"Brak plików do odczytu": 2}


async def test_statystyki_jako_string_json_tez_przechodza(zbuduj: Any) -> None:
    """monday zwraca JSON czasem jako string — pole ma typ JSON, nie obiekt."""
    jako_string = {
        "account_triggers_statistics_by_entity_id": {
            "id": "x",
            "automation_statistics": json.dumps({"999": {"total": 3, "Powód": 3}}),
            "workflow_statistics": None,
        }
    }

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        if "account_trigger_statistics" in cialo["query"]:
            return odpowiedz(STATYSTYKI)
        if "account_triggers_statistics_by_entity_id" in cialo["query"]:
            return odpowiedz(jako_string)
        return odpowiedz({"trigger_events": {"triggerEvents": []}})

    klient = zbuduj(uchwyt)
    wynik = await zbierz_automatyzacje(klient)

    assert wynik.automatyzacje_z_bledami[0]["automation_id"] == "999"


# ── sonda per tablica i jej sufit ────────────────────────────────────────


async def test_sonda_zawezona_do_wskazanej_tablicy(zbuduj: Any) -> None:
    wyslane: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        wyslane.append(cialo)
        return router({"5097387646": []})(zapytanie)

    klient = zbuduj(uchwyt)
    sondy, pominietych = await sonduj_tablice(klient, ["5097387646"])

    assert pominietych == 0
    assert sondy[0].board_id == "5097387646"
    assert sondy[0].zdarzen == 0
    # `boardId` jest Stringiem, więc 10-cyfrowy identyfikator przechodzi —
    # w przeciwieństwie do zepsutego Int32 w `account_trigger_statistics`.
    filtr = wyslane[-1]["variables"]["f"]
    assert filtr == {"boardId": "5097387646"}


async def test_sufit_sond_odnotowuje_pominiete(zbuduj: Any) -> None:
    """Cichy limit wyglądałby w raporcie jak „sprawdziliśmy wszystko"."""
    klient = zbuduj(router())
    tablice = [str(n) for n in range(30)]

    sondy, pominietych = await sonduj_tablice(klient, tablice, maks_sond=4)

    assert len(sondy) == 4
    assert pominietych == 26
    assert klient.liczba_wywolan == 4


async def test_pominiete_tablice_ida_do_snapshotu(zbuduj: Any) -> None:
    klient = zbuduj(router())

    wynik = await zbierz_automatyzacje(klient, board_ids=[str(n) for n in range(12)], maks_sond=3)

    assert wynik.podsumowanie()["tablic_sondowanych"] == 3
    assert wynik.podsumowanie()["tablic_pominietych"] == 9
    assert wynik.do_snapshotu()["podsumowanie"]["tablic_pominietych"] == 9


async def test_domyslny_sufit_jest_maly(zbuduj: Any) -> None:
    """Wolumen jest tu ryzykiem, nie wygodą — domyślnie sondujemy niewiele."""
    assert MAKS_SOND <= 10

    klient = zbuduj(router())
    wynik = await zbierz_automatyzacje(klient, board_ids=[str(n) for n in range(50)])

    assert len(wynik.sondy) == MAKS_SOND
    assert wynik.pominietych_tablic == 50 - MAKS_SOND


async def test_pelna_strona_jest_oznaczona_jako_urwana(zbuduj: Any) -> None:
    """200 zdarzeń znaczy „jest więcej", nie „tyle jest"."""
    klient = zbuduj(router({"1": [zdarzenie() for _ in range(ROZMIAR_STRONY)]}))

    sondy, _ = await sonduj_tablice(klient, ["1"])

    assert sondy[0].strona_pelna is True
    assert sondy[0].zdarzen == ROZMIAR_STRONY


async def test_sonda_liczy_stany_i_testy(zbuduj: Any) -> None:
    zdarzenia = [
        zdarzenie("success"),
        zdarzenie("success"),
        zdarzenie("failure"),
        zdarzenie("success", testowe=True),
    ]
    klient = zbuduj(router({"1": zdarzenia}))

    sondy, _ = await sonduj_tablice(klient, ["1"])

    assert sondy[0].po_stanie == {"success": 3, "failure": 1}
    assert sondy[0].testowych == 1
    assert sondy[0].najnowsze == "2026-07-29T10:00:00Z"
    assert sondy[0].strona_pelna is False


async def test_okno_czasowe_trafia_do_filtra(zbuduj: Any) -> None:
    """AUTOMATION_DEAD potrzebuje okna 90 dni, a nie sumy od początku świata."""
    wyslane: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        wyslane.append(cialo)
        return router({"1": []})(zapytanie)

    klient = zbuduj(uchwyt)
    await sonduj_tablice(klient, ["1"], od="2026-05-01", do="2026-07-30")

    assert wyslane[-1]["variables"]["f"]["dateRange"] == {
        "startDate": "2026-05-01",
        "endDate": "2026-07-30",
    }


# ── ograniczenia API jawnie w snapshocie ─────────────────────────────────


async def test_ograniczenia_api_sa_zapisane(zbuduj: Any) -> None:
    """Detektory muszą wiedzieć, że sygnał jest zwężony — inaczej zmyślą."""
    klient = zbuduj(router())

    discovery = (await zbierz_automatyzacje(klient)).discovery

    assert discovery["lista_automatyzacji_dostepna"] is False
    assert discovery["filtr_board_id_zepsuty_int32"] is True
    assert discovery["atrybucja_per_tablica"] is False


async def test_atrybucja_wlacza_sie_przy_sondach(zbuduj: Any) -> None:
    klient = zbuduj(router({"1": [zdarzenie()]}))

    wynik = await zbierz_automatyzacje(klient, board_ids=["1"])

    assert wynik.discovery["atrybucja_per_tablica"] is True


async def test_email_w_powodzie_bledu_przerywa(zbuduj: Any) -> None:
    """Powody błędów to teksty od monday, ale przechodzą tę samą bramę."""
    z_mailem = {
        "account_triggers_statistics_by_entity_id": {
            "id": "x",
            "automation_statistics": {"1": {"total": 1, "brak dostępu dla ktos@firma.test": 1}},
            "workflow_statistics": {},
        }
    }

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        if "account_trigger_statistics" in cialo["query"]:
            return odpowiedz(STATYSTYKI)
        if "account_triggers_statistics_by_entity_id" in cialo["query"]:
            return odpowiedz(z_mailem)
        return odpowiedz({"trigger_events": {"triggerEvents": []}})

    klient = zbuduj(uchwyt)

    with pytest.raises(PseudonimizacjaError, match="adresu e-mail"):
        await zbierz_automatyzacje(klient)


def test_wynik_jest_niemutowalny() -> None:
    wynik = WynikAutomatyzacji(
        uruchomien_sukces=1,
        uruchomien_bledow=0,
        uruchomien_razem=1,
        automatyzacje_z_bledami=(),
        statystyki=(),
        sondy=(),
        pominietych_tablic=0,
        discovery={},
    )

    with pytest.raises((AttributeError, TypeError)):
        wynik.uruchomien_razem = 2  # type: ignore[misc]


# ── przebieg automatyzacji z błędami (O52) ──────────────────────────────


UUID = "3f1c-uruchomienie"


def uruchomienie(stan: str, kiedy: str, *, uuid: str = UUID) -> dict[str, Any]:
    return {
        "triggerUuid": uuid,
        "eventState": stan,
        "triggerStartedAt": kiedy,
        "is_test_run": False,
    }


# Kształt zmierzony w O52 na `160020307`: trigger „item created", pada blok AI.
KROKI_AI = [
    {
        "title": "item created",
        "eventState": "success",
        "conditionSatisfied": None,
        "errorReason": None,
    },
    {
        "title": "Custom prompt",
        "eventState": "failure",
        "conditionSatisfied": None,
        "errorReason": "No results – there are no files for the AI to read.",
    },
]


async def test_przebieg_mowi_jaki_trigger_i_ktory_krok_pada(zbuduj: Any) -> None:
    """Pytanie Kuby: skąd wychodzi, jaki ma trigger, dlaczego nie działa. Tekst
    błędu sam tego nie mówi — „no files for the AI to read" to równie dobrze
    pomyłka człowieka, jak automatyzacja odpalana tam, gdzie plików nie ma."""
    historia = {
        156134682: [
            uruchomienie("failure", "2026-09-18T06:14:05Z", uuid="stare"),
            uruchomienie("failure", "2026-09-22T07:43:39Z"),
            uruchomienie("success", "2026-09-01T10:00:00Z", uuid="ok"),
        ]
    }
    klient = zbuduj(router(historia=historia, kroki={UUID: KROKI_AI}))

    wynik = await zbierz_automatyzacje(klient)

    rekord = next(r for r in wynik.statystyki if r["automation_id"] == "156134682")
    przebieg = rekord["przebieg"]
    assert przebieg["historia"]["uruchomien"] == 3
    assert przebieg["historia"]["po_stanie"] == {"failure": 2, "success": 1}
    assert przebieg["historia"]["ostatnie"] == "2026-09-22T07:43:39Z"
    # Kroki NAJNOWSZEGO nieudanego uruchomienia, nie pierwszego z listy.
    ostatni = przebieg["ostatni_nieudany"]
    assert ostatni["kiedy"] == "2026-09-22T07:43:39Z"
    assert ostatni["trigger"] == "item created"
    assert ostatni["padajacy_krok"] == "Custom prompt"
    assert "no files" in ostatni["blad_kroku"]


async def test_okno_historii_to_rok_a_nie_okno_statystyk(zbuduj: Any) -> None:
    """ZMIERZONE w O52: w roku 40 błędów, w statystykach konta 9."""
    from datetime import UTC, datetime

    filtry: list[dict[str, Any]] = []
    uchwyt = router()

    def podsluch(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        if "automationIds" in (cialo.get("variables") or {}).get("f", {}):
            filtry.append(cialo["variables"]["f"])
        return uchwyt(zapytanie)

    klient = zbuduj(podsluch)
    await zbierz_automatyzacje(klient, teraz=datetime(2026, 9, 23, tzinfo=UTC))

    assert filtry[0]["dateRange"] == {"startDate": "2025-09-23", "endDate": "2026-09-24"}


async def test_sufit_przebiegow_jest_odnotowany(zbuduj: Any) -> None:
    """Ta sama zasada co przy sondach: cichy limit wygląda jak komplet."""
    klient = zbuduj(router())

    wynik = await zbierz_automatyzacje(klient, maks_przebiegow=1)

    assert wynik.discovery["przebiegow_pobranych"] == 1
    assert wynik.discovery["przebiegow_pominietych"] == 1
    assert sum(1 for r in wynik.statystyki if "przebieg" in r) == 1


async def test_awaria_przebiegu_nie_przerywa_zbierania(zbuduj: Any) -> None:
    """Brak szczegółu jednej automatyzacji nie unieważnia reszty audytu."""
    uchwyt = router()

    def zle_kroki(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        if "automationIds" in (cialo.get("variables") or {}).get("f", {}):
            return httpx.Response(200, json={"errors": [{"message": "Field 'x' doesn't exist"}]})
        return uchwyt(zapytanie)

    klient = zbuduj(zle_kroki)

    wynik = await zbierz_automatyzacje(klient)

    przebiegi = [r["przebieg"] for r in wynik.statystyki if "przebieg" in r]
    assert przebiegi and all(p == {"blad_pobrania": "ZapytanieError"} for p in przebiegi)


async def test_email_w_bledzie_kroku_przerywa(zbuduj: Any) -> None:
    """Przebieg idzie przez tę samą bramkę PII co reszta — komunikat kroku
    potrafi wymienić odbiorcę."""
    historia = {156134682: [uruchomienie("failure", "2026-09-22T07:43:39Z")]}
    kroki = {UUID: [{**KROKI_AI[1], "errorReason": "nie można wysłać do jan@klient.test"}]}
    klient = zbuduj(router(historia=historia, kroki=kroki))

    with pytest.raises(PseudonimizacjaError):
        await zbierz_automatyzacje(klient)
