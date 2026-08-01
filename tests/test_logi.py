"""Testy samplingu activity logs (etap 3.7), warstwa 1 z 04-test.md.

Trzy testy pilnują rzeczy, na których ten etap stoi:
`test_zapytanie_nie_pyta_o_pole_data` (zero treści),
`test_created_at_nie_jest_data_iso` (pułapka formatu),
`test_pozornie_zywa_tablica` (sygnał z 3.7).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from monday_audit.klient import MondayClient
from monday_audit.logi import (
    JEDNOSTEK_NA_SEKUNDE,
    OKNO_OSTATNICH,
    ZAPYTANIE_LOGOW,
    LogiError,
    klasa_zdarzenia,
    na_iso,
    wybierz_probke,
    zbierz_logi,
)
from monday_audit.osoby import policz_hash
from monday_audit.tablice import Tablica

TOKEN = "tajny-token-klienta"
SOL = b"sol-testowa-dluga-na-tyle-ze-przechodzi"
KLIENT = "cxlabs"

# 17830789794688296 / 10^7 = 1783078979 s → 2026-07-03, zgodne z updated_at
# tablicy na koncie CXLABS.
CZAS_LOGU = 17830789794688296


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


def tablica(board_id: str, items_count: int | None = 10) -> Tablica:
    return Tablica(
        board_id=board_id,
        nazwa=f"Tablica {board_id}",
        typ="board",
        state="active",
        board_kind="public",
        items_count=items_count,
        workspace_id="6576039",
        workspace_nazwa="monday AI Agents",
        owners=(),
        subscribers=(),
        kolumny=(),
        created_at=None,
        updated_at=None,
    )


_licznik = itertools.count()


def wpis(
    *,
    event: str = "update_column_value",
    user_id: str = "101",
    entity: str = "pulse",
    czas: int = CZAS_LOGU,
    log_id: str | None = None,
) -> dict[str, Any]:
    """Identyfikator jest UNIKALNY z licznika.

    Collector deduplikuje wpisy po `id` (zabezpieczenie przed ignorowanym
    `page`), więc powtórzony identyfikator w atrapie skleiłby różne zdarzenia
    w jedno i test mierzyłby coś innego, niż myśli.
    """
    return {
        "id": log_id or f"log-{next(_licznik)}",
        "event": event,
        "entity": entity,
        "created_at": str(czas),
        "user_id": user_id,
    }


def _odpowiedz_http(board_id: str, logi: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "boards": [{"id": board_id, "activity_logs": logi}],
                "complexity": {"query": 12_650, "after": 9_000_000, "reset_in_x_seconds": 60},
            }
        },
    )


def odpowiedz(logi_per_tablica: dict[str, list[dict[str, Any]]]) -> Any:
    """Cała treść na pierwszej stronie, kolejne puste — tak kończy się log."""

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        zmienne = json.loads(zapytanie.content)["variables"]
        board_id = zmienne["ids"][0]
        strona = zmienne["p"]
        logi = logi_per_tablica.get(board_id, []) if strona == 1 else []
        return _odpowiedz_http(board_id, logi)

    return uchwyt


def odpowiedz_stronicowana(strony: dict[int, list[dict[str, Any]]]) -> Any:
    """Atrapa z prawdziwą paginacją: strona → wpisy."""

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        zmienne = json.loads(zapytanie.content)["variables"]
        return _odpowiedz_http(zmienne["ids"][0], strony.get(zmienne["p"], []))

    return uchwyt


def odpowiedz_ignorujaca_strone(logi: list[dict[str, Any]]) -> Any:
    """Atrapa API, które ma `page`, ale go nie stosuje — jak zepsuty filtr z O12."""

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        return _odpowiedz_http(json.loads(zapytanie.content)["variables"]["ids"][0], logi)

    return uchwyt


# ── zero treści w snapshocie ─────────────────────────────────────────────


def test_zapytanie_nie_pyta_o_pole_data() -> None:
    """`data` zawiera `value`, `previous_value` i `pulse_name` — treść klienta.

    Nie pobieramy jej wcale, żeby nie polegać na tym, że ktoś ją potem
    odfiltruje (D5 i granica PII).
    """
    assert "data" not in ZAPYTANIE_LOGOW
    assert "column_values" not in ZAPYTANIE_LOGOW
    assert "items" not in ZAPYTANIE_LOGOW
    # To, co faktycznie bierzemy, i nic ponad to.
    assert "id event entity created_at user_id" in ZAPYTANIE_LOGOW


async def test_snapshot_nie_zawiera_identyfikatorow_osob(zbuduj: Any) -> None:
    klient = zbuduj(odpowiedz({"1": [wpis(user_id="101"), wpis(user_id="102")]}))

    wynik = await zbierz_logi(
        klient,
        [tablica("1")],
        client_id=KLIENT,
        sol=SOL,
        znane_hashe={policz_hash(KLIENT, "101", SOL)},
    )
    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)

    assert '"101"' not in payload
    assert '"102"' not in payload
    assert policz_hash(KLIENT, "101", SOL) in payload


# ── pułapka formatu czasu ────────────────────────────────────────────────


def test_created_at_nie_jest_data_iso() -> None:
    """Log zwraca 17830789794688296, nie datę. Na tym stoi okno 90 dni."""
    wynik = na_iso(CZAS_LOGU)

    assert wynik is not None
    assert wynik.startswith("2026-07-03")
    assert pytest.approx(1783078979.47, abs=1) == CZAS_LOGU / JEDNOSTEK_NA_SEKUNDE


@pytest.mark.parametrize(("wejscie", "oczekiwane"), [(None, None), (0, None), ("0", None)])
def test_na_iso_odrzuca_puste(wejscie: Any, oczekiwane: None) -> None:
    assert na_iso(wejscie) is oczekiwane


def test_na_iso_normalizuje_wejscie_juz_w_iso() -> None:
    """Gdyby monday zmienił format — normalizujemy do UTC, nie zgadujemy."""
    assert na_iso("2026-07-03T11:44:39Z") == "2026-07-03T11:44:39+00:00"


def test_na_iso_oddaje_oryginal_gdy_nie_umie_sparsowac() -> None:
    assert na_iso("wczoraj po obiedzie") == "wczoraj po obiedzie"


# ── sygnał żywa kontra pozornie żywa ─────────────────────────────────────


async def test_pozornie_zywa_tablica(zbuduj: Any) -> None:
    """Wpisy są, ale żaden z ostatnich nie pochodzi od użytkownika konta."""
    logi = [wpis(user_id="999", czas=CZAS_LOGU + n) for n in range(6)]
    klient = zbuduj(odpowiedz({"1": logi}))

    wynik = await zbierz_logi(
        klient,
        [tablica("1")],
        client_id=KLIENT,
        sol=SOL,
        znane_hashe={policz_hash(KLIENT, "101", SOL)},
    )
    sygnal = wynik.sygnaly[0]

    assert sygnal.pozornie_zywa is True
    assert sygnal.ostatnich_od_znanych == 0
    assert sygnal.autorow_znanych == 0
    assert sygnal.autorow_nieznanych == 6
    assert wynik.podsumowanie()["tablic_pozornie_zywych"] == 1


async def test_zywa_tablica_nie_jest_pozorna(zbuduj: Any) -> None:
    logi = [wpis(user_id="101", czas=CZAS_LOGU + n) for n in range(3)]
    klient = zbuduj(odpowiedz({"1": logi}))

    wynik = await zbierz_logi(
        klient,
        [tablica("1")],
        client_id=KLIENT,
        sol=SOL,
        znane_hashe={policz_hash(KLIENT, "101", SOL)},
    )

    assert wynik.sygnaly[0].pozornie_zywa is False
    assert wynik.sygnaly[0].ostatnich_od_znanych == 3


async def test_pusta_tablica_nie_jest_pozornie_zywa(zbuduj: Any) -> None:
    """Brak wpisów to inny sygnał niż wpisy wyłącznie od automatu."""
    klient = zbuduj(odpowiedz({"1": []}))

    wynik = await zbierz_logi(
        klient,
        [tablica("1")],
        client_id=KLIENT,
        sol=SOL,
        znane_hashe={policz_hash(KLIENT, "101", SOL)},
    )

    assert wynik.sygnaly[0].wpisow == 0
    assert wynik.sygnaly[0].pozornie_zywa is False
    assert wynik.podsumowanie()["tablic_bez_wpisow"] == 1


async def test_okno_ostatnich_liczy_najnowsze_a_nie_pierwsze(zbuduj: Any) -> None:
    """Kolejność z API nie jest gwarantowana — sortujemy po czasie sami."""
    logi = [
        wpis(user_id="101", czas=CZAS_LOGU - 1_000_000),  # stare, od człowieka
        *[wpis(user_id="999", czas=CZAS_LOGU + n) for n in range(OKNO_OSTATNICH)],
    ]
    klient = zbuduj(odpowiedz({"1": logi}))

    wynik = await zbierz_logi(
        klient,
        [tablica("1")],
        client_id=KLIENT,
        sol=SOL,
        znane_hashe={policz_hash(KLIENT, "101", SOL)},
    )

    assert wynik.sygnaly[0].ostatnich_od_znanych == 0
    assert wynik.sygnaly[0].pozornie_zywa is True


# ── sampling ─────────────────────────────────────────────────────────────


def test_probka_bierze_najwieksze_i_najmniejsze() -> None:
    tablice = [tablica(str(n), items_count=n) for n in range(1, 21)]

    probka, pominietych = wybierz_probke(tablice, top=3, z_ogona=2)

    assert [t.items_count for t in probka] == [20, 19, 18, 2, 1]
    assert pominietych == 15


def test_brak_sufitu_bierze_wszystkie_tablice() -> None:
    """`top=None` to tryb „bez próbkowania" — jedyny, w którym nic nie ginie.

    Zero pominiętych jest tu równie ważne jak pełna próbka: snapshot z zerem
    znaczy „pokrycie kompletne", a detektor `BOARD_GHOST` może wtedy mówić
    o całym zakresie, nie o wycinku.
    """
    tablice = [tablica(str(n), items_count=n) for n in range(1, 151)]

    probka, pominietych = wybierz_probke(tablice, top=None)

    assert len(probka) == 150
    assert pominietych == 0


def test_brak_sufitu_jest_deterministyczny() -> None:
    """Bez sortowania po `items_count` kolejność musi trzymać się board_id."""
    tablice = [tablica(str(n), items_count=None) for n in (3, 1, 2)]

    probka, _ = wybierz_probke(tablice, top=None)

    assert [t.board_id for t in probka] == ["1", "2", "3"]


def test_probka_jest_deterministyczna() -> None:
    """Specyfikacja mówi „losowych z ogona"; losowanie łamie powtarzalność.

    04-test.md wymaga, żeby dwa runy na tym samym koncie dały snapshoty
    różniące się tylko znacznikami czasu.
    """
    tablice = [tablica(str(n), items_count=n % 7) for n in range(30)]

    pierwsza, _ = wybierz_probke(tablice, top=4, z_ogona=3)
    druga, _ = wybierz_probke(tablice, top=4, z_ogona=3)

    assert [t.board_id for t in pierwsza] == [t.board_id for t in druga]


def test_probka_nie_dubluje_tablic() -> None:
    """Przy małej liczbie tablic czoło i ogon mogłyby się nałożyć."""
    tablice = [tablica(str(n), items_count=n) for n in range(4)]

    probka, pominietych = wybierz_probke(tablice, top=3, z_ogona=3)

    assert len({t.board_id for t in probka}) == len(probka)
    assert len(probka) == 4
    assert pominietych == 0


def test_probka_radzi_sie_z_brakiem_items_count() -> None:
    tablice = [tablica("a", items_count=None), tablica("b", items_count=5)]

    probka, _ = wybierz_probke(tablice, top=1, z_ogona=0)

    assert probka[0].board_id == "b"


def test_ujemne_sufity_sa_bledem() -> None:
    with pytest.raises(LogiError, match="ujemne"):
        wybierz_probke([tablica("1")], top=-1)


async def test_pominiete_tablice_ida_do_snapshotu(zbuduj: Any) -> None:
    """Cichy sampling wyglądałby w raporcie jak pełne pokrycie."""
    tablice = [tablica(str(n), items_count=n) for n in range(20)]
    klient = zbuduj(odpowiedz({}))

    wynik = await zbierz_logi(
        klient,
        tablice,
        client_id=KLIENT,
        sol=SOL,
        znane_hashe={policz_hash(KLIENT, "101", SOL)},
        top=2,
        z_ogona=1,
    )

    assert wynik.podsumowanie()["tablic_zbadanych"] == 3
    assert wynik.podsumowanie()["tablic_pominietych"] == 17
    assert klient.liczba_wywolan == 3


async def test_jedno_wywolanie_na_tablice(zbuduj: Any) -> None:
    """Nie ma logu na poziomie konta — stąd sufit próbki."""
    tablice = [tablica(str(n)) for n in range(5)]
    klient = zbuduj(odpowiedz({}))

    await zbierz_logi(
        klient, tablice, client_id=KLIENT, sol=SOL, znane_hashe={"1"}, top=5, z_ogona=0
    )

    assert klient.liczba_wywolan == 5


# ── pozostałe sygnały ────────────────────────────────────────────────────


async def test_rozklad_typow_akcji(zbuduj: Any) -> None:
    logi = [
        wpis(event="create_column"),
        wpis(event="create_column"),
        wpis(event="update_column_value"),
        wpis(event="delete_group", entity="board"),
    ]
    klient = zbuduj(odpowiedz({"1": logi}))

    wynik = await zbierz_logi(klient, [tablica("1")], client_id=KLIENT, sol=SOL)
    sygnal = wynik.sygnaly[0]

    assert sygnal.po_event == {
        "create_column": 2,
        "update_column_value": 1,
        "delete_group": 1,
    }
    assert sygnal.po_entity == {"pulse": 3, "board": 1}
    assert wynik.podsumowanie()["najczestsze_zdarzenia"]["create_column"] == 2


async def test_sufit_stron_oznacza_log_jako_urwany(zbuduj: Any) -> None:
    """Sto wpisów na stronę to nie „tyle jest" — trzeba to odnotować."""
    klient = zbuduj(odpowiedz_stronicowana({1: [wpis(czas=CZAS_LOGU + n) for n in range(10)]}))

    wynik = await zbierz_logi(
        klient,
        [tablica("1")],
        client_id=KLIENT,
        sol=SOL,
        limit_wpisow=10,
        maks_stron=1,
    )

    assert wynik.sygnaly[0].urwane is True
    assert wynik.podsumowanie()["tablic_z_urwanym_logiem"] == 1


