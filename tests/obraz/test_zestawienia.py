"""Rollupy produktowe i reguła etapów końcowych (plan, faza 5a).

Reguła jest OSĄDEM, nie odczytem, więc testy pilnują przede wszystkim tego,
żeby jej cena była widoczna: nierozpoznany etap końcowy zawyża szanse, i musi
być to widać w `etykiety_w_toku`, a nie dopiero w cudzej reklamacji.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from monday_audit.obraz.zestawienia import (
    PRODUKT_CRM,
    PRODUKT_SERVICE,
    sklasyfikuj,
    zbuduj_zestawienia,
    znormalizuj,
)


@dataclass
class Lejek:
    """Atrapa `itemy.Lejek`. `stopien=1` znaczy „rozkład po ETAPACH"."""

    stopien: int = 1
    etapy_koncowe: frozenset[str] = frozenset()


@dataclass
class Agregat:
    """Atrapa `AgregatTablicy` — tylko pola, których dotyka ta warstwa."""

    produkt: str | None = PRODUKT_CRM
    itemow: int = 0
    rozklad: dict[str, int] = field(default_factory=dict)
    przyrost_dzienny: float = 0.0
    lejek: Lejek = field(default_factory=Lejek)


# ── normalizacja i słownik ───────────────────────────────────────────────


def test_ten_sam_etap_zapisany_na_trzy_sposoby() -> None:
    """Klient zapisze `Closed-Won`, `CLOSED WON` i `closed  won` — to jeden etap."""
    assert znormalizuj("Closed-Won") == znormalizuj("CLOSED WON") == znormalizuj("closed  won")


def test_wygrana_i_przegrana_sa_koncowe() -> None:
    assert sklasyfikuj("Won") == "wygrane"
    assert sklasyfikuj("Closed Lost") == "odpadlo"
    assert sklasyfikuj("Unqualified") == "odpadlo"


def test_nierozpoznany_etap_liczy_sie_jako_w_toku() -> None:
    """Sedno reguły. Słownictwo środka lejka jest dowolne („Po demo",
    „Do oddzwonienia"), więc rozpoznajemy końce, a nie otwarte."""
    assert sklasyfikuj("Po demo") == "w_toku"
    assert sklasyfikuj("Contacted") == "w_toku"


def test_podciag_nie_wystarcza() -> None:
    """Ta sama lekcja co w `zredaguj_pii`: „won" w środku słowa to nie wygrana."""
    assert sklasyfikuj("Wonderful Leads") == "w_toku"


def test_zamkniety_ticket_to_nie_wygrana() -> None:
    """`resolved` nie jest ani wygraną, ani przegraną — jest zamknięciem
    i tylko to znaczy. Wrzucenie go do wygranych robiłoby ze wsparcia
    technicznego dział sprzedaży."""
    assert sklasyfikuj("Resolved") == "zamkniete"


def test_etykieta_wielosłowna_trafia_po_słowie_kluczowym() -> None:
    """Poprawka wymuszona przez dane: `Brand Duplicate` to 123 itemy na
    `Leads e-commerce` i jest odpadem, a pierwsza wersja liczyła go jako
    szansę otwartą, bo porównywała CAŁĄ etykietę ze słownikiem."""
    assert sklasyfikuj("Brand Duplicate") == "odpadlo"
    assert sklasyfikuj("Lead Unqualified") == "odpadlo"


def test_wygrana_bije_zamkniecie_gdy_etykieta_niesie_oba() -> None:
    """`Closed Won` ma słowo zamknięcia i słowo wygranej. Odwrotna kolejność
    zamieniłaby każdą wygraną w bezbarwne „zamknięte" i zabrała raportowi
    jedyną liczbę, która mówi o skuteczności."""
    assert sklasyfikuj("Closed Won") == "wygrane"
    assert sklasyfikuj("Closed Lost") == "odpadlo"


def test_pusty_etap_jest_osobnym_kubelkiem() -> None:
    """Item bez etapu leży POZA lejkiem. Nie jest ani w toku, ani zamknięty —
    i jest to osobny problem, a nie zaokrąglenie."""
    assert sklasyfikuj("(bez etapu)") == "bez_etapu"
    assert sklasyfikuj("") == "bez_etapu"


# ── rollup ───────────────────────────────────────────────────────────────


