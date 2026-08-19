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
    # Klasa I obiekt — bez identyfikatora nie wiadomo, co poprawić, gdy zestaw
    # zakazuje czterech tablic z szesnastu.
    assert w.zgloszone_niedopuszczalne == ["PLAN_MISMATCH konto"]


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


def test_warianty_po_pionowej_kresce_wystarczy_jeden() -> None:
    """`a|b|c` znaczy „którekolwiek z trzech", nie „wszystkie trzy".

    ZMIERZONE na runie `ewal-uzytkownicy-s7`: rzeczowość wyszła 0,143, a odczyt
    findingów pokazał, że są rzeczowe — agent pisał „konto typu member" tam,
    gdzie zestaw mówił „zajmuje płatne miejsce". Miara ZANIŻAJĄCA kazałaby
    wydłużać opisy, żeby trafić w moje sformułowania, a cel jest odwrotny.
    Po poprawce: 0,857.
    """
    z: dict[str, Any] = {
        "oczekiwane": [
            {
                "klasa_id": "ZOMBIE_ACCOUNT",
                "obiekt": "abc123",
                "musi_zawierac": ["płatne miejsce|typu member|kind: member"],
            }
        ],
        "niedopuszczalne": [],
        "pominiete": [],
    }
    # Wariant drugi — agent nie użył słów „płatne miejsce", ale fakt podał.
    w = ocen(
        [finding(opis="Konto typu member, status ACTIVE.")],
        z,
        run_id="t",
        nazwa_zestawu="t.yaml",
    )
    assert w.rzeczowosc == 1.0

    # Żaden wariant — fakt faktycznie nieobecny.
    w2 = ocen(
        [finding(opis="Konto nieaktywne od dawna.", dowod={"user_hash": "abc123"})],
        z,
        run_id="t",
        nazwa_zestawu="t.yaml",
    )
    assert w2.rzeczowosc == 0.0


def test_wartosc_pola_dowodu_potwierdza_fakt() -> None:
    """`kind: member` w dowodzie jest twardsze niż jakiekolwiek zdanie agenta.

    To pole ze snapshotu, nie sformułowanie — więc liczy się na równi z tekstem.
    """
    z: dict[str, Any] = {
        "oczekiwane": [
            {"klasa_id": "ZOMBIE_ACCOUNT", "obiekt": "abc123", "musi_zawierac": ["kind: member"]}
        ],
        "niedopuszczalne": [],
        "pominiete": [],
    }
    w = ocen(
        [finding(opis="Nieużywane konto.", dowod={"user_hash": "abc123", "kind": "member"})],
        z,
        run_id="t",
        nazwa_zestawu="t.yaml",
    )
    assert w.rzeczowosc == 1.0


def test_zla_wartosc_pola_dowodu_nie_potwierdza_faktu() -> None:
    """`kind: guest` nie potwierdza faktu o płatnym miejscu — wymagamy OBU."""
    z: dict[str, Any] = {
        "oczekiwane": [
            {"klasa_id": "ZOMBIE_ACCOUNT", "obiekt": "abc123", "musi_zawierac": ["kind: member"]}
        ],
        "niedopuszczalne": [],
        "pominiete": [],
    }
    w = ocen(
        [finding(opis="Nieużywane konto.", dowod={"user_hash": "abc123", "kind": "guest"})],
        z,
        run_id="t",
        nazwa_zestawu="t.yaml",
    )
    assert w.rzeczowosc == 0.0