async def test_paginacja_zbiera_wpisy_z_wielu_stron(zbuduj: Any) -> None:
    klient = zbuduj(
        odpowiedz_stronicowana(
            {
                1: [wpis(czas=CZAS_LOGU + n) for n in range(10)],
                2: [wpis(czas=CZAS_LOGU + 100 + n) for n in range(4)],
            }
        )
    )

    wynik = await zbierz_logi(
        klient, [tablica("1")], client_id=KLIENT, sol=SOL, limit_wpisow=10, maks_stron=5
    )

    assert wynik.sygnaly[0].wpisow == 14
    assert wynik.sygnaly[0].urwane is False
    assert wynik.discovery["paginacja_logow_dziala"] is True


async def test_ignorowany_page_nie_zawyza_liczb(zbuduj: Any) -> None:
    """Gdyby `page` był zepsuty jak filtr board_id z O12 — nie liczymy dwa razy."""
    logi = [wpis(czas=CZAS_LOGU + n) for n in range(10)]
    klient = zbuduj(odpowiedz_ignorujaca_strone(logi))

    wynik = await zbierz_logi(
        klient, [tablica("1")], client_id=KLIENT, sol=SOL, limit_wpisow=10, maks_stron=5
    )

    assert wynik.sygnaly[0].wpisow == 10, "deduplikacja po id, nie 20 ani 50"
    assert wynik.discovery["paginacja_logow_dziala"] is False
    assert klient.liczba_wywolan == 2, "druga strona wystarcza, żeby to wykryć"


