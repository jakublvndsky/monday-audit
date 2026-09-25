"""Jedna sesja na całe konto (plan, faza 5b-2).

Testy dzielą się na dwie grupy i pierwsza jest ważniejsza:

- **treść zadania** — czy model dostaje zastrzeżenia RAZEM z liczbami, a nie
  po nich. Liczba bez swojego ograniczenia to najgroźniejszy rodzaj danych
  w tym projekcie, bo wygląda dokładnie jak liczba pewna,
- **kompletność rozstrzygnięć** — czy pominięta hipoteza jest słyszalna.

Sesji nie odpalamy: `ClaudeSDKClient` uruchamia podproces, a to mierzyłoby
SDK, nie nasz kod.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from monday_audit.analiza import SCIEZKA_PROMPTU_ANALIZY, zbuduj_zadanie
from monday_audit.detektory import Hipoteza

WEJSCIE: dict[str, Any] = {
    "konto": {"workspacow": 136, "tablic_aktywnych": 1315},
    "zestawienia": [{"produkt": "crm", "w_toku": 1290, "pokrycie": 0.614}],
    "zastrzezenia": [
        "[zestawienia] crm: 7113 itemów nie weszło do żadnego kubełka (O47). Pokrycie 61.4%",
        "[tablice] nieuruchomionych automatyzacji nie widać (O41)",
    ],
}


def _hipotezy(ile: int = 2) -> list[Hipoteza]:
    return [
        Hipoteza(
            klasa_id="BOARD_GHOST",
            obiekt_id=f"b{i}",
            fakty={"wpisow": 0, "nazwa": f"tablica {i}"},
            budzet_wywolan=2,
        )
        for i in range(ile)
    ]


# ── treść zadania ────────────────────────────────────────────────────────


def test_zastrzezenia_ida_przed_liczbami() -> None:
    """Kolejność jest treścią, nie formatowaniem. Model czytający liczby bez
    ich ograniczeń napisze uwagę opartą na liczbie, o której nie wie, że jest
    niepełna — a tego błędu nie widać w wyniku, dopóki nie zobaczy go klient."""
    zadanie = zbuduj_zadanie(_hipotezy(), WEJSCIE)

    assert zadanie.index("CZEGO TE LICZBY NIE OBEJMUJĄ") < zadanie.index("OBRAZ KONTA")
    assert "Pokrycie 61.4%" in zadanie


def test_zastrzezenia_nie_dublują_się_w_obrazie() -> None:
    """Raz jako ostrzeżenie, nie drugi raz jako pole JSON-a — powtórzenie
    tej samej treści w dwóch miejscach uczy model ją pomijać."""
    zadanie = zbuduj_zadanie(_hipotezy(), WEJSCIE)
    obraz = zadanie.split("OBRAZ KONTA")[1]

    assert "zastrzezenia" not in obraz


def test_wszystkie_hipotezy_sa_w_zadaniu() -> None:
    zadanie = zbuduj_zadanie(_hipotezy(3), WEJSCIE)

    assert "HIPOTEZY DO ROZSTRZYGNIĘCIA (3)" in zadanie
    for i in range(3):
        assert f"b{i}" in zadanie


def test_zadanie_mowi_wprost_ile_ma_byc_rozstrzygniec() -> None:
    """Bez tego zdania model rozstrzyga „te ciekawsze". Od 2026-09-25 zdanie
    żąda każdej hipotezy DOKŁADNIE RAZ, bo pokrycie sprawdzamy parami."""
    zadanie = zbuduj_zadanie(_hipotezy(7), WEJSCIE)

    assert "Rozstrzygnij wszystkie 7, każdą DOKŁADNIE RAZ" in zadanie
    assert "`obiekt_id`" in zadanie


def test_obraz_konta_jest_poprawnym_jsonem() -> None:
    zadanie = zbuduj_zadanie(_hipotezy(), WEJSCIE)
    fragment = zadanie.split("## OBRAZ KONTA")[1].split("\n## ")[0].strip()

    assert json.loads(fragment)["konto"]["workspacow"] == 136


def test_pusta_lista_zastrzezen_nie_wywraca_zadania() -> None:
    zadanie = zbuduj_zadanie(_hipotezy(), {"konto": {"workspacow": 1}})

    assert "OBRAZ KONTA" in zadanie


# ── prompt ───────────────────────────────────────────────────────────────


def test_prompt_analizy_istnieje_i_ma_blok() -> None:
    """Prompt jest runtime'em, nie dokumentacją — brak bloku to błąd wdrożenia,
    a nie literówka w pliku markdown."""
    from monday_audit.agent import _tekst_promptu

    tresc = _tekst_promptu(SCIEZKA_PROMPTU_ANALIZY)

    assert '"uwagi"' in tresc
    assert '"pominiete"' in tresc


def test_prompt_zabrania_wyceny_i_stopniowania() -> None:
    """Dwie rzeczy, które wypadły z zakresu decyzją Kuby. Gdyby prompt o nich
    milczał, model dopisałby wagi i kwoty z własnej inicjatywy — robi tak,
    bo tak wygląda większość audytów, które widział."""
    from monday_audit.agent import _tekst_promptu

    tresc = _tekst_promptu(SCIEZKA_PROMPTU_ANALIZY)

    assert "NIE WYCENIASZ" in tresc
    assert "NIE STOPNIUJESZ" in tresc


def test_prompt_wymaga_dowodu() -> None:
    from monday_audit.agent import _tekst_promptu

    assert "DOWÓD" in _tekst_promptu(SCIEZKA_PROMPTU_ANALIZY)


# ── budżet i sufity ──────────────────────────────────────────────────────


def test_budzet_jest_na_sesje_a_nie_na_hipoteze() -> None:
    """Sedno 5b-2: budżety z rubryki zastępuje JEDEN sufit na cały run."""
    from monday_audit.agent import MAKS_OBROTOW
    from monday_audit.analiza import BUDZET_NARZEDZI, MAKS_OBROTOW_ANALIZY

    assert BUDZET_NARZEDZI > 0
    # Więcej obrotów niż w sesji per hipoteza — jest do rozstrzygnięcia
    # kilkadziesiąt hipotez, a nie jedna.
    assert MAKS_OBROTOW_ANALIZY > MAKS_OBROTOW


async def test_brak_hipotez_przerywa_zamiast_placic_za_pusta_sesje() -> None:
    from monday_audit.agent import AgentError
    from monday_audit.analiza import zbadaj_konto

    with pytest.raises(AgentError, match="brak hipotez"):
        await zbadaj_konto([], zestaw=None, wejscie={}, klucz_api="")  # type: ignore[arg-type]


# ── regresje z pierwszego prawdziwego runu (2026-09-23) ──────────────────


def test_zombie_account_idzie_szablonem_a_nie_do_modelu() -> None:
    """ZMIERZONE: pierwsza wersja wysłała do modelu wszystkie 24 hipotezy,
    w tym 8 `ZOMBIE_ACCOUNT` rozstrzygalnych szablonem. Model zgubił
    `obecnosc_w_logach` w 7 z 8 — 7 z 9 odrzuceń tamtego runu. Stara ścieżka
    nigdy nie wysyła tej klasy do modelu, bo szablon daje trafność 1,000 za 0 USD."""
    from monday_audit.analiza import rozdziel_hipotezy
    from monday_audit.rubryka import wczytaj_rubryke

    zombie = Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT",
        obiekt_id="u1",
        fakty={
            "user_hash": "a1b2c3",
            "kind": "member",
            "status": "ACTIVE",
            "last_activity": "2025-01-15T10:00:00Z",
            "obecnosc_w_logach": False,
            "plan_tier": "enterprise",
        },
        budzet_wywolan=0,
    )
    ghost = _hipotezy(1)[0]

    do_modelu, z_szablonow = rozdziel_hipotezy([zombie, ghost], wczytaj_rubryke())

    assert [h.klasa_id for h in do_modelu] == ["BOARD_GHOST"]
    assert len(z_szablonow) == 1
    # Fakt o wartości `false` przeżywa szablon — to właśnie go gubił model.
    assert z_szablonow[0]["dowod"]["obecnosc_w_logach"] is False


def test_uwaga_z_szablonu_ma_nowy_ksztalt() -> None:
    """Szablon wciąż produkuje `waga` i `kwota_pln` dla starej ścieżki. Do nowej
    idą tylko cztery pola plus `zrodlo`, żeby czytający wiedział, że tego nie
    pisał model."""
    from monday_audit.analiza import rozdziel_hipotezy
    from monday_audit.rubryka import wczytaj_rubryke

    zombie = Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT", obiekt_id="u1", fakty={"user_hash": "h"}, budzet_wywolan=0
    )

    _, z_szablonow = rozdziel_hipotezy([zombie], wczytaj_rubryke())

    uwaga = z_szablonow[0]
    assert "waga" not in uwaga
    assert "kwota_pln" not in uwaga
    assert uwaga["zrodlo"] == "szablon"


def test_zadanie_podaje_wymagane_pola_dowodu() -> None:
    """ZMIERZONE: `PLAN_MISMATCH` miał w faktach wszystkie pięć wymaganych pól,
    a model wpisał trzy — bo nikt mu nie powiedział, które są obowiązkowe.
    Stara ścieżka podaje je od zawsze; pierwsza wersja nowej to zgubiła."""
    from monday_audit.rubryka import wczytaj_rubryke

    rubryka = wczytaj_rubryke()
    zadanie = zbuduj_zadanie(_hipotezy(1), WEJSCIE, rubryka)
    hipotezy = json.loads(
        zadanie.split("## HIPOTEZY DO ROZSTRZYGNIĘCIA (1)")[1].split("Rozstrzygnij")[0]
    )

    wymagane = [p.rstrip("[]") for p in rubryka.po_id["BOARD_GHOST"].dowod]
    assert hipotezy[0]["dowod_wymagany"] == wymagane


def test_prompt_mowi_ze_false_jest_faktem() -> None:
    """Druga lekcja z tego samego runu: model traktował `false` jak „nie ma
    o czym mówić" i pomijał pole. Prompt musi to powiedzieć wprost."""
    from monday_audit.agent import _tekst_promptu

    tresc = _tekst_promptu(SCIEZKA_PROMPTU_ANALIZY)

    assert "TEŻ JEST FAKTEM" in tresc
    assert "dowod_wymagany" in tresc


