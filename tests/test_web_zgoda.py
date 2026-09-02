"""Przepływ dwufazowy: zbieranie → wybór zakresu → zgoda → analiza.

Ten plik istnieje dlatego, że wszystkie dotychczasowe testy webowe podmieniają
`uruchom_audyt_w_tle` atrapą. Rozdzielenie audytu na dwie fazy nie ruszyło
żadnego z nich — czyli **nikt nie sprawdzał, czy `POST /api/audyt` dochodzi
do agenta**. Test, który przechodzi po zmianie przepływu, o zmianie przepływu
nic nie wie.

Tu pytamy endpointów tak, jak zapytałby front, i sprawdzamy PODŁĄCZENIE:
czy zadanie zatrzymuje się przed agentem, czy ekran wyboru liczy się z tego
samego snapshotu, i czy zgoda odpala fazę drugą z właściwym zawężeniem.

Sieci i modelu tu nie ma: snapshot wkładamy do bazy wprost, a obie fazy w tle
podmieniamy — sprawdzamy, Z CZYM zostają wywołane, bo to jest granica między
decyzją człowieka a rachunkiem, który zapłaci.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.dostep import ROLA_KLIENT, ROLA_ZESPOL, utworz_konto
from monday_audit.web.api import zbuduj_aplikacje
from monday_audit.zadania import (
    GODZIN_WAZNOSCI_ZGODY,
    STAN_CZEKA_NA_ZGODE,
    utworz_zadanie,
    zapisz_stan,
)

PREFIKS_JWT = "eyJhbGciOi" + "JIUzI1NiJ9"
ATRAPA_KLUCZA = PREFIKS_JWT + ".ATRAPA-DO-TESTU." + "podpis-nieprawdziwy"
ATRAPA_ANTHROPIC = "sk-ant-" + "atrapa-do-testu-nieprawdziwa"

HASLO_KLIENTA = "test-haslo-klienta-1"
HASLO_ZESPOLU = "test-haslo-zespolu-1"
EMAIL = "jle@cxlabs.digital"
RUN_AT = "2026-08-14T11:31:48+00:00"

# Dwie tablice w jednym workspace plus jedna w drugim. Trzecia jest
# `sub_items_board`, czyli nie wchodzi na listę wyboru — bez niej test nie
# sprawdziłby, że pomocnicze obiekty są odsiewane.
TABLICE = [
    {
        "board_id": "b1",
        "nazwa": "Leady",
        "typ": "board",
        "state": "active",
        "board_kind": "public",
        "items_count": 47,
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-05-01T10:00:00Z",
        "workspace_id": "w1",
        "workspace_nazwa": "Operacje",
        "owners": [],
        "subscribers": [],
        "kolumny": [{"id": "n", "title": "Name", "type": "name"}],
    },
    {
        "board_id": "b2",
        "nazwa": "Oferty",
        "typ": "board",
        "state": "active",
        "board_kind": "public",
        "items_count": 130,
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-05-01T10:00:00Z",
        "workspace_id": "w2",
        "workspace_nazwa": "Sprzedaż",
        "owners": [],
        "subscribers": [],
        "kolumny": [{"id": "n", "title": "Name", "type": "name"}],
    },
    {
        "board_id": "pomocnicza",
        "nazwa": "Subitems",
        "typ": "sub_items_board",
        "state": "active",
        "board_kind": "public",
        "items_count": 3,
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-05-01T10:00:00Z",
        "workspace_id": "w1",
        "workspace_nazwa": "Operacje",
        "owners": [],
        "subscribers": [],
        "kolumny": [],
    },
]

PAYLOAD: dict[str, Any] = {
    "meta": {
        "client_id": "cxlabs",
        "run_at": RUN_AT,
        "collector_ver": "0.1.0",
        "okno_dni": 90,
        "okno_od": "2026-05-16T11:31:48+00:00",
        "uwagi_o_zakresie": ["lista użytkowników jest z natury na poziomie konta"],
    },
    "konto": {
        "konto": {"id": "27690228", "nazwa": "CXLABS"},
        "plan": {"tier": "enterprise", "max_users": 100},
        "uprawnienia": {"is_admin": True, "is_guest": False},
        "zakres": {"typ": "cale_konto", "workspace_ids": [], "board_ids": []},
        "zastrzezenia": [],
    },
    "uzytkownicy": {
        "uzytkownicy": [
            {
                "user_hash": "aaaa1111bbbb2222",
                "kind": "member",
                "status": "ACTIVE",
                "enabled": True,
                "last_activity": None,
                "utworzono": "2025-01-01T10:00:00Z",
            }
        ],
        "podsumowanie": {"razem": 1, "agentow": 0},
        "discovery": {},
    },
    "tablice": {
        "tablice": TABLICE,
        "podsumowanie": {"razem": 3, "workspace_ow": 2},
        "discovery": {"po_typie": {"board": 2, "sub_items_board": 1}},
    },
    "automatyzacje": {"podsumowanie": {"automatyzacji_widzianych": 0}, "discovery": {}},
    "aktywnosc": {
        "aktywnosc_tablic": [
            {"board_id": "b1", "wpisow": 12, "kubelki_dni": {"0-7": 12}, "autorzy": []},
            {"board_id": "b2", "wpisow": 130, "kubelki_dni": {"0-7": 130}, "autorzy": []},
        ],
        "per_uzytkownik": {},
        "podsumowanie": {"tablic_zbadanych": 2},
        "discovery": {},
    },
    "agenci": {"podsumowanie": {}},
}


@pytest.fixture
def baza(tmp_path: Path) -> Iterator[Path]:
    sciezka = tmp_path / "zgoda.db"
    con = polacz(sciezka)
    zastosuj_migracje(con)
    con.execute(
        "INSERT INTO snapshots (id, client_id, run_at, collector_ver, payload) "
        "VALUES (7, 'cxlabs', ?, '0.1.0', ?)",
        (RUN_AT, json.dumps(PAYLOAD, ensure_ascii=False)),
    )
    con.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at) "
        "VALUES ('r-collector', 'cxlabs', 7, 'zakonczony', ?)",
        (RUN_AT,),
    )
    utworz_konto(con, rola=ROLA_KLIENT, haslo=HASLO_KLIENTA, client_id="cxlabs")
    utworz_konto(con, rola=ROLA_ZESPOL, haslo=HASLO_ZESPOLU, email=EMAIL)
    con.commit()
    con.close()
    yield sciezka


@pytest.fixture
def klient_http(baza: Path) -> Iterator[TestClient]:
    with TestClient(zbuduj_aplikacje(baza=baza), base_url="https://test") as c:
        c.post("/api/sesja/klient", json={"haslo": HASLO_KLIENTA, "client_id": "cxlabs"})
        yield c


@pytest.fixture
def atrapy_konfiguracji(monkeypatch: pytest.MonkeyPatch) -> None:
    """Atrapy sekretów dla testów wołających fazy WPROST.

    Warstwa 1 celowo odcina środowisko (`conftest._odetnij_srodowisko`), a te
    testy przechodzą przez `konfiguracja.wczytaj()`, bo sprawdzają prawdziwą
    ścieżkę, nie endpoint. Atrapy, nie prawdziwe sekrety: nic tu nie wychodzi
    do sieci, a test sięgający po `.env` przestałby działać na świeżym klonie.
    """
    monkeypatch.setenv("MONDAY_TOKEN", ATRAPA_KLUCZA)
    monkeypatch.setenv("SOL_PSEUDONIMIZACJI", "atrapa-soli-do-testow-16")
    monkeypatch.setenv("ANTHROPIC_API_KEY", ATRAPA_ANTHROPIC)


def zadanie_czekajace(baza: Path, *, wazne: bool = True) -> str:
    """Zadanie w stanie `czeka_na_zgode`, ze snapshotem 7 i terminem zgody."""
    con = polacz(baza)
    try:
        zadanie_id = utworz_zadanie(con, client_id="cxlabs", konto_id=1)
        przesuniecie = timedelta(hours=GODZIN_WAZNOSCI_ZGODY) if wazne else timedelta(hours=-1)
        zapisz_stan(
            con,
            zadanie_id,
            stan=STAN_CZEKA_NA_ZGODE,
            etap="czekam na wybór zakresu",
            postep=60,
            snapshot_id=7,
            zgoda_do=(datetime.now(tz=UTC) + przesuniecie).isoformat(),
        )
        con.commit()
        return zadanie_id
    finally:
        con.close()


# ── faza pierwsza zatrzymuje się PRZED agentem ───────────────────────────


def test_zbieranie_konczy_sie_na_zgodzie_a_nie_na_agencie(
    baza: Path, monkeypatch: pytest.MonkeyPatch, atrapy_konfiguracji: None
) -> None:
    """Zadanie po fazie pierwszej stoi w `czeka_na_zgode` i ma zapisany snapshot.

    To jest cała istota zmiany: agent nie może ruszyć, dopóki człowiek nie
    zatwierdzi zakresu, bo rachunek idzie na jego klucz.
    """
    from monday_audit.web import run as modul_run

    wywolania: list[tuple[Any, ...]] = []

    async def atrapa_collectora(**kwargs: Any) -> Any:
        class Raport:
            run_id = "r-collector"
            snapshot_id = 7
            wywolan = 130

        return Raport()

    monkeypatch.setattr(modul_run, "wykonaj_run", atrapa_collectora)
    monkeypatch.setattr(
        modul_run,
        "zbadaj_hipotezy",
        lambda *a, **k: wywolania.append(("AGENT", a)),
    )

    modul_run.uruchom_audyt_w_tle(
        baza, zadanie_czekajace(baza), "cxlabs", ATRAPA_KLUCZA, "cale_konto", None, ATRAPA_ANTHROPIC
    )

    assert wywolania == [], "agent ruszył w fazie pierwszej — zgoda jest wtedy fikcją"
    con = polacz(baza)
    stan = con.execute(
        "SELECT stan, snapshot_id, zgoda_do FROM zadania ORDER BY zaczeto DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert stan["stan"] == STAN_CZEKA_NA_ZGODE
    assert stan["snapshot_id"] == 7
    assert stan["zgoda_do"], "bez terminu zgoda nigdy by nie wygasła"


# ── ekran wyboru ─────────────────────────────────────────────────────────


def test_ekran_wyboru_liczy_sie_z_tego_snapshotu(klient_http: TestClient, baza: Path) -> None:
    zadanie_id = zadanie_czekajace(baza)

    odp = klient_http.get(f"/api/audyt/{zadanie_id}/wybor")

    assert odp.status_code == 200, odp.text
    dane = odp.json()
    assert [t["board_id"] for t in dane["tablice"]] == ["b1", "b2"]
    assert dane["pominietych_pomocniczych"] == 1
    assert {w["nazwa"] for w in dane["workspace_y"]} == {"Operacje", "Sprzedaż"}
    assert "srodek_usd" in dane["widelki"]
    # Zastrzeżenia collectora idą na ekran, a nie są pisane po raz drugi.
    assert dane["uwagi_o_zakresie"] == ["lista użytkowników jest z natury na poziomie konta"]
    assert dane["zgoda_do"]


def test_ekran_wyboru_cudzego_zadania_to_404(klient_http: TestClient, baza: Path) -> None:
    """Cudze i nieistniejące zadanie dają TO SAMO 404 — inaczej kod odpowiedzi
    zdradzałby, które identyfikatory istnieją."""
    assert klient_http.get("/api/audyt/nie-ma-takiego/wybor").status_code == 404


def test_ekran_wyboru_po_terminie_to_409(klient_http: TestClient, baza: Path) -> None:
    zadanie_id = zadanie_czekajace(baza, wazne=False)

    odp = klient_http.get(f"/api/audyt/{zadanie_id}/wybor")

    assert odp.status_code == 409
    assert "zestarzały" in odp.json()["detail"]


# ── zgoda odpala fazę drugą ──────────────────────────────────────────────


def test_zgoda_na_cale_konto_nie_zaweza(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Puste listy znaczą „całe konto", więc faza druga dostaje `None`.

    Pusty zbiór znaczyłby „nie wybrano ani jednej tablicy" i zostawiłby
    wyłącznie hipotezy o koncie — to inna zgoda niż ta, którą klient wyraził.
    """
    from monday_audit.web import api as modul_api

    przekazane: list[Any] = []
    monkeypatch.setattr(modul_api, "uruchom_analize_w_tle", lambda *a: przekazane.append(a))
    zadanie_id = zadanie_czekajace(baza)

    odp = klient_http.post(
        f"/api/audyt/{zadanie_id}/zgoda",
        json={"klucz_api": ATRAPA_KLUCZA, "klucz_anthropic": ATRAPA_ANTHROPIC},
    )

    assert odp.status_code == 200, odp.text
    assert przekazane[0][-1] is None


