"""Testy szybkiego podglądu konta — workspace'y i tablice przed zbieraniem.

Ten moduł istnieje po to, żeby klient nie czekał trzech minut na możliwość
wskazania workspace'u. Testy pilnują trzech rzeczy, których pomyłka jest cicha:

- **Podgląd nie może udawać, że wie o ciszy.** Bez logów nie wiemy, czy tablica
  zamilkła — a brak flagi czyta się jak „aktywna". Dlatego `TablicaDoWyboru`
  nie ma pola `wpisow` i nie wystawia flag `cisza_90_dni` ani `nieprobkowana`.
- **Obcinka listy musi być widoczna.** Konto ma 500+ tablic; milczące urwanie
  na czwartej stronie czytałoby się jak „to wszystkie tablice".
- **Podgląd nie zakłada runu.** Puste wiersze w `runy` już raz zepsuły listę
  audytów w panelu.

Zapytań do monday tu nie ma: `MondayClient` podmieniamy atrapą i sprawdzamy,
co moduł robi z odpowiedzią. Czas i complexity są zmierzone osobno, na żywym
koncie — tego test jednostkowy nie zweryfikuje.
"""

from __future__ import annotations

from typing import Any

import pytest

from monday_audit.podglad_zakresu import (
    LIMIT_PODGLADU,
    MAKS_STRON_PODGLADU,
    PodgladError,
    RejestrPodgladu,
    oszacuj_zgrubnie,
    pobierz_tablice,
    pobierz_workspace,
    podglad_do_json,
    zbuduj_podglad,
)
from monday_audit.wybor_zakresu import FLAGA_NIEUZYWANA, FLAGA_RAPORTOWA

ZALOZONA = "2026-01-01T10:00:00Z"
RUSZONA_PO_TYGODNIU = "2026-01-08T10:00:00Z"
RUSZONA_PO_GODZINIE = "2026-01-01T11:00:00Z"


def tablica(
    board_id: str,
    *,
    typ: str = "board",
    kolumny: list[dict[str, str]] | None = None,
    items: int = 10,
    utworzona: str = ZALOZONA,
    ruszona: str = RUSZONA_PO_TYGODNIU,
    nazwa: str | None = None,
) -> dict[str, Any]:
    return {
        "id": board_id,
        "name": nazwa or f"Tablica {board_id}",
        "type": typ,
        "state": "active",
        "items_count": items,
        "created_at": utworzona,
        "updated_at": ruszona,
        "workspace": {"id": "w1", "name": "Operacje"},
        "columns": kolumny if kolumny is not None else [{"id": "n", "type": "name"}],
    }


class AtrapaKlienta:
    """`MondayClient` bez sieci. Zapisuje, o co pytano — to jest tu istotne."""

    def __init__(
        self,
        *,
        workspaces: list[dict[str, Any]] | None = None,
        strony: list[list[dict[str, Any]]] | None = None,
        wyjatek: Exception | None = None,
    ) -> None:
        self._workspaces = workspaces if workspaces is not None else []
        self._strony = strony or [[]]
        self._wyjatek = wyjatek
        self.zapytania: list[tuple[str, dict[str, Any]]] = []

    async def query(
        self, gql: str, variables: dict[str, Any] | None = None, **_: Any
    ) -> dict[str, Any]:
        if self._wyjatek:
            raise self._wyjatek
        zmienne = dict(variables or {})
        self.zapytania.append(("workspaces" if "workspaces" in gql else "boards", zmienne))
        if "workspaces" in gql:
            return {"workspaces": self._workspaces}
        numer = int(zmienne.get("p", 1))
        partia = self._strony[numer - 1] if numer <= len(self._strony) else []
        return {"boards": partia}


# --- workspace'y -----------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_y_z_odpowiedzi_api() -> None:
    klient = AtrapaKlienta(workspaces=[{"id": "1", "name": "Operacje"}, {"id": "2", "name": "HR"}])

    wynik = await pobierz_workspace(klient)  # type: ignore[arg-type]

    assert [(w.workspace_id, w.nazwa) for w in wynik] == [("1", "Operacje"), ("2", "HR")]


@pytest.mark.asyncio
async def test_workspace_bez_nazwy_dostaje_identyfikator() -> None:
    """Pusta nazwa na liście wyboru byłaby pozycją, której nie da się wskazać."""
    klient = AtrapaKlienta(workspaces=[{"id": "7", "name": None}])

    wynik = await pobierz_workspace(klient)  # type: ignore[arg-type]

    assert wynik[0].nazwa == "7"


@pytest.mark.asyncio
async def test_wpis_bez_id_jest_pomijany() -> None:
    klient = AtrapaKlienta(workspaces=[{"name": "bez id"}, {"id": "3", "name": "ok"}])

    wynik = await pobierz_workspace(klient)  # type: ignore[arg-type]

    assert [w.workspace_id for w in wynik] == ["3"]


