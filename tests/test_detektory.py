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
    automation_dead,
    uruchom_detektory,
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

    pierwsze, _ = uruchom_detektory(con, snapshot_id)
    drugie, _ = uruchom_detektory(con, snapshot_id)

    assert [h.do_zapisu() for h in pierwsze] == [h.do_zapisu() for h in drugie]
    # I kolejność jest ustalona, nie przypadkowa.
    assert [(h.klasa_id, h.obiekt_id) for h in pierwsze] == [
        ("AUTOMATION_DEAD", "a1"),
        ("AUTOMATION_DEAD", "a2"),
        ("ZOMBIE_ACCOUNT", "h1"),
        ("ZOMBIE_ACCOUNT", "h2"),
        ("ZOMBIE_ACCOUNT", "h3"),
    ]


def test_raport_wymienia_klasy_bez_detektora(con: sqlite3.Connection) -> None:
    """Cicha pusta lista wyglądałaby jak „sprawdzone, nic nie ma"."""
    snapshot_id = zapisz(con, payload())

    _, raport = uruchom_detektory(con, snapshot_id)

    zbudowane = set(DETEKTORY)
    wszystkie = {k.id for k in wczytaj_rubryke().do_detekcji()}
    assert set(raport["klasy_bez_detektora"]) == wszystkie - zbudowane
    assert raport["rubric_version"] == "0.2"


def test_budzet_bierze_sie_z_rubryki(con: sqlite3.Connection) -> None:
    snapshot_id = zapisz(
        con, payload(uzytkownicy=[osoba("h1")], statystyki=[automatyzacja("a1", failure=1)])
    )

    hipotezy, raport = uruchom_detektory(con, snapshot_id)

    po_klasie = {h.klasa_id: h.budzet_wywolan for h in hipotezy}
    # ZOMBIE_ACCOUNT ma `rola_agenta: brak`, więc zero — agent go nie bada.
    assert po_klasie["ZOMBIE_ACCOUNT"] == 0
    assert po_klasie["AUTOMATION_DEAD"] == 5
    assert raport["budzet_zamowiony"] == 5


def test_brak_snapshotu_przerywa_jasno(con: sqlite3.Connection) -> None:
    with pytest.raises(DetektorError, match="nie istnieje"):
        zombie_account(con, 999, 0)


def test_hipoteza_jest_niemutowalna() -> None:
    h = Hipoteza(klasa_id="X", obiekt_id="1")

    with pytest.raises((AttributeError, TypeError)):
        h.obiekt_id = "2"  # type: ignore[misc]


def test_do_zapisu_jest_serializowalne(con: sqlite3.Connection) -> None:
    """Hipotezy idą dalej jako JSON (do agenta i do evali), więc muszą się zapisać."""
    snapshot_id = zapisz(con, payload(uzytkownicy=[osoba("h1")]))

    hipotezy, _ = uruchom_detektory(con, snapshot_id)

    assert json.loads(json.dumps([h.do_zapisu() for h in hipotezy], ensure_ascii=False))