def test_zgoda_na_jedna_tablice_zaweza_do_niej(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from monday_audit.web import api as modul_api

    przekazane: list[Any] = []
    monkeypatch.setattr(modul_api, "uruchom_analize_w_tle", lambda *a: przekazane.append(a))
    zadanie_id = zadanie_czekajace(baza)

    odp = klient_http.post(
        f"/api/audyt/{zadanie_id}/zgoda",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_ANTHROPIC,
            "board_ids": ["b1"],
        },
    )

    assert odp.status_code == 200, odp.text
    assert przekazane[0][-1] == frozenset({"b1"})


def test_zgoda_na_workspace_rozwija_sie_do_jego_tablic(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dalej w kodzie istnieje jedno pojęcie zawężenia — `board_ids`.

    Workspace rozwijamy tutaj, więc `odsiej_hipotezy` i cała reszta ścieżki
    nie muszą wiedzieć, że workspace'y istnieją.
    """
    from monday_audit.web import api as modul_api

    przekazane: list[Any] = []
    monkeypatch.setattr(modul_api, "uruchom_analize_w_tle", lambda *a: przekazane.append(a))
    zadanie_id = zadanie_czekajace(baza)

    odp = klient_http.post(
        f"/api/audyt/{zadanie_id}/zgoda",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_ANTHROPIC,
            "workspace_ids": ["w1"],
        },
    )

    assert odp.status_code == 200, odp.text
    # `pomocnicza` też jest w w1, ale nie jest tablicą do wyboru.
    assert przekazane[0][-1] == frozenset({"b1"})


def test_obcy_board_id_odrzucony(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cicha tolerancja pozwoliłaby zapłacić za audyt tablicy, której tu nie ma."""
    from monday_audit.web import api as modul_api

    monkeypatch.setattr(modul_api, "uruchom_analize_w_tle", lambda *a: None)
    zadanie_id = zadanie_czekajace(baza)

    odp = klient_http.post(
        f"/api/audyt/{zadanie_id}/zgoda",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_ANTHROPIC,
            "board_ids": ["b1", "nie-z-tego-snapshotu"],
        },
    )

    assert odp.status_code == 400
    assert "poza tym snapshotem" in odp.json()["detail"]