def test_rozklad_ladnie_sie_rozpada_na_kubelki() -> None:
    """Liczby z prawdziwego przebiegu fazy 3 na `Leads e-commerce`."""
    agregat = Agregat(
        itemow=9922,
        rozklad={"Unqualified": 9445, "Qualified": 354, "Brand Duplicate": 123},
    )

    z = zbuduj_zestawienia([agregat]).po_produkcie[PRODUKT_CRM]

    assert z.odpadlo == 9445 + 123
    assert z.w_toku == 354
    assert z.wygrane == 0


def test_etykiety_w_toku_sa_wypisane_do_kontroli() -> None:
    """Cena reguły: nierozpoznany etap końcowy zawyża szanse. Lista etykiet
    pozwala człowiekowi zobaczyć w dziesięć sekund, że wśród „otwartych"
    siedzi coś, co otwarte nie jest."""
    agregat = Agregat(itemow=10, rozklad={"Qualified": 5, "Do wyrzucenia": 5})

    z = zbuduj_zestawienia([agregat]).po_produkcie[PRODUKT_CRM]

    assert z.etykiety_w_toku == ("Do wyrzucenia", "Qualified")
    assert z.w_toku == 10


def test_reguła_jest_zawsze_w_zastrzezeniach() -> None:
    """Nawet gdy wszystko wyszło czysto. Czytający ma wiedzieć, na czym stoi
    liczba „w toku", a nie dowiadywać się tego tylko przy błędzie."""
    wynik = zbuduj_zestawienia([Agregat(itemow=1, rozklad={"Won": 1})])

    assert any("rozpoznajemy etapy KOŃCOWE" in u for u in wynik.zastrzezenia)


def test_tablica_bez_rozkladu_nie_jest_doliczana_jako_zero() -> None:
    """O47: tablica deklaruje 7076 itemów i nie oddaje żadnego. Dodanie zera
    i pokazanie kompletu byłoby cichym kłamstwem o pokryciu."""
    agregaty = [
        Agregat(itemow=100, rozklad={"Qualified": 100}),
        Agregat(itemow=7076, rozklad={}),
    ]

    wynik = zbuduj_zestawienia(agregaty)
    z = wynik.po_produkcie[PRODUKT_CRM]

    assert z.itemow_deklarowanych == 7176
    assert z.itemow_w_rozkladach == 100
    assert z.itemow_bez_rozkladu == 7076
    assert z.pokrycie < 0.02
    assert any("nie weszło do żadnego kubełka" in u for u in wynik.zastrzezenia)


def test_produkty_bez_wspolnego_znaczenia_etapow_nie_maja_zestawienia() -> None:
    """„Szansa sprzedaży" na tablicy projektowej to pojęcie, którego nikt nie
    obroni. Lepiej go nie produkować, niż produkować z gwiazdką."""
    wynik = zbuduj_zestawienia([Agregat(produkt="software", itemow=500, rozklad={"Done": 500})])

    assert wynik.zestawienia == ()


def test_zamkniecia_dziennie_to_szacunek_z_ilorazu() -> None:
    """Połowa itemów jest w etapach końcowych, przyrost to 4/dzień — więc
    zamknięć szacujemy na 2/dzień. To NIE jest pomiar: `column_values` oddaje
    etap bieżący, nie historię przejść."""
    agregat = Agregat(
        itemow=100,
        rozklad={"Won": 50, "Qualified": 50},
        przyrost_dzienny=4.0,
    )

    z = zbuduj_zestawienia([agregat]).po_produkcie[PRODUKT_CRM]

    assert z.zamkniec_dziennie == 2.0


def test_itemy_bez_etapu_nie_wchodza_do_zamkniec() -> None:
    """Item poza lejkiem nie jest zamknięciem. Gdyby wchodził, tablica
    z pustą kolumną etapu raportowałaby świetną skuteczność."""
    agregat = Agregat(itemow=10, rozklad={"(bez etapu)": 10}, przyrost_dzienny=5.0)

    wynik = zbuduj_zestawienia([agregat])
    z = wynik.po_produkcie[PRODUKT_CRM]

    assert z.bez_etapu == 10
    assert z.zamkniec_dziennie == 0.0
    assert any("POZA lejkiem" in u for u in wynik.zastrzezenia)


# ── deklaracja klienta bije słownik (O48) ────────────────────────────────


def test_etap_zadeklarowany_przez_klienta_jest_koncowy() -> None:
    """ZMIERZONE na tablicy 5095638019: `done_colors: [4]` wskazuje
    `Branding Completed`. Żaden słownik słów tego nie zna i nie ma szans znać —
    ale klient powiedział wprost, że to koniec procesu."""
    assert sklasyfikuj("Branding Completed") == "w_toku"
    assert sklasyfikuj("Branding Completed", frozenset({"Branding Completed"})) == "zamkniete"


