"""Testy wyboru zakresu — flagi, filtr hipotez, widełki kosztu.

Cały moduł liczy się z gotowego snapshotu, więc te testy nie wołają ani
monday, ani modelu. To ta sama zaleta co przy szablonach findingów: regresję
łapie pytest, nie run za trzy dolary.

Dwa miejsca są tu pilnowane szczególnie mocno, bo pomyłka w nich jest cicha:

- **Filtr może pominąć hipotezę, której nie wolno pominąć.** Klient zapłaci
  mniej i nigdy się nie dowie, że znalezisko wypadło. Dlatego testy sprawdzają
  imiennie, że hipotezy o ludziach i o koncie przechodzą filtr zawsze.
- **Flaga ciszy może udawać flagę porzucenia.** Nietknięty szablon też nie ma
  wpisów, więc bez wykluczenia obu flag „cisza 90 dni" trafiłaby na 30 tablic
  zamiast 3 i przestałaby cokolwiek znaczyć.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.detektory import Hipoteza
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.wybor_zakresu import (
    FLAGA_CISZA,
    FLAGA_NIEPROBKOWANA,
    FLAGA_NIEUZYWANA,
    FLAGA_RAPORTOWA,
    POWOD_POZA_ZAKRESEM,
    WyborError,
    identyfikatory_tablic,
    klasy_milczace,
    odsiej_hipotezy,
    opis_milczenia_par,
    opis_zawezenia,
    oszacuj_koszt,
    sprawdz_wybor,
    wybor_do_json,
    zapisz_pominiete,
    zbuduj_wybor,
)

RUBRYKA = wczytaj_rubryke()

# Znaczniki tak dobrane, żeby różnica mówiła sama za siebie: doba to granica
# „nietknięta od założenia" (SEKUND_NIERUSZONEJ).
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
    workspace: tuple[str, str] = ("100", "Operacje"),
    nazwa: str | None = None,
) -> dict[str, Any]:
    return {
        "board_id": board_id,
        "nazwa": nazwa or f"Tablica {board_id}",
        "typ": typ,
        "state": "active",
        "board_kind": "public",
        "items_count": items,
        "created_at": utworzona,
        "updated_at": ruszona,
        "workspace_id": workspace[0],
        "workspace_nazwa": workspace[1],
        "owners": [],
        "subscribers": [],
        "kolumny": kolumny
        if kolumny is not None
        else [{"id": "n", "title": "Name", "type": "name"}],
    }


def payload(
    tablice: list[dict[str, Any]],
    *,
    aktywnosc: list[dict[str, Any]] | None = None,
    uwagi: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "meta": {"uwagi_o_zakresie": uwagi or []},
        "tablice": {"tablice": tablice, "podsumowanie": {}, "discovery": {}},
        "aktywnosc": {"aktywnosc_tablic": aktywnosc or []},
    }


def wpis_aktywnosci(board_id: str, kubelki: dict[str, int]) -> dict[str, Any]:
    return {"board_id": board_id, "kubelki_dni": kubelki, "wpisow": sum(kubelki.values())}


def hipoteza(klasa_id: str, obiekt_id: str, **fakty: Any) -> Hipoteza:
    return Hipoteza(klasa_id=klasa_id, obiekt_id=obiekt_id, fakty=fakty, budzet_wywolan=1)


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "wybor.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


# --- Flagi -----------------------------------------------------------------


def test_tylko_prawdziwe_tablice_na_liscie(con: sqlite3.Connection) -> None:
    """`sub_items_board`, `custom_object` i `document` nie są do wyboru.

    ZMIERZONE na #7: ze 124 obiektów prawdziwymi tablicami jest 59. Ekran
    ze 124 wierszami byłby ekranem z 65 pozycjami, których nikt nie wybiera.
    """
    dane = payload(
        [
            tablica("1"),
            tablica("2", typ="sub_items_board"),
            tablica("3", typ="custom_object"),
            tablica("4", typ="document"),
        ]
    )
    wybor = zbuduj_wybor(dane, [], con, rubryka=RUBRYKA)

    assert [t.board_id for t in wybor.tablice] == ["1"]
    assert wybor.pominietych_pomocniczych == 3


def test_cisza_i_nietknieta_wykluczaja_sie(con: sqlite3.Connection) -> None:
    """Nietknięty szablon nie dostaje flagi ciszy — to dwie różne wiadomości.

    Bez tego wykluczenia ZMIERZONE na #7 dało 30 tablic z ciszą zamiast 3,
    czyli flaga przestawała odróżniać porzucony proces od pustego szablonu.
    """
    dane = payload(
        [
            tablica("nietknieta", utworzona=ZALOZONA, ruszona=RUSZONA_PO_GODZINIE),
            tablica("porzucona", utworzona=ZALOZONA, ruszona=RUSZONA_PO_TYGODNIU),
        ],
        aktywnosc=[
            wpis_aktywnosci("nietknieta", {"0-7": 0}),
            wpis_aktywnosci("porzucona", {"0-7": 0}),
        ],
    )
    wybor = zbuduj_wybor(dane, [], con, rubryka=RUBRYKA)
    flagi = {t.board_id: t.flagi for t in wybor.tablice}

    assert flagi["nietknieta"] == (FLAGA_NIEUZYWANA,)
    assert flagi["porzucona"] == (FLAGA_CISZA,)


def test_brak_w_probce_logow_to_nie_cisza(con: sqlite3.Connection) -> None:
    """Tablica poza próbką logów dostaje `nieprobkowana`, nie `cisza_90_dni`.

    Collector loguje najwyżej 100 tablic. Brak w próbce znaczy „nie wiemy",
    a pokazany jako brak flagi czytałby się jak „tablica żywa" — ta sama
    pułapka, którą w detektorach zamyka wymóg `LEFT JOIN`.
    """
    dane = payload([tablica("poza_probka")], aktywnosc=[])
    wybor = zbuduj_wybor(dane, [], con, rubryka=RUBRYKA)

    assert wybor.tablice[0].flagi == (FLAGA_NIEPROBKOWANA,)
    assert wybor.tablice[0].wpisow is None
    assert wybor.tablic_bez_logow == 1


def test_raportowa_liczy_udzial_kolumn_automatycznych(con: sqlite3.Connection) -> None:
    """`raportowa` zastępuje niewyliczalną flagę o pustych kolumnach (D5)."""
    auto = [{"id": str(i), "title": f"K{i}", "type": "formula"} for i in range(6)]
    reczne = [{"id": str(i), "title": f"R{i}", "type": "text"} for i in range(4)]
    dane = payload(
        [
            tablica("raportowa", kolumny=auto + reczne),
            tablica("procesowa", kolumny=reczne + auto[:1]),
        ],
        aktywnosc=[
            wpis_aktywnosci("raportowa", {"0-7": 5}),
            wpis_aktywnosci("procesowa", {"0-7": 5}),
        ],
    )
    wybor = zbuduj_wybor(dane, [], con, rubryka=RUBRYKA)
    flagi = {t.board_id: t.flagi for t in wybor.tablice}

    assert FLAGA_RAPORTOWA in flagi["raportowa"]
    assert FLAGA_RAPORTOWA not in flagi["procesowa"]
    kolumny_auto = {t.board_id: t.kolumn_automatycznych for t in wybor.tablice}
    assert kolumny_auto == {"raportowa": 6, "procesowa": 1}


def test_nieparsowalne_daty_nie_wywracaja_flag(con: sqlite3.Connection) -> None:
    """Zepsuty znacznik czasu daje brak flagi, nie wyjątek ani fałszywą flagę."""
    dane = payload([tablica("dziwna", utworzona="wczoraj", ruszona="dzisiaj")])
    wybor = zbuduj_wybor(dane, [], con, rubryka=RUBRYKA)

    assert FLAGA_NIEUZYWANA not in wybor.tablice[0].flagi


def test_workspace_y_powstaja_z_pol_tablic(con: sqlite3.Connection) -> None:
    """Konto nie ma zapytania listującego workspace'y — znamy je z tablic."""
    dane = payload(
        [
            tablica("1", workspace=("100", "Operacje")),
            tablica("2", workspace=("100", "Operacje")),
            tablica("3", workspace=("200", "HR")),
        ]
    )
    wybor = zbuduj_wybor(dane, [], con, rubryka=RUBRYKA)

    assert [(w.nazwa, w.tablic) for w in wybor.workspace_y] == [("Operacje", 2), ("HR", 1)]


