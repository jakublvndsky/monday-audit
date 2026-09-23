"""Maskowanie PII przed wysyłką poza serwer (plan, faza 4).

Testy dzielą się na trzy grupy i każda pilnuje czegoś innego:

- **co maskujemy** — że wzorzec w ogóle trafia,
- **czego NIE maskujemy** — że nie niszczy trace'u fałszywym trafieniem,
- **że zawodzi zamknięte** — bo to jedyny wymóg tej fazy, którego naruszenie
  jest nieodwracalne.

Druga grupa jest tu ważniejsza od pierwszej. Maskowanie, które zjada
identyfikatory monday, zostanie wyłączone przez pierwszego człowieka, który
spróbuje odczytać trace — a wtedy nie maskuje już nic.
"""

from __future__ import annotations

import pytest

from monday_audit.maskowanie import (
    MaskowanieError,
    zamaskuj,
    zamaskuj_tekst,
)
from monday_audit.osoby import WpisPII

# ── co maskujemy ─────────────────────────────────────────────────────────


def test_mail_leada_staje_sie_bez_tozsamosci() -> None:
    """Lead to klient naszego klienta — nie mamy go w tabeli mapowania, więc
    dostaje zamiennik BEZ hasza. Hasz sugerowałby tożsamość, której nie ma."""
    wynik, trafienia = zamaskuj_tekst("kontakt: nowy.lead@firma-klienta.test")

    assert wynik == "kontakt: [E-MAIL]"
    assert trafienia["email"] == 1


def test_dwa_rozne_maile_daja_ten_sam_zamiennik() -> None:
    """Świadoma strata informacji: bez tabeli mapowania nie odróżnimy leadów,
    a udawanie, że odróżniamy, byłoby gorsze niż przyznanie się do tego."""
    wynik, trafienia = zamaskuj_tekst("a@x.test oraz b@y.test")

    assert wynik == "[E-MAIL] oraz [E-MAIL]"
    assert trafienia["email"] == 2


@pytest.mark.parametrize(
    "numer",
    [
        "+48 501 234 567",
        "+48501234567",
        "501-234-567",
        "501 234 567",
        "22 123 45 67",
    ],
)
def test_telefon_w_ludzkich_zapisach(numer: str) -> None:
    wynik, trafienia = zamaskuj_tekst(f"tel. {numer}")

    assert wynik == "tel. [TELEFON]"
    assert trafienia["telefon"] == 1


def test_iban_przed_telefonem() -> None:
    """IBAN to też cyfry w grupach. Gdyby telefon szedł pierwszy, zjadłby ogon
    numeru konta i zostawił początek — czyli zamaskowałby go w połowie."""
    wynik, trafienia = zamaskuj_tekst("przelew na PL61 1090 1014 0000 0712 1981 2874")

    assert wynik == "przelew na [IBAN]"
    assert trafienia["iban"] == 1
    assert "telefon" not in trafienia


# ── czego NIE maskujemy ──────────────────────────────────────────────────


def test_identyfikator_monday_zostaje_nietkniety() -> None:
    """NAJWAŻNIEJSZY test w tym pliku. Identyfikatory monday to gołe ciągi
    9-10 cyfr. Wzorzec telefonu, który je łapie, robi z trace'u makulaturę:
    nie da się wskazać ani tablicy, ani itemu."""
    tekst = "board 9445123456, item 1234567890"

    wynik, trafienia = zamaskuj_tekst(tekst)

    assert wynik == tekst
    assert not trafienia


def test_data_i_znacznik_czasu_zostaja() -> None:
    """Pułapka z pierwszej wersji wzorca: `2026-09-22 12` to osiem cyfr
    w dwóch grupach i wpadało w regułę „cyfry z separatorami"."""
    tekst = "utworzono 2026-09-22 12:00, zmieniono 2026-09-22T14:30:00Z"

    wynik, trafienia = zamaskuj_tekst(tekst)

    assert wynik == tekst
    assert not trafienia


