"""Itemy i agregaty — reguła lejka, budżet i rozkłady (plan, faza 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from monday_audit.itemy import (
    LIMIT_ITEMOW,
    Lejek,
    policz_rozklad,
    rozpoznaj_lejek,
    zaplanuj_pobranie,
)

TERAZ = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _kolumna(identyfikator: str, typ: str = "status") -> dict[str, Any]:
    return {"id": identyfikator, "title": identyfikator, "type": typ}


# ── rozpoznanie lejka (O46) ──────────────────────────────────────────────


def test_kolumna_kanoniczna_daje_stopien_pierwszy() -> None:
    """ZMIERZONE: `Leads e-commerce` ma 14 kolumn `status`, a lejek niesie jedna."""
    kolumny = [_kolumna(f"color_mks{i}") for i in range(13)] + [_kolumna("lead_status")]

    lejek = rozpoznaj_lejek(kolumny, grup=2)

    assert lejek.stopien == 1
    assert lejek.kolumna == "lead_status"


def test_deal_stage_tez_jest_kanoniczne() -> None:
    lejek = rozpoznaj_lejek([_kolumna("deal_stage")], grup=5)

    assert lejek.stopien == 1
    assert lejek.kolumna == "deal_stage"


def test_goly_status_nie_jest_lejkiem() -> None:
    """Pułapka z O46: `status` to domyślne id pierwszej kolumny statusu na
    DOWOLNEJ tablicy — wyszło na podelementach w crm i w core. Potraktowane jak
    `lead_status` robiłoby lejek sprzedaży z czegokolwiek."""
    lejek = rozpoznaj_lejek([_kolumna("status")], grup=4)

    assert lejek.stopien == 2
    assert lejek.kolumna is None


def test_brak_kolumny_i_jedna_grupa_to_nierozpoznany_lejek() -> None:
    lejek = rozpoznaj_lejek([_kolumna("text_mkx1", "text")], grup=1)

    assert lejek.stopien == 3
    assert "nie rozpoznano" in lejek.opis


# ── budżet liczony przed pobieraniem ─────────────────────────────────────


def test_plan_miesci_sie_w_budzecie() -> None:
    plan = zaplanuj_pobranie([("a", 10_000), ("b", 300), ("c", 50)], budzet=10)

    # 50 itemów = 1 wywołanie, 300 = 3; na 10 000 (100 wywołań) nie ma miejsca.
    assert [b for b, _ in plan.do_pobrania] == ["c", "b"]
    assert plan.wywolan_szacunek == 4
    assert [b for b, _ in plan.pominiete] == ["a"]


def test_male_tablice_maja_pierwszenstwo() -> None:
    """Przy ciasnym budżecie dwadzieścia małych tablic mówi o koncie więcej niż
    jedna wielka — a wielka i tak ma policzone itemy z `items_count`."""
    plan = zaplanuj_pobranie([("duza", 5_000), ("mala1", 10), ("mala2", 10)], budzet=2)

    assert sorted(b for b, _ in plan.do_pobrania) == ["mala1", "mala2"]
    assert plan.mimo_budzetu_pominieto == 1


def test_pusta_tablica_kosztuje_jedno_wywolanie() -> None:
    """Zero itemów to nadal jedno zapytanie — budżet musi to widzieć."""
    plan = zaplanuj_pobranie([("pusta", 0)], budzet=5)

    assert plan.wywolan_szacunek == 1


def test_koszt_liczy_sie_po_sufit() -> None:
    plan = zaplanuj_pobranie([("a", LIMIT_ITEMOW + 1)], budzet=99)

    assert plan.wywolan_szacunek == 2


# ── rozkłady ─────────────────────────────────────────────────────────────


def _item(*, status: str | None = None, grupa: str = "G", dni_temu: int = 1) -> dict[str, Any]:
    powstal = (TERAZ - timedelta(days=dni_temu)).isoformat().replace("+00:00", "Z")
    return {
        "id": "1",
        "created_at": powstal,
        "updated_at": powstal,
        "group": {"id": "g", "title": grupa},
        "column_values": [{"id": "lead_status", "text": status}] if status is not None else [],
    }


def test_rozklad_po_etapach_gdy_lejek_znany() -> None:
    lejek = Lejek(stopien=1, kolumna="lead_status", opis="")
    itemy = [_item(status="Qualified"), _item(status="Qualified"), _item(status="New")]

    rozklad, w_oknie = policz_rozklad(itemy, lejek, teraz=TERAZ, okno_dni=90)

    assert rozklad == {"Qualified": 2, "New": 1}
    assert w_oknie == 3


def test_pusty_status_jest_osobna_kategoria() -> None:
    """Lead bez etapu leży poza lejkiem i ma być widoczny, a nie doliczony
    do pierwszego lepszego etapu."""
    lejek = Lejek(stopien=1, kolumna="lead_status", opis="")

    rozklad, _ = policz_rozklad(
        [_item(status=""), _item(status="New")], lejek, teraz=TERAZ, okno_dni=90
    )

    assert rozklad["(bez etapu)"] == 1


def test_stopien_drugi_liczy_grupy_a_nie_etapy() -> None:
    """Na `👤 Leads` grup jest 27 i są to źródła kampanii, nie etapy — dlatego
    stopień drugi raportuje GRUPY i nazywa je grupami."""
    lejek = Lejek(stopien=2, kolumna=None, opis="")
    itemy = [_item(grupa="Clay import"), _item(grupa="Clay import"), _item(grupa="Events")]

    rozklad, _ = policz_rozklad(itemy, lejek, teraz=TERAZ, okno_dni=90)

    assert rozklad == {"Clay import": 2, "Events": 1}


def test_okno_odcina_stare_itemy() -> None:
    lejek = Lejek(stopien=2, kolumna=None, opis="")
    itemy = [_item(dni_temu=5), _item(dni_temu=200), _item(dni_temu=89)]

    _, w_oknie = policz_rozklad(itemy, lejek, teraz=TERAZ, okno_dni=90)

    assert w_oknie == 2


def test_przyrost_dzienny_to_iloraz_nie_pomiar() -> None:
    from monday_audit.itemy import AgregatTablicy

    agregat = AgregatTablicy(
        board_id="1",
        nazwa="Leads",
        produkt="crm",
        itemow=900,
        lejek=Lejek(stopien=1, kolumna="lead_status", opis=""),
        rozklad={},
        powstalo_w_oknie=180,
        okno_dni=90,
        pobranych=900,
    )

    assert agregat.przyrost_dzienny == 2.0