def test_uwagi_o_zakresie_ida_ze_snapshotu(con: sqlite3.Connection) -> None:
    """Zastrzeżenia o niezawężalności już produkuje collector — nie piszemy ich drugi raz."""
    dane = payload([tablica("1")], uwagi=["lista użytkowników jest z natury na poziomie konta"])
    wybor = zbuduj_wybor(dane, [], con, rubryka=RUBRYKA)

    assert wybor.uwagi_o_zakresie == ("lista użytkowników jest z natury na poziomie konta",)


# --- Filtr hipotez ---------------------------------------------------------


def test_brak_zawezenia_przepuszcza_wszystko() -> None:
    hipotezy = [hipoteza("BOARD_GHOST", "1"), hipoteza("ZOMBIE_ACCOUNT", "hash")]
    badane, pominiete = odsiej_hipotezy(hipotezy, board_ids=None, znane_tablice=frozenset({"1"}))

    assert badane == hipotezy
    assert pominiete == []


def test_hipotezy_o_ludziach_i_koncie_przechodza_zawsze() -> None:
    """Wybór tablic nie może zabrać znaleziska, które nie dotyczy tablicy.

    `UZYTKOWNIK_WYGASZONY` ma `boardy[]` w dowodzie, bo osoba pracowała na
    tablicach — ale hipoteza jest o CZŁOWIEKU. Odsianie jej obniżało podłogę
    z 13 hipotez do 4 i klient tracił znaleziska, za które zapłacił.
    """
    hipotezy = [
        hipoteza("ZOMBIE_ACCOUNT", "hash_osoby"),
        hipoteza("UZYTKOWNIK_WYGASZONY", "hash_osoby_2", boardy=["9", "8"]),
        hipoteza("GUEST_SPRAWL", "27690228"),
        hipoteza("PLAN_MISMATCH", "27690228"),
        hipoteza("AUTOMATION_DEAD", "156196768", automation_id="156196768"),
    ]
    badane, pominiete = odsiej_hipotezy(
        hipotezy, board_ids=frozenset({"1"}), znane_tablice=frozenset({"1", "8", "9"})
    )

    assert badane == hipotezy
    assert pominiete == []


