"""Testy miernika jakości.

Miernik jest narzędziem, którym oceniamy agenta — więc sam musi być sprawdzony,
i to na przypadkach, w których łatwo się pomylić. Najgroźniejszy błąd to metryka
zawyżająca: taka, która pokazuje 1,0, bo czegoś nie sprawdziła.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from mierz import Finding, ocen


def finding(
    klasa: str = "ZOMBIE_ACCOUNT",
    obiekt: str = "abc123",
    opis: str = "",
    rekomendacja: str = "",
    dowod: dict[str, Any] | None = None,
) -> Finding:
    return Finding(
        klasa_id=klasa,
        obiekt=obiekt,
        opis=opis,
        rekomendacja=rekomendacja,
        dowod=dowod if dowod is not None else {"user_hash": obiekt},
    )


def zestaw(**nadpisz: Any) -> dict[str, Any]:
    baza: dict[str, Any] = {
        "oczekiwane": [
            {
                "klasa_id": "ZOMBIE_ACCOUNT",
                "obiekt": "abc123",
                "musi_zawierac": [
                    "liczba dni bez aktywności (co najmniej 285)",
                    "że konto zajmuje płatne miejsce (kind member albo admin)",
                ],
                "nie_powinno_zawierac": [
                    "spekulacja o przyczynie nieobecności",
                    "jakiekolwiek imię, nazwisko albo adres e-mail",
                ],
            }
        ],
        "niedopuszczalne": [{"klasa_id": "PLAN_MISMATCH", "obiekt": "konto"}],
        "pominiete": [],
    }
    baza.update(nadpisz)
    return baza


def wynik(findingi: list[Finding], **kw: Any) -> Any:
    return ocen(findingi, zestaw(**kw), run_id="test", nazwa_zestawu="test.yaml")


def test_trafienie_z_wszystkimi_faktami_jest_rzeczowe() -> None:
    w = wynik(
        [
            finding(
                opis="Konto member bez aktywności od 290 dni, zajmuje płatne miejsce.",
                dowod={"user_hash": "abc123", "kind": "member"},
            )
        ]
    )
    assert w.trafionych == 1
    assert w.trafnosc == 1.0
    assert w.rzeczowosc == 1.0
    assert w.pozycje[0].brakujace_fakty == []


def test_liczba_ponizej_progu_to_brak_faktu() -> None:
    """„co najmniej 285" znaczy ≥285. 100 dni nie spełnia tego faktu.

    To pilnuje najcichszej usterki: finding trafiony w obiekt, ale z liczbą
    z sufitu. Bez tego testu metryka przepuszczałaby zmyśloną wartość.
    """
    w = wynik([finding(opis="Konto member bez aktywności od 100 dni, zajmuje płatne miejsce.")])
    assert w.trafionych == 1
    assert w.rzeczowosc == 0.0
    assert any("285" in f for f in w.pozycje[0].brakujace_fakty)


def test_liczba_wyzsza_niz_prog_przechodzi() -> None:
    """Cisza rośnie z każdym dniem — finding policzony później podaje więcej dni."""
    w = wynik([finding(opis="Konto member bez aktywności od 400 dni, zajmuje płatne miejsce.")])
    assert w.rzeczowosc == 1.0


def test_mail_w_opisie_to_przeciek() -> None:
    """Granica PII. Jedyny zakaz sprawdzany wzorcem, bo jedyny tak sprawdzalny."""
    w = wynik(
        [
            finding(
                opis="Konto member bez aktywności od 290 dni, zajmuje płatne miejsce.",
                rekomendacja="Skontaktować się z jan.kowalski@firma.pl",
            )
        ]
    )
    assert w.trafionych == 1
    assert w.rzeczowosc == 0.0
    assert w.pozycje[0].przeciekle


def test_spekulacja_o_przyczynie_to_przeciek() -> None:
    w = wynik(
        [
            finding(
                opis="Konto member bez aktywności od 290 dni, zajmuje płatne miejsce.",
                rekomendacja="Osoba odeszła z firmy, usunąć konto.",
            )
        ]
    )
    assert w.rzeczowosc == 0.0


def test_finding_w_klasie_niedopuszczalnej_to_falszywka() -> None:
    """Sedno sekcji `niedopuszczalne` — bez tego zestaw tylko potwierdzałby agenta."""
    w = wynik([finding(klasa="PLAN_MISMATCH", obiekt="konto", opis="Nadpłata 10 miejsc.")])
    assert w.falszywek == 1
    assert w.odsetek_falszywek == 1.0
    assert w.progi_spelnione["falszywki"] is False
    assert w.zgloszone_niedopuszczalne == ["PLAN_MISMATCH"]


def test_klasa_poza_zestawem_nie_jest_falszywka() -> None:
    """Brak wpisu w zestawie znaczy „nie wiem", nie „błąd".

    Gdyby liczyć te findingi jako fałszywki, run badający klasy o tablicach
    dostałby 1,0 fałszywek przy zestawie o użytkownikach — czyli metryka
    karałaby za zakres, nie za jakość.
    """
    w = wynik([finding(klasa="BOARD_GHOST", obiekt="555")])
    assert w.falszywek == 0
    assert w.poza_zestawem == {"BOARD_GHOST": 1}


def test_nieznalezione_obniza_trafnosc() -> None:
    w = wynik([])
    assert w.trafnosc == 0.0
    assert w.progi_spelnione["trafnosc"] is False
    assert w.pozycje[0].znalezione is False


def test_zla_klasa_na_tym_samym_obiekcie_nie_jest_trafieniem() -> None:
    """Dopasowanie po PARZE (klasa, obiekt). Ten sam hash w innej klasie to nie to."""
    w = wynik([finding(klasa="ENGAGEMENT_DROP", obiekt="abc123")])
    assert w.trafionych == 0


def test_probka_zawezona_daje_trafnosc_w_zasiegu() -> None:
    """Run, który dostał 1 hipotezę z 3 pozycji, nie przegapił dwóch — nie widział ich.

    ZMIERZONE na `ewal-4klasy`: trafność 0,250 przy 2 trafieniach z 2 hipotez,
    czyli 1,0 w zasięgu. Bez tego rozróżnienia raport pokazywałby „poniżej progu"
    dla runu, który nie pomylił się ani raz.
    """
    trzy: dict[str, Any] = {
        "oczekiwane": [
            {"klasa_id": "ZOMBIE_ACCOUNT", "obiekt": o, "musi_zawierac": []}
            for o in ("a", "b", "c")
        ],
        "niedopuszczalne": [],
        "pominiete": [],
    }
    w = ocen(
        [finding(obiekt="a")],
        trzy,
        run_id="test",
        nazwa_zestawu="t.yaml",
        na_klase={"ZOMBIE_ACCOUNT": 1},
    )
    assert w.trafnosc == pytest.approx(1 / 3)
    assert w.osiagalna_trafnosc == pytest.approx(1 / 3)
    assert w.trafnosc_w_zasiegu == 1.0


def test_bez_danych_o_probce_nie_zglaszamy_zastrzezenia() -> None:
    """Puste `hipotez_na_klase` = „nie wiem, ile było próbki", nie „zero hipotez".

    Runy starsze niż migracja 010 nie mają wierszy w `zuzycie_hipotez`. Gdyby
    pusty słownik znaczył zero, `osiagalna_trafnosc` wyszłaby 0 i dzielenie
    dałoby 0,0 — czyli metryka twierdziłaby, że run nie mógł nic trafić.
    """
    w = wynik([finding(opis="290 dni, member, płatne miejsce")])
    assert w.osiagalna_trafnosc == 1.0
    assert w.trafnosc_w_zasiegu == w.trafnosc


def test_normalizacja_radzi_sobie_z_l_kreskowanym() -> None:
    """`ł` to jedyna polska litera, której NFKD nie rozkłada — U+0142, nie diakrytyk.

    ZMIERZONA USTERKA: bez osobnej podmiany „osoba odeszła" nie łapała się na
    wzorzec `osoba odeszla` i metryka raportowała „bez przecieku" dla findingu,
    który spekulował wprost. Miara zawyżająca — w narzędziu do oceny jakości
    najgroźniejszy rodzaj błędu, bo wygląda jak dobry wynik.
    """
    from mierz import _bez_ogonkow

    assert _bez_ogonkow("ĄĆĘŁŃÓŚŹŻ ałę") == "acelnoszz ale"
