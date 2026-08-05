"""Renderer raportu (3.12) — przede wszystkim GRANICE.

Trzy rzeczy, których ten plik nie przepuszcza, bo każda jest usterką widoczną
u odbiorcy zewnętrznego:

1. finding `tylko_wewnetrzne` w wersji klientowej
2. treść `trop` w wersji klientowej
3. surowy hash w którejkolwiek wersji

Testy 1 i 2 działają na danych SYNTETYCZNYCH, i to jest konieczne: na
snapshocie #5 wszystkie 17 findingów ma `widocznosc: klient`, a klasy
`tylko_wewnetrzne` (`ENGAGEMENT_DROP`, `PROCESS_BYPASS`) nie wzbudziły się ani
raz. Prawdziwy run tej granicy NIE sprawdza — i właśnie dlatego nie może być
jedynym dowodem, że działa.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.deanonimizacja import WZORZEC_HASHA
from monday_audit.raport import (
    ODBIORCA_KLIENT,
    ODBIORCA_WEWNETRZNY,
    RaportError,
    etykieta,
    odmiana,
    slownie,
    wyrenderuj,
    zapisz,
    zasob_data_uri,
    zbuduj_raport,
)
from monday_audit.rubryka import wczytaj_rubryke

RUBRYKA = wczytaj_rubryke()
RUN_AT = "2026-08-01T21:09:13.860699+00:00"
HASH_ANNY = "05677b1ab370bae1"
HASH_OBCY = "deadbeefdeadbeef"

# Klasy o przeciwnej widoczności — obie muszą istnieć w rubryce, inaczej test
# granicy nie ma czego rozdzielać.
KLASA_KLIENTA = "ZOMBIE_ACCOUNT"
KLASA_WEWNETRZNA = "PROCESS_BYPASS"

PAYLOAD = {
    "meta": {
        "client_id": "cxlabs",
        "run_at": RUN_AT,
        "collector_ver": "0.1.0",
        "wersja_api": "2026-07",
        "okno_dni": 90,
        "uwagi_o_zakresie": ["lista użytkowników jest z natury na poziomie konta"],
    },
    "konto": {
        "plan": {"tier": "enterprise"},
        "zakres": {"typ": "workspace", "workspace_ids": ["6576039"], "board_ids": []},
        "zastrzezenia": ["token bez uprawnień admina"],
    },
}


def _finding(
    con: sqlite3.Connection,
    klasa_id: str,
    *,
    waga: str = "wysoka",
    wysilek: str = "niski",
    kwota: float | None = None,
    opis: str = "opis znaleziska",
    dowod: dict | None = None,
) -> None:
    klasa = RUBRYKA.po_id[klasa_id]
    con.execute(
        "INSERT INTO findings (run_id, snapshot_id, klasa_id, rubric_ver, waga, wysilek, "
        "typ_wyceny, kwota_pln, widocznosc, opis, rekomendacja, dowod, pewnosc, trop) "
        "VALUES ('r1', 5, ?, ?, ?, ?, ?, ?, ?, ?, 'co zrobić', ?, 'wysoka', ?)",
        (
            klasa_id,
            RUBRYKA.wersja,
            waga,
            wysilek,
            klasa.typ_wyceny,
            kwota,
            klasa.widocznosc,
            opis,
            json.dumps(dowod or {"user_hash": HASH_ANNY}, ensure_ascii=False),
            klasa.trop_sprzedazowy,
        ),
    )


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    polaczenie.execute(
        "INSERT INTO snapshots (id, client_id, run_at, collector_ver, payload) "
        "VALUES (5, 'cxlabs', ?, '0.1.0', ?)",
        (RUN_AT, json.dumps(PAYLOAD, ensure_ascii=False)),
    )
    polaczenie.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
        "rubric_ver, cennik_ver, koszt_usd) "
        "VALUES ('r1', 'cxlabs', 5, 'zakonczony', ?, 'claude-sonnet-5', ?, NULL, 1.71)",
        (RUN_AT, RUBRYKA.wersja),
    )
    polaczenie.execute(
        "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
        "VALUES ('cxlabs', ?, 'Anna Górniak', 'anna@klient.test')",
        (HASH_ANNY,),
    )
    polaczenie.commit()
    yield polaczenie
    polaczenie.close()


# ── granica: treść wewnętrzna nie wychodzi do klienta ────────────────────


def test_klasa_tylko_wewnetrzna_nie_wchodzi_do_wersji_klientowej(con: sqlite3.Connection) -> None:
    """DoD z 3.12: „wersja klientowa nie zawiera ani jednego findingu
    oznaczonego `tylko_wewnetrzne`".
    """
    _finding(con, KLASA_KLIENTA)
    _finding(con, KLASA_WEWNETRZNA)
    con.commit()

    wewnetrzny = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY)
    klientowy = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)

    assert {f.klasa_id for f in wewnetrzny.findingi} == {KLASA_KLIENTA, KLASA_WEWNETRZNA}
    assert {f.klasa_id for f in klientowy.findingi} == {KLASA_KLIENTA}


def test_tresc_klasy_wewnetrznej_nie_pojawia_sie_w_html(con: sqlite3.Connection) -> None:
    """Nie sam identyfikator klasy — cała jej TREŚĆ.

    Sprawdzenie po `klasa_id` przeszłoby też wtedy, gdyby szablon wypisał opis
    findingu w innym miejscu strony.
    """
    _finding(con, KLASA_WEWNETRZNA, opis="TAJNY OPIS WEWNĘTRZNY")
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert "TAJNY OPIS WEWNĘTRZNY" not in html
    assert KLASA_WEWNETRZNA not in html


def test_trop_nie_wychodzi_do_klienta(con: sqlite3.Connection) -> None:
    """Trop jest w bazie i w rubryce, ale w wersji klientowej go nie ma."""
    _finding(con, KLASA_KLIENTA)
    con.commit()
    trop = RUBRYKA.po_id[KLASA_KLIENTA].trop_sprzedazowy
    assert trop, "test bez sensu, jeśli rubryka nie ma tropu dla tej klasy"

    wewnetrzny = wyrenderuj(
        zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY)
    )
    klientowy = wyrenderuj(
        zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)
    )

    assert trop in wewnetrzny
    assert trop not in klientowy
    assert "Trop" not in klientowy


def test_klient_nie_widzi_odrzucen_pinowania_ani_kosztu(con: sqlite3.Connection) -> None:
    """Diagnostyka runu to nasza sprawa, nie treść dla odbiorcy."""
    _finding(con, KLASA_KLIENTA)
    con.execute(
        "INSERT INTO hipotezy_odrzucone (run_id, klasa_id, obiekt_id, powod) "
        "VALUES ('r1', 'PLAN_MISMATCH', '27690228', 'konto rośnie')"
    )
    con.execute(
        "INSERT INTO findings_odrzucone (run_id, snapshot_id, klasa_id, regula, powod, finding) "
        "VALUES ('r1', 5, 'BOARD_GHOST', 'brak_dowodu', 'pusty dowód', '{}')"
    )
    con.commit()

    klientowy = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)
    html = wyrenderuj(klientowy)

    assert klientowy.hipotezy_odrzucone == ()
    assert klientowy.findingi_odrzucone == ()
    assert klientowy.pinowanie == {}
    assert klientowy.koszt_usd is None
    assert "konto rośnie" not in html
    assert "claude-sonnet-5" not in html
    assert "1.71" not in html


def test_wewnetrzny_widzi_wszystko(con: sqlite3.Connection) -> None:
    """Komplement poprzedniego: wersja wewnętrzna MUSI to mieć."""
    _finding(con, KLASA_KLIENTA)
    con.execute(
        "INSERT INTO hipotezy_odrzucone (run_id, klasa_id, obiekt_id, powod) "
        "VALUES ('r1', 'PLAN_MISMATCH', '27690228', 'konto rośnie')"
    )
    con.commit()

    html = wyrenderuj(
        zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY)
    )

    assert "konto rośnie" in html
    assert "claude-sonnet-5" in html
    assert "nie wysyłać klientowi" in html


# ── granica: żaden surowy hash nie wychodzi ──────────────────────────────


def test_hash_jako_klucz_dowodu_tez_jest_rozwijany(con: sqlite3.Connection) -> None:
    """Regresja z prawdziwego runu.

    `tablice_dostepne` w dowodzie `GUEST_SPRAWL` to mapa user_hash → lista
    tablic, czyli hash jest NAZWĄ POLA. Pierwsza wersja rekurencji schodziła
    tylko po wartościach i przepuszczała dziewięć hashy do obu plików.
    """
    _finding(con, KLASA_KLIENTA, dowod={"tablice_dostepne": {HASH_ANNY: ["Onboarding"]}})
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert "Anna Górniak" in html
    assert WZORZEC_HASHA.search(html) is None


def test_zaden_surowy_hash_nie_zostaje_w_zadnej_wersji(con: sqlite3.Connection) -> None:
    """Wszystkie miejsca naraz: opis, dowód skalarny, lista, klucz, nieznany."""
    _finding(
        con,
        KLASA_KLIENTA,
        opis=f"Konto (hash {HASH_ANNY}) jest martwe, a {HASH_OBCY} nieznane",
        dowod={
            "user_hash": HASH_ANNY,
            "guest_hash": [HASH_ANNY, HASH_OBCY],
            "tablice_dostepne": {HASH_OBCY: []},
            "powody_bledow": f"brak uprawnień u {HASH_ANNY}",
        },
    )
    con.commit()

    for odbiorca in (ODBIORCA_WEWNETRZNY, ODBIORCA_KLIENT):
        html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=odbiorca))
        trafienie = WZORZEC_HASHA.search(html)
        assert trafienie is None, (
            f"{odbiorca}: przeszedł hash {trafienie.group(0) if trafienie else ''}"
        )


def test_nieznany_hash_jest_policzony_w_raporcie(con: sqlite3.Connection) -> None:
    _finding(con, KLASA_KLIENTA, dowod={"user_hash": HASH_OBCY})
    con.commit()

    raport = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY)

    assert raport.nieznane_hashe == 1


# ── autoescaping ─────────────────────────────────────────────────────────


def test_nazwa_tablicy_ze_znacznikami_jest_escapowana(con: sqlite3.Connection) -> None:
    """Dowód, że `autoescape` jest włączony.

    Jinja domyślnie ma go WYŁĄCZONY. Dokument niesie nazwy tablic pisane przez
    klienta, więc bez tej flagi nazwa `Oferty <b>2026</b>` rozwala układ strony,
    a `<script>` staje się skryptem. To jedna flaga, którą łatwo zgubić przy
    refaktorze — i dlatego jest na nią test, a nie komentarz.
    """
    _finding(
        con,
        KLASA_KLIENTA,
        opis="Tablica Oferty <script>alert(1)</script> jest martwa",
        dowod={"nazwa": "Oferty <b>2026</b>"},
    )
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Oferty &lt;b&gt;2026&lt;/b&gt;" in html


# ── treść i kolejność ────────────────────────────────────────────────────


def test_findingi_sa_w_kolejnosci_z_rubryki(con: sqlite3.Connection) -> None:
    """Waga malejąco, przy równej wadze — tańsze w naprawie wyżej."""
    _finding(con, KLASA_KLIENTA, waga="srednia", wysilek="niski", opis="trzeci")
    _finding(con, KLASA_KLIENTA, waga="krytyczna", wysilek="wysoki", opis="drugi")
    _finding(con, KLASA_KLIENTA, waga="krytyczna", wysilek="niski", opis="pierwszy")
    con.commit()

    raport = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)

    assert [f.opis for f in raport.findingi] == ["pierwszy", "drugi", "trzeci"]


def test_kwoty_sa_sumowane(con: sqlite3.Connection) -> None:
    _finding(con, KLASA_KLIENTA, kwota=1200.0)
    _finding(con, KLASA_KLIENTA, kwota=1200.0)
    _finding(con, KLASA_KLIENTA, kwota=None)
    con.commit()

    raport = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)

    assert raport.suma_kwot == 2400.0
    assert raport.ma_kwoty


def test_bez_kwot_raport_mowi_to_wprost(con: sqlite3.Connection) -> None:
    """Cisza w tym miejscu czytałaby się jak „nie ma oszczędności"."""
    _finding(con, KLASA_KLIENTA, kwota=None)
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert not zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT).ma_kwoty
    assert "nie podano stawki" in html