# --- tablice ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_tylko_prawdziwe_tablice_wchodza_na_liste() -> None:
    """ZMIERZONE: ze 124 obiektów workspace'u 5610281 tylko 59 to `board`."""
    klient = AtrapaKlienta(
        strony=[
            [
                tablica("1"),
                tablica("2", typ="sub_items_board"),
                tablica("3", typ="custom_object"),
                tablica("4", typ="document"),
            ]
        ]
    )

    tablice, pominietych, urwano = await pobierz_tablice(klient, "w1")  # type: ignore[arg-type]

    assert [t.board_id for t in tablice] == ["1"]
    assert pominietych == 3
    assert not urwano


@pytest.mark.asyncio
async def test_zapytanie_idzie_z_filtrem_workspace() -> None:
    """Bez filtra pobralibyśmy 500+ tablic konta — ZMIERZONE 17 s i 2,5 mln complexity."""
    klient = AtrapaKlienta(strony=[[tablica("1")]])

    await pobierz_tablice(klient, "moj-ws")  # type: ignore[arg-type]

    _, zmienne = klient.zapytania[0]
    assert zmienne["ws"] == ["moj-ws"]
    assert zmienne["limit"] == LIMIT_PODGLADU


@pytest.mark.asyncio
async def test_paginacja_zbiera_wszystkie_strony() -> None:
    pelna = [tablica(str(i)) for i in range(LIMIT_PODGLADU)]
    klient = AtrapaKlienta(strony=[pelna, [tablica("ostatnia")]])

    tablice, _, urwano = await pobierz_tablice(klient, "w1")  # type: ignore[arg-type]

    assert len(tablice) == LIMIT_PODGLADU + 1
    assert not urwano


@pytest.mark.asyncio
async def test_obcinka_listy_jest_zglaszana() -> None:
    """Milczące urwanie czytałoby się jak „to wszystkie tablice tego workspace'u"."""
    pelna = [tablica(str(i)) for i in range(LIMIT_PODGLADU)]
    klient = AtrapaKlienta(strony=[pelna] * (MAKS_STRON_PODGLADU + 2))

    tablice, _, urwano = await pobierz_tablice(klient, "w1")  # type: ignore[arg-type]

    assert urwano
    assert len(tablice) == LIMIT_PODGLADU * MAKS_STRON_PODGLADU


# --- flagi -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_flaga_nieuzywana_z_samych_dat() -> None:
    klient = AtrapaKlienta(
        strony=[
            [
                tablica("swieza", utworzona=ZALOZONA, ruszona=RUSZONA_PO_GODZINIE),
                tablica("zywa", utworzona=ZALOZONA, ruszona=RUSZONA_PO_TYGODNIU),
            ]
        ]
    )

    tablice, _, _ = await pobierz_tablice(klient, "w1")  # type: ignore[arg-type]
    flagi = {t.board_id: t.flagi for t in tablice}

    assert flagi["swieza"] == (FLAGA_NIEUZYWANA,)
    assert flagi["zywa"] == ()


@pytest.mark.asyncio
async def test_flaga_raportowa_z_typow_kolumn() -> None:
    """`columns { id type }` kosztuje +0,46 s i daje tę flagę — warte tego."""
    auto = [{"id": str(i), "type": "formula"} for i in range(6)]
    reczne = [{"id": str(i), "type": "text"} for i in range(4)]
    klient = AtrapaKlienta(
        strony=[[tablica("raportowa", kolumny=auto + reczne), tablica("zwykla", kolumny=reczne)]]
    )

    tablice, _, _ = await pobierz_tablice(klient, "w1")  # type: ignore[arg-type]
    po_id = {t.board_id: t for t in tablice}

    assert FLAGA_RAPORTOWA in po_id["raportowa"].flagi
    assert po_id["raportowa"].kolumn_automatycznych == 6
    assert FLAGA_RAPORTOWA not in po_id["zwykla"].flagi


@pytest.mark.asyncio
async def test_podglad_nie_orzeka_o_ciszy() -> None:
    """Bez logów nie wiemy, czy tablica zamilkła — i nie wolno tego udawać.

    `cisza_90_dni` wymaga dziennika (47 s), którego ten podgląd nie pobiera.
    Wystawienie tej flagi tutaj byłoby stwierdzeniem bez danych; wystawienie
    pola `wpisow: null` sugerowałoby frontowi, że dane kiedyś dojdą.
    """
    klient = AtrapaKlienta(strony=[[tablica("1")]])

    tablice, _, _ = await pobierz_tablice(klient, "w1")  # type: ignore[arg-type]

    assert not hasattr(tablice[0], "wpisow")
    assert all(f in {FLAGA_NIEUZYWANA, FLAGA_RAPORTOWA} for f in tablice[0].flagi)


