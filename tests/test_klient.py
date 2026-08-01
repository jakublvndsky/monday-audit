"""Testy klienta GraphQL (etap 3.2), warstwa 1 z 04-test.md.

Zakres wymagany przez 04-test.md: backoff, licznik, rozdział błędów
rate-limit od błędów zapytania. Do tego to, co 03-build.md nazywa
wprost: complexity w każdym zapytaniu, każde wywołanie w tabeli
`wywolania`, wymuszony limit faktycznie przerywający działanie.

Zero sieci — `httpx.MockTransport` przepuszcza prawdziwy stos httpx,
ale odpowiedzi układamy tu. Test na koncie CXLABS to warstwa 2,
plik `test_klient_integracyjny.py`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from monday_audit import klient as klient_mod
from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.klient import (
    WERSJA_API,
    BudzetWyczerpanyError,
    LimitDziennyError,
    MondayClient,
    PaginacjaError,
    Postep,
    PrzejsciowyError,
    ZapytanieError,
    przygotuj_zapytanie,
)

TOKEN = "tajny-token-klienta"
CZAS = "2026-07-30T10:00:00+00:00"

ZAPYTANIE = "query { boards { id } }"
ZAPYTANIE_ZE_STRONA = "query ($p: Int!) { boards (limit: 25, page: $p) { id } }"

Uchwyt = Callable[[httpx.Request], httpx.Response]


class RejestrTestowy:
    """Atrapa `Rejestr` — zbiera wpisy, które poszłyby do tabeli `wywolania`."""

    def __init__(self) -> None:
        self.wpisy: list[dict[str, Any]] = []

    def zapisz(self, **kwargs: Any) -> None:
        self.wpisy.append(kwargs)


def odpowiedz_ok(dane: dict[str, Any], koszt: int = 10) -> httpx.Response:
    """Poprawna odpowiedź monday: `data` z polem complexity w korzeniu."""
    return httpx.Response(
        200,
        json={
            "data": {
                **dane,
                "complexity": {
                    "query": koszt,
                    "after": 5_000_000 - koszt,
                    "reset_in_x_seconds": 60,
                },
            }
        },
    )


@pytest.fixture
async def zbuduj() -> AsyncIterator[Callable[..., tuple[MondayClient, RejestrTestowy]]]:
    """Fabryka klientów na atrapie transportu. Zamyka je po teście.

    Domyślne `baza_czekania` jest mikroskopijne, żeby testy ponowień nie
    czekały realnych sekund. Sam kształt backoffu sprawdzają osobne testy
    `_czekanie`, gdzie czas nie płynie.
    """
    klienci: list[MondayClient] = []

    def fabryka(uchwyt: Uchwyt, **kwargs: Any) -> tuple[MondayClient, RejestrTestowy]:
        rejestr = RejestrTestowy()
        kwargs.setdefault("baza_czekania", 0.001)
        egzemplarz = MondayClient(TOKEN, rejestr, transport=httpx.MockTransport(uchwyt), **kwargs)
        klienci.append(egzemplarz)
        return egzemplarz, rejestr

    yield fabryka

    for egzemplarz in klienci:
        await egzemplarz.zamknij()


# ── complexity w każdym zapytaniu ────────────────────────────────────────


def test_complexity_wstawiane_do_korzenia() -> None:
    assert przygotuj_zapytanie(ZAPYTANIE).startswith(
        "query {\n  complexity { query after reset_in_x_seconds }"
    )


def test_complexity_nie_dubluje_sie() -> None:
    """Podwójne pole zepsułoby sumę complexity liczoną przez klienta."""
    juz_ma = "query { complexity { query } boards { id } }"
    assert przygotuj_zapytanie(juz_ma) == juz_ma


def test_argumenty_operacji_nie_sa_tluczone() -> None:
    """`{` wewnątrz argumentów nie jest korzeniem selekcji."""
    gql = "query ($f: JSON = {a: 1}) { items_page (query_params: {rules: []}) { cursor } }"
    przygotowane = przygotuj_zapytanie(gql)
    assert przygotowane.startswith("query ($f: JSON = {a: 1}) {\n  complexity {")
    assert "{a: 1}" in przygotowane


def test_komentarz_przed_operacja_nie_myli_skanera() -> None:
    gql = "# to nie mutation, tylko komentarz\nquery { boards { id } }"
    assert "complexity {" in przygotuj_zapytanie(gql)


def test_mutacja_jest_odrzucana() -> None:
    """Collector czyta i nic więcej (D6) — nie ma tu ścieżki zapisu."""
    with pytest.raises(ZapytanieError, match="wyłącznie czyta"):
        przygotuj_zapytanie('mutation { create_board (board_name: "x") { id } }')


def test_subskrypcja_jest_odrzucana() -> None:
    with pytest.raises(ZapytanieError, match="wyłącznie czyta"):
        przygotuj_zapytanie("subscription { events { id } }")


def test_zapytanie_bez_selekcji_jest_bledem() -> None:
    with pytest.raises(ZapytanieError, match="bez zbioru selekcji"):
        przygotuj_zapytanie("query Pusta")


async def test_complexity_nie_trafia_do_wyniku_ale_do_sumy(zbuduj: Any) -> None:
    egzemplarz, _ = zbuduj(lambda _: odpowiedz_ok({"boards": [{"id": "1"}]}, koszt=37))

    dane = await egzemplarz.query(ZAPYTANIE)

    assert dane == {"boards": [{"id": "1"}]}
    assert egzemplarz.complexity_suma == 37


async def test_token_idzie_w_naglowku_i_nie_w_repr(zbuduj: Any) -> None:
    naglowki: list[str] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        naglowki.append(zapytanie.headers["authorization"])
        return odpowiedz_ok({"me": {"id": "1"}})

    egzemplarz, _ = zbuduj(uchwyt)
    await egzemplarz.query("query { me { id } }")

    assert naglowki == [TOKEN]
    assert TOKEN not in repr(egzemplarz)


async def test_wersja_api_jest_przypieta_w_naglowku(zbuduj: Any) -> None:
    """Piąty element pinowania (O15) — bez nagłówka monday przesuwa wersję sam.

    Zmierzone: w wersji `2024-10` pole `Board.created_at` nie istnieje, a
    `tablice.py` o nie pyta. Brak przypięcia znaczy, że ten sam kod przestanie
    działać w dniu, w którym monday przestawi wersję domyślną konta.
    """
    wersje: list[str | None] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        wersje.append(zapytanie.headers.get("api-version"))
        return odpowiedz_ok({"me": {"id": "1"}})

    egzemplarz, _ = zbuduj(uchwyt)
    await egzemplarz.query("query { me { id } }")

    assert wersje == [WERSJA_API]
    assert WERSJA_API == "2026-07", "podnoszenie wersji idzie przez bramę promocji, nie mimochodem"


async def test_wersja_api_none_oddaje_sterowanie_kontu(zbuduj: Any) -> None:
    """Tryb do BADANIA nowej wersji, nie tryb produkcyjny."""
    wersje: list[str | None] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        wersje.append(zapytanie.headers.get("api-version"))
        return odpowiedz_ok({"me": {"id": "1"}})

    egzemplarz, _ = zbuduj(uchwyt, wersja_api=None)
    await egzemplarz.query("query { me { id } }")

    assert wersje == [None]


# ── rozdział błędów: ponawiane ───────────────────────────────────────────


@pytest.mark.parametrize(
    "nieudana",
    [
        pytest.param(httpx.Response(429), id="429"),
        pytest.param(httpx.Response(503), id="5xx"),
        pytest.param(
            httpx.Response(200, json={"errors": [{"message": "Complexity budget exhausted"}]}),
            id="complexity_w_200",
        ),
        pytest.param(
            httpx.Response(200, json={"errors": ["Rate Limit Exceeded"]}),
            id="rate_limit_w_200",
        ),
    ],
)
async def test_limit_chwilowy_jest_ponawiany(zbuduj: Any, nieudana: httpx.Response) -> None:
    proby: list[int] = []

    def uchwyt(_: httpx.Request) -> httpx.Response:
        proby.append(1)
        if len(proby) < 3:
            return nieudana
        return odpowiedz_ok({"boards": []})

    egzemplarz, rejestr = zbuduj(uchwyt)
    assert await egzemplarz.query(ZAPYTANIE) == {"boards": []}

    assert len(proby) == 3
    assert egzemplarz.liczba_wywolan == 3
    # Nieudane próby też lądują w rejestrze — zjadły limit klienta.
    assert [w.get("complexity") for w in rejestr.wpisy] == [None, None, 10]


async def test_blad_sieci_jest_ponawiany(zbuduj: Any) -> None:
    proby: list[int] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        proby.append(1)
        if len(proby) == 1:
            raise httpx.ConnectTimeout("timeout", request=zapytanie)
        return odpowiedz_ok({"boards": []})

    egzemplarz, _ = zbuduj(uchwyt)
    assert await egzemplarz.query(ZAPYTANIE) == {"boards": []}
    assert len(proby) == 2


async def test_wyczerpanie_prob_rzuca_blad_przejsciowy(zbuduj: Any) -> None:
    egzemplarz, rejestr = zbuduj(lambda _: httpx.Response(429), maks_prob=3)

    with pytest.raises(PrzejsciowyError, match="po 3 próbach"):
        await egzemplarz.query(ZAPYTANIE)

    assert egzemplarz.liczba_wywolan == 3
    assert len(rejestr.wpisy) == 3


# ── rozdział błędów: NIE ponawiane ───────────────────────────────────────


async def test_blad_zapytania_nie_jest_ponawiany(zbuduj: Any) -> None:
    """Powtórzenie złego pola da ten sam wynik, a zje wywołanie z limitu."""
    proby: list[int] = []

    def uchwyt(_: httpx.Request) -> httpx.Response:
        proby.append(1)
        return httpx.Response(200, json={"errors": [{"message": "Field 'xyz' doesn't exist"}]})

    egzemplarz, rejestr = zbuduj(uchwyt)
    with pytest.raises(ZapytanieError, match="xyz"):
        await egzemplarz.query(ZAPYTANIE)

    assert len(proby) == 1
    assert len(rejestr.wpisy) == 1


async def test_limit_dzienny_nie_jest_ponawiany(zbuduj: Any) -> None:
    """Dzienny reset przychodzi po godzinach — ponawianie tylko szkodzi klientowi."""
    proby: list[int] = []

    def uchwyt(_: httpx.Request) -> httpx.Response:
        proby.append(1)
        return httpx.Response(200, json={"error_code": "DAILY_LIMIT_EXCEEDED"})

    egzemplarz, _ = zbuduj(uchwyt)
    with pytest.raises(LimitDziennyError):
        await egzemplarz.query(ZAPYTANIE)

    assert len(proby) == 1


async def test_brak_uprawnien_nie_jest_ponawiany(zbuduj: Any) -> None:
    proby: list[int] = []

    def uchwyt(_: httpx.Request) -> httpx.Response:
        proby.append(1)
        return httpx.Response(401, text="Not Authenticated")

    egzemplarz, _ = zbuduj(uchwyt)
    with pytest.raises(ZapytanieError, match="401"):
        await egzemplarz.query(ZAPYTANIE)

    assert len(proby) == 1


async def test_odpowiedz_bez_data_jest_bledem(zbuduj: Any) -> None:
    egzemplarz, _ = zbuduj(lambda _: httpx.Response(200, json={"account_id": 123}))

    with pytest.raises(ZapytanieError, match="bez pola `data`"):
        await egzemplarz.query(ZAPYTANIE)


async def test_czesciowe_dane_z_bledami_nie_przechodza(zbuduj: Any) -> None:
    """Niepełny audyt udający pełny jest gorszy od braku audytu."""
    egzemplarz, _ = zbuduj(
        lambda _: httpx.Response(
            200,
            json={"data": {"boards": [{"id": "1"}]}, "errors": [{"message": "brak dostępu"}]},
        )
    )

    with pytest.raises(ZapytanieError, match="brak dostępu"):
        await egzemplarz.query(ZAPYTANIE)


# ── licznik wywołań ──────────────────────────────────────────────────────


async def test_budzet_przerywa_dzialanie(zbuduj: Any) -> None:
    """Przekroczenie rzuca wyjątek, nie loguje ostrzeżenia (03-build.md, 3.2)."""
    proby: list[int] = []

    def uchwyt(_: httpx.Request) -> httpx.Response:
        proby.append(1)
        return odpowiedz_ok({"boards": []})

    egzemplarz, _ = zbuduj(uchwyt, budzet_wywolan=2)
    await egzemplarz.query(ZAPYTANIE)
    await egzemplarz.query(ZAPYTANIE)

    with pytest.raises(BudzetWyczerpanyError, match="dziennego limitu konta klienta"):
        await egzemplarz.query(ZAPYTANIE)

    # Trzecie zapytanie nie poleciało do API.
    assert len(proby) == 2


async def test_ponowienia_zjadaja_budzet(zbuduj: Any) -> None:
    egzemplarz, _ = zbuduj(lambda _: httpx.Response(429), budzet_wywolan=2, maks_prob=5)

    with pytest.raises(BudzetWyczerpanyError):
        await egzemplarz.query(ZAPYTANIE)

    assert egzemplarz.liczba_wywolan == 2


async def test_ustaw_budzet_nie_moze_zejsc_ponizej_wydanych(zbuduj: Any) -> None:
    """Hak dla 3.3: plan klienta determinuje budżet reszty runu."""
    egzemplarz, _ = zbuduj(lambda _: odpowiedz_ok({"boards": []}))
    await egzemplarz.query(ZAPYTANIE)

    egzemplarz.ustaw_budzet(1000)
    assert egzemplarz.budzet_wywolan == 1000

    with pytest.raises(BudzetWyczerpanyError):
        egzemplarz.ustaw_budzet(0)


# ── backoff ──────────────────────────────────────────────────────────────


def test_backoff_rosnie_wykladniczo_i_ma_pelny_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pełny jitter znaczy losowanie z CAŁEGO [0, górna], nie z okolic górnej."""
    zakresy: list[tuple[float, float]] = []

    def falszywy_uniform(dolna: float, gorna: float) -> float:
        zakresy.append((dolna, gorna))
        return gorna

    monkeypatch.setattr(klient_mod.random, "uniform", falszywy_uniform)
    egzemplarz = MondayClient(TOKEN, RejestrTestowy(), baza_czekania=1.0)

    assert [egzemplarz._czekanie(proba, None) for proba in range(4)] == [1.0, 2.0, 4.0, 8.0]
    assert zakresy == [(0, 1.0), (0, 2.0), (0, 4.0), (0, 8.0)]