def test_czego_nie_widac_idzie_do_obu_wersji(con: sqlite3.Connection) -> None:
    """Decyzja, nie przeoczenie: raport ukrywający granice sugeruje pokrycie,
    którego nie ma.
    """
    _finding(con, KLASA_KLIENTA)
    con.commit()

    for odbiorca in (ODBIORCA_WEWNETRZNY, ODBIORCA_KLIENT):
        raport = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=odbiorca)
        html = wyrenderuj(raport)
        assert "token bez uprawnień admina" in html
        assert "lista użytkowników jest z natury na poziomie konta" in html
        assert len(raport.zastrzezenia) == 2


def test_nazwiska_sa_w_obu_wersjach(con: sqlite3.Connection) -> None:
    """Raport z hashami jest niewykonalny — klient nie wie, o kogo chodzi."""
    _finding(con, KLASA_KLIENTA, opis=f"Konto {HASH_ANNY} jest martwe")
    con.commit()

    for odbiorca in (ODBIORCA_WEWNETRZNY, ODBIORCA_KLIENT):
        html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=odbiorca))
        assert "Anna Górniak" in html


def test_email_tylko_w_wersji_wewnetrznej(con: sqlite3.Connection) -> None:
    """Nazwisko wystarcza klientowi; adres to już dodatkowa dana w dokumencie."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    wewnetrzny = wyrenderuj(
        zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY)
    )
    klientowy = wyrenderuj(
        zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)
    )

    assert "anna@klient.test" in wewnetrzny
    assert "anna@klient.test" not in klientowy


# ── nazwa pliku i błędy ──────────────────────────────────────────────────


def test_nazwa_pliku_bierze_miesiac_ze_snapshotu(con: sqlite3.Connection) -> None:
    """Raport z sierpniowego snapshotu dotyczy sierpnia, nie dnia renderowania."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    raport = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)

    assert raport.nazwa_pliku() == "2026-08_audyt_konta_cxlabs_klient.html"


