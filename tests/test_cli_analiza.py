"""Wpięcie trace'ów w `cli_analiza` — test przez całą komendę (faza 5b).

W tym repo zdarzyły się już dwie usterki klasy „przeszło testy, a nie było
podpięte" (`can_use_tool` i klucz API), a trzecia była blisko: do fazy 5b nowa
ścieżka nie wysyłała trace'ów wcale, bo tracing z fazy 4 siedział w innej
funkcji. Testy samego `zbuduj_trace_analizy` tego by nie wykryły — dlatego ten
plik przejeżdża `uruchom` w całości, z atrapą tylko tam, gdzie zaczyna się
świat zewnętrzny: model, Langfuse i konfiguracja.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from monday_audit import cli_analiza
from monday_audit.agent import AgentError
from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.detektory import Hipoteza
from monday_audit.przebieg import zapisz_snapshot


class AtrapaSladu:
    def __init__(self) -> None:
        self.wyslane: list[Any] = []
        self.zamkniety = False

    def wyslij(self, trace: Any) -> None:
        self.wyslane.append(trace)

    def zamknij(self) -> None:
        self.zamkniety = True


@pytest.fixture
def srodowisko(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    baza = tmp_path / "test.db"
    con = polacz(baza)
    zastosuj_migracje(con)
    snapshot_id = zapisz_snapshot(
        con, client_id="cxlabs", payload={"meta": {}}, run_at="2026-09-23T00:00:00Z"
    )
    con.close()

    slad = AtrapaSladu()
    hipoteza = Hipoteza(
        klasa_id="BOARD_GHOST",
        obiekt_id="b1",
        fakty={"wpisow": 0, "nazwa": "tablica 1"},
        budzet_wywolan=2,
    )
    monkeypatch.setattr(cli_analiza, "wczytaj", lambda: SimpleNamespace(monday_audit_db=baza))
    monkeypatch.setattr(cli_analiza, "sol_z_ustawien", lambda _: b"s" * 16)
    monkeypatch.setattr(cli_analiza, "klucz_anthropic", lambda _: "klucz-testowy")
    monkeypatch.setattr(cli_analiza, "uruchom_detektory", lambda *_: ([hipoteza], {}))
    monkeypatch.setattr(cli_analiza, "wysylka_z_ustawien", lambda _: slad)
    return {"baza": baza, "snapshot_id": snapshot_id, "slad": slad}


def _argumenty(
    srodowisko: dict[str, Any], run_id: str, *, w_pamieci: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(
        klient="cxlabs",
        snapshot=None if w_pamieci else srodowisko["snapshot_id"],
        zakres="cale_konto" if w_pamieci else None,
        id=[],
        baza=None,
        wejscie=None,
        wyjscie=None,
        tylko_szacunek=False,
        run_id=run_id,
        json=False,
    )


def _status(srodowisko: dict[str, Any], run_id: str) -> str:
    con = polacz(srodowisko["baza"])
    try:
        return str(con.execute("SELECT status FROM runy WHERE run_id = ?", (run_id,)).fetchone()[0])
    finally:
        con.close()


async def test_udana_analiza_wysyla_trace(
    srodowisko: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def atrapa_sesji(*_: Any, **__: Any) -> dict[str, Any]:
        return {
            "uwagi": [],
            "pominiete": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "b1", "powod": "świeża"}],
            "zuzycie": {"tokens_out": 100, "koszt_usd": 0.12},
            "wywolania_narzedzi": ["pobierz_inwentarz:konto"],
        }

    monkeypatch.setattr(cli_analiza, "zbadaj_konto", atrapa_sesji)

    assert await cli_analiza.uruchom(_argumenty(srodowisko, "t-ok")) == 0

    slad = srodowisko["slad"]
    assert len(slad.wyslane) == 1, "trace analizy nie wyszedł — tracing niepodpięty"
    trace = slad.wyslane[0]
    assert trace.nazwa == "analiza:konto"
    assert trace.metadane["run_id"] == "t-ok"
    assert trace.metadane["rozstrzygniecie"] == "zakonczona"
    # Bufor dosłany — inaczej krótki proces CLI kończy się przed eksportem.
    assert slad.zamkniety
    assert _status(srodowisko, "t-ok") == "zakonczony"


async def test_padnieta_analiza_tez_wysyla_trace(
    srodowisko: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run, który padł, jest NAJCIEKAWSZY w trace'ach. Pierwszy prawdziwy run
    nowej ścieżki padł po opłaconej sesji i nie zostawił po sobie nic —
    ani wyniku, ani śladu."""

    async def sesja_padajaca(*_: Any, **__: Any) -> dict[str, Any]:
        raise AgentError("sesja analizy padła: błąd API")

    monkeypatch.setattr(cli_analiza, "zbadaj_konto", sesja_padajaca)

    with pytest.raises(AgentError):
        await cli_analiza.uruchom(_argumenty(srodowisko, "t-awaria"))

    slad = srodowisko["slad"]
    assert len(slad.wyslane) == 1
    trace = slad.wyslane[0]
    assert trace.metadane["rozstrzygniecie"] == "blad"
    assert "błąd API" in trace.obserwacje[0].wyjscie["blad"]
    assert slad.zamkniety
    assert _status(srodowisko, "t-awaria") == "przerwany"