def test_zakaz_dotyczy_obiektu_nie_calej_klasy() -> None:
    """Zestaw zakazujący jednej tablicy nie czyni fałszywkami findingów o innych.

    ZMIERZONA USTERKA (2026-08-17): pierwsza wersja liczyła fałszywki po KLASIE.
    Zestaw zakazujący 4 tablic `BOARD_OVERCOMPLEX` uznał za fałszywki wszystkie
    12 findingów tej klasy z runu z 11 sierpnia — w tym poprawne, na zupełnie
    innych tablicach. Wyszło 0,444 zamiast 0,083, i to na metryce, która ma
    pierwszeństwo nad trafnością.
    """
    z: dict[str, Any] = {
        "oczekiwane": [],
        "niedopuszczalne": [{"klasa_id": "BOARD_OVERCOMPLEX", "obiekt": "111"}],
        "pominiete": [],
    }
    w = ocen(
        [
            finding(klasa="BOARD_OVERCOMPLEX", obiekt="111", dowod={"board_id": "111"}),
            finding(klasa="BOARD_OVERCOMPLEX", obiekt="222", dowod={"board_id": "222"}),
            finding(klasa="BOARD_OVERCOMPLEX", obiekt="333", dowod={"board_id": "333"}),
        ],
        z,
        run_id="t",
        nazwa_zestawu="t.yaml",
    )
    assert w.falszywek == 1, "tylko tablica 111 jest zakazana"
    assert w.odsetek_falszywek == pytest.approx(1 / 3)
    assert w.zgloszone_niedopuszczalne == ["BOARD_OVERCOMPLEX 111"]


def test_gwiazdka_zakazuje_calej_klasy() -> None:
    """`obiekt: "*"` to jawna decyzja „ta klasa nie ma prawa tu wystąpić".

    Zakaz całoklasowy musi być możliwy, ale jako świadomy wybór — nie skutek
    uboczny wskazania jednego obiektu.
    """
    z: dict[str, Any] = {
        "oczekiwane": [],
        "niedopuszczalne": [{"klasa_id": "PLAN_MISMATCH", "obiekt": "*"}],
        "pominiete": [],
    }
    w = ocen(
        [
            finding(klasa="PLAN_MISMATCH", obiekt="27690228"),
            finding(klasa="PLAN_MISMATCH", obiekt="cokolwiek"),
        ],
        z,
        run_id="t",
        nazwa_zestawu="t.yaml",
    )
    assert w.falszywek == 2


def test_obiekt_z_listy_board_ids_sklada_sie_w_pare() -> None:
    """`DUPLICATE_STRUCTURE` opisuje RELACJĘ, więc jego dowód niesie listę.

    ZMIERZONA USTERKA: miernik czytał tylko `board_id` w liczbie pojedynczej, więc
    wszystkim findingom tej klasy przypisywał „konto" i trafność wyszłaby 0,0 przy
    dobrym runie. Sortowanie jest konieczne — agent może wypisać identyfikatory
    w innej kolejności niż detektor, a „a+b" i „b+a" to różne napisy.
    """
    from mierz import _obiekt_findingu

    assert _obiekt_findingu({"board_ids": ["222", "111"]}) == "111+222"
    assert _obiekt_findingu({"board_id": "111"}) == "111"
    assert _obiekt_findingu({"user_hash": "abc"}) == "abc"
    assert _obiekt_findingu({}) == "konto"


def test_pusty_zestaw_oczekiwanych_to_trafnosc_jeden() -> None:
    """„Nie ma czego znaleźć" nie jest tym samym co „nie znalazł".

    ZMIERZONE na runie `wygaszeni-s7`: agent odrzucił obie hipotezy
    `UZYTKOWNIK_WYGASZONY` i po sprawdzeniu danych oba odrzucenia były trafne —
    jedna osoba to zamknięcie projektu na wszystkich jej tablicach, druga to
    przekazanie obowiązków. Zestaw ma tam pustą sekcję `oczekiwane`, więc trafność
    0,000 mówiłaby o wadzie tam, gdzie jej nie ma.
    """
    pusty: dict[str, Any] = {"oczekiwane": [], "niedopuszczalne": [], "pominiete": []}
    w = ocen([], pusty, run_id="t", nazwa_zestawu="t.yaml")

    assert w.trafnosc == 1.0
    assert w.rzeczowosc == 1.0
    assert w.progi_spelnione["trafnosc"] is True


def test_puste_oczekiwane_ale_falszywka_nadal_boli() -> None:
    """Brak oczekiwanych NIE rozbraja sekcji `niedopuszczalne`.

    Gdyby rozbrajał, zestaw złożony z samych zakazów przepuszczałby wszystko —
    a to jest właśnie ten kształt zestawu, który pilnuje regresji detektora.
    """
    z: dict[str, Any] = {
        "oczekiwane": [],
        "niedopuszczalne": [{"klasa_id": "UZYTKOWNIK_WYGASZONY", "obiekt": "agent1"}],
        "pominiete": [],
    }
    w = ocen(
        [finding(klasa="UZYTKOWNIK_WYGASZONY", obiekt="agent1")],
        z,
        run_id="t",
        nazwa_zestawu="t.yaml",
    )

    assert w.trafnosc == 1.0, "brak oczekiwanych"
    assert w.falszywek == 1
    assert w.progi_spelnione["falszywki"] is False


