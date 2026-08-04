"""Testy sondy rozpoznawczej agentów AI, warstwa 1 z 04-test.md.

Dwa testy pilnują rzeczy, które łatwo zepsuć i trudno zauważyć:

- `test_dane_z_nieprzypietej_wersji_sa_oznaczone` — sonda pyta wersje
  nieprzypięte, więc jej wynik NIE MOŻE wyglądać jak zwykłe dane
- `test_brak_pola_nie_przerywa_runu` — brak tych pól to dziś normalny stan
  (O20), nie awaria; run collectora musi przejść

Trzeci pilnuje granicy PII: klucze błędów nie mogą zawierać znaku `@`, bo
małpa w payloadzie jest wskaźnikiem e-maila w teście antyprzeciekowym z 3.4.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from monday_audit.agenci import (
    MAKS_AGENTOW,
    WERSJE_SONDY,
    WynikAgentow,
    sonduj_agentow,
)
from monday_audit.klient import WERSJA_API, MondayClient

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


def blad(komunikat: str) -> httpx.Response:
    return httpx.Response(200, json={"errors": [{"message": komunikat}]})


def ok(dane: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                **dane,
                "complexity": {"query": 10, "after": 9_000_000, "reset_in_x_seconds": 60},
            }
        },
    )


def router(
    *,
    agents_w: tuple[str, ...] = (),
    runy_w: tuple[str, ...] = (),
    runy: list[dict[str, Any]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Atrapa API, w której pola pojawiają się w KONKRETNYCH wersjach.

    Zbiory wersji, nie jedna wersja — bo tak jest w rzeczywistości. Zmierzone
    2026-08-04: `agents` działa i w `2027-01`, i w `dev`; `agent_runs` jest
    tylko w `dev` i tam wywala się na ISE. Atrapa z jedną wersją kazałaby
    kodowi zachowywać się inaczej, niż zachowuje się na żywo.
    """

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        wersja = zapytanie.headers.get("api-version", WERSJA_API)
        gql = json.loads(zapytanie.content)["query"]

        if "agent_runs" in gql:
            if wersja not in runy_w:
                return blad('Cannot query field "agent_runs" on type "Query".')
            return ok({"agent_runs": runy if runy is not None else []})

        if "agents" in gql:
            if wersja not in agents_w:
                return blad('Cannot query field "agents" on type "Query".')
            return ok({"agents": [{"id": "104861197"}, {"id": "104861231"}]})

        raise AssertionError(f"nieobsłużone zapytanie: {gql[:90]}")

    return uchwyt


# ── stan dzisiejszy: nic nie działa ──────────────────────────────────────


async def test_brak_pola_nie_przerywa_runu(zbuduj: Any) -> None:
    """Brak powierzchni agentowej to dziś NORMALNY stan (O20), nie awaria."""
    klient = zbuduj(router())

    wynik = await sonduj_agentow(klient)

    assert wynik.wersja_odpowiadajaca is None
    assert wynik.runy_dostepne is False
    assert wynik.kredyty_dostepne is False
    # Dosłowna treść błędu, bo bez niej „nie działa" jest bezużyteczne.
    assert "Cannot query field" in wynik.bledy[f"agents/{WERSJA_API}"]
    assert wynik.podsumowanie()["sledzenie_agentow_dostepne"] is False


async def test_sonda_sprawdza_wszystkie_wersje_gdy_runy_niedostepne(zbuduj: Any) -> None:
    """Sam `agents` NIE wystarcza — pytanie brzmi „czy da się policzyć kredyty".

    Zmierzone 2026-08-04: `2027-01` oddaje listę agentów, ale `agent_runs`
    jest dopiero w `dev`. Przerwanie na pierwszym `agents` ukryłoby ten fakt.
    """
    klient = zbuduj(router(agents_w=("2027-01", "dev")))

    wynik = await sonduj_agentow(klient)

    assert wynik.wersja_odpowiadajaca == "2027-01"
    assert wynik.agentow_widocznych == 2
    assert wynik.runy_dostepne is False
    # Doszła do `dev`, mimo że `agents` zadziałało wcześniej.
    assert "agent_runs/dev" in wynik.bledy


# ── stan docelowy: gdy monday to wypuści ─────────────────────────────────


async def test_runy_z_kosztem_ustawiaja_kredyty_dostepne(zbuduj: Any) -> None:
    klient = zbuduj(
        router(
            agents_w=("dev",),
            runy_w=("dev",),
            runy=[{"run_id": "r1", "status": "SUCCESS", "total_cost": 0.42}],
        )
    )

    wynik = await sonduj_agentow(klient)

    assert wynik.runy_dostepne is True
    assert wynik.kredyty_dostepne is True