def test_deklaracja_nie_zabiera_slownikowi_odpadow() -> None:
    """Sedno hierarchii. `done_colors` w monday znaczy „zakończone POMYŚLNIE",
    więc `Lost` prawie nigdy tam nie trafia. Gdyby deklaracja była jedynym
    źródłem, cała strona odpadów lądowałaby w „w toku" i zestawienie zawyżałoby
    szanse dokładnie tam, gdzie najbardziej boli."""
    koncowe = frozenset({"Branding Completed"})

    assert sklasyfikuj("Closed Lost", koncowe) == "odpadlo"
    assert sklasyfikuj("Closed Won", koncowe) == "wygrane"


def test_deklaracja_porownuje_sie_bez_wzgledu_na_wielkosc_liter() -> None:
    assert sklasyfikuj("branding completed", frozenset({"Branding Completed"})) == "zamkniete"


def test_rollup_bierze_etapy_z_lejka_tablicy() -> None:
    """Wpięcie, nie tylko funkcja obok: zestawienie ma sięgnąć po deklarację
    do lejka TEJ tablicy, a nie liczyć wszystkiego jednym słownikiem."""
    agregat = Agregat(
        itemow=10,
        rozklad={"Branding Completed": 6, "Scheduled": 4},
        lejek=Lejek(etapy_koncowe=frozenset({"Branding Completed"})),
    )

    z = zbuduj_zestawienia([agregat]).po_produkcie[PRODUKT_CRM]

    assert z.zamkniete == 6
    assert z.w_toku == 4
    assert z.etykiety_w_toku == ("Scheduled",)


# ── grupa nie jest etapem ────────────────────────────────────────────────


def test_tablica_bez_lejka_nie_wchodzi_do_etapow() -> None:
    """DEFEKT ZNALEZIONY PRZEZ PEŁNY PRZEBIEG 2026-09-22, nie przez rozumowanie.

    Pierwsza wersja liczyła wszystkie tablice produktu CRM jednakowo i dała
    **223 różne etykiety „otwarte"**, wśród nich `Active Projects` i `Admin
    overview & account setup`. To nazwy GRUP z tablic rozpoznanych stopniem 2,
    nie etapy lejka. `Repozytorium BEGOLDEN` (3158 itemów po grupach) lądowało
    w „otwartych szansach" — liczba wyglądała wiarygodnie i nie znaczyła nic.
    """
    agregaty = [
        Agregat(itemow=100, rozklad={"Qualified": 100}, lejek=Lejek(stopien=1)),
        Agregat(itemow=3158, rozklad={"Umowy_CRM.xlsx": 3155}, lejek=Lejek(stopien=2)),
    ]

    wynik = zbuduj_zestawienia(agregaty)
    z = wynik.po_produkcie[PRODUKT_CRM]

    assert z.w_toku == 100, "itemy z tablicy po grupach nie mogą być szansami"
    assert z.itemow_bez_lejka == 3158
    assert z.tablic_z_lejkiem == 1
    assert z.tablic == 2
    assert "Umowy_CRM.xlsx" not in z.etykiety_w_toku
    assert any("BEZ rozpoznanego lejka" in u for u in wynik.zastrzezenia)


def test_brak_lejka_to_nie_to_samo_co_brak_rozkladu() -> None:
    """Tablica bez lejka nie „zgubiła" rozkładu — ona go nie ma i mieć nie
    miała. Mieszanie tego z O47 raportowałoby brak lejka jako awarię API."""
    agregaty = [
        Agregat(itemow=100, rozklad={"Qualified": 100}, lejek=Lejek(stopien=1)),
        Agregat(itemow=500, rozklad={"Grupa": 500}, lejek=Lejek(stopien=2)),
    ]

    z = zbuduj_zestawienia(agregaty).po_produkcie[PRODUKT_CRM]

    assert z.itemow_bez_rozkladu == 0, "brak lejka to nie brak rozkładu"
    assert z.pokrycie == 1.0, "pokrycie liczy się od tablic Z LEJKIEM"


def test_crm_i_service_licza_sie_osobno() -> None:
    agregaty = [
        Agregat(produkt=PRODUKT_CRM, itemow=10, rozklad={"Qualified": 10}),
        Agregat(produkt=PRODUKT_SERVICE, itemow=20, rozklad={"Resolved": 20}),
    ]

    po = zbuduj_zestawienia(agregaty).po_produkcie

    assert po[PRODUKT_CRM].w_toku == 10
    assert po[PRODUKT_SERVICE].zamkniete == 20
