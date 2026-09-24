"""Trace jednej hipotezy: co wychodzi poza serwer (plan, faza 4).

Testy pilnują trzech rzeczy, w tej kolejności ważności:

1. **czego w trace NIE ma** — prompt systemowy niesie inwentarz klienta,
2. **że PII nie przechodzi** i że trafienie jest policzone, a nie przemilczane,
3. że kształt zgadza się z tym, czego oczekuje Langfuse.

Atrapa zamiast `WynikHipotezy` jest celowa: `agent.py` ciągnie Agent SDK,
a trace ma się dać zbudować bez podprocesu modelu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.obserwowalnosc import RODZAJ_GENERACJA, zbuduj_trace
from monday_audit.osoby import WpisPII

INWENTARZ = "tablica Zdzisławy, workspace Sprzedaż, kolumna lead_status"
ZNANA_OSOBA = "Zdzisława Wąchockańska"


def _zestaw_z_mapowaniem() -> Any:
    """Atrapa `Narzedzia` z PRAWDZIWĄ tabelą mapowania w bazie w pamięci.

    Pętla czyta z niej znane osoby dla drugiej siatki maskowania — więc atrapa
    bez `con` nie sprawdziłaby, że lista w ogóle dochodzi do trace'u.
    """
    con = polacz(":memory:")
    zastosuj_migracje(con)
    con.execute(
        "INSERT INTO osoby_mapowanie (client_id, user_hash, imie_nazwisko, email) "
        "VALUES ('cxlabs', 'abc', ?, 'zdzislawa@klient.test')",
        (ZNANA_OSOBA,),
    )
    return SimpleNamespace(snapshot_id=7, con=con, client_id="cxlabs")


@dataclass
class AtrapaHipotezy:
    klasa_id: str = "BOARD_OVERCOMPLEX"
    obiekt_id: str = "9445123456"
    zapis: dict[str, Any] = field(default_factory=dict)

    def do_zapisu(self) -> dict[str, Any]:
        return self.zapis or {"klasa_id": self.klasa_id, "obiekt_id": self.obiekt_id}


@dataclass
class AtrapaWyniku:
    hipoteza: AtrapaHipotezy = field(default_factory=AtrapaHipotezy)
    finding: dict[str, Any] | None = None
    odrzucona: dict[str, Any] | None = None
    wywolania_narzedzi: list[str] = field(default_factory=list)
    zuzycie: dict[str, float] = field(default_factory=dict)
    blad: str | None = None
    blokow_tekstu: int = 1
    znakow_finalnych: int = 400
    znakow_wyrzuconych: int = 0


def _zbuduj(wynik: AtrapaWyniku, wpisy: list[WpisPII] | None = None) -> Any:
    return zbuduj_trace(
        wynik,
        run_id="run-1",
        snapshot_id=7,
        model="claude-opus-5",
        prompt_hash="abc123",
        wpisy=wpisy or [],
    )


# ── czego w trace NIE ma ─────────────────────────────────────────────────


def test_prompt_systemowy_nie_wychodzi_tylko_jego_hasz() -> None:
    """Prompt systemowy niesie INWENTARZ: nazwy tablic, workspace'ów i kolumn
    klienta. Hasz daje to, po co trace'owi prompt — wiedzę, że dwa runy szły
    tym samym. Treść nie daje nic ponadto, a kosztuje wysłaniem inwentarza."""
    trace = _zbuduj(AtrapaWyniku(finding={"opis": "za dużo kolumn"}))

    assert trace.metadane["prompt_hash"] == "abc123"
    assert INWENTARZ not in str(trace)
    assert "prompt" not in str(trace.obserwacje).lower().replace("prompt_hash", "")


# ── PII nie przechodzi, a trafienie jest policzone ───────────────────────


def test_mail_w_findingu_zostaje_zamaskowany() -> None:
    wynik = AtrapaWyniku(finding={"dowod": "zgłoszenie od lead@obcy.test"})

    trace = _zbuduj(wynik)

    assert trace.obserwacje[0].wyjscie["dowod"] == "zgłoszenie od [E-MAIL]"
    assert trace.trafienia_maskowania["email"] == 1
    assert not trace.czysty


def test_trafienie_widac_w_metadanych_trace_u() -> None:
    """Liczba idzie do metadanych, żeby było ją widać W LANGFUSE, a nie tylko
    w naszym logu. Człowiek patrzący na trace ma zobaczyć, że coś przeciekło."""
    trace = _zbuduj(AtrapaWyniku(finding={"dowod": "x@y.test"}))

    assert trace.metadane["trafien_maskowania"] == 1
    assert trace.metadane["pola_z_trafieniami"] == ["wyjscie.dowod"]


def test_trafienie_krzyczy_do_logu_jako_ostrzezenie(caplog: Any) -> None:
    """Trafienie to NIE sukces maskowania. Pierwszą linią jest zasada, że PII
    nie wchodzi do kontekstu modelu — `[E-MAIL]` znaczy, że puściła."""
    with caplog.at_level(logging.WARNING):
        _zbuduj(AtrapaWyniku(finding={"dowod": "x@y.test"}))

    assert "PIERWSZA linia" in caplog.text
    assert "x@y.test" not in caplog.text


def test_czysty_trace_nie_ostrzega(caplog: Any) -> None:
    with caplog.at_level(logging.WARNING):
        trace = _zbuduj(AtrapaWyniku(finding={"dowod": "tablica ma 41 kolumn"}))

    assert trace.czysty
    assert not caplog.text


def test_uzytkownik_konta_dostaje_zamiennik_bez_hasza() -> None:
    """Decyzja Kuby z 2026-09-23: do Langfuse'a trafia `[IMIĘ] [NAZWISKO]`,
    nie `[OSOBA:hash]`. Hasz liczony stałą solą jest daną osobową; trace'om
    wystarcza wiedza, że chodzi o osobę."""
    wpisy = [WpisPII("a1b2c3", "Zdzisława Wąchockańska", "zdzislawa@klient.test")]
    wynik = AtrapaWyniku(finding={"dowod": "właściciel: Zdzisława Wąchockańska"})

    trace = _zbuduj(wynik, wpisy)

    assert trace.obserwacje[0].wyjscie["dowod"] == "właściciel: [IMIĘ] [NAZWISKO]"
    assert "a1b2c3" not in repr(trace)
    assert trace.czysty


def test_blad_api_tez_idzie_przez_maskowanie() -> None:
    """Treść błędu z API bywa fragmentem odpowiedzi — nie jest „tylko
    komunikatem" i nie ma powodu, żeby omijała warstwę."""
    trace = _zbuduj(AtrapaWyniku(blad="błąd API: odrzucono dla admin@klient.test"))

    assert "[E-MAIL]" in trace.obserwacje[0].wyjscie["blad"]
    assert trace.metadane["rozstrzygniecie"] == "blad"