def test_surowa_odpowiedz_laduje_na_dysku(tmp_path: Any) -> None:
    """Płatny wynik ma przeżyć każdą awarię, która przyjdzie po nim."""
    from monday_audit.cli_analiza import zapisz_surowa_odpowiedz

    sciezka = zapisz_surowa_odpowiedz("r1", {"uwagi": [{"a": 1}]}, tmp_path)

    assert json.loads(sciezka.read_text(encoding="utf-8")) == {"uwagi": [{"a": 1}]}


# ── definicje klas (zmierzone 2026-09-23) ────────────────────────────────


def _definicje(zadanie: str) -> list[dict[str, Any]]:
    fragment = zadanie.split("## DEFINICJE KLAS")[1].split("\n", 1)[1].split("\n## ")[0]
    return list(json.loads(fragment))


def _dead(obiekt: str) -> Hipoteza:
    return Hipoteza(
        klasa_id="AUTOMATION_DEAD",
        obiekt_id=obiekt,
        fakty={"automation_id": obiekt, "success": 2, "failure": 3, "exhausted": 0},
        budzet_wywolan=5,
    )


def test_zadanie_podaje_definicje_klasy_a_nie_samo_id() -> None:
    """ZMIERZONE na `analiza-20260923T103802Z`: model odrzucił trzy
    `AUTOMATION_DEAD` jako „nie jest martwa", bo znał tylko identyfikator.
    Rubryka definiuje klasę jako „uruchamia się i nie działa" — stara ścieżka
    podaje tę definicję od zawsze, nowa ją zgubiła."""
    from monday_audit.rubryka import wczytaj_rubryke

    rubryka = wczytaj_rubryke()
    klasa = rubryka.po_id["AUTOMATION_DEAD"]

    definicje = _definicje(zbuduj_zadanie([_dead("a1")], WEJSCIE, rubryka))

    assert definicje == [
        {
            "klasa_id": "AUTOMATION_DEAD",
            "nazwa": klasa.nazwa,
            "sygnal": klasa.sygnal.strip(),
            "rola_agenta": klasa.rola_agenta.strip(),
            "warunki_odrzucenia": list(klasa.warunki_odrzucenia),
        }
    ]
    assert "uruchamia się i nie działa" in definicje[0]["nazwa"]


