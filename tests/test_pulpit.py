"""Dashboardy (makieta frontu) — granice na DWÓCH warstwach.

Panel klienta jest nową powierzchnią wycieku, więc ma własne testy, nie
odziedziczone po raporcie. Nowość wobec 3.12: sprawdzamy nie tylko HTML,
ale i **payload JSON** — bo przy froncie w JS to on jedzie do odbiorcy,
a szablon jest u niego, nie u nas.

Trzy granice, każda w obu warstwach:

1. finding `tylko_wewnetrzne` nie wychodzi do klienta
2. treść `trop` nie wychodzi do klienta
3. surowy hash nie wychodzi nigdzie

Plus czwarta, specyficzna dla frontu: **klucze wewnętrzne nie ISTNIEJĄ**
w strukturze klientowej, a nie tylko nie są wyświetlane.
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
from monday_audit.dostep import ROLA_KLIENT, utworz_konto
from monday_audit.pulpit import (
    KLUCZE_WEWNETRZNE,
    do_json,
    lista_runow,
    wyrenderuj_indeks,
    wyrenderuj_pulpit,
    zbuduj_liste_klientow,
    zbuduj_pulpit,
)
from monday_audit.raport import ODBIORCA_KLIENT, ODBIORCA_WEWNETRZNY, RaportError
from monday_audit.rubryka import wczytaj_rubryke

RUBRYKA = wczytaj_rubryke()
RUN_AT = "2026-08-01T21:09:13.860699+00:00"
HASH_ANNY = "05677b1ab370bae1"
KLASA_KLIENTA = "ZOMBIE_ACCOUNT"
KLASA_WEWNETRZNA = "PROCESS_BYPASS"

PAYLOAD = {
    "meta": {
        "client_id": "cxlabs",
        "run_at": RUN_AT,
        "collector_ver": "0.1.0",
        "wersja_api": "2026-07",
        "uwagi_o_zakresie": ["lista użytkowników jest z natury na poziomie konta"],
    },
    "konto": {
        "konto": {"nazwa": "CXLABS"},
        "plan": {"tier": "enterprise"},
        "zakres": {"typ": "workspace", "workspace_ids": ["6576039"], "board_ids": []},
        "zastrzezenia": ["token bez uprawnień admina"],
    },
    "uzytkownicy": {
        "podsumowanie": {
            "razem": 95,
            "agentow": 36,
            "zajmujacych_miejsce": 19,
            "adminow": 10,
            "gosci": 12,
            "tylko_podglad": 28,
            "bez_last_activity": 37,
            "oczekujacych": 1,
        }
    },
    "tablice": {
        "podsumowanie": {
            "razem": 105,
            "kolumn_suma": 902,
            "kolumn_max": 21,
            "itemow_suma": 559,
            "tablic_bez_itemow": 8,
            "tablic_bez_wlasciciela": 0,
            "workspace_ow": 1,
        }
    },
    "automatyzacje": {
        "podsumowanie": {
            "automatyzacji_widzianych": 80,
            "automatyzacji_z_bledami": 7,
            "automatyzacji_z_wyczerpaniem": 7,
            "tablic_bez_zdarzen": 104,
            "tablic_sondowanych": 105,
        }
    },
    "aktywnosc": {
        "podsumowanie": {
            "wpisow_razem": 4432,
            "tablic_zbadanych": 105,
            "tablic_zdominowanych_jednym_autorem": 94,
            "tablic_pozornie_zywych": 0,
            "tablic_bez_wpisow": 0,
        }
    },
}


def _finding(
    con: sqlite3.Connection, klasa_id: str, *, run_id: str = "r1", **nadpisz: object
) -> None:
    klasa = RUBRYKA.po_id[klasa_id]
    con.execute(
        "INSERT INTO findings (run_id, snapshot_id, klasa_id, rubric_ver, waga, wysilek, "
        "typ_wyceny, kwota_pln, widocznosc, opis, rekomendacja, dowod, pewnosc, trop) "
        "VALUES (?, 5, ?, ?, ?, ?, ?, ?, ?, ?, 'co zrobić', ?, 'wysoka', ?)",
        (
            run_id,
            klasa_id,
            RUBRYKA.wersja,
            str(nadpisz.get("waga", "wysoka")),
            "niski",
            klasa.typ_wyceny,
            nadpisz.get("kwota"),
            klasa.widocznosc,
            str(nadpisz.get("opis", "opis znaleziska")),
            json.dumps(nadpisz.get("dowod") or {"user_hash": HASH_ANNY}, ensure_ascii=False),
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
        "rubric_ver, prompt_hash, hipotez_zbadanych, findingow, koszt_usd) "
        "VALUES ('r1', 'cxlabs', 5, 'zakonczony', ?, 'claude-sonnet-5', ?, 'abc123', 19, 2, 1.71)",
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


# ── granica: HTML ────────────────────────────────────────────────────────


def test_klasa_wewnetrzna_nie_wchodzi_do_panelu_klienta(con: sqlite3.Connection) -> None:
    _finding(con, KLASA_KLIENTA)
    _finding(con, KLASA_WEWNETRZNA, opis="TAJNY OPIS WEWNĘTRZNY")
    con.commit()
    klienci = zbuduj_liste_klientow(con)

    wewn = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY)
    klient = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)

    assert {f.klasa_id for f in wewn.findingi} == {KLASA_KLIENTA, KLASA_WEWNETRZNA}
    assert {f.klasa_id for f in klient.findingi} == {KLASA_KLIENTA}
    html = wyrenderuj_pulpit(klient, klienci=klienci)
    assert "TAJNY OPIS WEWNĘTRZNY" not in html
    assert KLASA_WEWNETRZNA not in html


def test_trop_nie_wychodzi_do_panelu_klienta(con: sqlite3.Connection) -> None:
    _finding(con, KLASA_KLIENTA)
    con.commit()
    klienci = zbuduj_liste_klientow(con)
    trop = RUBRYKA.po_id[KLASA_KLIENTA].trop_sprzedazowy
    assert trop, "test bez sensu, jeśli rubryka nie ma tropu"

    wewn = wyrenderuj_pulpit(
        zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY),
        klienci=klienci,
    )
    klient = wyrenderuj_pulpit(
        zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT),
        klienci=klienci,
    )

    assert trop in wewn
    assert trop not in klient


def test_panel_klienta_nie_ma_diagnostyki_runu(con: sqlite3.Connection) -> None:
    """Koszt, pinowanie i odrzucenia to nasza sprawa, nie treść dla odbiorcy."""
    _finding(con, KLASA_KLIENTA)
    con.execute(
        "INSERT INTO hipotezy_odrzucone (run_id, klasa_id, obiekt_id, powod) "
        "VALUES ('r1', 'PLAN_MISMATCH', '27690228', 'konto rośnie')"
    )
    con.commit()
    klienci = zbuduj_liste_klientow(con)

    klient = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)
    html = wyrenderuj_pulpit(klient, klienci=klienci)

    assert klient.hipotezy_odrzucone == ()
    assert klient.pinowanie == {}
    assert klient.koszt_usd is None
    assert "konto rośnie" not in html
    assert "claude-sonnet-5" not in html
    assert "Diagnostyka runu" not in html


def test_zaden_surowy_hash_nie_wychodzi_w_html(con: sqlite3.Connection) -> None:
    _finding(
        con,
        KLASA_KLIENTA,
        opis=f"Konto (hash {HASH_ANNY}) jest martwe",
        dowod={"user_hash": HASH_ANNY, "tablice_dostepne": {HASH_ANNY: ["Onboarding"]}},
    )
    con.commit()
    klienci = zbuduj_liste_klientow(con)

    for odbiorca in (ODBIORCA_WEWNETRZNY, ODBIORCA_KLIENT):
        html = wyrenderuj_pulpit(
            zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=odbiorca),
            klienci=klienci,
        )
        trafienie = WZORZEC_HASHA.search(html)
        assert trafienie is None, (
            f"{odbiorca}: przeszedł hash {trafienie.group(0) if trafienie else ''}"
        )
        assert "Anna Górniak" in html


# ── granica: payload JSON dla frontu w JS ────────────────────────────────


def test_payload_klienta_nie_ma_kluczy_wewnetrznych(con: sqlite3.Connection) -> None:
    """Najważniejszy test tego pliku.

    Przy froncie w JS payload jest widoczny w narzędziach przeglądarki, więc
    „wyślij wszystko i ukryj w widoku" znaczy „wyślij wszystko". Klucze muszą
    być USUNIĘTE ze struktury, nie wyzerowane — brak klucza jest sprawdzalny,
    zero nie jest.
    """
    _finding(con, KLASA_KLIENTA)
    con.commit()

    wewn = do_json(
        zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=ODBIORCA_WEWNETRZNY)
    )
    klient = do_json(
        zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)
    )

    for klucz in KLUCZE_WEWNETRZNE:
        assert klucz in wewn, f"wersja wewnętrzna musi mieć {klucz}"
        assert klucz not in klient, f"{klucz} PRZESZEDŁ do payloadu klienta"


def test_payload_klienta_nie_niesie_tropu_ani_hasha(con: sqlite3.Connection) -> None:
    """Trop jest polem findingu, więc nie wystarczy usunąć kluczy z korzenia."""
    _finding(con, KLASA_KLIENTA, dowod={"user_hash": HASH_ANNY})
    con.commit()
    trop = RUBRYKA.po_id[KLASA_KLIENTA].trop_sprzedazowy
    assert trop, "test bez sensu, jeśli rubryka nie ma tropu dla tej klasy"

    tekst = json.dumps(
        do_json(zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca=ODBIORCA_KLIENT)),
        ensure_ascii=False,
    )

    assert trop not in tekst
    assert WZORZEC_HASHA.search(tekst) is None


def test_payload_przechodzi_przez_json_dumps(con: sqlite3.Connection) -> None:
    """Bez tego obietnica „przepisujemy szablony, nie logikę" jest pustym słowem.

    Front w JS dostanie ten obiekt przez sieć, więc musi być czystym JSON-em:
    bez dat, bez `Decimal`, bez obiektów Pythona.
    """
    _finding(con, KLASA_KLIENTA, kwota=1200.0)
    con.commit()

    payload = do_json(zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA))
    odtworzony = json.loads(json.dumps(payload, ensure_ascii=False))

    assert odtworzony["client_id"] == "cxlabs"
    assert odtworzony["findingow"] == 1
    assert odtworzony["sekcje"][0]["metryki"][1]["udzial"] == 37.9


# ── liczby zgodne z danymi ───────────────────────────────────────────────


def test_agregaty_pochodza_ze_snapshotu(con: sqlite3.Connection) -> None:
    """Sekcje liczą to, co collector policzył — bez dopytywania monday."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    pulpit = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA)
    metryki = {m.nazwa: m for s in pulpit.sekcje for m in s.metryki}

    assert metryki["kont razem"].wartosc == 95
    assert metryki["agentów AI"].wartosc == 36
    assert metryki["agentów AI"].udzial == 37.9
    assert metryki["zdominowanych jednym autorem"].wartosc == 94
    assert metryki["zdominowanych jednym autorem"].udzial == 89.5
    assert len(pulpit.sekcje) == 4