def test_backoff_jest_ograniczony_od_gory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(klient_mod.random, "uniform", lambda _, gorna: gorna)
    egzemplarz = MondayClient(TOKEN, RejestrTestowy(), baza_czekania=1.0, maks_czekanie=5.0)

    assert egzemplarz._czekanie(10, None) == 5.0


def test_retry_after_ma_pierwszenstwo_nad_jitterem() -> None:
    egzemplarz = MondayClient(TOKEN, RejestrTestowy(), maks_czekanie=30.0)

    assert egzemplarz._czekanie(0, 12.0) == 12.0
    assert egzemplarz._czekanie(0, 999.0) == 30.0  # nadal pod naszym sufitem


async def test_retry_after_z_odpowiedzi_jest_czytany(zbuduj: Any) -> None:
    proby: list[int] = []

    def uchwyt(_: httpx.Request) -> httpx.Response:
        proby.append(1)
        if len(proby) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return odpowiedz_ok({"boards": []})

    egzemplarz, _ = zbuduj(uchwyt)
    assert await egzemplarz.query(ZAPYTANIE) == {"boards": []}
    assert len(proby) == 2


# ── rozłożenie complexity w czasie ───────────────────────────────────────


def odpowiedz_z_zapasem(
    dane: dict[str, Any], *, koszt: int, pozostalo: int, reset: int = 60
) -> httpx.Response:
    """Odpowiedź z zadanym stanem okna complexity."""
    return httpx.Response(
        200,
        json={
            "data": {
                **dane,
                "complexity": {
                    "query": koszt,
                    "after": pozostalo,
                    "reset_in_x_seconds": reset,
                },
            }
        },
    )