# ── kształt dla Langfuse ─────────────────────────────────────────────────


def test_zuzycie_przetlumaczone_na_nazwy_langfuse() -> None:
    """Mapowanie nazw siedzi tutaj, nie w `agent.py` — tam zużycie odpowiada
    przed D8, a nie przed cudzym systemem."""
    wynik = AtrapaWyniku(
        finding={"ok": True},
        zuzycie={
            "tokens_in": 10,
            "tokens_out": 20,
            "tokens_cache_read": 3000,
            "tokens_cache_write": 40,
            "koszt_usd": 0.42,
        },
    )

    generacja = _zbuduj(wynik).obserwacje[0]

    assert generacja.rodzaj == RODZAJ_GENERACJA
    assert generacja.zuzycie == {
        "input": 10,
        "output": 20,
        "cache_read_input_tokens": 3000,
        "cache_creation_input_tokens": 40,
    }
    assert generacja.koszt_usd == 0.42


def test_narzedzia_sa_osobnymi_wezlami() -> None:
    wynik = AtrapaWyniku(finding={"ok": True}, wywolania_narzedzi=["snapshot", "probka"])

    nazwy = [o.nazwa for o in _zbuduj(wynik).obserwacje]

    assert nazwy == ["hipoteza:BOARD_OVERCOMPLEX", "narzedzie:snapshot", "narzedzie:probka"]


