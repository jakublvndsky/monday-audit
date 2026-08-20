"""Testy detektorów deterministycznych (etap 3.9), warstwa 1 z 04-test.md.

Dwa testy pilnują warunków odbioru z `03-build.md`:
`test_ten_sam_snapshot_daje_ta_sama_liste` (powtarzalność) i
`test_prog_czasowy_pochodzi_ze_snapshotu_nie_z_zegara` (zamrożony snapshot).

Reszta pilnuje rzeczy, które w wersji 0.1 rubryki były zwyczajnie policzone
błędnie — najważniejszy z nich to
`test_agenci_goscie_i_podglad_nie_sa_zombie`: bez tego filtra klasa liczyłaby
95 rekordów zamiast 19 i wystawiła klientowi rachunek za konta AI.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.detektory import (
    DETEKTORY,
    DetektorError,
    Hipoteza,
    automation_absent,
    automation_dead,
    board_ghost,
    board_no_owner,
    board_overcomplex,
    duplicate_structure,
    engagement_drop,
    guest_sprawl,
    plan_mismatch,
    process_bypass,
    uruchom_detektory,
    uzytkownik_wygaszony,
    zombie_account,
)
from monday_audit.przebieg import zapisz_snapshot
from monday_audit.rubryka import wczytaj_rubryke

OKNO_OD = "2026-05-03T18:00:00+00:00"
RUN_AT = "2026-08-01T18:00:00+00:00"

# Przed oknem i po oknie — w formacie, w jakim monday zwraca `last_activity`.
DAWNO = "2026-01-21T02:41:52Z"
NIEDAWNO = "2026-07-30T20:25:49Z"


def osoba(
    user_hash: str,
    *,
    kind: str = "member",
    status: str = "ACTIVE",
    last_activity: str | None = DAWNO,
    became_active_at: str | None = "2025-01-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "user_hash": user_hash,
        "kind": kind,
        "status": status,
        "last_activity": last_activity,
        "became_active_at": became_active_at,
        "title": None,
        "zespoly": [],
        "is_deleted": False,
        "is_email_confirmed": False,
        "created_at": "2025-01-01T00:00:00Z",
    }


def automatyzacja(
    automation_id: str,
    *,
    success: int = 0,
    failure: int = 0,
    exhausted: int = 0,
    powody: dict[str, int] | None = None,
) -> dict[str, Any]:
    rekord: dict[str, Any] = {
        "automation_id": automation_id,
        "success": success,
        "failure": failure,
        "exhausted": exhausted,
    }
    if powody:
        rekord["powody_bledow"] = powody
    return rekord


def payload(
    *,
    uzytkownicy: list[dict[str, Any]] | None = None,
    autorzy_w_logach: list[str] | None = None,
    statystyki: list[dict[str, Any]] | None = None,
    tier: str | None = "enterprise",
    okno_od: str = OKNO_OD,
) -> dict[str, Any]:
    """Minimalny snapshot o kształcie, jaki produkuje 3.8."""
    return {
        "meta": {"okno_od": okno_od, "run_at": RUN_AT, "okno_dni": 90},
        "konto": {"plan": {"tier": tier, "max_users": None}},
        "uzytkownicy": {"uzytkownicy": uzytkownicy or []},
        "aktywnosc": {
            "aktywnosc_tablic": [
                {"board_id": "1", "autorzy": autorzy_w_logach or []},
            ]
        },
        "automatyzacje": {"statystyki_automatyzacji": statystyki or []},
    }


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


def zapisz(con: sqlite3.Connection, dane: dict[str, Any]) -> int:
    return zapisz_snapshot(con, client_id="cxlabs", payload=dane, run_at=RUN_AT)


def budzet(klasa_id: str) -> int:
    return wczytaj_rubryke().budzet(klasa_id)


# ── ZOMBIE_ACCOUNT ───────────────────────────────────────────────────────


def test_konto_bez_aktywnosci_w_oknie_jest_zombie(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz(con, payload(uzytkownicy=[osoba("h1")]))

    hipotezy = zombie_account(con, snapshot_id, budzet("ZOMBIE_ACCOUNT"))

    assert [h.obiekt_id for h in hipotezy] == ["h1"]
    assert hipotezy[0].fakty["podstawa"] == "last_activity starsze niż okno"
    assert hipotezy[0].fakty["plan_tier"] == "enterprise"


def test_agenci_goscie_i_podglad_nie_sa_zombie(con: sqlite3.Connection) -> None:
    """NAJWAŻNIEJSZY test tej klasy — bez niego raport kłamie o pieniądzach.

    Zmierzone na CXLABS: z 95 rekordów tylko 19 zajmuje płatne miejsce, a 36 to
    konta agentów AI. Liczenie po wszystkich rekordach zawyżyłoby wynik
    czterokrotnie i wystawiło klientowi rachunek za konta, które nie są ludźmi.
    """
    ludzie = [
        osoba("agent", kind="personal_agent_member"),
        osoba("gosc", kind="guest"),
        osoba("podglad", kind="view_only"),
        osoba("czlowiek", kind="member"),
        osoba("admin", kind="admin"),
    ]
    snapshot_id = zapisz(con, payload(uzytkownicy=ludzie))

    hipotezy = zombie_account(con, snapshot_id, budzet("ZOMBIE_ACCOUNT"))

    assert sorted(h.obiekt_id for h in hipotezy) == ["admin", "czlowiek"]


def test_obecnosc_w_logach_znosi_hipoteze(con: sqlite3.Connection) -> None:
    """`last_activity` bywa nieaktualne — log aktywności jest mocniejszym dowodem."""
    snapshot_id = zapisz(con, payload(uzytkownicy=[osoba("h1")], autorzy_w_logach=["h1", "h2"]))

    assert zombie_account(con, snapshot_id, 0) == []


def test_brak_last_activity_bez_sladu_w_logach_jest_zombie(con: sqlite3.Connection) -> None:
    """Pole puste u 37 z 95 osób na CXLABS. Rozstrzyga druga przesłanka."""
    snapshot_id = zapisz(con, payload(uzytkownicy=[osoba("h1", last_activity=None)]))

    hipotezy = zombie_account(con, snapshot_id, 0)

    assert len(hipotezy) == 1
    assert hipotezy[0].fakty["podstawa"] == "brak last_activity ORAZ brak autora w logach okna"


def test_brak_last_activity_ale_autor_w_logach_to_nie_zombie(con: sqlite3.Connection) -> None:
    """`null` znaczy „nie wiem", nie „martwy" — i log to właśnie rozstrzyga."""
    snapshot_id = zapisz(
        con, payload(uzytkownicy=[osoba("h1", last_activity=None)], autorzy_w_logach=["h1"])
    )

    assert zombie_account(con, snapshot_id, 0) == []


def test_swieza_aktywnosc_nie_jest_zombie(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz(con, payload(uzytkownicy=[osoba("h1", last_activity=NIEDAWNO)]))

    assert zombie_account(con, snapshot_id, 0) == []


def test_konto_oczekujace_nalezy_do_innej_klasy(con: sqlite3.Connection) -> None:
    """Rubryka mówi wprost: PENDING to NEVER_ACTIVATED, nie ZOMBIE_ACCOUNT."""
    snapshot_id = zapisz(con, payload(uzytkownicy=[osoba("h1", status="PENDING")]))

    assert zombie_account(con, snapshot_id, 0) == []


def test_swiezo_dodane_konto_nie_jest_zombie(con: sqlite3.Connection) -> None:
    """Człowiek dodany w tym tygodniu nie ma historii i to nie jego wina."""
    snapshot_id = zapisz(
        con,
        payload(
            uzytkownicy=[osoba("h1", last_activity=None, became_active_at="2026-07-20T10:00:00Z")]
        ),
    )

    assert zombie_account(con, snapshot_id, 0) == []


def test_prog_czasowy_pochodzi_ze_snapshotu_nie_z_zegara(con: sqlite3.Connection) -> None:
    """Warunek odbioru: zamrożony snapshot daje ten sam wynik za rok.

    Ta sama osoba z tym samym `last_activity` jest zombie przy oknie
    otwierającym się później i nie jest przy oknie otwierającym się wcześniej.
    Gdyby detektor brał `now()`, wynik zależałby od dnia uruchomienia.
    """
    pozniej = zapisz(con, payload(uzytkownicy=[osoba("h1", last_activity="2026-02-01T00:00:00Z")]))
    wczesniej = zapisz(
        con,
        payload(
            uzytkownicy=[osoba("h1", last_activity="2026-02-01T00:00:00Z")],
            okno_od="2026-01-01T00:00:00+00:00",
        ),
    )

    assert len(zombie_account(con, pozniej, 0)) == 1
    assert zombie_account(con, wczesniej, 0) == []


def test_znaczniki_z_roznymi_ogonami_porownuja_sie(con: sqlite3.Connection) -> None:
    """Monday oddaje `...52Z`, collector zapisuje `...36.382683+00:00`.

    Porównanie leksykograficzne pełnych napisów zależałoby od tego, czy `Z`
    wypada w ASCII przed czy po kropce. Detektor obcina oba do sekundy, więc
    sekunda przed oknem jest przed oknem, a sekunda po nim — po.
    """
    okno = "2026-05-03T18:00:00.382683+00:00"
    sekunde_przed = zapisz(
        con,
        payload(uzytkownicy=[osoba("h1", last_activity="2026-05-03T17:59:59Z")], okno_od=okno),
    )
    sekunde_po = zapisz(
        con,
        payload(uzytkownicy=[osoba("h1", last_activity="2026-05-03T18:00:01Z")], okno_od=okno),
    )

    assert len(zombie_account(con, sekunde_przed, 0)) == 1, "przed oknem = zombie"
    assert zombie_account(con, sekunde_po, 0) == [], "po oknie = aktywny"


def test_fakty_pokrywaja_dowod_z_rubryki(con: sqlite3.Connection) -> None:
    """Hipoteza bez faktów z `dowod` jest hipotezą, której agent nie domknie."""
    snapshot_id = zapisz(con, payload(uzytkownicy=[osoba("h1")]))
    klasa = wczytaj_rubryke().po_id["ZOMBIE_ACCOUNT"]

    fakty = zombie_account(con, snapshot_id, 0)[0].fakty

    for pole in klasa.dowod:
        assert pole in fakty, f"`dowod` wymienia {pole}, a hipoteza go nie niesie"


# ── AUTOMATION_DEAD ──────────────────────────────────────────────────────


def test_automatyzacja_z_bledem_wzbudza(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz(
        con,
        payload(
            statystyki=[
                automatyzacja("a1", success=2, failure=1, powody={"Brak plików": 1}),
                automatyzacja("a2", success=50),
            ]
        ),
    )

    hipotezy = automation_dead(con, snapshot_id, budzet("AUTOMATION_DEAD"))

    assert [h.obiekt_id for h in hipotezy] == ["a1"]
    assert hipotezy[0].fakty["powody_bledow"] == {"Brak plików": 1}
    assert hipotezy[0].fakty["udzial_bledow"] == pytest.approx(0.3333, abs=1e-4)
    assert hipotezy[0].fakty["powyzej_progu_udzialu"] is True
    assert hipotezy[0].budzet_wywolan == 5


def test_wyczerpanie_limitu_wzbudza_bez_bledow(con: sqlite3.Connection) -> None:
    """`exhausted` to automatyzacja zatrzymana limitem — cicho przestała działać."""
    snapshot_id = zapisz(con, payload(statystyki=[automatyzacja("a1", success=10, exhausted=3)]))

    hipotezy = automation_dead(con, snapshot_id, 0)

    assert [h.obiekt_id for h in hipotezy] == ["a1"]
    assert hipotezy[0].fakty["exhausted"] == 3
    assert hipotezy[0].fakty["udzial_bledow"] == 0.0


def test_pojedynczy_blad_przy_tysiacach_jest_ponizej_progu(con: sqlite3.Connection) -> None:
    """Detektor NIE odrzuca sam — podaje ocenę, bo odrzucenie musi być uzasadnione.

    Rubryka wymienia „pojedynczy błąd przy tysiącach udanych uruchomień" jako
    warunek odrzucenia, ale odrzuca agent i zapisuje powód w `hipotezy_odrzucone`
    (D8). Cicha filtracja tutaj zabrałaby evalom z etapu 4 dane wejściowe.
    """
    snapshot_id = zapisz(con, payload(statystyki=[automatyzacja("a1", success=1000, failure=1)]))

    hipotezy = automation_dead(con, snapshot_id, 0)

    assert len(hipotezy) == 1, "hipoteza ma powstać"
    assert hipotezy[0].fakty["powyzej_progu_udzialu"] is False, "ale z oceną: to szum"


def test_automatyzacja_bez_bledow_nie_wzbudza(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz(con, payload(statystyki=[automatyzacja("a1", success=100)]))

    assert automation_dead(con, snapshot_id, 0) == []


def test_hipoteza_nie_zmyśla_tablicy(con: sqlite3.Connection) -> None:
    """API nie oddaje przypisania automatyzacji do tablicy (O1, O12).

    Gdyby `board_id` kiedykolwiek pojawiło się w faktach, znaczyłoby to, że ktoś
    je skądś zgadł — a finding wskazujący klientowi złą tablicę jest gorszy
    niż brak findingu.
    """
    snapshot_id = zapisz(con, payload(statystyki=[automatyzacja("a1", failure=1)]))

    fakty = automation_dead(con, snapshot_id, 0)[0].fakty

    assert "board_id" not in fakty


# ── runner: powtarzalność i uczciwość raportu ────────────────────────────


def test_ten_sam_snapshot_daje_ta_sama_liste(con: sqlite3.Connection) -> None:
    """WARUNEK ODBIORU 3.9: wynik powtarzalny co do kolejności."""
    snapshot_id = zapisz(
        con,
        payload(
            uzytkownicy=[osoba("h3"), osoba("h1"), osoba("h2")],
            statystyki=[automatyzacja("a2", failure=1), automatyzacja("a1", failure=1)],
        ),
    )

    pierwsze, raport_a = uruchom_detektory(con, snapshot_id)
    drugie, raport_b = uruchom_detektory(con, snapshot_id)

    assert [h.do_zapisu() for h in pierwsze] == [h.do_zapisu() for h in drugie]
    assert raport_a == raport_b

    # Kolejność jest ustalona, nie przypadkowa — posortowana po (klasa, obiekt).
    klucze = [(h.klasa_id, h.obiekt_id) for h in pierwsze]
    assert klucze == sorted(klucze)
    # I wejście było celowo podane w kolejności odwrotnej niż wynik.
    zombie = [h.obiekt_id for h in pierwsze if h.klasa_id == "ZOMBIE_ACCOUNT"]
    automaty = [h.obiekt_id for h in pierwsze if h.klasa_id == "AUTOMATION_DEAD"]
    assert zombie == ["h1", "h2", "h3"]
    assert automaty == ["a1", "a2"]


def test_raport_wymienia_klasy_bez_detektora(con: sqlite3.Connection) -> None:
    """Cicha pusta lista wyglądałaby jak „sprawdzone, nic nie ma"."""
    snapshot_id = zapisz(con, payload())

    _, raport = uruchom_detektory(con, snapshot_id)

    zbudowane = set(DETEKTORY)
    wszystkie = {k.id for k in wczytaj_rubryke().do_detekcji()}
    assert set(raport["klasy_bez_detektora"]) == wszystkie - zbudowane
    # 0.3 przy dodaniu UZYTKOWNIK_WYGASZONY, 0.4 przy doprecyzowaniu warunku
    # odrzucenia BOARD_GHOST (O34) — oba w etapie 4.
    assert raport["rubric_version"] == "0.4"