def test_hipoteza_o_niewybranej_tablicy_wypada() -> None:
    hipotezy = [hipoteza("BOARD_GHOST", "wybrana"), hipoteza("BOARD_GHOST", "inna")]
    badane, pominiete = odsiej_hipotezy(
        hipotezy,
        board_ids=frozenset({"wybrana"}),
        znane_tablice=frozenset({"wybrana", "inna"}),
    )

    assert [h.obiekt_id for h in badane] == ["wybrana"]
    assert [h.obiekt_id for h in pominiete] == ["inna"]


def test_para_wymaga_obu_stron() -> None:
    """`DUPLICATE_STRUCTURE` bez drugiej tablicy nie ma czego porównać."""
    para = hipoteza("DUPLICATE_STRUCTURE", "a+b")
    znane = frozenset({"a", "b"})

    jedna, pominiete = odsiej_hipotezy([para], board_ids=frozenset({"a"}), znane_tablice=znane)
    assert jedna == [] and pominiete == [para]

    obie, brak = odsiej_hipotezy([para], board_ids=znane, znane_tablice=znane)
    assert obie == [para] and brak == []


def test_obiekt_nieznany_snapshotowi_zostaje() -> None:
    """Domyślnie zostawiamy. Pomyłka „zbadaj" kosztuje centy, „pomiń" — znalezisko."""
    dziwna = hipoteza("BOARD_GHOST", "identyfikator_nie_z_tego_snapshotu")
    badane, pominiete = odsiej_hipotezy(
        [dziwna], board_ids=frozenset({"1"}), znane_tablice=frozenset({"1"})
    )

    assert badane == [dziwna]
    assert pominiete == []


def test_identyfikatory_tablic_obejmuja_pomocnicze() -> None:
    """Hipoteza o `sub_items_board` nadal jest hipotezą o tablicy."""
    dane = payload([tablica("1"), tablica("2", typ="sub_items_board")])

    assert identyfikatory_tablic(dane) == frozenset({"1", "2"})


# --- Widełki ---------------------------------------------------------------


def wstaw_zuzycie(con: sqlite3.Connection, run_id: str, pozycje: list[tuple[str, float]]) -> None:
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) VALUES (?, ?, ?, ?)",
        (run_id, "test", "zakonczony", "2026-08-01T00:00:00Z"),
    )
    con.executemany(
        "INSERT INTO zuzycie_hipotez (run_id, klasa_id, obiekt_id, koszt_usd, zapisano) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (run_id, klasa, f"o{i}", koszt, "2026-08-01T00:00:00Z")
            for i, (klasa, koszt) in enumerate(pozycje)
        ],
    )
    con.commit()