def test_rozstrzygniecie_odrzucona_ma_swoje_wyjscie() -> None:
    trace = _zbuduj(AtrapaWyniku(odrzucona={"powod": "kolumny są używane"}))

    assert trace.metadane["rozstrzygniecie"] == "odrzucona"
    assert trace.obserwacje[0].wyjscie == {"powod": "kolumny są używane"}


def test_brak_rozstrzygniecia_nie_wywraca_budowania() -> None:
    """Hipoteza, która padła przed odpowiedzią, ma mieć trace — to właśnie
    ona jest najciekawsza przy szukaniu przyczyny."""
    trace = _zbuduj(AtrapaWyniku())

    assert trace.metadane["rozstrzygniecie"] == "brak"
    assert trace.obserwacje[0].wyjscie is None


# ── WPIĘCIE w pętlę audytu ───────────────────────────────────────────────
#
# Ta sekcja istnieje, bo w tym module zdarzyły się już DWIE usterki klasy
# „przeszło testy, a nie było podpięte" (`can_use_tool` i klucz API). Testy
# samego `zbuduj_trace` nie wykryłyby, że nikt go nie woła.


class AtrapaSladu:
    def __init__(self, blad: Exception | None = None) -> None:
        self.wyslane: list[Any] = []
        self.zamkniety = False
        self.blad = blad

    def wyslij(self, trace: Any) -> None:
        if self.blad:
            raise self.blad
        self.wyslane.append(trace)

    def zamknij(self) -> None:
        self.zamkniety = True


def test_petla_audytu_przyjmuje_slad() -> None:
    """Kontrakt sygnatury. Bez tego parametru cała obserwowalność jest fikcją —
    dokładnie tak, jak `postep` w tej samej pętli."""
    import inspect

    from monday_audit.agent import zbadaj_hipotezy

    assert "slad" in inspect.signature(zbadaj_hipotezy).parameters


async def test_petla_faktycznie_wysyla_trace(monkeypatch: Any) -> None:
    """Hipoteza szablonowa nie woła modelu, więc pętlę da się przejechać
    w całości bez podprocesu — i sprawdzić, że trace naprawdę wychodzi."""
    from monday_audit import agent as modul_agenta
    from monday_audit.detektory import Hipoteza
    from monday_audit.rubryka import wczytaj_rubryke

    monkeypatch.setattr(modul_agenta, "_inwentarz", lambda _: "{}")

    slad = AtrapaSladu()
    hipoteza = Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT",
        obiekt_id="u1",
        # Nazwisko w KLUCZU, bo tam wpadało bez śladu do review 2026-09-23 —
        # i tylko lista znanych osób z bazy ma jak je rozpoznać.
        fakty={"user_hash": "abc", "dni_nieaktywnosci": 200, "grupy": {ZNANA_OSOBA: 3}},
        budzet_wywolan=0,
    )

    await modul_agenta.zbadaj_hipotezy(
        [hipoteza],
        zestaw=_zestaw_z_mapowaniem(),
        rubryka=wczytaj_rubryke(),
        run_id="run-7",
        klucz_api="",
        slad=slad,
    )

    assert len(slad.wyslane) == 1
    trace = slad.wyslane[0]
    assert trace.nazwa == "hipoteza:ZOMBIE_ACCOUNT"
    assert trace.metadane["run_id"] == "run-7"
    assert trace.metadane["snapshot_id"] == 7
    # Hasz promptu liczony RAZ, poza pętlą, i faktycznie dochodzi.
    assert trace.metadane["prompt_hash"]
    # Lista znanych osób DOCHODZI do maskowania. Do review 2026-09-23 parametr
    # `wpisy` istniał, ale nikt go nie podawał — druga siatka była martwa.
    assert ZNANA_OSOBA not in repr(trace)
    assert "[IMIĘ] [NAZWISKO]" in repr(trace.obserwacje[0].wejscie)