def test_udzial_bez_calosci_jest_none(con: sqlite3.Connection) -> None:
    """Procent bez mianownika byłby wymyślony — więc go nie ma."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    metryki = {
        m.nazwa: m
        for s in zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA).sekcje
        for m in s.metryki
    }

    assert metryki["kolumn razem"].udzial is None


# ── brak wymyślonych danych ──────────────────────────────────────────────


def test_lista_klientow_pochodzi_z_bazy(con: sqlite3.Connection) -> None:
    """Panel z fałszywymi klientami wyglądałby lepiej i kłamał."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    klienci = zbuduj_liste_klientow(con)

    assert len(klienci) == 1
    assert klienci[0].client_id == "cxlabs"
    assert klienci[0].findingow == 1


def test_indeks_mowi_wprost_ze_klient_jest_jeden(con: sqlite3.Connection) -> None:
    _finding(con, KLASA_KLIENTA)
    con.commit()

    html = wyrenderuj_indeks(zbuduj_liste_klientow(con))

    assert "nie dorabiamy przykładowych" in html
    assert "cxlabs" in html


# ── wybór wersji audytu ──────────────────────────────────────────────────
#
# Do 2026-08-06 `_ostatni_run` sortował `hipotez_zbadanych DESC` i test w tym
# miejscu pilnował, żeby run techniczny nie przesłonił pełnego audytu. Obejście
# zniknęło, gdy panel dostał jawny drop-down wersji — ale **puste miejsce po
# teście to gorsze rozwiązanie niż test na starą regułę**. Trzy poniżej pilnują
# tego, co dziś ma być prawdą, w tym najważniejszego: że starszy, obszerniejszy
# audyt nadal daje się otworzyć.