async def test_maly_zapas_complexity_wstrzymuje_kolejne_zapytanie(zbuduj: Any) -> None:
    """Zmierzone na CXLABS: pełny przelot po tablicach to ~2× limit minutowy.

    Bez pauzy collector dostaje `ComplexityException` w połowie zbierania.
    """
    zdarzenia: list[Postep] = []
    egzemplarz, _ = zbuduj(
        lambda _: odpowiedz_z_zapasem({"boards": []}, koszt=128_000, pozostalo=10_000, reset=0),
        postep=zdarzenia.append,
        zapas_po_resecie=0.05,
    )

    for _ in range(3):
        await egzemplarz.query(ZAPYTANIE, etykieta="boards")

    # Pierwsze zapytanie leci bez pauzy — klient jeszcze nie zna stanu okna.
    pauzy = [z for z in zdarzenia if z.czekanie_s]
    assert len(pauzy) == 2, "każde zapytanie po pierwszym powinno odczekać reset"


async def test_duzy_zapas_complexity_nie_wstrzymuje(zbuduj: Any) -> None:
    zdarzenia: list[Postep] = []
    egzemplarz, _ = zbuduj(
        lambda _: odpowiedz_z_zapasem({"boards": []}, koszt=128_000, pozostalo=4_900_000),
        postep=zdarzenia.append,
    )

    for _ in range(3):
        await egzemplarz.query(ZAPYTANIE, etykieta="boards")

    assert [z for z in zdarzenia if z.czekanie_s] == []