# ── cztery sygnały pod health score ──────────────────────────────────────


async def test_znacznik_ostatniej_zmiany_od_czlowieka(zbuduj: Any) -> None:
    """„Kiedy człowiek ostatnio tknął tablicę" to inne pytanie niż „kiedy cokolwiek"."""
    haszyk = policz_hash(KLIENT, "101", SOL)
    logi = [
        wpis(user_id="101", czas=CZAS_LOGU),
        wpis(user_id="999", czas=CZAS_LOGU + 10_000_000_000),  # automat, później
    ]
    klient = zbuduj(odpowiedz({"1": logi}))

    sygnal = (
        await zbierz_logi(klient, [tablica("1")], client_id=KLIENT, sol=SOL, znane_hashe={haszyk})
    ).sygnaly[0]

    assert sygnal.najnowszy_od_znanego_at is not None
    assert sygnal.najnowszy_od_znanego_at < str(sygnal.najnowszy_at)


async def test_brak_zmiany_od_czlowieka_jest_liczony(zbuduj: Any) -> None:
    klient = zbuduj(odpowiedz({"1": [wpis(user_id="999")]}))

    wynik = await zbierz_logi(
        klient, [tablica("1")], client_id=KLIENT, sol=SOL, znane_hashe={"nieistotny"}
    )

    assert wynik.sygnaly[0].najnowszy_od_znanego_at is None
    assert wynik.podsumowanie()["tablic_bez_zmiany_od_czlowieka"] == 1