def _run_testowy(con: sqlite3.Connection, run_id: str, kiedy: str, hipotez: int = 1) -> None:
    con.execute(
        "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, "
        "hipotez_zbadanych, findingow) VALUES (?, 'cxlabs', 5, 'zakonczony', ?, ?, 1)",
        (run_id, kiedy, hipotez),
    )
    _finding(con, KLASA_KLIENTA, run_id=run_id)


def test_domyslnie_najnowszy_audyt(con: sqlite3.Connection) -> None:
    """Panel otwiera NAJNOWSZY audyt, nie najobszerniejszy.

    Odwrotnie niż do 2026-08-06: wtedy wygrywał run z największą liczbą hipotez,
    więc panel pokazywał dane z 1 sierpnia, choć audyt szedł 5 sierpnia — i nikt
    nie wiedział dlaczego. Wersję wybiera się teraz jawnie, więc ukrywanie
    najnowszej byłoby tylko zaskoczeniem.
    """
    _finding(con, KLASA_KLIENTA, run_id="r1")  # pełny, wcześniejszy
    _run_testowy(con, "r2-proba", "2026-08-05T10:00:00+00:00")
    con.commit()

    assert zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA).run_id == "r2-proba"


def test_starszy_audyt_zostaje_osiagalny(con: sqlite3.Connection) -> None:
    """To jest cena za zmianę domyślnego wyboru — i musi być zapłacona.

    Skoro panel nie chroni już odbiorcy przed chudym runem, MUSI dać mu dojść do
    pełnego. Gdyby `run_id` przestało działać, zmiana domyślnego wyboru byłaby
    regresją, nie poprawką.
    """
    _finding(con, KLASA_KLIENTA, run_id="r1")
    _run_testowy(con, "r2-proba", "2026-08-05T10:00:00+00:00")
    con.commit()

    pulpit = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, run_id="r1")
    assert pulpit.run_id == "r1"
    # Lista wersji jedzie razem z danymi, więc drop-down ma z czego się zbudować.
    assert [w.run_id for w in pulpit.wersje] == ["r2-proba", "r1"], "najnowszy pierwszy"