def test_widelki_z_historii_a_nie_z_powietrza(con: sqlite3.Connection) -> None:
    wstaw_zuzycie(
        con,
        "historia",
        [
            ("BOARD_GHOST", 0.10),
            ("BOARD_GHOST", 0.20),
            ("BOARD_GHOST", 0.30),
            ("BOARD_GHOST", 0.40),
        ],
    )
    widelki = oszacuj_koszt(
        [hipoteza("BOARD_GHOST", "1")], con, rubryka=RUBRYKA, znane_tablice=frozenset({"1"})
    )

    assert widelki.dolna_usd <= widelki.srodek_usd <= widelki.gorna_usd
    assert not widelki.oszacowane_z_zapasu


def test_klasa_bez_historii_jest_wymieniona(con: sqlite3.Connection) -> None:
    """Cicha zerówka zaniżyłaby widełki, a klient dostałby rachunek wyższy niż zgoda."""
    widelki = oszacuj_koszt(
        [hipoteza("BOARD_GHOST", "1")], con, rubryka=RUBRYKA, znane_tablice=frozenset({"1"})
    )

    assert widelki.klasy_bez_historii == ("BOARD_GHOST",)
    assert widelki.oszacowane_z_zapasu
    assert widelki.srodek_usd > 0


def test_klasa_szablonowa_nic_nie_kosztuje(con: sqlite3.Connection) -> None:
    """`ZOMBIE_ACCOUNT` ma `rola_agenta: brak` — nie woła modelu, więc 0 USD."""
    assert RUBRYKA.po_id["ZOMBIE_ACCOUNT"].rola_agenta == "brak"
    wstaw_zuzycie(con, "h", [("BOARD_GHOST", 0.10), ("BOARD_GHOST", 0.20)])

    widelki = oszacuj_koszt(
        [hipoteza("ZOMBIE_ACCOUNT", "hash")],
        con,
        rubryka=RUBRYKA,
        znane_tablice=frozenset(),
    )

    assert widelki.srodek_usd == 0.0
    assert widelki.klasy_bez_historii == ()


def test_podloga_to_hipotezy_niezalezne_od_wyboru(con: sqlite3.Connection) -> None:
    """Podłogi nie zbije żaden wybór tablic — klient musi ją widzieć."""
    wstaw_zuzycie(
        con,
        "h",
        [
            ("GUEST_SPRAWL", 0.20),
            ("GUEST_SPRAWL", 0.20),
            ("BOARD_GHOST", 0.10),
            ("BOARD_GHOST", 0.10),
        ],
    )
    widelki = oszacuj_koszt(
        [hipoteza("GUEST_SPRAWL", "konto"), hipoteza("BOARD_GHOST", "1")],
        con,
        rubryka=RUBRYKA,
        znane_tablice=frozenset({"1"}),
    )

    assert widelki.hipotez_o_koncie == 1
    assert widelki.podloga_usd == pytest.approx(0.20)


# --- Walidacja i ślad ------------------------------------------------------


def test_obcy_board_id_to_blad() -> None:
    """Cicha tolerancja pozwoliłaby zapłacić za audyt tablicy, której nie ma."""
    dane = payload([tablica("1")])

    with pytest.raises(WyborError, match="poza tym snapshotem"):
        sprawdz_wybor(dane, ["1", "nieistnieje"])


def test_pusty_wybor_jest_dozwolony() -> None:
    dane = payload([tablica("1")])

    assert sprawdz_wybor(dane, []) == frozenset()


def test_pomocnicze_nie_sa_wybieralne() -> None:
    """Na ekranie ich nie ma, więc nie wolno ich przysłać jako wybór."""
    dane = payload([tablica("1"), tablica("2", typ="sub_items_board")])

    with pytest.raises(WyborError):
        sprawdz_wybor(dane, ["2"])