async def test_udzial_autorow_wykrywa_dominacje(zbuduj: Any) -> None:
    """Jedna osoba na 90% zmian to inny stan konta niż pięć osób po równo."""
    logi = [wpis(user_id="101") for _ in range(9)] + [wpis(user_id="102")]
    klient = zbuduj(odpowiedz({"1": logi}))

    wynik = await zbierz_logi(klient, [tablica("1")], client_id=KLIENT, sol=SOL)
    sygnal = wynik.sygnaly[0]

    assert sygnal.udzial_autorow[policz_hash(KLIENT, "101", SOL)] == 9
    assert sygnal.udzial_najaktywniejszego == pytest.approx(0.9)
    assert wynik.podsumowanie()["tablic_zdominowanych_jednym_autorem"] == 1


async def test_kubelki_czasowe_pokazuja_rozklad(zbuduj: Any) -> None:
    """Bez rozkładu w czasie nie ma ENGAGEMENT_DROP — spadek widać w kształcie."""
    teraz = datetime(2026, 7, 31, tzinfo=UTC)

    def kiedys(dni: int) -> int:
        return int((teraz - timedelta(days=dni)).timestamp() * JEDNOSTEK_NA_SEKUNDE)

    logi = [
        wpis(czas=kiedys(5)),
        wpis(czas=kiedys(20)),
        wpis(czas=kiedys(45)),
        wpis(czas=kiedys(80)),
        wpis(czas=kiedys(200)),
    ]
    klient = zbuduj(odpowiedz({"1": logi}))

    sygnal = (
        await zbierz_logi(klient, [tablica("1")], client_id=KLIENT, sol=SOL, teraz=teraz)
    ).sygnaly[0]

    assert sygnal.kubelki_dni == {"0-30": 2, "31-60": 1, "61-90": 1, "starsze": 1}