async def test_padniety_slad_nie_przerywa_audytu(monkeypatch: Any, caplog: Any) -> None:
    """Klient zapłacił za run, nie za trace'y. Langfuse niedostępny nie może
    kosztować wyniku audytu."""
    from monday_audit import agent as modul_agenta
    from monday_audit.detektory import Hipoteza
    from monday_audit.rubryka import wczytaj_rubryke

    monkeypatch.setattr(modul_agenta, "_inwentarz", lambda _: "{}")

    hipoteza = Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT",
        obiekt_id="u1",
        fakty={"user_hash": "abc", "dni_nieaktywnosci": 200},
        budzet_wywolan=0,
    )

    with caplog.at_level(logging.WARNING):
        odpowiedz = await modul_agenta.zbadaj_hipotezy(
            [hipoteza],
            zestaw=_zestaw_z_mapowaniem(),
            rubryka=wczytaj_rubryke(),
            run_id="run-7",
            klucz_api="",
            slad=AtrapaSladu(blad=ConnectionError("Langfuse leży")),
        )

    assert odpowiedz["findings"], "finding musi wrócić mimo padniętego trace'u"
    assert "nie udało się wysłać" in caplog.text


async def test_bezpiecznik_maskowania_krzyczy_ale_run_konczy(monkeypatch: Any, caplog: Any) -> None:
    """Dwa poziomy logu, nie jeden. `MaskowanieError` znaczy, że payload
    zawierał coś, czego nie umiemy zamaskować — i trace NIE wyszedł. To jest
    ERROR do obejrzenia, a nie stracony ślad."""
    from monday_audit import agent as modul_agenta
    from monday_audit.detektory import Hipoteza
    from monday_audit.maskowanie import MaskowanieError
    from monday_audit.rubryka import wczytaj_rubryke

    monkeypatch.setattr(modul_agenta, "_inwentarz", lambda _: "{}")

    hipoteza = Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT",
        obiekt_id="u1",
        fakty={"user_hash": "abc", "dni_nieaktywnosci": 200},
        budzet_wywolan=0,
    )

    with caplog.at_level(logging.ERROR):
        odpowiedz = await modul_agenta.zbadaj_hipotezy(
            [hipoteza],
            zestaw=_zestaw_z_mapowaniem(),
            rubryka=wczytaj_rubryke(),
            run_id="run-7",
            klucz_api="",
            slad=AtrapaSladu(blad=MaskowanieError("nieznany typ w polu x")),
        )

    assert odpowiedz["findings"], "audyt ma się dokończyć mimo bezpiecznika"
    assert "trace NIE wyszedł" in caplog.text


# ── nowa ścieżka: jedna sesja na całe konto (faza 5b) ────────────────────
#
# Do fazy 5b `zbadaj_konto` nie wysyłało trace'ów wcale — tracing z fazy 4
# siedział w `zbadaj_hipotezy`. Te testy pilnują, że druga droga na zewnątrz
# idzie tą samą bramką i nie wynosi więcej niż pierwsza.


def _trace_analizy(**nadpisz: Any) -> Any:
    from monday_audit.obserwowalnosc import zbuduj_trace_analizy

    parametry: dict[str, Any] = {
        "run_id": "analiza-1",
        "snapshot_id": 1,
        "model": "claude-sonnet-5",
        "prompt_hash": "p123",
        "obraz_hash": "o456",
        "hipotezy": [{"klasa_id": "AUTOMATION_DEAD", "obiekt_id": "132931514", "fakty": {}}],
        "odpowiedz": {
            "uwagi": [{"klasa_id": "AUTOMATION_DEAD", "opis": "pada", "dowod": {"a": 1}}],
            "pominiete": [],
            "zuzycie": {"tokens_in": 6, "tokens_out": 23129, "koszt_usd": 0.63},
            "wywolania_narzedzi": ["zapytaj_snapshot:osoba"],
        },
        "z_szablonu": 8,
    }
    parametry.update(nadpisz)
    return zbuduj_trace_analizy(**parametry)


def test_obraz_konta_nie_wychodzi_tylko_jego_hasz() -> None:
    """W nowej ścieżce inwentarz jedzie w ZADANIU, nie w prompcie systemowym.
    Wysłanie zadania w całości obeszłoby regułę z CLAUDE.md bocznymi drzwiami."""
    trace = _trace_analizy()

    assert trace.metadane["obraz_hash"] == "o456"
    assert "zastrzezenia" not in str(trace)
    assert "OBRAZ KONTA" not in str(trace)