def test_definicja_raz_na_klase_w_kolejnosci_wystapien() -> None:
    """Jedenaście hipotez tej samej klasy to nie jedenaście kopii definicji."""
    hipotezy = [_dead("a1"), *_hipotezy(2), _dead("a2"), _dead("a3")]

    zadanie = zbuduj_zadanie(hipotezy, WEJSCIE)

    assert [d["klasa_id"] for d in _definicje(zadanie)] == ["AUTOMATION_DEAD", "BOARD_GHOST"]
    assert "## DEFINICJE KLAS (2)" in zadanie


def test_definicje_ida_przed_hipotezami() -> None:
    """Model ma wiedzieć, co znaczy klasa, zanim przeczyta jej fakty."""
    zadanie = zbuduj_zadanie([_dead("a1")], WEJSCIE)

    assert zadanie.index("## OBRAZ KONTA") < zadanie.index("## DEFINICJE KLAS")
    assert zadanie.index("## DEFINICJE KLAS") < zadanie.index("## HIPOTEZY DO ROZSTRZYGNIĘCIA")


def test_definicje_nie_niosa_metadanej_oceniajacej() -> None:
    """Waga, wysiłek i wycena umarły razem z rubryką (decyzja Kuby
    2026-09-23). Podanie ich modelowi, któremu prompt zabrania stopniowania
    i wyceny, byłoby dwiema sprzecznymi instrukcjami naraz."""
    from monday_audit.rubryka import wczytaj_rubryke

    rubryka = wczytaj_rubryke()
    wszystkie = [
        Hipoteza(klasa_id=k, obiekt_id="x", fakty={}, budzet_wywolan=1) for k in rubryka.po_id
    ]

    for definicja in _definicje(zbuduj_zadanie(wszystkie, WEJSCIE, rubryka)):
        assert set(definicja) == {
            "klasa_id",
            "nazwa",
            "sygnal",
            "rola_agenta",
            "warunki_odrzucenia",
        }, definicja["klasa_id"]