def test_lista_wersji_liczy_findingi_z_tabeli(con: sqlite3.Connection) -> None:
    """`runy.findingow` może się rozjechać z tabelą — liczymy WIERSZE.

    Ten licznik zapisuje się przy domknięciu runu. Drop-down mówiący „5 znalezisk"
    przy runie, który ma jedno, kłamie dokładnie w momencie, w którym odbiorca
    wybiera, czemu zaufać.
    """
    _run_testowy(con, "r-rozjazd", "2026-08-05T10:00:00+00:00")
    con.execute("UPDATE runy SET findingow = 99 WHERE run_id = 'r-rozjazd'")
    con.commit()

    wersje = {w.run_id: w.findingow for w in lista_runow(con, "cxlabs")}
    assert wersje["r-rozjazd"] == 1, "wziął licznik z runy zamiast policzyć wiersze"


def test_porownanie_liczy_sie_wzgledem_wybranej_wersji(con: sqlite3.Connection) -> None:
    """Przełączenie na starszy audyt nie może porównywać go z PRZYSZŁOŚCIĄ.

    `_poprzedni_run` szuka poprzednika względem wybranego runu, nie najnowszego.
    Działało tak przed drop-downem wersji, ale nikt tego nie sprawdzał — a przy
    jawnym wyborze wersji to jest różnica między liczbą prawdziwą i bezsensowną.
    """
    _finding(con, KLASA_KLIENTA, run_id="r1")  # 2026-08-01 (z fixture)
    _run_testowy(con, "r-sroda", "2026-08-03T10:00:00+00:00")
    _run_testowy(con, "r-piatek", "2026-08-05T10:00:00+00:00")
    con.commit()

    najnowszy = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA)
    sredni = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, run_id="r-sroda")
    najstarszy = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, run_id="r1")

    assert najnowszy.poprzedni_run_at is not None
    assert najnowszy.poprzedni_run_at.startswith("2026-08-03"), "poprzednik piątku to środa"
    assert sredni.poprzedni_run_at is not None
    assert sredni.poprzedni_run_at.startswith("2026-08-01"), "poprzednik środy to 1 sierpnia"
    assert najstarszy.ma_porownanie is False, "najstarszy audyt nie ma z czym się porównać"