def test_druga_zgoda_na_to_samo_zadanie_odrzucona(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bez tego drugie kliknięcie „zatwierdź" odpalałoby agenta po raz drugi —
    czyli podwójny rachunek na kluczu klienta."""
    from monday_audit.web import api as modul_api

    monkeypatch.setattr(modul_api, "uruchom_analize_w_tle", lambda *a: None)
    zadanie_id = zadanie_czekajace(baza)
    ciało = {"klucz_api": ATRAPA_KLUCZA, "klucz_anthropic": ATRAPA_ANTHROPIC}

    assert klient_http.post(f"/api/audyt/{zadanie_id}/zgoda", json=ciało).status_code == 200
    powtorna = klient_http.post(f"/api/audyt/{zadanie_id}/zgoda", json=ciało)

    assert powtorna.status_code == 409
    assert "nie czeka na zgodę" in powtorna.json()["detail"]


def test_zgoda_po_terminie_odrzucona(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from monday_audit.web import api as modul_api

    wywolania: list[Any] = []
    monkeypatch.setattr(modul_api, "uruchom_analize_w_tle", lambda *a: wywolania.append(a))
    zadanie_id = zadanie_czekajace(baza, wazne=False)

    odp = klient_http.post(
        f"/api/audyt/{zadanie_id}/zgoda",
        json={"klucz_api": ATRAPA_KLUCZA, "klucz_anthropic": ATRAPA_ANTHROPIC},
    )

    assert odp.status_code == 409
    assert wywolania == [], "analiza ruszyła na przedawnionej zgodzie"


def test_wybor_zapisany_do_zadania(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raport ma napisać, ile tablic objął audyt — a snapshot jest pełny,
    więc sam tego nie zdradza."""
    from monday_audit.web import api as modul_api

    monkeypatch.setattr(modul_api, "uruchom_analize_w_tle", lambda *a: None)
    zadanie_id = zadanie_czekajace(baza)

    klient_http.post(
        f"/api/audyt/{zadanie_id}/zgoda",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_ANTHROPIC,
            "board_ids": ["b1"],
        },
    )

    con = polacz(baza)
    wybor = con.execute("SELECT wybor FROM zadania WHERE id = ?", (zadanie_id,)).fetchone()
    con.close()
    assert json.loads(wybor["wybor"])["board_ids"] == ["b1"]


