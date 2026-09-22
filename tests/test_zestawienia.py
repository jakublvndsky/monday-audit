"""Rollupy produktowe i reguła etapów końcowych (plan, faza 5a).

Reguła jest OSĄDEM, nie odczytem, więc testy pilnują przede wszystkim tego,
żeby jej cena była widoczna: nierozpoznany etap końcowy zawyża szanse, i musi
być to widać w `etykiety_w_toku`, a nie dopiero w cudzej reklamacji.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from monday_audit.zestawienia import (
    PRODUKT_CRM,
    PRODUKT_SERVICE,
    sklasyfikuj,
    zbuduj_zestawienia,
    znormalizuj,
)


@dataclass
class Agregat:
    """Atrapa `AgregatTablicy` — tylko pola, których dotyka ta warstwa."""

    produkt: str | None = PRODUKT_CRM
    itemow: int = 0
    rozklad: dict[str, int] = field(default_factory=dict)
    przyrost_dzienny: float = 0.0


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


def test_crm_i_service_licza_sie_osobno() -> None:
    agregaty = [
        Agregat(produkt=PRODUKT_CRM, itemow=10, rozklad={"Qualified": 10}),
        Agregat(produkt=PRODUKT_SERVICE, itemow=20, rozklad={"Resolved": 20}),
    ]

    po = zbuduj_zestawienia(agregaty).po_produkcie

    assert po[PRODUKT_CRM].w_toku == 10
    assert po[PRODUKT_SERVICE].zamkniete == 20