def test_zapis_tworzy_plik(con: sqlite3.Connection, tmp_path: Path) -> None:
    _finding(con, KLASA_KLIENTA)
    con.commit()
    raport = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)

    cel = zapisz(raport, katalog=tmp_path / "raporty")

    assert cel.is_file()
    assert "Audyt konta monday.com" in cel.read_text(encoding="utf-8")


def test_nieznany_odbiorca_odpada(con: sqlite3.Connection) -> None:
    """Literówka w odbiorcy nie może dać po cichu wersji pełnej."""
    with pytest.raises(RaportError, match="nieznany odbiorca"):
        zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca="klientowy")


def test_nieistniejacy_run_odpada(con: sqlite3.Connection) -> None:
    with pytest.raises(RaportError, match="nie ma runu"):
        zbuduj_raport(con, run_id="nie-ma-takiego", rubryka=RUBRYKA)


def test_run_bez_snapshotu_w_runy_bierze_go_z_findingow(con: sqlite3.Connection) -> None:
    """Runy agenta sprzed 2026-08-04 mają `runy.snapshot_id` NULL.

    `findings.snapshot_id` jest NOT NULL, więc prawda jest w bazie i nie ma
    powodu odmawiać wyrenderowania starszego runu.
    """
    _finding(con, KLASA_KLIENTA)
    con.execute("UPDATE runy SET snapshot_id = NULL WHERE run_id = 'r1'")
    con.commit()

    raport = zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY)

    assert raport.pinowanie["snapshot_id"] == 5