async def test_runy_bez_kosztu_to_nie_brak_dostepu(zbuduj: Any) -> None:
    """Enterprise ma agentów DARMOWYCH (O21) — zero nie znaczy „nie działa".

    Gdyby sonda wnioskowała z braku kwoty, że API nie działa, na koncie
    Enterprise raportowałaby fałszywie „kredytów nie da się policzyć".
    """
    klient = zbuduj(
        router(
            agents_w=("dev",),
            runy_w=("dev",),
            runy=[{"run_id": "r1", "status": "SUCCESS", "total_cost": None}],
        )
    )

    wynik = await sonduj_agentow(klient)

    assert wynik.runy_dostepne is True, "runy SĄ osiągalne"
    assert wynik.kredyty_dostepne is False, "ale kwoty nie ma — i to jest inna informacja"


# ── granice: nieprzypięta wersja i PII ───────────────────────────────────


async def test_dane_z_nieprzypietej_wersji_sa_oznaczone(zbuduj: Any) -> None:
    """Audyt na nieprzypiętej wersji jest nieodtwarzalny (05-deploy, D4).

    Sonda ma prawo pytać `dev`, ale jej wynik nie może wyglądać jak zwykłe
    dane — inaczej ktoś kiedyś zbuduje na tym finding.
    """
    klient = zbuduj(router(agents_w=("2027-01", "dev")))

    wynik = await sonduj_agentow(klient)
    dane = wynik.do_snapshotu()

    assert wynik.zrodlo_nieprzypiete is True
    assert dane["zrodlo_nieprzypiete"] is True
    assert "NIE_DO_FINDINGOW" in dane
    assert WERSJA_API in dane["NIE_DO_FINDINGOW"]
    assert dane["wersja_przypieta"] == WERSJA_API


async def test_odpowiedz_z_wersji_przypietej_nie_jest_oznaczona(zbuduj: Any) -> None:
    """Gdy monday wypuści to do wersji przypiętej, flaga ma zniknąć sama."""
    klient = zbuduj(router(agents_w=(WERSJA_API,), runy_w=(WERSJA_API,), runy=[]))

    wynik = await sonduj_agentow(klient)
    dane = wynik.do_snapshotu()

    assert wynik.zrodlo_nieprzypiete is False
    assert "NIE_DO_FINDINGOW" not in dane
    assert wynik.podsumowanie()["sledzenie_agentow_dostepne"] is True


async def test_klucze_bledow_nie_zawieraja_malpy(zbuduj: Any) -> None:
    """Znak `@` w payloadzie jest wskaźnikiem e-maila w teście z 3.4.

    Własny separator w kluczu nie ma prawa osłabiać tego stróża — a `@`
    jako separator wersji wywalał `assert "@" not in payload` w 3.8.
    """
    klient = zbuduj(router())

    wynik = await sonduj_agentow(klient)

    assert wynik.bledy, "test straciłby sens bez ani jednego błędu"
    for klucz in wynik.bledy:
        assert "@" not in klucz
    assert "@" not in json.dumps(wynik.do_snapshotu(), ensure_ascii=False)


# ── koszt sondy ──────────────────────────────────────────────────────────


async def test_sonda_jest_tania(zbuduj: Any) -> None:
    """Sonda ma odpowiedzieć „czy działa", nie zinwentaryzować konto."""
    klient = zbuduj(router(agents_w=("2027-01", "dev")))

    wynik = await sonduj_agentow(klient)

    # Po jednym `agents` na wersję plus po jednym `agent_runs` tam, gdzie
    # agenci byli widoczni. Przy trzech wersjach to najwyżej pięć wywołań.
    assert wynik.wywolan <= 2 * len(WERSJE_SONDY)
    assert klient.liczba_wywolan == wynik.wywolan


def test_sufit_agentow_jest_maly() -> None:
    """Inwentaryzacja z nieprzypiętej wersji byłaby zbieraniem danych."""
    assert 0 < MAKS_AGENTOW <= 10


def test_wersje_sondy_zaczynaja_od_przypietej() -> None:
    """Nie ma powodu pytać `dev`, gdy wersja przypięta odpowiada."""
    assert WERSJE_SONDY[0] == WERSJA_API
    assert WERSJE_SONDY[-1] == "dev"


def test_wynik_bez_odpowiedzi_nie_udaje_dostepnosci() -> None:
    puste = WynikAgentow()

    assert puste.zrodlo_nieprzypiete is False
    assert puste.podsumowanie()["sledzenie_agentow_dostepne"] is False
    assert "NIE_DO_FINDINGOW" not in puste.do_snapshotu()