# ── braki i błędy ────────────────────────────────────────────────────────


def test_brak_porownania_jest_napisany_wprost(con: sqlite3.Connection) -> None:
    """Zero udające brak zmian byłoby kłamstwem — panel musi powiedzieć, że nie ma."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    pulpit = zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA)
    html = wyrenderuj_pulpit(pulpit, klienci=zbuduj_liste_klientow(con))

    assert not pulpit.ma_porownanie
    assert "To pierwszy audyt tego konta" in html


def test_klient_bez_audytu_odpada(con: sqlite3.Connection) -> None:
    with pytest.raises(RaportError, match="nie ma zakończonego audytu"):
        zbuduj_pulpit(con, client_id="nie-ma-takiego", rubryka=RUBRYKA)


def test_nieznany_odbiorca_odpada(con: sqlite3.Connection) -> None:
    _finding(con, KLASA_KLIENTA)
    con.commit()
    with pytest.raises(RaportError, match="nieznany odbiorca"):
        zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA, odbiorca="klientowy")


# ── wygląd: wspólne tokeny i brak zasobów zewnętrznych ───────────────────


def test_panel_i_raport_maja_te_same_tokeny(con: sqlite3.Connection) -> None:
    """D14 obiecuje, że firmowy CSS podmienia się w JEDNYM miejscu.

    Dwa niezależne bloki stylów rozjechałyby się przy pierwszej zmianie marki,
    i to cicho — bo nikt nie porównuje dwóch arkuszy linijka w linijkę.
    """
    from monday_audit.raport import wyrenderuj, zbuduj_raport

    _finding(con, KLASA_KLIENTA)
    con.commit()

    raport = wyrenderuj(zbuduj_raport(con, run_id="r1", rubryka=RUBRYKA))
    panel = wyrenderuj_pulpit(
        zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA), klienci=zbuduj_liste_klientow(con)
    )

    for token in ("--cx-ink:", "--cx-lime:", "--space-5:", "--radius-lg:", "--font-display:"):
        assert token in raport, f"raport stracił {token}"
        assert token in panel, f"panel stracił {token}"


def test_panel_nie_ma_zasobow_zewnetrznych_ani_fontow(con: sqlite3.Connection) -> None:
    """Ta sama granica co w raporcie: offline i licencja fontów (D14)."""
    _finding(con, KLASA_KLIENTA)
    con.commit()

    for html in (
        wyrenderuj_pulpit(
            zbuduj_pulpit(con, client_id="cxlabs", rubryka=RUBRYKA),
            klienci=zbuduj_liste_klientow(con),
        ),
        wyrenderuj_indeks(zbuduj_liste_klientow(con)),
    ):
        assert re.search(r"""(src|href)\s*=\s*["']https?://""", html) is None
        assert "@font-face" not in html
        assert "data:image/png;base64," in html, "znak marki ma być osadzony"