def test_pominiete_ida_do_istniejacej_tabeli(con: sqlite3.Connection) -> None:
    """Panel pokazuje `hipotezy_odrzucone` jako „czego nie widać"."""
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) VALUES (?, ?, ?, ?)",
        ("run-x", "test", "w_toku", "2026-08-01T00:00:00Z"),
    )
    zapisz_pominiete(con, run_id="run-x", pominiete=[hipoteza("BOARD_GHOST", "9")])
    con.commit()

    wiersz = con.execute(
        "SELECT klasa_id, obiekt_id, powod FROM hipotezy_odrzucone WHERE run_id = ?",
        ("run-x",),
    ).fetchone()
    assert (wiersz["klasa_id"], wiersz["obiekt_id"], wiersz["powod"]) == (
        "BOARD_GHOST",
        "9",
        POWOD_POZA_ZAKRESEM,
    )


def test_zapis_pustej_listy_nic_nie_robi(con: sqlite3.Connection) -> None:
    zapisz_pominiete(con, run_id="run-x", pominiete=[])

    assert con.execute("SELECT COUNT(*) c FROM hipotezy_odrzucone").fetchone()["c"] == 0


# --- Adnotacje do raportu --------------------------------------------------


def test_pelny_zakres_nie_dodaje_adnotacji() -> None:
    assert opis_zawezenia(wybranych=59, wszystkich=59, o_koncie=13) == ""


def test_zawezenie_mowi_ile_tablic_i_ze_konto_zbadane() -> None:
    """Bez tego raport z jedną tablicą wygląda jak pusty audyt całego konta."""
    tekst = opis_zawezenia(wybranych=1, wszystkich=59, o_koncie=13)

    assert "1 z 59" in tekst
    assert "13" in tekst


def test_adnotacja_o_milczeniu_par() -> None:
    """Brak `DUPLICATE_STRUCTURE` przy jednej tablicy to nie „nie ma duplikatów"."""
    pominiete = [hipoteza("DUPLICATE_STRUCTURE", "a+b"), hipoteza("BOARD_GHOST", "c")]

    assert klasy_milczace(pominiete, RUBRYKA) == ["DUPLICATE_STRUCTURE"]
    tekst = opis_milczenia_par(klasy_milczace(pominiete, RUBRYKA), RUBRYKA)
    assert "nie znaczy" in tekst


def test_brak_milczacych_klas_daje_pusty_opis() -> None:
    assert opis_milczenia_par([], RUBRYKA) == ""


# --- Payload dla frontu ----------------------------------------------------


def test_json_ma_pola_ktorych_uzywa_front(con: sqlite3.Connection) -> None:
    dane = payload([tablica("1")], aktywnosc=[wpis_aktywnosci("1", {"0-7": 3})])
    dokument = wybor_do_json(zbuduj_wybor(dane, [], con, rubryka=RUBRYKA))

    assert set(dokument) == {
        "workspace_y",
        "tablice",
        "widelki",
        "pominietych_pomocniczych",
        "tablic_bez_logow",
        "uwagi_o_zakresie",
    }
    assert dokument["tablice"][0]["wpisow"] == 3
    assert dokument["tablice"][0]["oflagowana"] is False
    assert "podloga_usd" in dokument["widelki"]


def test_podloga_nigdy_nie_przekracza_dolnej_granicy(con: sqlite3.Connection) -> None:
    """ZMIERZONA USTERKA na snapshocie #8: `dolna=0,99` przy `podloga=2,07`.

    Podłoga liczyła się z mediany, a dolna granica z p25 — więc gdy większość
    hipotez dotyczyła konta (15 z 24), suma median przekraczała sumę p25
    wszystkich. Kwota niższa od własnej podłogi jest nieprawdą, a klient czyta
    obie liczby obok siebie.
    """
    wstaw_zuzycie(
        con,
        "h",
        # Szeroki rozrzut, żeby p25 i mediana były wyraźnie różne.
        [("GUEST_SPRAWL", k) for k in (0.05, 0.10, 0.30, 0.60)]
        + [("BOARD_GHOST", k) for k in (0.05, 0.10, 0.30, 0.60)],
    )
    # Przewaga hipotez o KONCIE — to ten przypadek wywalił proporcję.
    hipotezy = [hipoteza("GUEST_SPRAWL", f"konto{i}") for i in range(6)]
    hipotezy += [hipoteza("BOARD_GHOST", "1")]

    widelki = oszacuj_koszt(hipotezy, con, rubryka=RUBRYKA, znane_tablice=frozenset({"1"}))

    assert widelki.podloga_usd <= widelki.dolna_usd, (
        f"podłoga {widelki.podloga_usd} wyżej niż dolna granica {widelki.dolna_usd}"
    )
    assert widelki.dolna_usd <= widelki.srodek_usd <= widelki.gorna_usd