def test_dokument_nie_ma_zasobow_zewnetrznych(con: sqlite3.Connection) -> None:
    """Ma się otworzyć z dysku i wydrukować u kogoś bez dostępu do naszej sieci."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert re.search(r"""(src|href)\s*=\s*["']https?://""", html) is None
    assert "<style>" in html


# ── polszczyzna w dokumencie ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("liczba", "oczekiwane"),
    [(1, "pole"), (2, "pola"), (4, "pola"), (5, "pól"), (7, "pól"), (12, "pól"), (22, "pola")],
)
def test_odmiana_po_liczbie(liczba: int, oczekiwane: str) -> None:
    """Bez tego dokument mówi „Dowód (7 pola)".

    Drobiazg, ale pierwszy sygnał niedbałości podważa resztę raportu —
    a ten raport ma przekonać klienta, że liczby w nim są policzone uważnie.
    """
    assert odmiana(liczba, "pole", "pola", "pól") == oczekiwane


def test_etykieta_pola_z_hashem_mowi_o_koncie() -> None:
    """Po deanonimizacji `user_hash: Maciej Zieliński` jest sprzeczne samo w sobie."""
    assert etykieta("user_hash") == "konto"
    assert etykieta("guest_hash") == "konta gości"
    assert etykieta("top_kontrybutor_hash") == "najaktywniejsza osoba"


def test_nieznane_pole_dowodu_traci_podkreslenia() -> None:
    """Rubryka może dodać pole w każdej chwili — fallback musi być czytelny."""
    assert etykieta("obecnosc_w_logach") == "obecnosc w logach"