# ── lista klientów: konta ORAZ audyty ────────────────────────────────────
#
# ZMIERZONA USTERKA. Do 2026-08-10 lista powstawała tylko z `runy`, więc panel
# ukrywał dwa stany naraz. W bazie produkcyjnej: `acme` miał konto i zero
# audytów (niewidoczny, choć hasło wydane), `cxlabs` miał 17 audytów i żadnego
# konta (widoczny, ale klient nie mógł się zalogować — tego nie było widać
# w ogóle). Zgłosił to Kuba pytaniem „czy nie powinienem widzieć również acme".


def test_klient_z_kontem_bez_audytu_jest_widoczny(con: sqlite3.Connection) -> None:
    """Administrator MUSI widzieć, komu wydał hasło — także przed pierwszym audytem.

    Inaczej między założeniem konta a pierwszym audytem klient jest niewidoczny,
    a panel nie ma miejsca, w którym widać wydane dostępy.
    """
    utworz_konto(con, rola=ROLA_KLIENT, haslo="haslo55-testowe66-dlugie77", client_id="acme")
    con.commit()

    pozycje = {p.client_id: p for p in zbuduj_liste_klientow(con)}

    assert "acme" in pozycje, "klient z kontem, ale bez audytu, jest niewidoczny"
    assert pozycje["acme"].audytow == 0
    assert pozycje["acme"].ma_konto is True
    # Bez runu nie ma czego liczyć — i to nie może wywalić budowania listy.
    assert pozycje["acme"].ostatni_run_id is None
    assert pozycje["acme"].findingow == 0


def test_klient_z_audytem_bez_konta_jest_oznaczony(con: sqlite3.Connection) -> None:
    """Najgroźniejszy z dwóch stanów: audyt jest, a odbiorca nie może go zobaczyć.

    Wcześniej taki klient wyglądał w panelu identycznie jak każdy inny, więc nikt
    nie wiedział, że brakuje mu hasła.
    """
    _finding(con, KLASA_KLIENTA)
    con.commit()

    pozycje = {p.client_id: p for p in zbuduj_liste_klientow(con)}

    assert pozycje["cxlabs"].audytow == 1
    assert pozycje["cxlabs"].ma_konto is False, "brak konta nie jest oznaczony"


def test_lista_nie_dubluje_klienta_z_kontem_i_audytem(con: sqlite3.Connection) -> None:
    """`UNION` łączy dwa źródła — bez tego klient normalny byłby na liście dwa razy."""
    _finding(con, KLASA_KLIENTA)
    utworz_konto(con, rola=ROLA_KLIENT, haslo="haslo55-testowe66-dlugie77", client_id="cxlabs")
    con.commit()

    identyfikatory = [p.client_id for p in zbuduj_liste_klientow(con)]

    assert identyfikatory.count("cxlabs") == 1, f"duplikat na liście: {identyfikatory}"
    assert zbuduj_liste_klientow(con)[0].ma_konto is True


def test_dezaktywowane_konto_nie_liczy_sie_jako_dostep(con: sqlite3.Connection) -> None:
    """Konto z `aktywne = 0` nie wpuszcza, więc panel nie może mówić, że wpuszcza."""
    _finding(con, KLASA_KLIENTA)
    utworz_konto(con, rola=ROLA_KLIENT, haslo="haslo55-testowe66-dlugie77", client_id="cxlabs")
    con.execute("UPDATE konta_dostepu SET aktywne = 0 WHERE client_id = 'cxlabs'")
    con.commit()

    assert zbuduj_liste_klientow(con)[0].ma_konto is False