def test_adres_ip_zostaje() -> None:
    """Powód, dla którego kropka NIE jest separatorem telefonu: `192.168.100.101`
    to dwanaście cyfr w czterech grupach. Adres IP w trace jest bardziej
    prawdopodobny niż telefon pisany kropkami."""
    tekst = "worker 192.168.100.101 odpowiedział"

    wynik, trafienia = zamaskuj_tekst(tekst)

    assert wynik == tekst
    assert not trafienia


def test_liczby_z_audytu_zostaja() -> None:
    """Agregaty to cały sens trace'u. 1109 wywołań i 7076 itemów mają przejść."""
    tekst = "1109 wywołań, 7076 itemów, 954 tablice, wersja 2026-07"

    wynik, trafienia = zamaskuj_tekst(tekst)

    assert wynik == tekst
    assert not trafienia


# ── znani ludzie idą inną ścieżką niż nieznani ───────────────────────────


def test_uzytkownik_konta_dostaje_pseudonim_a_nie_zamiennik() -> None:
    """Kolejność przebiegów jest tu całą treścią testu. Gdyby wzorce szły
    pierwsze, mail użytkownika konta zmieniłby się w `[E-MAIL]` i zniknęłaby
    informacja, że w dwóch polach występuje TA SAMA osoba."""
    wpisy = [WpisPII("a1b2c3", "Zdzisława Wąchockańska", "zdzislawa@klient.test")]
    dane = {"autor": "zdzislawa@klient.test", "wlasciciel": "Zdzisława Wąchockańska"}

    wynik = zamaskuj(dane, wpisy)

    assert wynik.dane["autor"] == "[EMAIL:a1b2c3]"
    assert wynik.dane["wlasciciel"] == "[OSOBA:a1b2c3]"
    assert not wynik.trafienia


def test_lead_i_uzytkownik_obok_siebie() -> None:
    """Jedno pole, dwa źródła PII i dwie różne odpowiedzi."""
    wpisy = [WpisPII("a1b2c3", "Zdzisława Wąchockańska", "zdzislawa@klient.test")]

    wynik = zamaskuj({"notatka": "zdzislawa@klient.test pisze do lead@obcy.test"}, wpisy)

    assert wynik.dane["notatka"] == "[EMAIL:a1b2c3] pisze do [E-MAIL]"
    assert wynik.trafienia["email"] == 1


# ── liczniki i ścieżki ───────────────────────────────────────────────────


def test_trafienia_niosa_kategorie_a_sciezki_miejsce() -> None:
    dane = {"lead": {"kontakt": "x@y.test"}, "notatki": ["tel. +48 501 234 567"]}

    wynik = zamaskuj(dane)

    assert wynik.trafienia == {"email": 1, "telefon": 1}
    assert set(wynik.sciezki) == {"lead.kontakt", "notatki[0]"}


def test_ani_licznik_ani_sciezki_nie_niosa_wartosci() -> None:
    """Raport o wycieku PII, który sam wpisuje PII, nie jest zabezpieczeniem."""
    wynik = zamaskuj({"pole": "tajny.adres@klient.test"})

    assert "tajny.adres" not in wynik.podsumowanie()
    assert not any("tajny.adres" in s for s in wynik.sciezki)


def test_czysty_payload_mowi_ze_warstwa_wyzej_zadzialala() -> None:
    wynik = zamaskuj({"tablic": 954, "wywolan": 1109})

    assert wynik.czyste
    assert wynik.podsumowanie() == "maskowanie: czysto"


def test_struktura_przechodzi_na_wylot() -> None:
    dane = {"a": [1, True, None, {"b": 2.5}]}

    assert zamaskuj(dane).dane == dane


# ── zawodzi zamknięte ────────────────────────────────────────────────────