async def test_nieznana_etykieta_szacuje_po_najdrozszym_zapytaniu(zbuduj: Any) -> None:
    """Świadomie pesymistycznie: nieznany koszt liczymy jak najdroższy znany.

    Alternatywa (brak pauzy przy nieznanej etykiecie) oznacza spalone wywołanie
    z dziennego limitu klienta — a to jedyny limit, którego nie da się odczekać.
    """
    zdarzenia: list[Postep] = []
    egzemplarz, _ = zbuduj(
        lambda _: odpowiedz_z_zapasem({"boards": []}, koszt=128_000, pozostalo=10_000, reset=0),
        postep=zdarzenia.append,
        zapas_po_resecie=0.05,
    )

    await egzemplarz.query(ZAPYTANIE, etykieta="boards")
    await egzemplarz.query(ZAPYTANIE, etykieta="konto")

    pauzy = [z for z in zdarzenia if z.czekanie_s]
    assert [p.narzedzie for p in pauzy] == ["graphql:konto"]


async def test_pauza_nie_zjada_budzetu_wywolan(zbuduj: Any) -> None:
    """Czekanie to nie wywołanie — licznik dziennego limitu stoi w miejscu."""
    egzemplarz, _ = zbuduj(
        lambda _: odpowiedz_z_zapasem({"boards": []}, koszt=128_000, pozostalo=10_000, reset=0),
        zapas_po_resecie=0.05,
    )

    for _ in range(3):
        await egzemplarz.query(ZAPYTANIE, etykieta="boards")

    assert egzemplarz.liczba_wywolan == 3


