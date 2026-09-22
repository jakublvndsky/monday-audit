"""Przegląd tablic — agregaty i przypisanie uruchomień (plan, faza 2b)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from monday_audit.przeglad_tablic import (
    kubelek_aktywnosci,
    na_datetime,
    pobierz_zdarzenia,
    policz_agregaty,
    zbierz_uruchomienia,
)

TERAZ = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _tablica(**nadpisz: Any) -> dict[str, Any]:
    baza: dict[str, Any] = {
        "id": "1",
        "name": "Tablica",
        "type": "board",
        "board_kind": "public",
        "updated_at": "2026-09-20T10:00:00Z",
        "workspace": {"id": "7", "name": "WS"},
        "owners": [],
        "subscribers": [],
    }
    baza.update(nadpisz)
    return baza


# ── znaczniki czasu ──────────────────────────────────────────────────────


def test_rowne_momenty_w_roznych_strefach_sa_rowne() -> None:
    """Sedno: leksykograficznie `10:41+02:00` > `08:41Z`, a to ten sam moment."""
    z_zulu = na_datetime("2026-09-22T08:41:25.933Z")
    z_offsetem = na_datetime("2026-09-22T10:41:25.933+02:00")

    assert z_zulu == z_offsetem
    # A tak wypadłoby porównanie napisów — ten sam moment, a „większy" jest drugi.
    napis_z_offsetem = "2026-09-22T10:41:25.933+02:00"
    napis_z_zulu = "2026-09-22T08:41:25.933Z"
    assert napis_z_offsetem > napis_z_zulu


def test_niesparsowalny_znacznik_nie_wygrywa() -> None:
    """Wolimy starszą datę, o której coś wiemy, niż nowszą-śmieć."""
    wynik = zbierz_uruchomienia(
        [
            {
                "hostInstanceId": "5",
                "eventState": "success",
                "triggerStartedAt": "2026-09-01T00:00:00Z",
            },
            {"hostInstanceId": "5", "eventState": "success", "triggerStartedAt": "wczoraj"},
        ]
    )

    assert wynik["5"]["ostatnie"] == "2026-09-01T00:00:00Z"
    assert wynik["5"]["uruchomien"] == 2


def test_ostatnie_uruchomienie_po_momencie_nie_po_napisie() -> None:
    """Zdarzenie z offsetem `+02:00` jest PÓŹNIEJSZE mimo mniejszego napisu."""
    wynik = zbierz_uruchomienia(
        [
            {
                "hostInstanceId": "5",
                "eventState": "success",
                "triggerStartedAt": "2026-09-22T09:00:00+02:00",
            },
            {
                "hostInstanceId": "5",
                "eventState": "success",
                "triggerStartedAt": "2026-09-22T08:00:00Z",
            },
        ]
    )

    # 09:00+02:00 to 07:00Z, czyli WCZEŚNIEJ niż 08:00Z — mimo że napis większy.
    assert wynik["5"]["ostatnie"] == "2026-09-22T08:00:00Z"


def test_kubelki_aktywnosci() -> None:
    assert kubelek_aktywnosci("2026-09-20T10:00:00Z", teraz=TERAZ) == "30d"
    assert kubelek_aktywnosci("2026-08-01T10:00:00Z", teraz=TERAZ) == "90d"
    assert kubelek_aktywnosci("2026-01-01T10:00:00Z", teraz=TERAZ) == "365d"
    assert kubelek_aktywnosci("2020-01-01T10:00:00Z", teraz=TERAZ) == "starsze"
    assert kubelek_aktywnosci(None, teraz=TERAZ) == "nieznane"
    assert kubelek_aktywnosci("nie-data", teraz=TERAZ) == "nieznane"


# ── uruchomienia ─────────────────────────────────────────────────────────


def test_zdarzenia_bez_tablicy_odpadaja() -> None:
    """97% zdarzeń bez filtra `hostType` nie ma przypisania — nie wolno ich zgadywać."""
    wynik = zbierz_uruchomienia(
        [
            {"hostInstanceId": None, "eventState": "success"},
            {"hostInstanceId": "5", "eventState": "success"},
        ]
    )

    assert list(wynik) == ["5"]


def test_bledy_liczone_osobno() -> None:
    wynik = zbierz_uruchomienia(
        [
            {"hostInstanceId": "5", "eventState": "success"},
            {"hostInstanceId": "5", "eventState": "failure"},
            {"hostInstanceId": "5", "eventState": "exhausted"},
        ]
    )

    assert wynik["5"]["uruchomien"] == 3
    assert wynik["5"]["bledow"] == 2


# ── agregaty ─────────────────────────────────────────────────────────────


def test_srednia_i_mediana_stoja_obok_siebie() -> None:
    """Jedna tablica ogólnofirmowa zawyża średnią i sugeruje zaangażowanie,
    którego na pozostałych nie ma — dlatego mediana jest w wyniku, nie zamiast."""
    tablice = [
        _tablica(id="1", subscribers=[{"id": str(i)} for i in range(100)]),
        _tablica(id="2", subscribers=[{"id": "a"}]),
        _tablica(id="3", subscribers=[{"id": "b"}]),
    ]

    wynik = policz_agregaty(tablice, goscie=set(), teraz=TERAZ)

    assert wynik["userow_srednio"] == 34.0
    assert wynik["userow_mediana"] == 1.0


def test_goscie_liczeni_po_rodzaju_konta() -> None:
    tablice = [
        _tablica(id="1", subscribers=[{"id": "g1"}, {"id": "u1"}]),
        _tablica(id="2", subscribers=[{"id": "u2"}]),
    ]

    wynik = policz_agregaty(tablice, goscie={"g1"}, teraz=TERAZ)

    assert wynik["gosci_na_tablicach"] == 1
    assert wynik["tablic_z_goscmi"] == 1


def test_rozbicie_po_rodzaju_i_typie() -> None:
    tablice = [
        _tablica(id="1", board_kind="public", type="board"),
        _tablica(id="2", board_kind="private", type="board"),
        _tablica(id="3", board_kind="public", type="document"),
    ]

    wynik = policz_agregaty(tablice, goscie=set(), teraz=TERAZ)

    # `po_typie` obejmuje WSZYSTKO, co zwróciło API — po to, żeby było widać,
    # co odpadło. `po_rodzaju` i reszta agregatów liczy już tylko `type: board`,
    # bo średnia userów po kontenerach podelementów nie opisuje niczego.
    assert wynik["po_typie"] == {"board": 2, "document": 1}
    assert wynik["po_rodzaju"] == {"private": 1, "public": 1}
    assert wynik["tablic"] == 2


class _KlientZdarzen:
    """Atrapa oddająca zdarzenia per przedział. `pelne` to daty, które dają
    pełne 200 — czyli przedziały urwane przez limit odpowiedzi."""

    def __init__(self, pelne: set[str] | None = None) -> None:
        self.pelne = pelne or set()
        self.przedzialy: list[tuple[str, str]] = []

    async def query(
        self,
        gql: str,
        variables: dict[str, Any] | None = None,
        *,
        etykieta: str | None = None,
        wersja_api: str | None = None,
    ) -> dict[str, Any]:
        zakres = (variables or {})["f"]["dateRange"]
        od, do = zakres["startDate"], zakres["endDate"]
        self.przedzialy.append((od, do))
        ile = 200 if od in self.pelne else 2
        return {
            "trigger_events": {
                "triggerEvents": [
                    {
                        "triggerUuid": f"{od}-{i}",
                        "hostInstanceId": "5",
                        "eventState": "success",
                        "triggerStartedAt": f"{od}T10:00:00Z",
                    }
                    for i in range(ile)
                ]
            }
        }


@pytest.mark.asyncio
async def test_okno_kroi_sie_na_tygodnie_bo_stronicowania_nie_ma() -> None:
    """ZMIERZONE: `nextPageOffset > 0` daje po stronie monday błąd serwera,
    a strona urywa się na 200. Więc zawężamy okno zamiast stronicować."""
    klient = _KlientZdarzen()

    zdarzenia, zastrzezenia = await pobierz_zdarzenia(klient, okno_dni=28)  # type: ignore[arg-type]

    assert len(klient.przedzialy) == 4  # cztery tygodnie
    assert len(zdarzenia) == 8
    assert zastrzezenia == []


@pytest.mark.asyncio
async def test_pelny_tydzien_schodzi_na_dni_i_melduje_urwanie() -> None:
    """Tydzień z limitem 200 dzielimy na dni; dzień, który dalej jest pełny,
    ma trafić do zastrzeżeń Z DATĄ, a nie urwać się po cichu."""
    teraz = datetime.now(UTC).date()
    tydzien_od = (teraz - timedelta(days=7)).isoformat()
    klient = _KlientZdarzen(
        pelne={tydzien_od, *[(teraz - timedelta(days=d)).isoformat() for d in range(1, 8)]}
    )

    _, zastrzezenia = await pobierz_zdarzenia(klient, okno_dni=7)  # type: ignore[arg-type]

    assert zastrzezenia, "urwany dzień musi być zgłoszony"
    assert "URWANY" in zastrzezenia[0]


def test_pusta_lista_tablic_nie_wywraca_sredniej() -> None:
    wynik = policz_agregaty([], goscie=set(), teraz=TERAZ)

    assert wynik["userow_srednio"] is None
    assert wynik["userow_mediana"] is None