def test_hasz_obrazu_nie_zalezy_od_kolejnosci_kluczy() -> None:
    """Kolejność kluczy nie jest treścią. Bez `sort_keys` ten sam obraz dawałby
    różne hasze i porównanie między runami by kłamało."""
    from monday_audit.obserwowalnosc import hasz_obrazu

    assert hasz_obrazu({"a": 1, "b": 2}) == hasz_obrazu({"b": 2, "a": 1})
    assert hasz_obrazu({"a": 1}) != hasz_obrazu({"a": 2})


def test_wyjscie_generacji_to_tylko_rozstrzygniecia_modelu() -> None:
    """Uwagi z szablonów nie są wywołaniem modelu. Wmieszane do wyjścia
    generacji kazałyby czytającemu przypisać modelowi coś, czego nie napisał —
    dlatego ich liczba jest w metadanych, a nie w wyjściu."""
    trace = _trace_analizy()

    generacja = trace.obserwacje[0]
    assert generacja.rodzaj == RODZAJ_GENERACJA
    assert len(generacja.wyjscie["uwagi"]) == 1
    assert trace.metadane["uwag_z_szablonu"] == 8


def test_odrzucone_widac_jako_reguly_a_nie_tresc() -> None:
    """„Odrzucono 9" i „odrzucono 9 za brak pola w dowodzie" to dwie różne
    poprawki. Nazwy reguł są naszymi stałymi, bez danych klienta."""
    trace = _trace_analizy(
        odrzucone_reguly=["dowod nie pokrywa pol", "dowod nie pokrywa pol", "brak pola"]
    )

    assert trace.metadane["uwag_odrzuconych"] == 3
    assert trace.metadane["odrzucone_reguly"] == {"dowod nie pokrywa pol": 2, "brak pola": 1}


def test_zuzycie_i_koszt_sesji_trafiaja_do_generacji() -> None:
    generacja = _trace_analizy().obserwacje[0]

    assert generacja.zuzycie["output"] == 23129
    assert generacja.koszt_usd == 0.63


def test_trace_awarii_niesie_blad_a_nie_puste_wyjscie() -> None:
    """Run, który padł, jest najciekawszy w trace'ach — i musi mówić, CO padło."""
    trace = _trace_analizy(odpowiedz=None, blad="AgentError: sesja padła")

    assert trace.metadane["rozstrzygniecie"] == "blad"
    assert trace.obserwacje[0].wyjscie == {"blad": "AgentError: sesja padła"}


def test_mail_w_uwadze_modelu_jest_zamaskowany_i_policzony() -> None:
    """Ta sama bramka co w starej ścieżce — trafienie to alarm, nie sukces."""
    trace = _trace_analizy(odpowiedz={"uwagi": [{"opis": "zgłasza lead@obcy.test"}], "zuzycie": {}})

    assert "[E-MAIL]" in trace.obserwacje[0].wyjscie["uwagi"][0]["opis"]
    assert trace.metadane["trafien_maskowania"] == 1


# ── wspólna reguła „nie wywracaj runu, ale nie milcz" ────────────────────


def test_wspolna_wysylka_przelyka_awarie_sieci(caplog: Any) -> None:
    from monday_audit.obserwowalnosc import wyslij_bezpiecznie

    with caplog.at_level(logging.WARNING):
        wyslij_bezpiecznie(
            AtrapaSladu(blad=ConnectionError("Langfuse leży")),
            _trace_analizy,
            opis="analiza x",
        )

    assert "nie udało się wysłać" in caplog.text


def test_wspolna_wysylka_krzyczy_przy_bezpieczniku_z_budowy(caplog: Any) -> None:
    """`MaskowanieError` rodzi się zwykle przy BUDOWIE trace'u, nie przy
    wysyłce — dlatego `wyslij_bezpiecznie` bierze funkcję, a nie gotowy trace."""
    from monday_audit.maskowanie import MaskowanieError
    from monday_audit.obserwowalnosc import wyslij_bezpiecznie

    def budowa_padajaca() -> Any:
        raise MaskowanieError("nieznany typ w polu x")

    with caplog.at_level(logging.ERROR):
        wyslij_bezpiecznie(AtrapaSladu(), budowa_padajaca, opis="analiza x")

    assert "trace NIE wyszedł" in caplog.text