# ── postęp ───────────────────────────────────────────────────────────────


async def test_postep_zglaszany_po_kazdym_wywolaniu(zbuduj: Any) -> None:
    zdarzenia: list[Postep] = []
    egzemplarz, _ = zbuduj(
        lambda _: odpowiedz_ok({"boards": []}, koszt=10), postep=zdarzenia.append
    )

    await egzemplarz.query(ZAPYTANIE, etykieta="konto")
    await egzemplarz.query(ZAPYTANIE, etykieta="konto")

    assert [z.wywolania for z in zdarzenia] == [1, 2]
    assert [z.complexity_suma for z in zdarzenia] == [10, 20]
    assert zdarzenia[0].budzet == egzemplarz.budzet_wywolan


async def test_postep_paginacji_liczy_strony_i_elementy(zbuduj: Any) -> None:
    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        numer = json.loads(zapytanie.content)["variables"]["p"]
        if numer > 2:
            return odpowiedz_ok({"boards": []})
        return odpowiedz_ok({"boards": [{"id": f"{numer}a"}, {"id": f"{numer}b"}]})

    zdarzenia: list[Postep] = []
    egzemplarz, _ = zbuduj(uchwyt, postep=zdarzenia.append)

    async for _ in egzemplarz.paginate(ZAPYTANIE_ZE_STRONA, "boards", etykieta="boards"):
        pass

    strony = [(z.strona, z.zebrane) for z in zdarzenia if z.strona is not None]
    assert strony == [(1, 2), (2, 4), (3, 4)]


async def test_bez_postepu_klient_dziala_w_ciszy(zbuduj: Any) -> None:
    egzemplarz, _ = zbuduj(lambda _: odpowiedz_ok({"boards": []}))
    assert await egzemplarz.query(ZAPYTANIE) == {"boards": []}


def test_opis_postepu_zawiera_to_co_czyta_czlowiek() -> None:
    postep = Postep(
        narzedzie="graphql:boards",
        wywolania=37,
        budzet=400,
        complexity_suma=4_800_000,
        complexity_pozostalo=120_000,
        strona=12,
        zebrane=300,
    )
    opis = postep.opis()

    assert "graphql:boards" in opis
    assert "37/400" in opis
    assert "strona 12" in opis
    assert "zebranych 300" in opis
    assert "PAUZA" not in opis


# ── tabela wywolania ─────────────────────────────────────────────────────


async def test_etykieta_trafia_do_kolumny_narzedzie(zbuduj: Any) -> None:
    egzemplarz, rejestr = zbuduj(lambda _: odpowiedz_ok({"boards": []}))

    await egzemplarz.query(ZAPYTANIE, etykieta="boards")

    assert rejestr.wpisy[0]["narzedzie"] == "graphql:boards"
    assert rejestr.wpisy[0]["latency_ms"] >= 0