def test_brak_trafionych_przy_niepustych_oczekiwanych_to_zero() -> None:
    """Rozróżnienie od testu wyżej: tu przeoczenie jest REALNE."""
    w = wynik([])

    assert w.trafnosc == 0.0
    assert w.rzeczowosc == 0.0


# ── powtarzalność (próg z 04-test.md: ≥0,8) ──────────────────────────────


def _con_z_runami() -> Any:
    """Baza w pamięci z dwiema tabelami, których dotyka `zmierz_powtarzalnosc`."""
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE findings (run_id TEXT, klasa_id TEXT, dowod TEXT)")
    con.execute("CREATE TABLE hipotezy_odrzucone (run_id TEXT, klasa_id TEXT, obiekt_id TEXT)")
    # Findingi ODRZUCONE PRZEZ WALIDACJĘ — miernik czyta i tę tabelę, bo agent je
    # ORZEKŁ. Kolumna nazywa się `finding` (całość), nie `dowod`, jak w produkcji.
    con.execute("CREATE TABLE findings_odrzucone (run_id TEXT, klasa_id TEXT, finding TEXT)")
    return con


def _finding_w(con: Any, run: str, klasa: str, obiekt: str) -> None:
    con.execute(
        "INSERT INTO findings (run_id, klasa_id, dowod) VALUES (?, ?, ?)",
        (run, klasa, f'{{"user_hash": "{obiekt}"}}'),
    )


def _odrzucona_w(con: Any, run: str, klasa: str, obiekt: str) -> None:
    con.execute(
        "INSERT INTO hipotezy_odrzucone (run_id, klasa_id, obiekt_id) VALUES (?, ?, ?)",
        (run, klasa, obiekt),
    )


def test_identyczne_runy_daja_zgodnosc_jeden() -> None:
    """Dwa runy orzekające to samo o każdej hipotezie."""
    from mierz import zmierz_powtarzalnosc

    con = _con_z_runami()
    for run in ("a", "b"):
        _finding_w(con, run, "ZOMBIE_ACCOUNT", "h1")
        _finding_w(con, run, "ZOMBIE_ACCOUNT", "h2")
        _odrzucona_w(con, run, "ZOMBIE_ACCOUNT", "h3")

    w = zmierz_powtarzalnosc(con, "a", "b")

    assert w.hipotez_wspolnych == 3
    assert w.zgodnosc == 1.0
    assert w.prog_spelniony is True
    assert w.rozbieznosci == ()


def test_rozne_rozstrzygniecie_obniza_zgodnosc() -> None:
    """Ta sama hipoteza, raz finding, raz odrzucenie — to jest niestabilność.

    Dokładnie to, o co pyta próg: „niżej = prompt zbyt luźny". Agent, który dwa
    razy rozstrzyga tę samą hipotezę inaczej, nie ma jasnego kryterium.
    """
    from mierz import zmierz_powtarzalnosc

    con = _con_z_runami()
    _finding_w(con, "a", "ZOMBIE_ACCOUNT", "h1")
    _finding_w(con, "b", "ZOMBIE_ACCOUNT", "h1")
    _finding_w(con, "a", "ZOMBIE_ACCOUNT", "h2")
    _odrzucona_w(con, "b", "ZOMBIE_ACCOUNT", "h2")  # ← rozbieżność

    w = zmierz_powtarzalnosc(con, "a", "b")

    assert w.hipotez_wspolnych == 2
    assert w.zgodnosc == 0.5
    assert w.prog_spelniony is False
    assert w.rozbieznosci == (("ZOMBIE_ACCOUNT h2", "finding", "odrzucona"),)