def test_stara_sciezka_korzysta_z_tej_samej_reguly() -> None:
    """Dwie kopie reguły o bezpieczniku to dwie okazje, żeby jedna z nich
    zaczęła łapać `MaskowanieError` razem z błędami sieci."""
    import inspect

    from monday_audit import agent

    assert "wyslij_bezpiecznie" in inspect.getsource(agent._wyslij_slad)
    assert "except MaskowanieError" not in inspect.getsource(agent._wyslij_slad)


# ── bez tożsamości w trace (decyzja Kuby 2026-09-23) ─────────────────────


PSEUDONIM = "1dcfeabe7fa5d9a7"


def test_pseudonim_w_faktach_i_metadanych_staje_sie_osoba() -> None:
    """`user_hash` w faktach ZOMBIE_ACCOUNT i `obiekt_id` w metadanych to ten
    sam pseudonim. Oba wychodziły do Langfuse'a wprost."""
    hipoteza = AtrapaHipotezy(
        klasa_id="ZOMBIE_ACCOUNT",
        obiekt_id=PSEUDONIM,
        zapis={
            "obiekt_id": PSEUDONIM,
            "fakty": {"user_hash": PSEUDONIM, "guest_hash": [PSEUDONIM]},
        },
    )

    trace = _zbuduj(AtrapaWyniku(hipoteza=hipoteza, finding={"dowod": {"user_hash": PSEUDONIM}}))

    assert PSEUDONIM not in repr(trace)
    assert trace.metadane["obiekt_id"] == "[OSOBA]"
    assert trace.obserwacje[0].wejscie["fakty"] == {
        "user_hash": "[OSOBA]",
        "guest_hash": ["[OSOBA]"],
    }


def test_hasz_promptu_nie_jest_mylony_z_osoba() -> None:
    """`prompt_hash` i `obraz_hash` mają kształt pseudonimu, a są haszem pliku —
    bez nich trace traci porównywalność między runami."""
    from monday_audit.obserwowalnosc import zbuduj_trace_analizy

    trace = zbuduj_trace_analizy(
        run_id="r",
        snapshot_id=1,
        model="m",
        prompt_hash="e434645e0836d635",
        obraz_hash="44136fa355b3678a",
        hipotezy=[{"obiekt_id": PSEUDONIM}],
        odpowiedz={"uwagi": [], "pominiete": []},
        z_szablonu=0,
    )

    assert trace.metadane["prompt_hash"] == "e434645e0836d635"
    assert trace.metadane["obraz_hash"] == "44136fa355b3678a"
    assert trace.obserwacje[0].wejscie == {"hipotezy": [{"obiekt_id": "[OSOBA]"}]}


def test_sciezki_trafien_tez_bez_pseudonimu() -> None:
    """Review 2026-09-23: `pola_z_trafieniami` składało się z kluczy, a klucz
    mapy `tablice_dostepne` to pseudonim gościa — wychodził obok czystych danych."""
    from monday_audit.obserwowalnosc import zbuduj_trace_analizy

    trace = zbuduj_trace_analizy(
        run_id="r",
        snapshot_id=1,
        model="m",
        prompt_hash="p",
        obraz_hash="o",
        hipotezy=[{"fakty": {"tablice_dostepne": {PSEUDONIM: ["Kontakt biuro@klient.test"]}}}],
        odpowiedz={"uwagi": [], "pominiete": []},
        z_szablonu=0,
    )

    assert PSEUDONIM not in repr(trace.metadane)
    assert trace.metadane["pola_z_trafieniami"] == [
        "wejscie.hipotezy[0].fakty.tablice_dostepne.[OSOBA][0]"
    ]


# ── narzędzia i zadanie w trace (zgłoszone przez Kubę 2026-09-24) ─────────


def _odpowiedz_z_przebiegiem(przebieg: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "uwagi": [],
        "pominiete": [],
        "zuzycie": {},
        "wywolania_narzedzi": [f"{w['narzedzie']}:x" for w in przebieg],
        "przebieg_narzedzi": przebieg,
    }