@pytest.fixture
def con_z_runem(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Baza z otwartym runem — `wywolania.run_id` to NOT NULL REFERENCES."""
    con = polacz(tmp_path / "test.db")
    zastosuj_migracje(con)
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) VALUES ('r1', 'cxlabs', ?, ?)",
        ("w_toku", CZAS),
    )
    con.commit()
    yield con
    con.close()


async def test_rejestr_pisze_do_tabeli_wywolania(con_z_runem: sqlite3.Connection) -> None:
    egzemplarz = MondayClient(
        TOKEN,
        RejestrWywolan(con_z_runem, "r1"),
        transport=httpx.MockTransport(lambda _: odpowiedz_ok({"boards": []}, koszt=42)),
    )

    await egzemplarz.query(ZAPYTANIE, etykieta="boards")
    await egzemplarz.zamknij()

    wiersz = con_z_runem.execute(
        "SELECT narzedzie, complexity, latency_ms, tokens_in, model FROM wywolania"
    ).fetchone()
    assert wiersz["narzedzie"] == "graphql:boards"
    assert wiersz["complexity"] == 42
    assert wiersz["latency_ms"] >= 0
    assert wiersz["tokens_in"] is None  # GraphQL nie zużywa tokenów modelu
    assert wiersz["model"] is None


def test_rejestr_wymaga_otwartego_runu(tmp_path: Path) -> None:
    con = polacz(tmp_path / "test.db")
    zastosuj_migracje(con)

    with pytest.raises(sqlite3.IntegrityError):
        RejestrWywolan(con, "run-ktorego-nie-ma").zapisz(narzedzie="graphql")
    con.close()


# ── paginacja ────────────────────────────────────────────────────────────


async def test_paginacja_konczy_na_pustej_stronie(zbuduj: Any) -> None:
    strony: list[int] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        numer = json.loads(zapytanie.content)["variables"]["p"]
        strony.append(numer)
        if numer > 2:
            return odpowiedz_ok({"boards": []})
        return odpowiedz_ok({"boards": [{"id": f"{numer}a"}, {"id": f"{numer}b"}]})

    egzemplarz, _ = zbuduj(uchwyt)
    zebrane = [b async for b in egzemplarz.paginate(ZAPYTANIE_ZE_STRONA, "boards")]

    assert [b["id"] for b in zebrane] == ["1a", "1b", "2a", "2b"]
    assert strony == [1, 2, 3]


async def test_paginacja_przekazuje_wlasne_zmienne(zbuduj: Any) -> None:
    zmienne: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        zmienne.append(json.loads(zapytanie.content)["variables"])
        return odpowiedz_ok({"boards": []})

    egzemplarz, _ = zbuduj(uchwyt)
    async for _ in egzemplarz.paginate(ZAPYTANIE_ZE_STRONA, "boards", {"limit": 25}):
        pass

    assert zmienne == [{"limit": 25, "p": 1}]


async def test_paginacja_ma_bezpiecznik_na_zapetlenie(zbuduj: Any) -> None:
    """Zapytanie ignorujące `page` kręciłoby się w kółko po limicie klienta."""
    egzemplarz, _ = zbuduj(lambda _: odpowiedz_ok({"boards": [{"id": "1"}]}))

    with pytest.raises(PaginacjaError, match="nie domknęła się w 3 stronach"):
        async for _ in egzemplarz.paginate(ZAPYTANIE_ZE_STRONA, "boards", maks_stron=3):
            pass

    assert egzemplarz.liczba_wywolan == 3


async def test_paginacja_zglasza_zla_sciezke(zbuduj: Any) -> None:
    egzemplarz, _ = zbuduj(lambda _: odpowiedz_ok({"boards": []}))

    with pytest.raises(PaginacjaError, match="nie istnieje"):
        async for _ in egzemplarz.paginate(ZAPYTANIE_ZE_STRONA, "users"):
            pass


async def test_paginacja_zglasza_sciezke_ktora_nie_jest_lista(zbuduj: Any) -> None:
    egzemplarz, _ = zbuduj(lambda _: odpowiedz_ok({"me": {"id": "1"}}))

    with pytest.raises(PaginacjaError, match="nie wskazuje na listę"):
        async for _ in egzemplarz.paginate("query ($p: Int!) { me { id } }", "me"):
            pass