def test_klucze_nie_ida_do_bazy(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D11/D12 obowiązuje też w drugiej fazie — ona przyjmuje klucze na nowo.

    Szukamy w CAŁEJ bazie, nie tylko w `zadania`: nowa kolumna `wybor` też
    jest miejscem, w które dałoby się coś wsypać przez pomyłkę.
    """
    from monday_audit.web import api as modul_api

    monkeypatch.setattr(modul_api, "uruchom_analize_w_tle", lambda *a: None)
    zadanie_id = zadanie_czekajace(baza)

    klient_http.post(
        f"/api/audyt/{zadanie_id}/zgoda",
        json={"klucz_api": ATRAPA_KLUCZA, "klucz_anthropic": ATRAPA_ANTHROPIC},
    )

    con = polacz(baza)
    trafienia: list[str] = []
    for (tabela,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        kolumny = [k[1] for k in con.execute(f"PRAGMA table_info({tabela})")]
        for kolumna in kolumny:
            znalezione = con.execute(
                f"SELECT COUNT(*) c FROM {tabela} "  # noqa: S608
                f"WHERE CAST({kolumna} AS TEXT) LIKE ? OR CAST({kolumna} AS TEXT) LIKE ?",
                (f"%{ATRAPA_KLUCZA}%", f"%{ATRAPA_ANTHROPIC}%"),
            ).fetchone()["c"]
            if znalezione:
                trafienia.append(f"{tabela}.{kolumna}")
    con.close()
    assert trafienia == [], f"klucz wylądował w bazie: {trafienia}"


def test_stan_mowi_frontowi_ze_czeka_na_decyzje(klient_http: TestClient, baza: Path) -> None:
    """Front przestaje odpytywać przy `trwa: false`, więc musi odróżnić
    „skończone" od „czeka na ciebie"."""
    zadanie_id = zadanie_czekajace(baza)

    dane = klient_http.get(f"/api/audyt/{zadanie_id}").json()

    assert dane["trwa"] is False
    assert dane["czeka_na_zgode"] is True


# ── faza druga odsiewa zgodnie ze zgodą ──────────────────────────────────


def test_faza_druga_bada_tylko_zatwierdzony_zakres(
    baza: Path, monkeypatch: pytest.MonkeyPatch, atrapy_konfiguracji: None
) -> None:
    """Sprawdzenie końca łańcucha: agent dostaje hipotezy PO odsianiu.

    Wcześniejsze testy pilnują, co API przekazuje. Ten pilnuje, co z tym robi
    faza druga — bo między jednym a drugim jest filtr, który może odsiać za
    dużo albo za mało.
    """
    from monday_audit.web import run as modul_run

    dostane: list[Any] = []

    async def atrapa_agenta(hipotezy: Any, **kwargs: Any) -> dict[str, Any]:
        dostane.append(list(hipotezy))
        return {"findingi": [], "hipotezy_odrzucone": [], "zuzycie": {}, "per_hipoteza": []}

    monkeypatch.setattr(modul_run, "zbadaj_hipotezy", atrapa_agenta)
    monkeypatch.setattr(modul_run, "MondayClient", _AtrapaKlienta)
    zadanie_id = zadanie_czekajace(baza)

    modul_run.uruchom_analize_w_tle(
        baza, zadanie_id, "cxlabs", ATRAPA_KLUCZA, ATRAPA_ANTHROPIC, frozenset({"b1"})
    )

    assert dostane, "agent nie został wywołany"
    obiekty = {h.obiekt_id for h in dostane[0]}
    assert "b2" not in obiekty, "faza druga zbadała tablicę spoza zgody"


def test_run_z_panelu_pinuje_model_i_prompt(
    baza: Path, monkeypatch: pytest.MonkeyPatch, atrapy_konfiguracji: None
) -> None:
    """Ścieżka panelu musi pinować to samo, co ścieżka CLI.

    Regresja z 2026-09-02, znaleziona na PIERWSZYM runie produkcyjnym z panelu:
    `runy.prompt_hash` był pusty, bo `web/run.py` nie wstawiał tej kolumny —
    dokładne powtórzenie usterki opisanej w `agent.py` („do 2026-08-05
    `prompt_hash` był NULL we WSZYSTKICH runach"), naprawionej wtedy tylko
    w `cli_agent`, bo panel nie dochodził jeszcze do zapisu.

    Test pilnuje SKUTKU w bazie, nie treści zapytania SQL: trzecia kopia tej
    usterki wejdzie inną drogą niż dwie poprzednie.

    `model` sprawdzany przez `is MODEL`, nie przez porównanie z napisem —
    literał w teście zamieniłby jedno źródło prawdy na dwa, czyli powtórzyłby
    błąd, który ten test ma wyłapywać.
    """
    from monday_audit.agent import MODEL, hash_promptu
    from monday_audit.web import run as modul_run

    async def atrapa_agenta(hipotezy: Any, **kwargs: Any) -> dict[str, Any]:
        return {"findingi": [], "hipotezy_odrzucone": [], "zuzycie": {}, "per_hipoteza": []}

    monkeypatch.setattr(modul_run, "zbadaj_hipotezy", atrapa_agenta)
    monkeypatch.setattr(modul_run, "MondayClient", _AtrapaKlienta)
    zadanie_id = zadanie_czekajace(baza)

    modul_run.uruchom_analize_w_tle(
        baza, zadanie_id, "cxlabs", ATRAPA_KLUCZA, ATRAPA_ANTHROPIC, frozenset({"b1"})
    )

    with contextlib.closing(polacz(baza)) as con:
        wiersz = con.execute(
            "SELECT model, rubric_ver, prompt_hash FROM runy WHERE run_id LIKE '%-agent'"
        ).fetchone()

    assert wiersz is not None, "run agenta nie trafił do `runy`"
    model, rubric_ver, prompt_hash = wiersz
    assert model == MODEL, "model nie jest pinowany ze stałej `agent.MODEL`"
    assert rubric_ver, "rubric_ver pusty"
    assert prompt_hash == hash_promptu(), "prompt_hash nie jest pinowany"


class _AtrapaKlienta:
    """`MondayClient` bez sieci — faza druga tworzy go, choć agent jest atrapą."""

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    async def __aenter__(self) -> _AtrapaKlienta:
        return self

    async def __aexit__(self, *args: Any) -> None: ...


def test_pominiete_hipotezy_zostawiaja_slad(
    baza: Path, monkeypatch: pytest.MonkeyPatch, atrapy_konfiguracji: None
) -> None:
    """Panel pokazuje `hipotezy_odrzucone` jako „czego nie widać".

    Bez tego śladu klient nie ma jak sprawdzić, co jego wybór wyciął z audytu.
    """
    from monday_audit.web import run as modul_run

    async def atrapa_agenta(hipotezy: Any, **kwargs: Any) -> dict[str, Any]:
        return {"findingi": [], "hipotezy_odrzucone": [], "zuzycie": {}, "per_hipoteza": []}

    monkeypatch.setattr(modul_run, "zbadaj_hipotezy", atrapa_agenta)
    monkeypatch.setattr(modul_run, "MondayClient", _AtrapaKlienta)

    modul_run.uruchom_analize_w_tle(
        baza,
        zadanie_czekajace(baza),
        "cxlabs",
        ATRAPA_KLUCZA,
        ATRAPA_ANTHROPIC,
        frozenset({"b1"}),
    )

    con = polacz(baza)
    powody = [w["powod"] for w in con.execute("SELECT powod FROM hipotezy_odrzucone").fetchall()]
    con.close()
    assert any("poza wybranym zakresem" in p for p in powody), (
        f"brak śladu po pominiętych hipotezach: {powody}"
    )


def test_mozliwosc_wskazuje_zadanie_czekajace(klient_http: TestClient, baza: Path) -> None:
    """Odświeżenie strony nie może gubić zebranych danych.

    `zadanie_id` żyje w stanie komponentu, więc po przeładowaniu karty front nie
    miałby jak wrócić do wyboru zakresu: limit monday zużyty, zgoda ważna
    dwanaście godzin, a klient widzi znowu formularz na klucz.
    """
    zadanie_id = zadanie_czekajace(baza)

    dane = klient_http.get("/api/audyt/mozliwosc").json()

    assert dane["zadanie_czekajace"] == zadanie_id


def test_brak_czekajacego_zadania_daje_null(klient_http: TestClient, baza: Path) -> None:
    """Komplement — inaczej front wskazywałby na zadanie, którego nie ma."""
    assert klient_http.get("/api/audyt/mozliwosc").json()["zadanie_czekajace"] is None


def test_zadanie_po_terminie_nie_wraca(klient_http: TestClient, baza: Path) -> None:
    """`wolno_odpalic` woła reapera, więc przedawniona zgoda jest już błędem.

    Bez tego front wracałby do ekranu, na którym każde kliknięcie kończy się
    409 — i klient nie wiedziałby, że ma po prostu zbierać od nowa.
    """
    zadanie_czekajace(baza, wazne=False)

    assert klient_http.get("/api/audyt/mozliwosc").json()["zadanie_czekajace"] is None


def test_znacznik_bez_strefy_nie_wywraca_hamulca(klient_http: TestClient, baza: Path) -> None:
    """ZMIERZONA USTERKA: panel odpowiadał 500 na każde żądanie tego klienta.

    `wolno_odpalic` porównywało `datetime.fromisoformat(zaczeto)` z
    `datetime.now(tz=UTC)`. Wiersz wstawiony SQL-owym `datetime('now')` nie ma
    strefy, więc porównanie rzucało `TypeError` — a hamulec kosztu czyta tę
    kolumnę przy każdym kliknięciu „wygeneruj audyt".

    Wyszło na wierszu z mojego skryptu podglądowego, nie z kodu produkcyjnego,
    ale to nie znaczy, że nie może się powtórzyć: ręczny SQL i przyszła migracja
    zapisują tak samo. Hamulec nie może padać na KSZTAŁCIE danych.
    """
    con = polacz(baza)
    con.execute(
        "INSERT INTO zadania (id, client_id, konto_id, stan, etap, postep, zaczeto) "
        "VALUES ('bez-strefy', 'cxlabs', 1, 'gotowe', '', 100, datetime('now'))"
    )
    con.commit()
    con.close()

    odp = klient_http.get("/api/audyt/mozliwosc")

    assert odp.status_code == 200, (
        f"hamulec kosztu wywrócił się na znaczniku bez strefy: {odp.text}"
    )


# ── podgląd PRZED zbieraniem ─────────────────────────────────────────────


def test_podglad_wymaga_sesji(baza: Path) -> None:
    """Endpoint niesie klucz monday, więc bez sesji nie odpowiada."""
    from monday_audit.web.api import zbuduj_aplikacje

    with TestClient(zbuduj_aplikacje(baza=baza), base_url="https://test") as c:
        odp = c.post("/api/audyt/podglad", json={"klucz_api": ATRAPA_KLUCZA})

    assert odp.status_code == 401


def test_podglad_klucz_nie_idzie_w_url(
    klient_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST, nie GET — adresy trafiają do logów serwera i historii przeglądarki.

    Sprawdzamy przez FastAPI: gdyby endpoint był `GET` z parametrem, żądanie
    `POST` z kluczem w ciele dostałoby 405.
    """
    trafil: dict[str, Any] = {}

    class Atrapa:
        def __init__(self, token: str, rejestr: Any) -> None:
            trafil["token"] = token

        async def __aenter__(self) -> Atrapa:
            return self

        async def __aexit__(self, *_: object) -> None: ...

        async def query(self, gql: str, variables: Any = None, **_: Any) -> dict[str, Any]:
            return {"workspaces": [{"id": "1", "name": "Operacje"}]}

    monkeypatch.setattr("monday_audit.web.api.MondayClient", Atrapa)

    odp = klient_http.post("/api/audyt/podglad", json={"klucz_api": ATRAPA_KLUCZA})

    assert odp.status_code == 200
    assert odp.json()["workspace_y"] == [{"workspace_id": "1", "nazwa": "Operacje"}]
    assert trafil["token"] == ATRAPA_KLUCZA


def test_podglad_nie_zaklada_runu(
    klient_http: TestClient, baza: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Puste wiersze w `runy` zepsuły już raz listę audytów w panelu.

    Podgląd to trzy zapytania, nie run — nie ma prawa zostawić po sobie wiersza
    ani w `runy`, ani w `wywolania`.
    """

    class Atrapa:
        def __init__(self, *a: Any, **k: Any) -> None: ...

        async def __aenter__(self) -> Atrapa:
            return self

        async def __aexit__(self, *_: object) -> None: ...

        async def query(self, gql: str, variables: Any = None, **_: Any) -> dict[str, Any]:
            return {"workspaces": [{"id": "1", "name": "Operacje"}]}

    monkeypatch.setattr("monday_audit.web.api.MondayClient", Atrapa)
    con = polacz(baza)
    przed = con.execute("SELECT COUNT(*) c FROM runy").fetchone()["c"]
    con.close()

    klient_http.post("/api/audyt/podglad", json={"klucz_api": ATRAPA_KLUCZA})

    con = polacz(baza)
    po = con.execute("SELECT COUNT(*) c FROM runy").fetchone()["c"]
    wywolan = con.execute("SELECT COUNT(*) c FROM wywolania").fetchone()["c"]
    con.close()
    assert po == przed, "podgląd założył wiersz w `runy`"
    assert wywolan == 0, "podgląd zapisał wywołania, choć nie ma runu"


def test_zly_klucz_daje_komunikat_bez_szczegolow_api(
    klient_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wyjątek z API może nieść fragment odpowiedzi — nie wkładamy go do odpowiedzi.

    Ta sama droga wycieku, którą przy zapisie do bazy zamyka `_bez_sekretow`.
    """

    class Atrapa:
        def __init__(self, *a: Any, **k: Any) -> None: ...

        async def __aenter__(self) -> Atrapa:
            return self

        async def __aexit__(self, *_: object) -> None: ...

        async def query(self, *a: Any, **k: Any) -> dict[str, Any]:
            raise RuntimeError(f"401 Unauthorized: token {ATRAPA_KLUCZA} odrzucony")

    monkeypatch.setattr("monday_audit.web.api.MondayClient", Atrapa)

    odp = klient_http.post("/api/audyt/podglad", json={"klucz_api": ATRAPA_KLUCZA})

    assert odp.status_code == 400
    assert ATRAPA_KLUCZA not in odp.text, "klucz wyciekł w komunikacie błędu"
    assert "Unauthorized" not in odp.text


def test_zbieranie_dostaje_zakres_z_podgladu(
    klient_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wybór z ekranu podglądu MUSI dojść do collectora.

    Bez tego zbieranie leciałoby po całym koncie (500+ tablic, ZMIERZONE 17 s
    samych zapytań o tablice), a klient patrzyłby na rachunek, na który się
    nie zgadzał.
    """
    przekazane: dict[str, Any] = {}
    monkeypatch.setattr(
        "monday_audit.web.api.uruchom_audyt_w_tle",
        lambda *a: przekazane.__setitem__("argumenty", a),
    )

    odp = klient_http.post(
        "/api/audyt",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_ANTHROPIC,
            "zakres": "tablice",
            "board_ids": ["b1", "b2"],
        },
    )

    assert odp.status_code == 200, odp.text
    argumenty = przekazane["argumenty"]
    assert "tablice" in argumenty
    assert ["b1", "b2"] in argumenty


def test_nieznany_tryb_zakresu_odrzucony(klient_http: TestClient) -> None:
    """Wzorzec w `DaneAudytu` pilnuje trzech trybów, które zna `Zakres`."""
    odp = klient_http.post(
        "/api/audyt",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "klucz_anthropic": ATRAPA_ANTHROPIC,
            "zakres": "wymyslony",
        },
    )

    assert odp.status_code == 422


# ── porzucenie zebranych danych ──────────────────────────────────────────


def test_porzucenie_zwalnia_konto_na_kolejny_audyt(klient_http: TestClient, baza: Path) -> None:
    """Po porzuceniu klient może od razu zbierać od nowa.

    Sufit i odstęp zdjęte 2026-08-25 (koszt na kluczu klienta), ale ZOSTAŁO
    sprawdzenie „audyt już trwa" — ochrona spójności, nie kosztu. Ten test
    pilnuje, że porzucone zadanie NIE liczy się jako trwające: inaczej klient
    zobaczyłby „audyt tego konta już trwa" przy audycie, z którego właśnie
    zrezygnował, i nie miałby jak się odblokować.
    """
    from monday_audit.zadania import wolno_odpalic

    # Kilka podejść z rzędu — dawniej trzecie wypaliłoby limit.
    for _ in range(4):
        zadanie_id = zadanie_czekajace(baza)
        assert klient_http.post(f"/api/audyt/{zadanie_id}/porzuc").status_code == 200

    con = polacz(baza)
    wolno, powod = wolno_odpalic(con, "cxlabs")
    con.close()
    assert wolno, f"cztery porzucenia zablokowały klienta: {powod}"


def test_porzucenie_zostawia_slad_a_nie_kasuje(klient_http: TestClient, baza: Path) -> None:
    """Wiersz zostaje jako `blad` — limit monday został zużyty i to jest fakt.

    Usunięcie wiersza kłamałoby, że nic się nie stało.
    """
    zadanie_id = zadanie_czekajace(baza)

    klient_http.post(f"/api/audyt/{zadanie_id}/porzuc")

    con = polacz(baza)
    wiersz = con.execute("SELECT stan, blad FROM zadania WHERE id = ?", (zadanie_id,)).fetchone()
    snapshot = con.execute("SELECT COUNT(*) c FROM snapshots WHERE id = 7").fetchone()["c"]
    con.close()
    assert wiersz is not None, "wiersz zadania został usunięty"
    assert wiersz["stan"] == "blad"
    assert "porzucone" in wiersz["blad"]
    # Snapshot jest NIEMUTOWALNY (D7) — porzucenie zgody go nie dotyczy.
    assert snapshot == 1, "porzucenie usunęło snapshot"


def test_porzucenie_zwalnia_ekran_wyboru(klient_http: TestClient, baza: Path) -> None:
    """Po porzuceniu front nie może wracać do tego samego ekranu zgody."""
    zadanie_id = zadanie_czekajace(baza)
    assert klient_http.get("/api/audyt/mozliwosc").json()["zadanie_czekajace"] == zadanie_id

    klient_http.post(f"/api/audyt/{zadanie_id}/porzuc")

    assert klient_http.get("/api/audyt/mozliwosc").json()["zadanie_czekajace"] is None


def test_nie_da_sie_porzucic_audytu_w_analizie(klient_http: TestClient, baza: Path) -> None:
    """Analiza już płaci — porzucenie nie odwróciłoby wydanych pieniędzy.

    409, nie 404: zadanie istnieje, tylko nie jest w stanie, z którego da się
    zrezygnować.
    """
    from monday_audit.zadania import STAN_ANALIZUJE, utworz_zadanie, zapisz_stan

    con = polacz(baza)
    zadanie_id = utworz_zadanie(con, client_id="cxlabs", konto_id=1)
    zapisz_stan(con, zadanie_id, stan=STAN_ANALIZUJE, snapshot_id=7)
    con.commit()
    con.close()

    odp = klient_http.post(f"/api/audyt/{zadanie_id}/porzuc")

    assert odp.status_code == 409
    assert "nie da się już porzucić" in odp.json()["detail"]


def test_cudzego_audytu_nie_da_sie_porzucic(klient_http: TestClient, baza: Path) -> None:
    """Ta sama granica co przy każdym endpoincie zadania: 404, nie 403."""
    assert klient_http.post("/api/audyt/nie-ma-takiego/porzuc").status_code == 404


# ── granularny postęp analizy ────────────────────────────────────────────


def test_analiza_melduje_postep_po_kazdej_hipotezie(
    baza: Path, monkeypatch: pytest.MonkeyPatch, atrapy_konfiguracji: None
) -> None:
    """ZGŁOSZONE (Kuba, 2026-08-25): „nie wiesz, kiedy co się stanie".

    Wcześniej cała analiza — dziewięć minut — miała JEDEN zapis stanu, więc
    ekran nie odróżniał pracy od zawieszenia. Teraz `zbadaj_hipotezy` woła hook
    po każdej hipotezie, a `_analizuj` przekłada to na `etap` i `postep`.

    Test sprawdza PODŁĄCZENIE: czy hook faktycznie dochodzi do bazy. Sama pętla
    agenta wymaga modelu, więc podmieniamy ją atrapą, która ten hook wywołuje —
    dokładnie tak, jak robi to prawdziwa pętla.
    """
    from monday_audit.web import run as modul_run

    async def atrapa_agenta(hipotezy: Any, **kwargs: Any) -> dict[str, Any]:
        melduj = kwargs.get("postep")
        assert melduj is not None, "faza druga nie podała hooka postępu"
        # Trzy „zbadane" hipotezy z pięciu — jak prawdziwa pętla.
        for i in (1, 2, 3):
            melduj(i, 5, "BOARD_GHOST")
        return {"findingi": [], "hipotezy_odrzucone": [], "zuzycie": {}, "per_hipoteza": []}

    monkeypatch.setattr(modul_run, "zbadaj_hipotezy", atrapa_agenta)
    monkeypatch.setattr(modul_run, "MondayClient", _AtrapaKlienta)
    zadanie_id = zadanie_czekajace(baza)

    stany: list[tuple[str, int]] = []
    prawdziwy_zapis = modul_run.zapisz_stan

    def podsluch(con: Any, zid: str, **kw: Any) -> None:
        if kw.get("etap", "").startswith("zbadano"):
            stany.append((kw["etap"], kw["postep"]))
        prawdziwy_zapis(con, zid, **kw)

    monkeypatch.setattr(modul_run, "zapisz_stan", podsluch)

    modul_run.uruchom_analize_w_tle(
        baza, zadanie_id, "cxlabs", ATRAPA_KLUCZA, ATRAPA_ANTHROPIC, None
    )

    assert len(stany) == 3, f"postęp zgłoszony {len(stany)} razy, oczekiwano 3: {stany}"
    assert stany[0][0] == "zbadano 1 z 5 sygnałów"
    # Postęp ROŚNIE i mieści się w pasie analizy (62–94), żeby pasek się nie
    # cofał po zebraniu danych ani nie wyprzedzał walidacji.
    procenty = [p for _, p in stany]
    assert procenty == sorted(procenty), f"postęp nie rośnie: {procenty}"
    assert all(62 <= p <= 94 for p in procenty), f"postęp poza pasem analizy: {procenty}"


def test_padniety_hook_nie_przerywa_analizy(
    baza: Path, monkeypatch: pytest.MonkeyPatch, atrapy_konfiguracji: None
) -> None:
    """Raportowanie postępu jest mniej ważne niż wynik, za który klient zapłacił.

    Gdyby wyjątek z hooka leciał w górę, zapis do bazy w złym momencie
    (zamknięte połączenie, blokada SQLite) przerywałby analizę po ósmej z dwudziestu
    czterech hipotez — i klient płaciłby za nic.
    """
    import inspect

    from monday_audit.agent import zbadaj_hipotezy
    from monday_audit.web import run as modul_run

    # Kontrakt sygnatury: bez tego parametru całe raportowanie jest fikcją.
    assert "postep" in inspect.signature(zbadaj_hipotezy).parameters

    wywolano: list[int] = []

    async def atrapa_agenta(hipotezy: Any, **kwargs: Any) -> dict[str, Any]:
        # Tu sprawdzamy KONTRAKT: `_analizuj` musi przekazać hook, a pętla
        # agenta ma go wołać w `try`. Symulujemy padający hook i sprawdzamy,
        # że wynik i tak wraca.
        melduj = kwargs["postep"]
        for i in (1, 2):
            # Dokładnie to robi pętla agenta: łapie wszystko z hooka i leci
            # dalej, bo raportowanie postępu jest mniej ważne niż wynik.
            with contextlib.suppress(Exception):
                melduj(i, 2, "X")
            wywolano.append(i)
        return {"findingi": [], "hipotezy_odrzucone": [], "zuzycie": {}, "per_hipoteza": []}

    monkeypatch.setattr(modul_run, "zbadaj_hipotezy", atrapa_agenta)
    monkeypatch.setattr(modul_run, "MondayClient", _AtrapaKlienta)

    def zapis_padajacy(*a: Any, **kw: Any) -> None:
        if kw.get("etap", "").startswith("zbadano"):
            raise RuntimeError("baza zajęta")

    monkeypatch.setattr(modul_run, "zapisz_stan", zapis_padajacy)

    modul_run.uruchom_analize_w_tle(
        baza, zadanie_czekajace(baza), "cxlabs", ATRAPA_KLUCZA, ATRAPA_ANTHROPIC, None
    )

    assert wywolano == [1, 2], "analiza przerwała się na padniętym raportowaniu postępu"


def test_pelne_zaznaczenie_daje_zakres_workspace_nie_liste_tablic(
    klient_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ZMIERZONA USTERKA (Kuba, 2026-08-25): raport pokazywał więcej tablic,
    niż klient wybrał.

    Front przy pełnym zaznaczeniu wysyłał `zakres: "tablice"` z listą WSZYSTKICH
    identyfikatorów (na koncie acme: 97). Snapshot zapisywał wtedy zakres
    „97 wskazanych tablic" zamiast „ten workspace", a raport czyta zakres ze
    snapshotu — więc mówił coś innego, niż klient wybrał.

    Ten test pilnuje granicy po stronie API: przy PUSTEJ liście `board_ids`
    zakres musi zostać `workspace`, a `workspace_id` dojść do collectora.
    """
    przekazane: dict[str, Any] = {}
    monkeypatch.setattr(
        "monday_audit.web.api.uruchom_audyt_w_tle",
        lambda *a: przekazane.__setitem__("argumenty", a),
    )

    odp = klient_http.post(
        "/api/audyt",
        json={
            "klucz_api": ATRAPA_KLUCZA,
            "zakres": "workspace",
            "workspace_id": "w1",
            "board_ids": [],
        },
    )

    assert odp.status_code == 200, odp.text
    argumenty = przekazane["argumenty"]
    assert "workspace" in argumenty, f"zakres nie doszedł: {argumenty}"
    assert "w1" in argumenty, f"workspace_id nie doszedł: {argumenty}"
    assert [] in argumenty, "pusta lista tablic musi dojść jako pusta"