# ── faza 5c: po runie na dysku nie zostaje nic o konkretnej osobie ────────
#
# To jest weryfikowalny rezultat fazy 5c i dlatego test przegląda CAŁĄ
# trwałą bazę — każdą tabelę, każdy wiersz — a nie tylko tabele, o których
# wiemy, że mogłyby coś zawierać. Dane osoby w tabeli, o której nikt nie
# pomyślał, to dokładnie ten przypadek, który ma się nie zdarzyć.

NAZWISKO = "Zdzisława Wąchockańska"
MAIL = "zdzislawa@klient.test"
PSEUDONIM = "1dcfeabe7fa5d9a7"
DATA_AKTYWNOSCI = "2026-06-09"


def _cala_baza(sciezka: Path) -> str:
    con = polacz(sciezka)
    try:
        tabele = [
            w["name"] for w in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        zrzut = []
        for tabela in tabele:
            for wiersz in con.execute(f'SELECT * FROM "{tabela}"'):  # noqa: S608
                zrzut.append(f"{tabela}: {dict(wiersz)}")
        return "\n".join(zrzut)
    finally:
        con.close()


async def test_run_w_pamieci_nie_zostawia_na_dysku_nic_o_osobie(
    srodowisko: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def collector_w_pamieci(*, con: Any, client_id: str, **_: Any) -> Any:
        # Dokładnie to, co robi prawdziwy collector: snapshot z pseudonimem
        # i tabela mapowania z PRAWDZIWYM nazwiskiem i mailem — w połączeniu,
        # które dostał.
        con.execute(
            "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
            "VALUES (?, ?, ?, ?)",
            (client_id, PSEUDONIM, NAZWISKO, MAIL),
        )
        snapshot_id = zapisz_snapshot(
            con,
            client_id=client_id,
            payload={"uzytkownicy": {"uzytkownicy": [{"user_hash": PSEUDONIM}]}},
            run_at="2026-09-23T00:00:00Z",
        )
        con.commit()
        return SimpleNamespace(snapshot_id=snapshot_id, wywolan=42)

    zombie = Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT",
        obiekt_id=PSEUDONIM,
        fakty={
            "user_hash": PSEUDONIM,
            "kind": "member",
            "status": "ACTIVE",
            "last_activity": f"{DATA_AKTYWNOSCI}T13:01:12Z",
            "obecnosc_w_logach": False,
            "plan_tier": "enterprise",
        },
        budzet_wywolan=0,
    )
    ghost = Hipoteza(klasa_id="BOARD_GHOST", obiekt_id="b1", fakty={"wpisow": 0}, budzet_wywolan=2)

    async def atrapa_sesji(*_: Any, **__: Any) -> dict[str, Any]:
        return {
            "uwagi": [],
            "pominiete": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "b1", "powod": "świeża"}],
            "zuzycie": {"tokens_out": 100, "koszt_usd": 0.12},
            "wywolania_narzedzi": [],
        }

    monkeypatch.setattr(cli_analiza, "wykonaj_run", collector_w_pamieci)
    monkeypatch.setattr(cli_analiza, "uruchom_detektory", lambda *_: ([zombie, ghost], {}))
    monkeypatch.setattr(cli_analiza, "zbadaj_konto", atrapa_sesji)
    monkeypatch.setattr(
        cli_analiza,
        "wczytaj",
        lambda: SimpleNamespace(
            monday_audit_db=srodowisko["baza"],
            monday_token=SimpleNamespace(get_secret_value=lambda: "token-testowy"),
        ),
    )

    assert await cli_analiza.uruchom(_argumenty(srodowisko, "t-pamiec", w_pamieci=True)) == 0

    zrzut = _cala_baza(srodowisko["baza"])

    # Nic o osobie — w ŻADNEJ tabeli.
    assert NAZWISKO not in zrzut
    assert MAIL not in zrzut
    assert PSEUDONIM not in zrzut
    assert DATA_AKTYWNOSCI not in zrzut

    con = polacz(srodowisko["baza"])
    try:
        # Snapshot fixture'u sprzed fazy 5c jest jeden; nowy NIE doszedł.
        assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM osoby_mapowanie").fetchone()[0] == 0

        # Ale wynik audytu JEST — zamaskowany, z liczbami (prośba Kuby).
        uwaga = con.execute(
            "SELECT klasa_id, zrodlo, dowod FROM uwagi_zapisane WHERE run_id = 't-pamiec'"
        ).fetchone()
        assert uwaga["klasa_id"] == "ZOMBIE_ACCOUNT"
        assert uwaga["zrodlo"] == "szablon"
        assert "[OSOBA]" in uwaga["dowod"]
        assert "dni przed runem" in uwaga["dowod"]

        run = con.execute("SELECT * FROM runy WHERE run_id = 't-pamiec'").fetchone()
        assert run["status"] == "zakonczony"
        # Snapshot z pamięci nie istnieje w tej bazie — klucz obcy by go odrzucił.
        assert run["snapshot_id"] is None
        assert run["findingow"] == 1
        assert run["hipotez_zbadanych"] == 2
        assert run["hipotez_odrzuconych"] == 1
        assert run["wywolania_monday"] == 42
    finally:
        con.close()