def test_nieznany_typ_przerywa_zamiast_przepuscic() -> None:
    """Wymóg twardy fazy 4. Obiekt, którego nie rozumiemy, mógłby mieć
    `__str__` wypisujący cokolwiek — maskowanie po `str()` na oślep jest
    zgadywaniem, a zgadywanie po stronie wysyłki do firmy trzeciej jest
    wysyłką."""

    class Cos:
        pass

    with pytest.raises(MaskowanieError) as blad:
        zamaskuj({"payload": Cos()})

    assert "payload" in str(blad.value)
    assert "Cos" in str(blad.value)


def test_komunikat_bledu_nie_wypisuje_wartosci() -> None:
    class Tajne:
        def __str__(self) -> str:  # pragma: no cover - nie powinno być wołane
            return "zdzislawa@klient.test"

    with pytest.raises(MaskowanieError) as blad:
        zamaskuj({"pole": Tajne()})

    assert "zdzislawa" not in str(blad.value)


# ── klucze słowników są treścią (review 2026-09-23) ──────────────────────
#
# Pierwsza wersja maskowała wyłącznie wartości. Nazwa grupy i etykieta etapu
# siedzą w `rozklad` jako KLUCZE, komunikat monday w `powody_bledow` też —
# i wychodziły bez zmian, a licznik mówił „czysto".


def test_mail_i_telefon_w_kluczu_sa_maskowane_i_liczone() -> None:
    dane = {
        "dowod": {
            "powody_bledow": {
                "Nie można przypisać jan.kowalski@firma.test do kolumny Osoba": 1,
                "tel +48 501 234 567 odrzucony": 2,
            }
        }
    }

    wynik = zamaskuj(dane)

    powody = wynik.dane["dowod"]["powody_bledow"]
    assert powody == {
        "Nie można przypisać [E-MAIL] do kolumny Osoba": 1,
        "tel [TELEFON] odrzucony": 2,
    }
    assert wynik.trafienia == {"email": 1, "telefon": 1}
    assert not wynik.czyste


def test_sciezka_trafienia_w_kluczu_nie_niesie_wartosci() -> None:
    """Ścieżka składana z SUROWEGO klucza byłaby wyciekiem w raporcie o wycieku."""
    wynik = zamaskuj({"rozklad": {"Leady od tajny@klient.test": 12}})

    assert wynik.sciezki == ("rozklad.Leady od [E-MAIL] (klucz)",)
    assert not any("tajny" in s for s in wynik.sciezki)


def test_znana_osoba_w_kluczu_dostaje_pseudonim() -> None:
    dane = {"rozklad": {"Zdzisława Wąchockańska": 20, "Nowe": 3}}

    wynik = zamaskuj(dane, [WpisPII("abc", "Zdzisława Wąchockańska", None)])

    assert wynik.dane == {"rozklad": {"[OSOBA:abc]": 20, "Nowe": 3}}


def test_sklejone_klucze_nie_gubia_wartosci() -> None:
    """Dwa maile dają dwa razy `[E-MAIL]`. Nadpisanie zgubiłoby liczbę z
    rozkładu, na którą model potem wskaże."""
    wynik = zamaskuj({"rozklad": {"a@x.test": 12, "b@y.test": 8, "[E-MAIL]": 1}})

    rozklad = wynik.dane["rozklad"]
    assert sorted(rozklad.values()) == [1, 8, 12]
    assert set(rozklad) == {"[E-MAIL]", "[E-MAIL] (2)", "[E-MAIL] (3)"}


def test_klucze_liczbowe_przechodza() -> None:
    assert zamaskuj({1: "a", 2.5: "b", None: "c"}).dane == {1: "a", 2.5: "b", None: "c"}


def test_nieznany_typ_klucza_przerywa() -> None:
    with pytest.raises(MaskowanieError, match="klucza typu tuple"):
        zamaskuj({"pole": {("a", "b"): 1}})