def test_budzet_bierze_sie_z_rubryki(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz(
        con, payload(uzytkownicy=[osoba("h1")], statystyki=[automatyzacja("a1", failure=1)])
    )

    hipotezy, raport = uruchom_detektory(con, snapshot_id)

    po_klasie = {h.klasa_id: h.budzet_wywolan for h in hipotezy}
    rubryka = wczytaj_rubryke()
    # ZOMBIE_ACCOUNT ma `rola_agenta: brak`, więc zero — agent go nie bada.
    assert po_klasie["ZOMBIE_ACCOUNT"] == 0
    assert po_klasie["AUTOMATION_DEAD"] == 5
    # Każda hipoteza niesie budżet swojej klasy, a raport ich sumę.
    for h in hipotezy:
        assert h.budzet_wywolan == rubryka.budzet(h.klasa_id)
    assert raport["budzet_zamowiony"] == sum(h.budzet_wywolan for h in hipotezy)


def test_brak_snapshotu_przerywa_jasno(con: sqlite3.Connection) -> None:
    with pytest.raises(DetektorError, match="nie istnieje"):
        zombie_account(con, 999, 0)


def test_zapytania_sa_parametryzowane() -> None:
    """Zastępuje regułę S608, wyciszoną dla tego modułu w pyproject.

    Reguła zgłasza każde SQL składane z f-stringa i nie umie odróżnić stałej
    modułowej od wejścia użytkownika. Ten test sprawdza to, co jest naprawdę
    groźne: czy w zapytaniach nie ma WSTAWIONEJ wartości. Wolno składać
    fragmenty (`{_TABLICE}`), nie wolno wstawiać danych.
    """
    import re

    from monday_audit import detektory as modul

    zrodlo = Path(modul.__file__).read_text(encoding="utf-8")
    dozwolone = {"_TABLICE", "_AKTYWNOSC"}

    # Tylko bloki f-stringowe, bo tylko one składają SQL. Zwykłe f-stringi
    # w komunikatach błędów nie mają z tym nic wspólnego.
    bloki = re.findall(r'f"""(.*?)"""', zrodlo, flags=re.DOTALL)
    assert bloki, "test straciłby sens, gdyby nie było już składanych zapytań"
    for blok in bloki:
        if "SELECT" not in blok:
            continue
        for wstawka in re.findall(r"\{([a-zA-Z_][a-zA-Z_0-9]*)\}", blok):
            assert wstawka in dozwolone, (
                f"do SQL-a wstawiono `{wstawka}` — wartości idą przez parametry "
                f"`:nazwa`, a składać wolno tylko {sorted(dozwolone)}"
            )

    # I żadna stała SQL nie została zepsuta komentarzem Pythona — SQLite zna
    # `--`, nie `#`. Ten błąd zdarzył się raz, przy dopisywaniu `noqa`.
    for nazwa in dir(modul):
        wartosc = getattr(modul, nazwa)
        if isinstance(wartosc, str) and "SELECT" in wartosc:
            assert "#" not in wartosc, f"{nazwa}: komentarz Pythona wewnątrz SQL-a"
            assert "{" not in wartosc, f"{nazwa}: nierozwiązana wstawka w SQL-u"


def test_kazda_klasa_z_rubryki_ma_detektor() -> None:
    """3.9 wymaga detektora dla każdej klasy poza `status: do_weryfikacji`."""
    z_rubryki = {k.id for k in wczytaj_rubryke().do_detekcji()}

    assert set(DETEKTORY) == z_rubryki, "rejestr rozjechał się z rubryką"


def test_hipoteza_jest_niemutowalna() -> None:
    h = Hipoteza(klasa_id="X", obiekt_id="1")

    with pytest.raises((AttributeError, TypeError)):
        h.obiekt_id = "2"  # type: ignore[misc]


def test_do_zapisu_jest_serializowalne(con: sqlite3.Connection) -> None:
    """Hipotezy idą dalej jako JSON (do agenta i do evali), więc muszą się zapisać."""
    snapshot_id = zapisz(con, payload(uzytkownicy=[osoba("h1")]))

    hipotezy, _ = uruchom_detektory(con, snapshot_id)

    assert json.loads(json.dumps([h.do_zapisu() for h in hipotezy], ensure_ascii=False))


# ── klasy tablicowe: dane, które faktycznie wzbudzają ────────────────────


def tablica(
    board_id: str,
    *,
    nazwa: str = "Tablica",
    typ: str = "board",
    state: str = "active",
    items_count: int = 10,
    created_at: str = "2025-01-01T00:00:00Z",
    updated_at: str = "2026-07-01T00:00:00Z",
    workspace_id: str | None = "ws1",
    owners: list[str] | None = None,
    subscribers: list[str] | None = None,
    kolumny: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "board_id": board_id,
        "nazwa": nazwa,
        "typ": typ,
        "state": state,
        "items_count": items_count,
        "created_at": created_at,
        "updated_at": updated_at,
        "workspace_id": workspace_id,
        "workspace_nazwa": "Workspace",
        "owners": owners if owners is not None else ["wl1"],
        "subscribers": subscribers if subscribers is not None else [],
        "kolumny": kolumny if kolumny is not None else [{"title": "Status", "type": "status"}],
        "grup": 1,
    }


def aktywnosc(
    board_id: str,
    *,
    wpisow: int = 5,
    najnowszy_at: str | None = NIEDAWNO,
    autorzy: list[str] | None = None,
    udzial: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "board_id": board_id,
        "wpisow": wpisow,
        "najnowszy_at": najnowszy_at,
        "autorzy": autorzy if autorzy is not None else [],
        "udzial_autorow": udzial or {},
        "po_klasie": {"operacyjne": wpisow},
        "kubelki_dni": {"0-7": wpisow},
    }


def pelny(
    *,
    uzytkownicy: list[dict[str, Any]] | None = None,
    tablice: list[dict[str, Any]] | None = None,
    aktywnosci: list[dict[str, Any]] | None = None,
    automatyzacje_sondy: dict[str, Any] | None = None,
    okno_od: str = OKNO_OD,
) -> dict[str, Any]:
    """Snapshot ze wszystkimi sekcjami, których dotykają detektory tablicowe."""
    return {
        "meta": {"okno_od": okno_od, "run_at": RUN_AT, "okno_dni": 90},
        "konto": {"konto": {"id": "27690228"}, "plan": {"tier": "enterprise", "max_users": None}},
        "uzytkownicy": {"uzytkownicy": uzytkownicy or []},
        "tablice": {"tablice": tablice or []},
        "aktywnosc": {"aktywnosc_tablic": aktywnosci or []},
        "automatyzacje": {
            "statystyki_automatyzacji": [],
            "uruchomienia": {"razem": 100},
            "podsumowanie": automatyzacje_sondy
            or {"tablic_sondowanych": 10, "tablic_bez_zdarzen": 1, "tablic_pominietych": 0},
        },
    }


def test_board_ghost_stoi_na_logu_nie_na_updated_at(con: sqlite3.Connection) -> None:
    """O18: `updated_at` był nowszy od logu w 94 na 105 tablic, do 40 dni.

    Tablica ma świeże `updated_at`, ale log milczy od stycznia. Sygnał na
    `updated_at` przegapiłby ją zupełnie.
    """
    snapshot_id = zapisz(
        con,
        pelny(
            tablice=[tablica("b1", updated_at="2026-07-30T00:00:00Z")],
            aktywnosci=[aktywnosc("b1", najnowszy_at=DAWNO)],
        ),
    )

    hipotezy = board_ghost(con, snapshot_id, budzet("BOARD_GHOST"))

    assert [h.obiekt_id for h in hipotezy] == ["b1"]
    assert hipotezy[0].fakty["najnowszy_at"] == DAWNO
    assert hipotezy[0].fakty["updated_at"] == "2026-07-30T00:00:00Z"


@pytest.mark.parametrize("typ", ["sub_items_board", "document"])
def test_board_ghost_pomija_podelementy_i_dokumenty(con: sqlite3.Connection, typ: str) -> None:
    """O14: `boards` zwraca 8 obiektów ze 105, które tablicą nie są."""
    snapshot_id = zapisz(
        con,
        pelny(
            tablice=[tablica("b1", typ=typ)],
            aktywnosci=[aktywnosc("b1", najnowszy_at=DAWNO)],
        ),
    )

    assert board_ghost(con, snapshot_id, 0) == []


def test_board_ghost_nie_orzeka_o_tablicy_bez_probki(con: sqlite3.Connection) -> None:
    """„Nie próbkowano" i „brak aktywności" to dwie różne rzeczy."""
    snapshot_id = zapisz(con, pelny(tablice=[tablica("b1")], aktywnosci=[]))

    assert board_ghost(con, snapshot_id, 0) == []


def test_board_no_owner_lapie_brak_i_nieaktywnych(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz(
        con,
        pelny(
            uzytkownicy=[osoba("wl_martwy", status="INACTIVE"), osoba("wl_zywy")],
            tablice=[
                tablica("bez", owners=[]),
                tablica("martwy", owners=["wl_martwy"]),
                tablica("ok", owners=["wl_zywy"]),
            ],
            aktywnosci=[aktywnosc("bez", udzial={"top": 9, "inny": 2})],
        ),
    )

    hipotezy = board_no_owner(con, snapshot_id, budzet("BOARD_NO_OWNER"))

    assert sorted(h.obiekt_id for h in hipotezy) == ["bez", "martwy"]
    bez = next(h for h in hipotezy if h.obiekt_id == "bez")
    assert bez.fakty["podstawa"] == "brak właścicieli"
    assert bez.fakty["top_kontrybutor_hash"] == "top"


def test_board_overcomplex_nie_udaje_ze_zna_martwe_kolumny(con: sqlite3.Connection) -> None:
    kolumny = [{"title": f"K{n}", "type": "text"} for n in range(16)]
    snapshot_id = zapisz(con, pelny(tablice=[tablica("b1", kolumny=kolumny)]))

    hipotezy = board_overcomplex(con, snapshot_id, budzet("BOARD_OVERCOMPLEX"))

    assert hipotezy[0].fakty["liczba_kolumn"] == 16
    assert hipotezy[0].fakty["kolumny_martwe"] is None, "wymaga próbki itemów (D5) — robota agenta"
    assert hipotezy[0].fakty["typy_kolumn"] == {"text": 16}


def test_duplicate_structure_liczy_jaccarda(con: sqlite3.Connection) -> None:
    """Tablica-wycinek NIE jest duplikatem — dlatego suma, nie mniejszy zbiór."""
    wspolne = [{"title": f"K{n}", "type": "text"} for n in range(8)]
    snapshot_id = zapisz(
        con,
        pelny(
            tablice=[
                tablica("a", kolumny=wspolne),
                tablica("b", kolumny=wspolne),
                tablica("wycinek", kolumny=wspolne[:2]),
            ]
        ),
    )

    hipotezy = duplicate_structure(con, snapshot_id, budzet("DUPLICATE_STRUCTURE"))

    assert [h.obiekt_id for h in hipotezy] == ["a+b"]
    assert hipotezy[0].fakty["nakladanie_kolumn"] == 1.0


def test_duplicate_structure_nie_lapie_roznych_workspace(con: sqlite3.Connection) -> None:
    wspolne = [{"title": f"K{n}", "type": "text"} for n in range(8)]
    snapshot_id = zapisz(
        con,
        pelny(
            tablice=[
                tablica("a", kolumny=wspolne, workspace_id="ws1"),
                tablica("b", kolumny=wspolne, workspace_id="ws2"),
            ]
        ),
    )

    assert duplicate_structure(con, snapshot_id, 0) == []


def test_process_bypass_wymaga_dwoch_nowych_tablic(con: sqlite3.Connection) -> None:
    """Koniunkcja z rubryki: jedna nowa tablica to nie obejście procesu."""
    wspolne = [{"title": f"K{n}", "type": "text"} for n in range(8)]
    zamilkla = "2026-03-01T00:00:00Z"
    snapshot_id = zapisz(
        con,
        pelny(
            tablice=[
                tablica("stara", kolumny=wspolne, created_at="2025-01-01T00:00:00Z"),
                tablica("nowa1", kolumny=wspolne, created_at="2026-03-10T00:00:00Z"),
            ],
            aktywnosci=[aktywnosc("stara", najnowszy_at=zamilkla)],
        ),
    )

    assert process_bypass(con, snapshot_id, 0) == [], "jedna nowa tablica nie wystarcza"


def test_process_bypass_lapie_ucieczke(con: sqlite3.Connection) -> None:
    wspolne = [{"title": f"K{n}", "type": "text"} for n in range(8)]
    zamilkla = "2026-03-01T00:00:00Z"
    snapshot_id = zapisz(
        con,
        pelny(
            tablice=[
                tablica("stara", kolumny=wspolne, created_at="2025-01-01T00:00:00Z"),
                tablica("nowa1", kolumny=wspolne, created_at="2026-03-10T00:00:00Z"),
                tablica("nowa2", kolumny=wspolne, created_at="2026-03-20T00:00:00Z"),
            ],
            aktywnosci=[aktywnosc("stara", najnowszy_at=zamilkla)],
        ),
    )

    hipotezy = process_bypass(con, snapshot_id, budzet("PROCESS_BYPASS"))

    assert [h.obiekt_id for h in hipotezy] == ["stara"]
    assert sorted(hipotezy[0].fakty["boardy_nowe"]) == ["nowa1", "nowa2"]
    assert hipotezy[0].fakty["data_zamilkniecia"] == zamilkla
    assert hipotezy[0].fakty["hipoteza_przyczyny"] is None, "przyczyna to robota agenta"


def test_process_bypass_pomija_tablice_powstale_poza_oknem(con: sqlite3.Connection) -> None:
    """±30 dni od zamilknięcia. Tablice z zupełnie innego okresu to nie ucieczka."""
    wspolne = [{"title": f"K{n}", "type": "text"} for n in range(8)]
    snapshot_id = zapisz(
        con,
        pelny(
            tablice=[
                tablica("stara", kolumny=wspolne, created_at="2025-01-01T00:00:00Z"),
                tablica("nowa1", kolumny=wspolne, created_at="2025-06-01T00:00:00Z"),
                tablica("nowa2", kolumny=wspolne, created_at="2025-06-02T00:00:00Z"),
            ],
            aktywnosci=[aktywnosc("stara", najnowszy_at="2026-03-01T00:00:00Z")],
        ),
    )

    assert process_bypass(con, snapshot_id, 0) == []


def test_guest_sprawl_jedna_hipoteza_na_konto(con: sqlite3.Connection) -> None:
    ludzie = [
        osoba("g1", kind="guest", last_activity="2025-06-01T00:00:00Z"),
        osoba("g2", kind="guest", last_activity=NIEDAWNO),
        osoba("m1", kind="member"),
    ]
    snapshot_id = zapisz(
        con, pelny(uzytkownicy=ludzie, tablice=[tablica("b1", subscribers=["g1"])])
    )

    hipotezy = guest_sprawl(con, snapshot_id, budzet("GUEST_SPRAWL"))

    assert len(hipotezy) == 1
    assert hipotezy[0].obiekt_id == "27690228"
    fakty = hipotezy[0].fakty
    assert fakty["liczba_guest"] == 2
    assert fakty["liczba_members"] == 1
    assert [g["user_hash"] for g in fakty["goscie_nieaktywni"]] == ["g1"]
    assert fakty["goscie_nieaktywni"][0]["tablice_dostepne"] == ["b1"]


def test_plan_mismatch_zapisuje_ktore_zrodlo_miejsc(con: sqlite3.Connection) -> None:
    """Miejsca KUPIONE i ZAJĘTE to nie to samo — agent musi wiedzieć, co liczył."""
    ludzie = [osoba(f"m{n}", kind="member") for n in range(10)]
    ludzie[0] = osoba("m0", kind="member", last_activity=NIEDAWNO)
    snapshot_id = zapisz(con, pelny(uzytkownicy=ludzie))

    hipotezy = plan_mismatch(con, snapshot_id, budzet("PLAN_MISMATCH"))

    assert len(hipotezy) == 1
    assert "ZAJĘTE" in hipotezy[0].fakty["podstawa_miejsc"]
    assert hipotezy[0].fakty["liczba_miejsc"] == 10
    assert hipotezy[0].fakty["aktywni_30d"] == 1


def test_automation_absent_liczy_udzial_i_odnotowuje_pominiete(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz(
        con,
        pelny(
            tablice=[
                tablica(
                    "b1",
                    kolumny=[
                        {"title": "Status", "type": "status"},
                        {"title": "Termin", "type": "date"},
                    ],
                )
            ],
            automatyzacje_sondy={
                "tablic_sondowanych": 10,
                "tablic_bez_zdarzen": 9,
                "tablic_pominietych": 95,
            },
        ),
    )

    hipotezy = automation_absent(con, snapshot_id, budzet("AUTOMATION_ABSENT"))

    assert hipotezy[0].fakty["udzial"] == 0.9
    assert hipotezy[0].fakty["tablic_pominietych_w_sondowaniu"] == 95
    assert [k["board_id"] for k in hipotezy[0].fakty["kandydaci"]] == ["b1"]


def test_engagement_drop_wymaga_minimalnej_grupy(con: sqlite3.Connection) -> None:
    """W zespole dwuosobowym jedna osoba na urlopie daje 50% spadku."""
    mala = [osoba(f"m{n}", kind="member") for n in range(2)]
    for czlowiek in mala:
        czlowiek["zespoly"] = ["Mały zespół"]
    snapshot_id = zapisz(con, pelny(uzytkownicy=mala))

    assert engagement_drop(con, snapshot_id, 0) == []


# ── UZYTKOWNIK_WYGASZONY ─────────────────────────────────────────────────
#
# Klasa dopełnia ZOMBIE_ACCOUNT: tamten bierze osoby NIEOBECNE w logach, ta
# osoby, które w logach są. Zbiory muszą pozostać rozłączne, i jeden z testów
# tego pilnuje.


def payload_z_osobami(
    per_uzytkownik: list[dict[str, Any]],
    uzytkownicy: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Snapshot z sekcją `aktywnosc.per_uzytkownik` — tą, którą czyta detektor."""
    dane = payload(uzytkownicy=uzytkownicy or [])
    dane["aktywnosc"]["per_uzytkownik"] = per_uzytkownik
    return dane


def wpis_aktywnosci(**nadpisz: Any) -> dict[str, Any]:
    baza: dict[str, Any] = {
        "user_hash": "h1",
        "akcji": 100,
        "tablic": 3,
        "boardy": ["1", "2", "3"],
        "kubelki_dni": {"31-60": 100},
        "po_event": {"create_pulse": 100},
        "aktywny_ostatnie_7d": False,
    }
    baza.update(nadpisz)
    return baza


def test_osoba_z_akcjami_tylko_w_starych_kubelkach_jest_wygaszona(
    con: sqlite3.Connection,
) -> None:
    snapshot_id = zapisz(
        con,
        payload_z_osobami([wpis_aktywnosci()], uzytkownicy=[osoba("h1", kind="member")]),
    )

    hipotezy = uzytkownik_wygaszony(con, snapshot_id, budzet("UZYTKOWNIK_WYGASZONY"))

    assert [h.obiekt_id for h in hipotezy] == ["h1"]
    assert hipotezy[0].fakty["udzial_swiezych"] == 0.0
    assert hipotezy[0].fakty["akcji_starych_31_90"] == 100


def test_agent_ai_nie_jest_osoba(con: sqlite3.Connection) -> None:
    """ZMIERZONE na #7: 3 z 8 autorów w logach to `personal_agent_member`.

    Bez tego filtra klasa zgłaszałaby „agent Quotation przestał pracować".
    Warunek sprawdzamy w DETEKTORZE, nie zostawiamy agentowi — inaczej płacilibyśmy
    za to samo rozumowanie w każdej sesji (D1).
    """
    snapshot_id = zapisz(
        con,
        payload_z_osobami(
            [wpis_aktywnosci()],
            uzytkownicy=[osoba("h1", kind="personal_agent_member")],
        ),
    )

    assert uzytkownik_wygaszony(con, snapshot_id, 6) == []


def test_autor_nieobecny_na_liscie_kont_jest_pomijany(con: sqlite3.Connection) -> None:
    """Konto usunięte albo spoza zakresu — nie ma o kim orzekać.

    ZMIERZONE: 2 z 8 autorów w logach #7 nie ma w `uzytkownicy`, w tym
    NAJAKTYWNIEJSZY (205 akcji na 7 tablicach). Zgłoszenie takiego autora dałoby
    finding o osobie, o której nie wiemy nawet, czy nadal ma konto.
    """
    snapshot_id = zapisz(con, payload_z_osobami([wpis_aktywnosci()], uzytkownicy=[]))

    assert uzytkownik_wygaszony(con, snapshot_id, 6) == []


def test_osoba_nadal_aktywna_nie_jest_wygaszona(con: sqlite3.Connection) -> None:
    """Próg 0,15 świeżych wobec starych.

    ZMIERZONE na #7: osoba z 29 akcjami świeżymi wobec 48 starych (udział 0,60)
    słusznie odpada — ona nadal pracuje, tylko mniej. To nie wygaszenie.
    """
    snapshot_id = zapisz(
        con,
        payload_z_osobami(
            [wpis_aktywnosci(kubelki_dni={"0-7": 29, "31-60": 48}, akcji=77)],
            uzytkownicy=[osoba("h1", kind="member")],
        ),
    )

    assert uzytkownik_wygaszony(con, snapshot_id, 6) == []


def test_ponizej_progu_akcji_to_szum(con: sqlite3.Connection) -> None:
    """Cztery kliknięcia sprzed dwóch miesięcy to nie porzucony proces."""
    snapshot_id = zapisz(
        con,
        payload_z_osobami(
            [wpis_aktywnosci(akcji=4, kubelki_dni={"31-60": 4})],
            uzytkownicy=[osoba("h1", kind="member")],
        ),
    )

    assert uzytkownik_wygaszony(con, snapshot_id, 6) == []


def test_brak_starej_aktywnosci_to_nie_wygaszenie(con: sqlite3.Connection) -> None:
    """Ktoś, kto zaczął w tym tygodniu, nie „przestał"."""
    snapshot_id = zapisz(
        con,
        payload_z_osobami(
            [wpis_aktywnosci(kubelki_dni={"0-7": 100})],
            uzytkownicy=[osoba("h1", kind="member")],
        ),
    )

    assert uzytkownik_wygaszony(con, snapshot_id, 6) == []


def test_dowod_pokrywa_pola_wymagane_przez_rubryke(con: sqlite3.Connection) -> None:
    """Bez tego finding nie przejdzie walidacji, a run kosztuje pieniądze."""
    snapshot_id = zapisz(
        con,
        payload_z_osobami([wpis_aktywnosci()], uzytkownicy=[osoba("h1", kind="member")]),
    )

    fakty = uzytkownik_wygaszony(con, snapshot_id, 6)[0].fakty
    wymagane = wczytaj_rubryke().po_id["UZYTKOWNIK_WYGASZONY"].dowod

    for pole in wymagane:
        assert pole.rstrip("[]") in fakty, f"brak pola dowodu: {pole}"


def test_zbior_wygaszonych_jest_rozlaczny_z_zombie(con: sqlite3.Connection) -> None:
    """Dwie klasy o osobach nie mogą orzekać o tej samej osobie.

    `zombie_account` ma w SQL `autorzy.user_hash IS NULL`, czyli bierze WYŁĄCZNIE
    osoby nieobecne w logach. `uzytkownik_wygaszony` czyta `per_uzytkownik`, które
    powstaje Z logów. Rozłączność jest konstrukcyjna — ten test pilnuje, żeby
    zmiana w którymkolwiek detektorze jej nie zepsuła.
    """
    dane = payload_z_osobami(
        [wpis_aktywnosci()],
        uzytkownicy=[osoba("h1", kind="member"), osoba("h2", kind="member")],
    )
    # `h1` jest autorem w logach, `h2` nie ma go tam wcale.
    dane["aktywnosc"]["aktywnosc_tablic"] = [{"board_id": "1", "autorzy": ["h1"]}]
    snapshot_id = zapisz(con, dane)

    wygaszeni = {h.obiekt_id for h in uzytkownik_wygaszony(con, snapshot_id, 6)}
    zombie = {h.obiekt_id for h in zombie_account(con, snapshot_id, 0)}

    assert wygaszeni == {"h1"}
    assert zombie == {"h2"}
    assert not (wygaszeni & zombie)


# ── BOARD_GHOST: warunki wzorca sprawdzane w DETEKTORZE ──────────────────
#
# ZMIERZONE (O34): warunek „rozpoznaj po nazwie" zostawiony agentowi dał
# powtarzalność 0,797 wobec progu ≥0,8 — 11 z 30 hipotez rozstrzygniętych
# różnie w dwóch runach TEGO SAMEGO snapshotu. Deterministyczne warunki nie
# mają prawa kosztować sesji modelu (D1).


def payload_z_tablicami(tablice: list[dict[str, Any]]) -> dict[str, Any]:
    """`payload()` nie ma sekcji `tablice` — detektory tablic potrzebują jej jawnie."""
    dane = payload()
    dane["tablice"] = {"tablice": tablice, "podsumowanie": {"razem": len(tablice)}}
    dane["aktywnosc"]["aktywnosc_tablic"] = [
        {"board_id": str(t["board_id"]), "wpisow": 0, "autorzy": []} for t in tablice
    ]
    return dane


def tablica_testowa(**nadpisz: Any) -> dict[str, Any]:
    baza: dict[str, Any] = {
        "board_id": "1",
        "nazwa": "Proces",
        "typ": "board",
        "state": "active",
        "items_count": 5,
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-04-01T10:00:00Z",
        "workspace_id": "9",
        "workspace_nazwa": "Produkcja",
        "kolumny": [],
    }
    baza.update(nadpisz)
    return baza


def test_nazwa_tablicy_ze_slowem_wzorca_nie_daje_hipotezy(con: sqlite3.Connection) -> None:
    """Lista słów jest zamknięta — „rozpoznaj po nazwie" bez listy to zgadywanie."""
    snapshot_id = zapisz(
        con, payload_z_tablicami([tablica_testowa(nazwa="Szablon procesu sprzedaży")])
    )

    assert board_ghost(con, snapshot_id, 4) == []


def test_workspace_demo_wymaga_drugiego_sygnalu(con: sqlite3.Connection) -> None:
    """Sama nazwa workspace'u NIE wystarcza.

    Klient może trzymać produkcję w workspace nazwanym „Demo" po nieudanym
    pilocie — wtedy `updated_at` jest późniejszy od `created_at` i tablica JEST
    kandydatem. Ta sama zasada co przy `DUPLICATE_STRUCTURE` po O33.
    """
    snapshot_id = zapisz(
        con,
        payload_z_tablicami(
            [
                # utworzona i nigdy nieruszona → odrzucenie
                tablica_testowa(
                    board_id="1",
                    workspace_nazwa="CRM_PL_Demo",
                    created_at="2026-03-18T11:13:28Z",
                    updated_at="2026-03-18T11:13:30Z",
                ),
                # ruszana po utworzeniu → ZOSTAJE, mimo workspace „Demo"
                tablica_testowa(
                    board_id="2",
                    workspace_nazwa="CRM_PL_Demo",
                    created_at="2026-01-15T10:00:00Z",
                    updated_at="2026-04-02T09:00:00Z",
                ),
            ]
        ),
    )

    assert [h.obiekt_id for h in board_ghost(con, snapshot_id, 4)] == ["2"]


def test_brak_daty_nie_znaczy_nieruszona(con: sqlite3.Connection) -> None:
    """„Nie wiem" nie może udawać „nie ruszano".

    Tablica bez `updated_at` w workspace „Sandbox" ZOSTAJE kandydatem — inaczej
    brak danych usprawiedliwiałby odrzucenie, a to najcichszy rodzaj usterki.
    """
    snapshot_id = zapisz(
        con,
        payload_z_tablicami([tablica_testowa(workspace_nazwa="Sandbox", updated_at=None)]),
    )

    assert [h.obiekt_id for h in board_ghost(con, snapshot_id, 4)] == ["1"]


def test_nazwa_workspace_jest_w_faktach_hipotezy(con: sqlite3.Connection) -> None:
    """Bez tego agent bierze ją z inwentarza i stosuje warunek niekonsekwentnie.

    To była bezpośrednia przyczyna rozjazdu powtarzalności (O34): wszystkie
    odrzucenia powoływały się na nazwę workspace'u, której hipoteza NIE ZAWIERAŁA.
    """
    snapshot_id = zapisz(con, payload_z_tablicami([tablica_testowa(workspace_nazwa="Operacje")]))

    fakty = board_ghost(con, snapshot_id, 4)[0].fakty
    assert fakty["workspace_nazwa"] == "Operacje"