def _zapisz_obraz(katalog: Path) -> Path:
    """Obraz konta z treścią klienta w KLUCZACH — nazwa grupy per handlowiec,
    telefon, data, mail. Synchronicznie, bo w `async` pathlib blokuje pętlę."""
    obraz = {
        "konto": {"nazwa": "Konto testowe", "uzytkownikow": 19},
        "itemy": {
            "razem": 40,
            "per_produkt": {"crm": 40},
            "najwieksze_tablice": [
                {
                    "itemow": 40,
                    "rozklad": {
                        NAZWISKO: 20,
                        "tel +48 501 234 567": 8,
                        DATA_AKTYWNOSCI: 1,
                        f"Leady od {MAIL}": 11,
                    },
                }
            ],
        },
    }
    sciezka = katalog / "obraz.json"
    sciezka.write_text(json.dumps(obraz, ensure_ascii=False), encoding="utf-8")
    return sciezka


ZOMBIE_FAKTY = {
    "user_hash": PSEUDONIM,
    "kind": "member",
    "status": "ACTIVE",
    "last_activity": f"{DATA_AKTYWNOSCI}T13:01:12Z",
    "obecnosc_w_logach": False,
    "plan_tier": "enterprise",
}


async def test_tresc_klienta_w_kluczach_nie_zostaje_na_dysku(
    srodowisko: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review 2026-09-23: nazwy grup i etykiety siedzą w `rozklad` jako KLUCZE
    i do tej poprawki trafiały do `statystyki_runow` wprost. Test poprzedni
    tego nie łapał, bo w jego danych nie było ani jednego słownika po treści.

    Przy okazji: druga siatka w trace'ach dostaje listę znanych osób z bazy
    W PAMIĘCI — nazwisko przemycone w faktach hipotezy nie wychodzi."""

    async def collector_w_pamieci(*, con: Any, client_id: str, **_: Any) -> Any:
        con.execute(
            "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
            "VALUES (?, ?, ?, ?)",
            (client_id, PSEUDONIM, NAZWISKO, MAIL),
        )
        snapshot_id = zapisz_snapshot(
            con, client_id=client_id, payload={"meta": {}}, run_at="2026-09-23T00:00:00Z"
        )
        con.commit()
        return SimpleNamespace(snapshot_id=snapshot_id, wywolan=7)

    zombie = Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT", obiekt_id=PSEUDONIM, fakty=ZOMBIE_FAKTY, budzet_wywolan=0
    )
    ghost = Hipoteza(
        klasa_id="BOARD_GHOST",
        obiekt_id="b1",
        fakty={"wpisow": 0, "grupy": {NAZWISKO: 3}},
        budzet_wywolan=2,
    )

    async def atrapa_sesji(*_: Any, **__: Any) -> dict[str, Any]:
        return {
            "uwagi": [],
            "pominiete": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "b1", "powod": "świeża"}],
            "zuzycie": {"tokens_out": 100, "koszt_usd": 0.12},
            "wywolania_narzedzi": [],
        }

    monkeypatch.setattr(cli_analiza, "wykonaj_run", collector_w_pamieci)
    monkeypatch.setattr(cli_analiza, "uruchom_detektory", lambda *_: ([zombie, ghost], {}))
    monkeypatch.setattr(cli_analiza, "zbadaj_konto", atrapa_sesji)
    monkeypatch.setattr(
        cli_analiza,
        "wczytaj",
        lambda: SimpleNamespace(
            monday_audit_db=srodowisko["baza"],
            monday_token=SimpleNamespace(get_secret_value=lambda: "token-testowy"),
        ),
    )
    argumenty = _argumenty(srodowisko, "t-klucze", w_pamieci=True)
    argumenty.wejscie = _zapisz_obraz(tmp_path)

    assert await cli_analiza.uruchom(argumenty) == 0

    zrzut = _cala_baza(srodowisko["baza"])
    for slad_osoby in (NAZWISKO, MAIL, PSEUDONIM, DATA_AKTYWNOSCI, "501 234 567"):
        assert slad_osoby not in zrzut, f"na dysku został ślad osoby: {slad_osoby[:4]}…"

    con = polacz(srodowisko["baza"])
    try:
        # Statystyki JEST — z liczbami i kluczami naszego schematu.
        wiersz = con.execute(
            "SELECT dane FROM statystyki_runow WHERE run_id = 't-klucze'"
        ).fetchone()
        dane = json.loads(wiersz["dane"])
        assert dane["itemy"]["per_produkt"] == {"crm": 40}
        assert dane["konto"] == {"uzytkownikow": 19}
    finally:
        con.close()

    trace = srodowisko["slad"].wyslane[0]
    assert NAZWISKO not in repr(trace)
    assert f"[OSOBA:{PSEUDONIM}]" in repr(trace.obserwacje[0].wejscie)


async def test_padniete_statystyki_nie_kasuja_zapisanych_uwag(
    srodowisko: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: Any
) -> None:
    """Uwagi i statystyki szły w jednym `try`. Gdy padały statystyki, log
    mówił „uwagi NIE zapisane", a `findingow` dostawało 0 — choć uwagi były
    już zatwierdzone w bazie (review 2026-09-23)."""
    from monday_audit.przechowanie import PrzechowanieError

    zombie = Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT", obiekt_id=PSEUDONIM, fakty=ZOMBIE_FAKTY, budzet_wywolan=0
    )

    def statystyki_padaja(*_: Any, **__: Any) -> None:
        raise PrzechowanieError("w zapisie został adres e-mail — zapis przerwany")

    monkeypatch.setattr(cli_analiza, "uruchom_detektory", lambda *_: ([zombie], {}))
    monkeypatch.setattr(cli_analiza, "zapisz_statystyki", statystyki_padaja)
    argumenty = _argumenty(srodowisko, "t-statystyki")
    argumenty.wejscie = _zapisz_obraz(tmp_path)

    with caplog.at_level(logging.ERROR):
        assert await cli_analiza.uruchom(argumenty) == 0

    assert "statystyki NIE zapisane" in caplog.text
    assert "uwagi NIE zapisane" not in caplog.text
    con = polacz(srodowisko["baza"])
    try:
        run = con.execute("SELECT * FROM runy WHERE run_id = 't-statystyki'").fetchone()
        assert run["findingow"] == 1
        zapisanych = con.execute(
            "SELECT COUNT(*) FROM uwagi_zapisane WHERE run_id = 't-statystyki'"
        ).fetchone()[0]
        assert zapisanych == 1
    finally:
        con.close()


def _pliki(katalog: Path, wzorzec: str) -> list[Path]:
    """Synchronicznie, bo w funkcji `async` metody `pathlib` blokują pętlę."""
    return list(katalog.rglob(wzorzec))


async def test_pelna_odpowiedz_na_dysk_tylko_na_zadanie(
    srodowisko: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Do fazy 5c plik z pełną odpowiedzią powstawał zawsze — i niósł pseudonimy
    na dysk. Teraz tylko z `--wyjscie`."""

    async def atrapa_sesji(*_: Any, **__: Any) -> dict[str, Any]:
        return {"uwagi": [], "pominiete": [], "zuzycie": {}, "wywolania_narzedzi": []}

    monkeypatch.setattr(cli_analiza, "zbadaj_konto", atrapa_sesji)

    await cli_analiza.uruchom(_argumenty(srodowisko, "t-bez-pliku"))
    assert not _pliki(tmp_path, "analiza_t-bez-pliku.json")

    argumenty = _argumenty(srodowisko, "t-z-plikiem")
    argumenty.wyjscie = tmp_path / "wyniki"
    await cli_analiza.uruchom(argumenty)
    assert _pliki(tmp_path / "wyniki", "analiza_t-z-plikiem.json")