def test_wartosci_slownika_dostaja_polskie_znaki() -> None:
    """Słowniki rubryki są bez diakrytyków, bo służą też jako klucze w SQL."""
    assert slownie("srednia") == "średnia"
    assert slownie("sredni") == "średni"
    assert slownie("wysoka") == "wysoka"


def test_dokument_nie_pokazuje_surowego_klucza_user_hash(con: sqlite3.Connection) -> None:
    _finding(con, KLASA_KLIENTA, dowod={"user_hash": HASH_ANNY})
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert "user_hash" not in html
    assert "Anna Górniak" in html


# ── marka CXLABS i licencja fontów ───────────────────────────────────────


def test_dokument_nie_osadza_zadnego_fontu(con: sqlite3.Connection) -> None:
    """GRANICA LICENCYJNA, nie estetyka.

    Clash Display jest darmowy, ale jego EULA (`szablony/fonty/FFL.txt`) mówi:
    osadzać wolno tylko „in a secured, read-only mode", a „the extraction of the
    Font Software in whole or in part is prohibited". Plik HTML jest tekstem,
    więc `data:` URI z woff2 każdy odbiorca wyjmie jednym poleceniem — to łamie
    licencję. Avenir jest komercyjny i tam jest jeszcze ciaśniej.

    Dlatego raport odwołuje się do fontów ZAINSTALOWANYCH w systemie, a stos
    schodzi na drugi krój marki. Ten test istnieje, żeby ktoś „nie poprawił"
    wyglądu przez osadzenie fontu.
    """
    _finding(con, KLASA_KLIENTA)
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert "@font-face" not in html
    assert "font/woff" not in html
    assert "font/otf" not in html
    assert "font/ttf" not in html


def test_repo_nie_zawiera_binarek_fontow() -> None:
    """To samo ograniczenie, drugi front: §02 EULA zabrania wysyłania fontu
    na publiczny serwer, a to repo idzie na GitHub.
    """
    korzen = Path(__file__).resolve().parent.parent
    znalezione = [
        str(p.relative_to(korzen))
        for wzor in ("*.otf", "*.ttf", "*.woff", "*.woff2")
        for p in korzen.rglob(wzor)
        if ".venv" not in p.parts
    ]

    assert znalezione == [], f"binarki fontów w repo łamią §02 licencji: {znalezione}"


def test_stos_fontow_schodzi_na_drugi_kroj_marki(con: sqlite3.Connection) -> None:
    """Skoro Clash Display nie jest osadzony, degradacja musi trafić w Avenira.

    Bez tego nagłówki u kogoś bez Clash Display spadłyby na systemowy krój,
    czego README marki zabrania wprost („Never use […] system fonts as primary").
    """
    _finding(con, KLASA_KLIENTA)
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert '"Clash Display", "Avenir Next", "Avenir"' in html


def test_znak_marki_jest_osadzony_a_nie_linkowany(con: sqlite3.Connection) -> None:
    """Logo to własny zasób CXLABS — osadzamy, bo dokument działa offline."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert "data:image/png;base64," in html


def test_brak_logo_nie_wywala_raportu(tmp_path: Path, con: sqlite3.Connection) -> None:
    """Zasób może zniknąć przy refaktorze — dokument ma zostać czytelny."""
    _finding(con, KLASA_KLIENTA)
    con.commit()
    assert zasob_data_uri("nie-ma-takiego.png", katalog=tmp_path) is None


def test_dokument_uzywa_tokenow_marki(con: sqlite3.Connection) -> None:
    """Kolory z `colors_and_type.css`, nie wymyślone.

    Ink na nagłówku i lime jako akcent liczby — README marki każe używać lime
    oszczędnie („Never decorative"), więc sprawdzamy, że jest, a nie ile go jest.
    """
    _finding(con, KLASA_KLIENTA, kwota=1200.0)
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    assert "rgb(18, 32, 33)" in html
    assert "rgb(36, 56, 56)" in html
    assert "rgb(173, 247, 99)" in html


def test_brak_emoji_i_piktogramow(con: sqlite3.Connection) -> None:
    """README marki: „Never use emoji. Never use unicode pictographs."."""
    _finding(con, KLASA_KLIENTA, kwota=1200.0)
    con.commit()

    html = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT))

    piktogramy = [
        znak
        for znak in html
        if znak
        in "\u2705\u274c\u26a0\u2b50\U0001f4ca\U0001f512\U0001f4c8\U0001f680\u2757\u2b07\u2b06"
    ]
    assert piktogramy == [], f"piktogramy w dokumencie: {piktogramy}"