def test_hipoteza_tylko_w_jednym_runie_nie_liczy_sie_do_zgodnosci() -> None:
    """Różny ZAKRES to nie różnica rozstrzygnięcia.

    Run z `--na-klase 2` i run pełny mają inne zbiory hipotez. Liczenie tego jako
    niezgodności mierzyłoby dobór próbki, nie stabilność agenta — ale trzeba to
    RAPORTOWAĆ, bo inaczej porównanie jest o czymś innym, niż się wydaje.
    """
    from mierz import zmierz_powtarzalnosc

    con = _con_z_runami()
    _finding_w(con, "a", "ZOMBIE_ACCOUNT", "h1")
    _finding_w(con, "b", "ZOMBIE_ACCOUNT", "h1")
    _finding_w(con, "a", "ZOMBIE_ACCOUNT", "tylko-a")
    _finding_w(con, "b", "ZOMBIE_ACCOUNT", "tylko-b")

    w = zmierz_powtarzalnosc(con, "a", "b")

    assert w.hipotez_wspolnych == 1
    assert w.zgodnosc == 1.0, "wspólna hipoteza rozstrzygnięta tak samo"
    assert w.tylko_w_a == ("ZOMBIE_ACCOUNT tylko-a",)
    assert w.tylko_w_b == ("ZOMBIE_ACCOUNT tylko-b",)


def test_zgodnosc_liczy_rozstrzygniecia_nie_tresc_opisow() -> None:
    """Model nie jest deterministyczny — dwa opisy tej samej rzeczy zawsze różnią
    się słowami.

    Miara oparta na tekście mierzyłaby temperaturę próbkowania, nie stabilność
    ROZUMOWANIA. Ten test przypina tę decyzję: findingi o różnej treści, ale tym
    samym rozstrzygnięciu, są ZGODNE.
    """
    from mierz import zmierz_powtarzalnosc

    con = _con_z_runami()
    con.execute(
        "INSERT INTO findings (run_id, klasa_id, dowod) VALUES "
        '(\'a\', \'ZOMBIE_ACCOUNT\', \'{"user_hash": "h1", "kind": "member"}\')'
    )
    con.execute(
        "INSERT INTO findings (run_id, klasa_id, dowod) VALUES "
        '(\'b\', \'ZOMBIE_ACCOUNT\', \'{"user_hash": "h1", "kind": "member", "extra": 1}\')'
    )

    assert zmierz_powtarzalnosc(con, "a", "b").zgodnosc == 1.0


def test_prog_powtarzalnosci_jest_stala_z_specyfikacji() -> None:
    """0,8 z tabeli progów w `docs/etapy/04-test.md`."""
    from mierz import PROG_POWTARZALNOSCI

    assert PROG_POWTARZALNOSCI == 0.8


def test_finding_odrzucony_walidacja_liczy_sie_jako_finding() -> None:
    """Agent go ORZEKŁ — to, że kontrakt go odrzucił, nie jest niestabilnością agenta.

    ZMIERZONA USTERKA (2026-08-19): pierwsza wersja czytała tylko `findings`, więc
    finding odrzucony przez walidację wyglądał jak „brak" i liczył się jako
    `odrzucona` — czyli NIEZGODNOŚĆ z runem, w którym ten sam finding przeszedł.

    Konkretny przypadek: `BOARD_OVERCOMPLEX 5093573347` — run A zgłosił, walidacja
    odrzuciła za nazwę licznika wpisów, run B zgłosił i przeszedł. Agent był
    stabilny, rozjechał się kontrakt. Miara mierzyła NASZĄ walidację.

    Ta usterka ZAWYŻAŁA wynik (0,808 zamiast 0,797), czyli pokazała liczbę
    korzystną — i dlatego jest osobnym testem.
    """
    from mierz import zmierz_powtarzalnosc

    con = _con_z_runami()
    # Run „a": finding przeszedł walidację.
    _finding_w(con, "a", "BOARD_GHOST", "b1")
    # Run „b": ten sam finding, ale walidacja go odrzuciła.
    con.execute(
        "INSERT INTO findings_odrzucone (run_id, klasa_id, finding) VALUES "
        "('b', 'BOARD_GHOST', '{\"dowod\": {\"board_id\": \"b1\"}}')"
    )

    w = zmierz_powtarzalnosc(con, "a", "b")

    assert w.hipotez_wspolnych == 1
    assert w.zgodnosc == 1.0, "oba runy ORZEKŁY finding"
    assert w.rozbieznosci == ()