def test_narzedzie_niesie_argumenty_i_wynik_po_maskowaniu() -> None:
    """Trace pokazywał same nazwy narzędzi. Teraz wejście i wyjście — ale przez
    tę samą bramkę: pseudonim → [OSOBA], e-mail → [E-MAIL]."""
    przebieg = [
        {
            "narzedzie": "zapytaj_snapshot",
            "argumenty": {"pytanie": "osoba", "obiekt_id": "a1b2c3d4e5f60718"},
            "wynik": {"user_hash": "a1b2c3d4e5f60718", "notatka": "pisz do jan@firma.test"},
            "start": "2026-09-24T09:00:00Z",
            "ms": 3,
        }
    ]

    trace = _trace_analizy(odpowiedz=_odpowiedz_z_przebiegiem(przebieg))

    [narzedzie] = [o for o in trace.obserwacje if o.nazwa.startswith("narzedzie:")]
    assert narzedzie.nazwa == "narzedzie:zapytaj_snapshot:osoba"
    assert narzedzie.wejscie == {"pytanie": "osoba", "obiekt_id": "[OSOBA]"}
    assert narzedzie.wyjscie == {"user_hash": "[OSOBA]", "notatka": "pisz do [E-MAIL]"}
    assert narzedzie.metadane == {"kolejnosc": 1, "start": "2026-09-24T09:00:00Z", "ms": 3}
    assert "a1b2c3d4e5f60718" not in str(trace)
    assert trace.metadane["trafien_maskowania"] == 1


def test_narzedzie_ktore_padlo_jest_bledem_w_trace() -> None:
    przebieg = [
        {"narzedzie": "probka_kolumn", "argumenty": {"board_id": "7"}, "blad": "NarzedzieError: x"}
    ]

    trace = _trace_analizy(odpowiedz=_odpowiedz_z_przebiegiem(przebieg))

    [narzedzie] = [o for o in trace.obserwacje if o.nazwa.startswith("narzedzie:")]
    assert (narzedzie.poziom, narzedzie.komunikat) == ("ERROR", "NarzedzieError: x")


def test_wejscie_generacji_jest_rozmowa_z_haszami() -> None:
    """Langfuse odtwarza generację z listy wiadomości. Prompt systemowy i obraz
    konta zostają haszami — to samo zadanie, co dostał model, bez obrazu."""
    from monday_audit.analiza import zbuduj_zadanie
    from monday_audit.detektory import Hipoteza

    hipotezy = [Hipoteza(klasa_id="BOARD_GHOST", obiekt_id="b1", fakty={"nazwa": "x"})]
    wejscie = {"konto": {"tajne": "Obraz"}, "zastrzezenia": ["[tablice] cos"]}
    zadanie = zbuduj_zadanie(hipotezy, wejscie, obraz_zastepczy="[obraz konta — tylko hasz o456]")

    trace = _trace_analizy(zadanie=zadanie)

    [generacja] = [o for o in trace.obserwacje if o.rodzaj == "generation"]
    system, user = generacja.wejscie
    assert system == {"role": "system", "content": "[prompt systemowy — tylko hasz p123]"}
    assert user["role"] == "user"
    assert "[obraz konta — tylko hasz o456]" in user["content"]
    assert "## HIPOTEZY DO ROZSTRZYGNIĘCIA (1)" in user["content"]
    assert "tajne" not in user["content"] and "[tablice] cos" not in user["content"]


def test_awaria_parsowania_niesie_surowy_tekst_po_maskowaniu() -> None:
    """2026-09-24: trace awarii miał sam komunikat — tekstu modelu nie było gdzie obejrzeć."""
    trace = _trace_analizy(
        blad="AnalizaError: nie jest JSON-em",
        odpowiedz={"surowy_tekst": '{"uwagi": [ pisz do jan@firma.test', "przebieg_narzedzi": []},
    )

    [generacja] = [o for o in trace.obserwacje if o.rodzaj == "generation"]
    assert generacja.wyjscie["surowy_tekst"] == '{"uwagi": [ pisz do [E-MAIL]'