async def test_podzial_zdarzen_oddziela_uprawnienia_od_uzywania(zbuduj: Any) -> None:
    """Na tablicy CXLABS 32 ze 100 wpisów to była zmiana dostępu, nie praca."""
    logi = [
        wpis(event="update_column_value"),
        wpis(event="create_pulse"),
        wpis(event="create_column"),
        wpis(event="subscribe"),
        wpis(event="set_entity_board_role"),
        wpis(event="zdarzenie_ktorego_nie_znamy"),
    ]
    klient = zbuduj(odpowiedz({"1": logi}))

    wynik = await zbierz_logi(klient, [tablica("1")], client_id=KLIENT, sol=SOL)

    assert wynik.sygnaly[0].po_klasie == {
        "operacyjne": 2,
        "strukturalne": 1,
        "uprawnienia": 2,
        "inne": 1,
    }
    assert wynik.discovery["nieznane_zdarzenia"] == ["zdarzenie_ktorego_nie_znamy"]


@pytest.mark.parametrize(
    ("event", "klasa"),
    [
        ("create_column", "strukturalne"),
        ("update_column_value", "operacyjne"),
        ("subscribe", "uprawnienia"),
        ("set_entity_board_role", "uprawnienia"),
        ("cokolwiek_nowego", "inne"),
    ],
)
def test_klasyfikacja_zdarzen(event: str, klasa: str) -> None:
    assert klasa_zdarzenia(event) == klasa


async def test_okno_czasowe_trafia_do_zapytania(zbuduj: Any) -> None:
    wyslane: list[dict[str, Any]] = []

    def uchwyt(zapytanie: httpx.Request) -> httpx.Response:
        cialo = json.loads(zapytanie.content)
        wyslane.append(cialo)
        return odpowiedz({"1": []})(zapytanie)

    klient = zbuduj(uchwyt)
    await zbierz_logi(
        klient,
        [tablica("1")],
        client_id=KLIENT,
        sol=SOL,
        od="2026-05-01T00:00:00Z",
        do="2026-07-30T00:00:00Z",
    )

    assert wyslane[0]["variables"]["od"] == "2026-05-01T00:00:00Z"
    assert wyslane[0]["variables"]["do"] == "2026-07-30T00:00:00Z"


async def test_heurystyka_jest_oznaczona_jako_heurystyka(zbuduj: Any) -> None:
    """API nie ma znacznika bota — nie wolno tego podawać jako faktu."""
    klient = zbuduj(odpowiedz({"1": []}))

    wynik = await zbierz_logi(klient, [tablica("1")], client_id=KLIENT, sol=SOL)

    assert "heurystyka" in wynik.discovery["rozroznienie_czlowiek_automat"]
    assert wynik.discovery["created_at_w_jednostkach_100ns"] is True


async def test_brak_listy_uzytkownikow_daje_bezwartosciowy_sygnal(zbuduj: Any) -> None:
    """Bez listy z 3.4 każdy autor wychodzi jako nieznany — to musi być widoczne."""
    klient = zbuduj(odpowiedz({"1": [wpis(user_id="101")]}))

    wynik = await zbierz_logi(klient, [tablica("1")], client_id=KLIENT, sol=SOL)

    assert wynik.discovery["znanych_uzytkownikow_na_wejsciu"] == 0
    assert wynik.sygnaly[0].autorow_nieznanych == 1
