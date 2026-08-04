"""Testy stawek i scrapera cennika, warstwa 1 z 04-test.md.

**Zero żądań sieciowych.** Scraper testujemy na ZAPISANYM tekście strony,
bo test zależny od cudzej strony przestaje być testem, a zaczyna być
monitoringiem.

Trzy testy pilnują rzeczy, które w raporcie klienta byłyby błędem, nie
niedogodnością:

- `test_wartosc_poza_przedzialem_nie_wchodzi_do_bazy` — strona monday ma na
  sobie i `$0.01`, i `$9`; scraper, który weźmie drugie, policzyłby klientowi
  kwotę 900 razy za dużą
- `test_nietrafiony_wzorzec_nie_nadpisuje_dobrej_wartosci` — cichy zapis
  śmiecia jest groźniejszy od braku odświeżenia
- `test_przeterminowana_stawka_nie_pozwala_liczyc` — cicho zgniła stawka
  w raporcie jest groźniejsza od braku kwoty
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.cennik import (
    PRZEDZIALY,
    CennikError,
    Stawka,
    aktualna,
    przeglad,
    sprawdz_przedzial,
    stawki_dla,
    wersja_cennika,
    wersja_uzytych,
    zapisz_stawke,
    zapisz_stawke_klienta,
)
from monday_audit.cli_cennik import WZORCE, na_tekst, wyciagnij

# Fragment PRAWDZIWEJ strony monday, pobrany 2026-08-04. Skrócony, ale zdania
# zostawione dosłownie — na nich stoją wzorce. Dwie tabele złożoności są tu
# celowo obie, bo to one były źródłem pomyłki przy pierwszym uruchomieniu.
STRONA = """
<html><body><p>AI Credits are consumed when certain AI tasks are triggered.</p>
<p>AI blocks 8 credits per action. All actions carried out on the same item
within a 24-hour window count once.</p>
<p>AI Notetaker 120 credits per meeting hour Usage is measured by meeting
minutes.</p>
<p>Offering AI credits consumption Notes monday agents Credit consumption
varies based on task complexity: Simple (~10&ndash;50 credits), Intermediate
(~50&ndash;150 credits), Complex (~150&ndash;250 credits), Extra complex
(~250+ credits). Each agent run may include multiple tasks depending on the
prompt.</p>
<p>monday sidekick Credit consumption per message varies based on task
complexity: Simple (~10&ndash;30 credits), Intermediate (~30&ndash;80 credits),
Complex (~80&ndash;150 credits), Extra complex (~150+ credits). Consumption
depends on the model.</p>
<script>var cena = "$9";</script>
</body></html>
"""


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    polaczenie = polacz(tmp_path / "test.db")
    zastosuj_migracje(polaczenie)
    yield polaczenie
    polaczenie.close()


def teraz_plus(dni: int) -> str:
    return (datetime.now(tz=UTC) + timedelta(days=dni)).isoformat()


# ── przedziały rozsądku ──────────────────────────────────────────────────


def test_wartosc_poza_przedzialem_nie_wchodzi_do_bazy(con: sqlite3.Connection) -> None:
    """Strona monday ma na sobie `$0.01` (kredyt) i `$9` (plan per user).

    Scraper, który weźmie drugie jako stawkę kredytu, policzyłby klientowi
    kwotę 900 razy za dużą. Odrzucenie musi nastąpić przy ZAPISIE.
    """
    with pytest.raises(CennikError, match="poza przedziałem"):
        zapisz_stawke(
            con,
            pozycja="kredyt_ai_usd",
            wartosc=9.0,
            jednostka="kredyt",
            waluta="USD",
            sposob="scraper",
            wiarygodnosc="zrodlo_pierwotne",
        )

    assert con.execute("SELECT COUNT(*) FROM cennik").fetchone()[0] == 0


def test_stawka_w_przedziale_przechodzi(con: sqlite3.Connection) -> None:
    zapisz_stawke(
        con,
        pozycja="kredyt_ai_usd",
        wartosc=0.01,
        jednostka="kredyt",
        waluta="USD",
        # `reczna`, bo stawki kredytu w USD nie ma na stronie monday — wpisuje
        # ją człowiek ze źródła zewnętrznego i tak jest oznaczona.
        sposob="reczna",
        wiarygodnosc="zewnetrzne",
    )

    stawka = aktualna(con, "kredyt_ai_usd")
    assert stawka is not None
    assert stawka.wartosc == 0.01
    assert stawka.wiarygodnosc == "zewnetrzne"


@pytest.mark.parametrize("zla", [0, -1, -0.5])
def test_stawka_niedodatnia_odpada(zla: float) -> None:
    with pytest.raises(CennikError, match="dodatnia"):
        sprawdz_przedzial("kredyt_ai_usd", zla)


def test_nowa_pozycja_bez_przedzialu_ostrzega(caplog: pytest.LogCaptureFixture) -> None:
    """Pozycja bez przedziału przechodzi, ale nie po cichu."""
    import logging

    assert "wymyslona_pozycja" not in PRZEDZIALY
    with caplog.at_level(logging.WARNING):
        sprawdz_przedzial("wymyslona_pozycja", 123.0)

    assert "przedziału rozsądku" in caplog.text


# ── pierwszeństwo stawki klienta ─────────────────────────────────────────


def test_stawka_klienta_bije_publiczna(con: sqlite3.Connection) -> None:
    """Cena Enterprise jest NEGOCJOWANA — publiczny cennik jej nie zawiera (O7)."""
    zapisz_stawke(
        con,
        pozycja="koszt_licencji_mies",
        wartosc=9.0,
        jednostka="miesiac",
        waluta="USD",
        sposob="scraper",
        wiarygodnosc="zrodlo_pierwotne",
    )
    zapisz_stawke_klienta(
        con,
        client_id="cxlabs",
        pozycja="koszt_licencji_mies",
        wartosc=100.0,
        waluta="PLN",
        zrodlo="faktura 07/2026",
    )

    publiczna = aktualna(con, "koszt_licencji_mies")
    klienta = aktualna(con, "koszt_licencji_mies", client_id="cxlabs")

    assert publiczna is not None and publiczna.wartosc == 9.0
    assert klienta is not None and klienta.wartosc == 100.0
    assert klienta.per_klient is True
    # Raport musi umieć powiedzieć, na czym stoi kwota.
    assert klienta.zrodlo == "faktura 07/2026"
    assert klienta.wiarygodnosc == "od_klienta"


def test_inny_klient_nie_widzi_stawki_sasiada(con: sqlite3.Connection) -> None:
    zapisz_stawke_klienta(
        con,
        client_id="cxlabs",
        pozycja="koszt_licencji_mies",
        wartosc=100.0,
        waluta="PLN",
        zrodlo="faktura",
    )

    assert aktualna(con, "koszt_licencji_mies", client_id="inny-klient") is None


def test_brak_stawki_to_none_nie_wyjatek(con: sqlite3.Connection) -> None:
    """Brak stawki jest poprawną odpowiedzią — wtedy finding leci bez kwoty (O7)."""
    assert aktualna(con, "kredyt_ai_usd") is None


# ── przeterminowanie ─────────────────────────────────────────────────────


def test_przeterminowana_stawka_nie_pozwala_liczyc() -> None:
    """Cicho zgniła stawka w raporcie klienta jest groźniejsza od braku kwoty."""
    stara = Stawka(
        pozycja="kredyt_ai_usd",
        wartosc=0.01,
        waluta="USD",
        jednostka="kredyt",
        zrodlo="x",
        wiarygodnosc="zrodlo_pierwotne",
        pobrano_at=teraz_plus(-100),
        wazna_do=teraz_plus(-70),
    )

    assert stara.przeterminowana is True
    assert stara.wolno_liczyc is False
    assert stara.dni_od_odswiezenia is not None and stara.dni_od_odswiezenia >= 99


def test_stawka_klienta_nie_przeterminowuje_sie() -> None:
    """Człowiek podał ją świadomie na ten run — nikt jej nie musi potwierdzać."""
    od_klienta = Stawka(
        pozycja="koszt_licencji_mies",
        wartosc=100.0,
        waluta="PLN",
        jednostka="miesiac",
        zrodlo="faktura",
        wiarygodnosc="od_klienta",
        pobrano_at=teraz_plus(-400),
        wazna_do=teraz_plus(-300),
        per_klient=True,
    )

    assert od_klienta.wolno_liczyc is True


def test_swieza_stawka_pozwala_liczyc(con: sqlite3.Connection) -> None:
    zapisz_stawke(
        con,
        pozycja="ai_block_kredyty",
        wartosc=8.0,
        jednostka="akcja",
        sposob="scraper",
        wiarygodnosc="zrodlo_pierwotne",
    )

    stawka = aktualna(con, "ai_block_kredyty")
    assert stawka is not None
    assert stawka.przeterminowana is False
    assert stawka.wolno_liczyc is True


# ── historia i wersja ────────────────────────────────────────────────────


def test_zapis_dopisuje_a_nie_nadpisuje(con: sqlite3.Connection) -> None:
    """Snapshot sprzed trzech miesięcy musi dać się zinterpretować starą stawką (D7)."""
    for wartosc in (8.0, 10.0):
        zapisz_stawke(
            con,
            pozycja="ai_block_kredyty",
            wartosc=wartosc,
            jednostka="akcja",
            sposob="scraper",
            wiarygodnosc="zrodlo_pierwotne",
        )

    assert con.execute("SELECT COUNT(*) FROM cennik").fetchone()[0] == 2
    biezaca = aktualna(con, "ai_block_kredyty")
    assert biezaca is not None and biezaca.wartosc == 10.0


def test_wersja_cennika_idzie_do_snapshotu(con: sqlite3.Connection) -> None:
    assert wersja_cennika(con) is None

    zapisz_stawke(
        con,
        pozycja="ai_block_kredyty",
        wartosc=8.0,
        jednostka="akcja",
        sposob="scraper",
        wiarygodnosc="zrodlo_pierwotne",
    )

    assert wersja_cennika(con) is not None


def test_przeglad_pokazuje_stawki_klienta(con: sqlite3.Connection) -> None:
    zapisz_stawke(
        con,
        pozycja="ai_block_kredyty",
        wartosc=8.0,
        jednostka="akcja",
        sposob="scraper",
        wiarygodnosc="zrodlo_pierwotne",
    )
    zapisz_stawke_klienta(
        con,
        client_id="cxlabs",
        pozycja="koszt_licencji_mies",
        wartosc=100.0,
        waluta="PLN",
        zrodlo="faktura",
    )

    pozycje = {s.pozycja for s in przeglad(con, client_id="cxlabs")}
    assert pozycje == {"ai_block_kredyty", "koszt_licencji_mies"}
    # Bez klienta stawka per klient nie wychodzi.
    assert {s.pozycja for s in przeglad(con)} == {"ai_block_kredyty"}


# ── scraper na ZAPISANEJ stronie ─────────────────────────────────────────


def test_wzorce_trafiaja_w_prawdziwy_tekst_strony() -> None:
    """Wzorce sprawdzane na zapisanym fragmencie, nie na żywej stronie.

    Test zależny od cudzej strony przestaje być testem, a zaczyna być
    monitoringiem — i pada, gdy monday zmieni układ, zamiast gdy MY zepsujemy
    kod.
    """
    tekst = na_tekst(STRONA)
    wyniki = {w.pozycja: wyciagnij(tekst, w) for w in WZORCE}

    assert wyniki["ai_block_kredyty"] is not None
    assert wyniki["ai_block_kredyty"][0] == 8.0
    assert wyniki["ai_notetaker_kredyty_godzina"][0] == 120.0  # type: ignore[index]


def test_wzorzec_agenta_nie_lapie_tabeli_sidekicka() -> None:
    """DWIE tabele o identycznym kształcie zdania — to była realna pomyłka.

    Strona ma widełki agentów (10–50/50–150/150–250/250+) i sidekicka
    (10–30/30–80/80–150/150+). Wzorzec bez kotwicy w słowie `agents` łapie
    pierwszą napotkaną. Wykryte przez `surowy_fragment` przy pierwszym
    uruchomieniu scrapera.
    """
    tekst = na_tekst(STRONA)

    minimum = wyciagnij(tekst, next(w for w in WZORCE if w.pozycja == "agent_run_kredyty_min"))
    maksimum = wyciagnij(tekst, next(w for w in WZORCE if w.pozycja == "agent_run_kredyty_max"))

    assert minimum is not None and minimum[0] == 10.0
    assert maksimum is not None and maksimum[0] == 250.0, "250 to agenci, 150 to sidekick"
    # Cytat musi dowodzić, że trafiliśmy w tabelę AGENTÓW.
    assert "agents" in minimum[1].lower()
    assert "agent run" in maksimum[1].lower()


def test_nietrafiony_wzorzec_nie_nadpisuje_dobrej_wartosci(con: sqlite3.Connection) -> None:
    """Cichy zapis śmiecia jest groźniejszy od braku odświeżenia."""
    zapisz_stawke(
        con,
        pozycja="ai_block_kredyty",
        wartosc=8.0,
        jednostka="akcja",
        sposob="scraper",
        wiarygodnosc="zrodlo_pierwotne",
    )
    wzorzec = next(w for w in WZORCE if w.pozycja == "ai_block_kredyty")

    # Strona bez tego zdania — wzorzec nie trafia.
    assert wyciagnij(na_tekst("<p>Zupełnie inna treść.</p>"), wzorzec) is None

    # Dobra wartość nietknięta, bo `odswiez` nie zapisuje przy braku trafienia.
    stawka = aktualna(con, "ai_block_kredyty")
    assert stawka is not None and stawka.wartosc == 8.0


def test_skrypty_nie_wchodza_do_tekstu() -> None:
    """`<script>var cena = "$9"` nie może stać się źródłem stawki."""
    tekst = na_tekst(STRONA)

    assert "var cena" not in tekst
    assert "$9" not in tekst


def test_cytat_towarzyszy_kazdej_wartosci() -> None:
    """Bez cytatu nie da się odróżnić „zmieniła się cena" od „zmienił się HTML"."""
    tekst = na_tekst(STRONA)

    for wzorzec in WZORCE:
        wynik = wyciagnij(tekst, wzorzec)
        assert wynik is not None, wzorzec.pozycja
        _, cytat = wynik
        assert len(cytat) > 20, f"{wzorzec.pozycja}: cytat za krótki, by cokolwiek sprawdzić"


def test_scraper_nie_dotyka_stawek_klienta() -> None:
    """Cena Enterprise jest negocjowana — scraper nie ma prawa jej podstawić."""
    from monday_audit import cli_cennik

    zrodlo = Path(cli_cennik.__file__).read_text(encoding="utf-8")

    assert "zapisz_stawke_klienta" not in zrodlo, "scraper nie zapisuje stawek per klient"
    assert "stawki_klienta" not in zrodlo.replace("`stawki_klienta`", "")


def test_wszystkie_pozycje_wzorcow_maja_przedzial() -> None:
    """Nowa pozycja bez przedziału to stawka, której nikt nie sprawdza."""
    for wzorzec in WZORCE:
        assert wzorzec.pozycja in PRZEDZIALY, (
            f"{wzorzec.pozycja} nie ma przedziału rozsądku — dopisz go w cennik.PRZEDZIALY"
        )


# ── stawki dla runu i pinowanie (migracja 004) ───────────────────────────


def test_stawki_dla_pomija_brakujace_zamiast_zgadywac(con: sqlite3.Connection) -> None:
    """Brakująca pozycja NIE dostaje wartości zastępczej.

    Nie ma jej w słowniku, więc walidacja kontraktu odrzuci kwotę policzoną
    „na czymkolwiek". Cichy fallback byłby tu groźniejszy od braku kwoty (O7).
    """
    zapisz_stawke(
        con,
        pozycja="ai_block_kredyty",
        wartosc=8,
        jednostka="akcja",
        sposob="reczna",
        wiarygodnosc="zrodlo_pierwotne",
    )

    stawki = stawki_dla(con, {"ai_block_kredyty", "koszt_licencji_mies"})

    assert set(stawki) == {"ai_block_kredyty"}


def test_wersja_uzytych_bierze_najswiezszy_z_uzytych_stawek(con: sqlite3.Connection) -> None:
    """Pin idzie z odczytów, na których run naprawdę liczył."""
    zapisz_stawke(
        con,
        pozycja="ai_block_kredyty",
        wartosc=8,
        jednostka="akcja",
        sposob="reczna",
        wiarygodnosc="zrodlo_pierwotne",
    )
    stawki = stawki_dla(con, {"ai_block_kredyty"})

    assert wersja_uzytych(stawki) == stawki["ai_block_kredyty"].pobrano_at


def test_run_bez_stawek_nie_pinuje_cudzej_daty(con: sqlite3.Connection) -> None:
    """Cennik w bazie JEST, ale ten run z niego nie skorzystał.

    Wpisanie mu `MAX(pobrano_at)` byłoby pinem fałszywym — sugerowałoby, że
    stawki miały wpływ na wynik, a run nie policzył żadnej kwoty.
    """
    zapisz_stawke(
        con,
        pozycja="ai_block_kredyty",
        wartosc=8,
        jednostka="akcja",
        sposob="reczna",
        wiarygodnosc="zrodlo_pierwotne",
    )

    assert wersja_cennika(con) is not None
    assert wersja_uzytych({}) is None