def test_prompt_kaze_czytac_definicje_a_nie_nazwe() -> None:
    from monday_audit.agent import _tekst_promptu

    tresc = _tekst_promptu(SCIEZKA_PROMPTU_ANALIZY)

    assert "z DEFINICJI, nie z identyfikatora" in tresc
    assert "Warunki odrzucenia są jedynymi powodami" in tresc
    # Rola z katalogu potrafi mówić o wadze (GUEST_SPRAWL) — zakazy wygrywają.
    assert "pierwszeństwo przed rolą" in tresc


# ── sufit na klasę (zmierzone 2026-09-24) ────────────────────────────────


def test_sufit_nie_rusza_klas_ponizej_i_zachowuje_kolejnosc() -> None:
    from monday_audit.analiza import przytnij_do_sufitu

    hipotezy = _hipotezy(3)

    wynik, przyciete = przytnij_do_sufitu(hipotezy, sufit=3)

    assert wynik == hipotezy
    assert przyciete == []


def test_sufit_bierze_najsilniejsze_a_remis_rozstrzyga_obiekt() -> None:
    """Ranking decyduje, CO przechodzi, nie o kolejności — i jest powtarzalny."""
    from monday_audit.analiza import przytnij_do_sufitu

    kolumn = {"a": 20, "b": 30, "c": 20, "d": 16}
    hipotezy = [
        Hipoteza(klasa_id="BOARD_OVERCOMPLEX", obiekt_id=o, fakty={"liczba_kolumn": n})
        for o, n in kolumn.items()
    ]

    wynik, [przyciete] = przytnij_do_sufitu([*hipotezy, *_hipotezy(1)], sufit=2)

    assert [h.obiekt_id for h in wynik] == ["a", "b", "b0"]
    assert (przyciete.klasa_id, przyciete.zbadanych, przyciete.wszystkich) == (
        "BOARD_OVERCOMPLEX",
        2,
        4,
    )
    assert przyciete.kryterium == "najwięcej kolumn"


def test_remis_bez_faktu_rankingu_bierze_kolejnosc_detektora() -> None:
    from monday_audit.analiza import przytnij_do_sufitu

    wynik, [przyciete] = przytnij_do_sufitu(_hipotezy(5), sufit=2)

    # BOARD_GHOST ma ranking po items_count; bez tego faktu remis → kolejność.
    assert [h.obiekt_id for h in wynik] == ["b0", "b1"]
    assert przyciete.pominietych == 3