@pytest.mark.asyncio
async def test_zepsute_daty_nie_wywracaja_podgladu() -> None:
    klient = AtrapaKlienta(strony=[[tablica("1", utworzona="wczoraj", ruszona="dzis")]])

    tablice, _, _ = await pobierz_tablice(klient, "w1")  # type: ignore[arg-type]

    assert FLAGA_NIEUZYWANA not in tablice[0].flagi


# --- składanie podglądu ----------------------------------------------------


@pytest.mark.asyncio
async def test_bez_workspace_id_pobieramy_tylko_liste() -> None:
    """Pierwszy krok ma być natychmiastowy: ZMIERZONE 0,52–0,96 s."""
    klient = AtrapaKlienta(workspaces=[{"id": "1", "name": "Operacje"}])

    podglad = await zbuduj_podglad(klient)  # type: ignore[arg-type]

    assert len(podglad.workspace_y) == 1
    assert podglad.tablice == ()
    assert [n for n, _ in klient.zapytania] == ["workspaces"]


@pytest.mark.asyncio
async def test_z_workspace_id_nie_pobieramy_listy_ponownie() -> None:
    """Powtórka to zmarnowana sekunda i zbędne wywołanie z limitu klienta."""
    klient = AtrapaKlienta(strony=[[tablica("1")]])

    podglad = await zbuduj_podglad(klient, workspace_id="w1")  # type: ignore[arg-type]

    assert [n for n, _ in klient.zapytania] == ["boards"]
    assert len(podglad.tablice) == 1


@pytest.mark.asyncio
async def test_workspace_bez_widocznych_tablic_to_blad() -> None:
    """Pusta lista ma dwie przyczyny i klient musi wiedzieć którą.

    Workspace bez tablic wygląda identycznie jak workspace niewidoczny tym
    tokenem — a w drugim przypadku problemem jest klucz, nie konto.
    """
    klient = AtrapaKlienta(strony=[[]])

    with pytest.raises(PodgladError, match="nie ma widocznych tablic"):
        await zbuduj_podglad(klient, workspace_id="pusty")  # type: ignore[arg-type]


# --- zgrubny szacunek ------------------------------------------------------


def test_szacunek_rosnie_z_liczba_tablic() -> None:
    maly = oszacuj_zgrubnie(5)
    duzy = oszacuj_zgrubnie(59)

    assert maly[0] < duzy[0] and maly[1] < duzy[1]
    assert maly[0] < maly[1]


def test_szacunek_obejmuje_realny_koszt_snapshotu_7() -> None:
    """ZMIERZONE: 59 tablic dało realne widełki 2,28–5,00 USD po zbieraniu.

    Zgrubny szacunek liczony PRZED zbieraniem (z liczby tablic, nie z liczby
    hipotez) musi ten przedział obejmować — inaczej klient zgadza się na jedną
    kwotę, a widzi inną i traci zaufanie do obu.
    """
    od, do = oszacuj_zgrubnie(59)

    assert od <= 2.28, f"dolna granica {od} wyżej niż realna 2,28"
    assert do >= 5.00, f"górna granica {do} niżej niż realna 5,00"


def test_zero_tablic_daje_podloge_a_nie_zero() -> None:
    """Hipotezy o koncie (ludzie, goście, plan) idą niezależnie od tablic."""
    od, _ = oszacuj_zgrubnie(0)

    assert od > 0


# --- rejestr i payload -----------------------------------------------------


def test_rejestr_liczy_ale_nie_zapisuje() -> None:
    """Podgląd nie zakłada wiersza w `runy` — puste runy zepsuły już listę audytów."""
    rejestr = RejestrPodgladu()

    rejestr.zapisz(narzedzie="graphql:workspaces", latency_ms=840, complexity=1000)
    rejestr.zapisz(narzedzie="graphql:boards_podglad", complexity=623_724)

    assert rejestr.wywolan == 2
    assert rejestr.complexity == 624_724


@pytest.mark.asyncio
async def test_json_ma_pola_ktorych_uzywa_front() -> None:
    klient = AtrapaKlienta(strony=[[tablica("1")]])
    podglad = await zbuduj_podglad(klient, workspace_id="w1")  # type: ignore[arg-type]

    dokument = podglad_do_json(podglad)

    assert set(dokument) == {
        "workspace_y",
        "tablice",
        "pominietych_pomocniczych",
        "urwano_na_stronach",
        "zgrubnie_od_usd",
        "zgrubnie_do_usd",
    }
    assert dokument["tablice"][0]["oflagowana"] is False
    assert "wpisow" not in dokument["tablice"][0]