# ── odpowiedź w kilku blokach (zmierzone 2026-09-24) ─────────────────────


def test_odpowiedz_pocieta_na_bloki_sklada_sie_w_calosc() -> None:
    """`analiza-20260924T110941Z` padła na „Extra data", bo parser brał tylko
    ostatni blok, a 99 hipotez to odpowiedź w kilku blokach."""
    from monday_audit.analiza import odpowiedz_z_blokow

    calosc = json.dumps({"uwagi": [{"a": 1}, {"b": 2}], "pominiete": []})
    bloki = [calosc[:20], calosc[20:]]

    assert odpowiedz_z_blokow(bloki) == json.loads(calosc)


def test_krotka_odpowiedz_nadal_z_ostatniego_bloku() -> None:
    from monday_audit.analiza import odpowiedz_z_blokow

    assert odpowiedz_z_blokow(["Rozstrzygam.", '{"uwagi": [], "pominiete": []}']) == {
        "uwagi": [],
        "pominiete": [],
    }


def test_brak_jsona_to_blad_a_nie_cisza() -> None:
    from monday_audit.agent import AgentError
    from monday_audit.analiza import odpowiedz_z_blokow

    with pytest.raises(AgentError):
        odpowiedz_z_blokow(["nie mam odpowiedzi"])


# ── pokrycie rozstrzygnięć parami (klasa, obiekt) ────────────────────────


def _hip(klasa: str, obiekt: str) -> Hipoteza:
    return Hipoteza(klasa_id=klasa, obiekt_id=obiekt, fakty={}, budzet_wywolan=0)


def test_pokrycie_pelne_gdy_kazda_hipoteza_dokladnie_raz() -> None:
    from monday_audit.analiza import sprawdz_pokrycie

    hipotezy = [_hip("BOARD_GHOST", "1"), _hip("BOARD_GHOST", "2")]
    odpowiedz = {
        "uwagi": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "1"}],
        "pominiete": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "2", "powod": "x"}],
    }

    assert sprawdz_pokrycie(hipotezy, odpowiedz).pelne


def test_zguba_i_podwojenie_nie_znosza_sie() -> None:
    """ZMIERZONE 2026-09-25: 95 rozstrzygnięć na 94 hipotezy. Suma tego nie
    rozróżnia, a zgubiona + podwojona dawałyby zgodny wynik."""
    from monday_audit.analiza import sprawdz_pokrycie

    hipotezy = [_hip("BOARD_GHOST", "1"), _hip("BOARD_GHOST", "2")]
    odpowiedz = {
        "uwagi": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "1"}],
        "pominiete": [{"klasa_id": "BOARD_GHOST", "obiekt_id": "1", "powod": "x"}],
    }

    pokrycie = sprawdz_pokrycie(hipotezy, odpowiedz)

    assert pokrycie.brakujace == (("BOARD_GHOST", "2"),)
    assert pokrycie.podwojone == (("BOARD_GHOST", "1"),)
    assert "bez rozstrzygnięcia 1" in pokrycie.opis()
    assert "więcej niż raz 1" in pokrycie.opis()


def test_obce_i_bez_obiektu_sa_nazwane() -> None:
    from monday_audit.analiza import sprawdz_pokrycie

    odpowiedz = {
        "uwagi": [{"klasa_id": "BOARD_GHOST"}, {"klasa_id": "BOARD_GHOST", "obiekt_id": "9"}],
        "pominiete": [],
    }

    pokrycie = sprawdz_pokrycie([_hip("BOARD_GHOST", "1")], odpowiedz)

    assert pokrycie.obce == (("BOARD_GHOST", "9"),)
    assert pokrycie.bez_obiektu == 1
    assert pokrycie.brakujace == (("BOARD_GHOST", "1"),)


def test_prompt_wymaga_obiekt_id_w_uwadze() -> None:
    tresc = SCIEZKA_PROMPTU_ANALIZY.read_text(encoding="utf-8")

    assert '"obiekt_id": "ID obiektu z hipotezy, niezmienione",\n      "opis"' in tresc
