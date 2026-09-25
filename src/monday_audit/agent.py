"""Pętla agenta analitycznego (etap 3.11).

Przepływ: hipotezy → agent bada każdą w ramach budżetu → JSON → walidacja
(`kontrakt`) → baza.

**Jedna sesja na hipotezę, nie jedna na run.** Trzy powody, wszystkie
praktyczne:

1. Budżet jest per hipoteza (rubryka), a narzędzia są domknięciami nad
   `NarzedziaHipotezy`. Wspólna sesja musiałaby przełączać licznik w trakcie,
   czyli trzymać w jednym miejscu stan, który ma być rozdzielony.
2. Kontekst zostaje mały. Dziewiętnaście hipotez w jednej rozmowie to pod
   koniec kilkadziesiąt wyników narzędzi, których model już nie czyta.
3. Jedna hipoteza z treścią klienta zawierającą prompt injection nie zatruwa
   pozostałych osiemnastu — sesja kończy się razem z nią.

Prompt systemowy jest IDENTYCZNY w każdej sesji (`PROMPT_AGENTA.md` +
inwentarz), więc prompt caching z D2 nadal działa: to ten sam prefiks
w każdym wywołaniu.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from monday_audit.baza import MapowanieOsob
from monday_audit.cennik import Stawka
from monday_audit.detektory import Hipoteza
from monday_audit.narzedzia import Narzedzia, NarzedziaHipotezy
from monday_audit.obserwowalnosc import Wysylka, wyslij_bezpiecznie, zbuduj_trace
from monday_audit.osoby import MaPII
from monday_audit.rubryka import Klasa, Rubryka
from monday_audit.sdk import (
    MODEL,
    SCIEZKA_PROMPTU,
    AgentError,
    _blad_api,
    _tekst_promptu,
    _wyluskaj_json,
    _zbuduj_narzedzia,
    _zuzycie,
    hash_promptu,
    zbuduj_opcje,
)
from monday_audit.szablony_findingow import z_szablonu

logger = logging.getLogger(__name__)


@dataclass
class WynikHipotezy:
    """Rozstrzygnięcie jednej hipotezy. Kształt wewnętrzny, nie D8.

    D8 opisuje dokument CAŁEGO runu. Tu jest jedna hipoteza, a dokument
    składamy z tych części — dlatego to osobny, mniejszy kształt.
    """

    hipoteza: Hipoteza
    finding: dict[str, Any] | None = None
    odrzucona: dict[str, Any] | None = None
    wywolania_narzedzi: list[str] = field(default_factory=list)
    przebieg_narzedzi: list[dict[str, Any]] = field(default_factory=list)
    zuzycie: dict[str, float] = field(default_factory=dict)
    blad: str | None = None
    # ── ROZBICIE WYJŚCIA (etap 4, instrumentacja) ─────────────────────────
    #
    # `tokens_out` skleja trzy rzeczy o TRZECH RÓŻNYCH dźwigniach, a bez ich
    # rozdzielenia wybór dźwigni jest zgadywaniem po ~0,6 USD za próbę:
    #
    #   1. tokeny MYŚLENIA — model ma myślenie adaptacyjne, `display: omitted`,
    #      więc nie pojawiają się w żadnym `TextBlock`. Dźwignia: `effort`.
    #   2. wcześniejsze `TextBlock` — pętla niżej bierze OSTATNI niepusty blok,
    #      więc poprzednie są rozliczone w tokenach i wyrzucone. Dźwignia:
    #      instrukcja w prompcie.
    #   3. finalny JSON — to, co faktycznie trafia do raportu. Dźwignia: limit
    #      długości w kontrakcie.
    #
    # ZMIERZONE przed tą zmianą (szacunek: znaki findingu / 3,3): 79-86% wyjścia
    # to NIE finding. Ale szacunek nie rozdziela punktu 1 od 2, a to one decydują,
    # czy ciągnąć `effort`, czy prompt.
    #
    # Liczymy ZNAKI, nie tokeny — tokenizatora tu nie mamy, a stosunek znaków
    # wystarcza do rozstrzygnięcia proporcji.
    blokow_tekstu: int = 0
    znakow_wyrzuconych: int = 0
    znakow_finalnych: int = 0


def _inwentarz(narzedzia: Narzedzia) -> str:
    """Stały prefiks kontekstu: podsumowania, nie pełne listy.

    Idzie do promptu systemowego, więc jest identyczny w każdej sesji runu —
    i dzięki temu podlega prompt cachingowi (D2). Pełne listy tu NIE wchodzą:
    105 tablic w każdej sesji to koszt bez wartości, a szczegół agent bierze
    narzędziem, gdy jest mu potrzebny.
    """
    sekcje = {
        "meta": narzedzia.wycinek("$.meta"),
        "konto": narzedzia.wycinek("$.konto"),
        "uzytkownicy": narzedzia.wycinek("$.uzytkownicy.podsumowanie"),
        "uzytkownicy_discovery": narzedzia.wycinek("$.uzytkownicy.discovery"),
        "tablice": narzedzia.wycinek("$.tablice.podsumowanie"),
        "tablice_discovery": narzedzia.wycinek("$.tablice.discovery"),
        "automatyzacje": narzedzia.wycinek("$.automatyzacje.podsumowanie"),
        "automatyzacje_uruchomienia": narzedzia.wycinek("$.automatyzacje.uruchomienia"),
        "aktywnosc": narzedzia.wycinek("$.aktywnosc.podsumowanie"),
        "aktywnosc_discovery": narzedzia.wycinek("$.aktywnosc.discovery"),
        # PEŁNA lista aktywności per osoba, nie podsumowanie — wyjątek od reguły
        # „tu wchodzą tylko agregaty", i to świadomy.
        #
        # Powód: klasy o użytkownikach (`ZOMBIE_ACCOUNT`, `ENGAGEMENT_DROP`,
        # `GUEST_SPRAWL`) pytają o KONKRETNE osoby, a nie o rozkład. Bez tej sekcji
        # agent musiałby przejść narzędziem 100 tablic i sam zsumować akcje per
        # osoba — czyli płacić rozumowaniem za coś deterministycznego (D1).
        #
        # ZMIERZONE: ~550 tokenów dla 8 aktywnych osób. Idzie do prefiksu, więc
        # przy prompt cachingu (79% z cache, pomiar 2026-08-12) to ~0,002 USD na
        # kilkanaście hipotez. Lista rośnie z liczbą osób WIDOCZNYCH W LOGACH, nie
        # z liczbą kont — na koncie CXLABS to 8 z 94.
        "aktywnosc_per_uzytkownik": narzedzia.wycinek("$.aktywnosc.per_uzytkownik"),
    }
    return json.dumps(sekcje, ensure_ascii=False, indent=1)


def _opis_wyceny(klasa: Klasa, stawki: dict[str, Stawka]) -> str:
    """Sekcja PARAMETRY WYCENY do zadania. Albo stawka, albo jawny zakaz.

    Nigdy nie zostawiamy tego pola pustym: agent bez informacji dopuszcza
    założenie, a założona stawka w raporcie klienta to najgorszy możliwy
    błąd tego produktu.
    """
    if not klasa.ma_wycene:
        return (
            f"Ta klasa ma `typ_wyceny: {klasa.typ_wyceny}` i NIE PODAJE KWOTY. "
            f"`kwota_pln` musi być `null`."
        )

    brakujace = [z for z in klasa.zmienne_od_klienta if z not in stawki]
    if brakujace:
        return (
            f"Wzór: {klasa.wzor}\n"
            f"BRAK STAWEK: {', '.join(brakujace)}. Nie podano ich przy tym runie, "
            f"więc `kwota_pln` musi być `null`. NIE zakładaj żadnej stawki "
            f"i nie bierz jej z pamięci — walidacja odrzuci taki finding."
        )

    linie = [f"Wzór: {klasa.wzor}", "Stawki dostępne w tym runie:"]
    for nazwa in klasa.zmienne_od_klienta:
        stawka = stawki[nazwa]
        linie.append(
            f"  {nazwa} = {stawka.wartosc} {stawka.waluta or stawka.jednostka} "
            f"(źródło: {stawka.zrodlo})"
        )
    linie.append("Policz kwotę z tego wzoru i tych stawek. Nic nie zakładaj.")
    return "\n".join(linie)


def _opis_klasy(klasa: Klasa) -> str:
    """Definicja klasy z rubryki. To jest skill agenta, nie kontekst dodatkowy."""
    return json.dumps(
        {
            "klasa_id": klasa.id,
            "nazwa": klasa.nazwa,
            "sygnal": klasa.sygnal,
            "rola_agenta": klasa.rola_agenta,
            "warunki_odrzucenia": list(klasa.warunki_odrzucenia),
            "waga": klasa.waga,
            "wysilek_naprawy": klasa.wysilek_naprawy,
            "typ_wyceny": klasa.typ_wyceny,
            "dowod_wymagany": list(klasa.dowod),
            "budzet_wywolan": klasa.budzet_wywolan,
            # Wzór i zmienne. Bez nich agent nie ma czym policzyć kwoty —
            # i dokładnie dlatego pełny run 19 hipotez dał `kwota_pln: null`
            # nawet w klasach, gdzie kwota była przewidziana.
            "wzor": klasa.wzor,
            "zmienne_od_klienta": list(klasa.zmienne_od_klienta),
        },
        ensure_ascii=False,
        indent=1,
    )


ZADANIE = """\
Rozstrzygnij DOKŁADNIE JEDNĄ hipotezę.

## Definicja klasy z rubryki

{klasa}

## Hipoteza

{hipoteza}

## PARAMETRY WYCENY

{wycena}

## Co masz zwrócić

Ostatnia wiadomość musi być SAMYM obiektem JSON, bez komentarza i bez bloku
kodu. Dwie dopuszczalne postaci:

Potwierdzenie:
{{"rozstrzygniecie": "finding", "finding": {{
  "klasa_id": "{klasa_id}", "waga": "{waga}", "wysilek_naprawy": "{wysilek}",
  "typ_wyceny": "{typ_wyceny}", "kwota_pln": null,
  "opis": "...", "rekomendacja": "...",
  "dowod": {{ ... pola: {dowod} ... }},
  "pewnosc": "wysoka|srednia|niska"}}}}

Odrzucenie:
{{"rozstrzygniecie": "odrzucona", "powod": "dlaczego hipoteza nie wytrzymuje"}}

`waga`, `wysilek_naprawy` i `typ_wyceny` przepisz z definicji klasy — nie są
twoją decyzją. `kwota_pln` zostaw `null`, chyba że klasa ma `typ_wyceny:
oszczednosc_bezposrednia` i znasz wszystkie liczby ze wzoru.
"""


async def zbadaj_hipoteze(
    hipoteza: Hipoteza,
    *,
    zestaw: Narzedzia,
    rubryka: Rubryka,
    prompt: str,
    inwentarz: str,
    serwer: Any,
    biezace: dict[str, NarzedziaHipotezy],
    klucz_api: str,
    stawki: dict[str, Stawka] | None = None,
    model: str = MODEL,
    effort: str | None = None,
) -> WynikHipotezy:
    """Jedna hipoteza, jedna sesja, budżet z rubryki."""
    klasa = rubryka.po_id[hipoteza.klasa_id]
    narzedzia_hipotezy = zestaw.dla_hipotezy(hipoteza)
    biezace["aktywne"] = narzedzia_hipotezy

    opcje = zbuduj_opcje(
        prompt=prompt,
        inwentarz=inwentarz,
        snapshot_id=zestaw.snapshot_id,
        serwer=serwer,
        klucz_api=klucz_api,
        model=model,
        effort=effort,
    )
    zadanie = ZADANIE.format(
        klasa=_opis_klasy(klasa),
        wycena=_opis_wyceny(klasa, stawki or {}),
        hipoteza=json.dumps(hipoteza.do_zapisu(), ensure_ascii=False, indent=1),
        klasa_id=klasa.id,
        waga=klasa.waga,
        wysilek=klasa.wysilek_naprawy,
        typ_wyceny=klasa.typ_wyceny,
        dowod=", ".join(klasa.dowod),
    )

    wynik = WynikHipotezy(hipoteza=hipoteza)
    # Wszystkie niepuste bloki tekstu, nie tylko ostatni. Zachowanie się NIE
    # zmienia — `ostatni_tekst` to nadal ostatni blok — ale wcześniejsze
    # przestają ginąć bez śladu. To one są kandydatem na 79-86% rachunku za
    # wyjście i bez ich policzenia nie wiadomo, czy skracać prompt, czy `effort`.
    bloki: list[str] = []
    try:
        async with ClaudeSDKClient(options=opcje) as klient:
            await klient.query(zadanie)
            async for wiadomosc in klient.receive_response():
                if isinstance(wiadomosc, AssistantMessage):
                    for blok in wiadomosc.content:
                        if isinstance(blok, TextBlock) and blok.text.strip():
                            bloki.append(blok.text)
                elif isinstance(wiadomosc, ResultMessage):
                    wynik.zuzycie = _zuzycie(wiadomosc)
                    # BŁĄD API NIE RZUCA WYJĄTKU. Wraca jako `is_error=True`
                    # w `ResultMessage` — i to przy `subtype='success'`, co
                    # jest mylące. Bez tego sprawdzenia nieprawidłowy klucz
                    # albo limit API objawiałby się jako „nie znalazłem JSON-a"
                    # przy KAŻDEJ hipotezie: dziewiętnaście niejasnych błędów
                    # parsowania zamiast jednego czytelnego „401".
                    # Zmierzone 2026-08-05 na celowo złym kluczu.
                    if wiadomosc.is_error:
                        wynik.blad = _blad_api(wiadomosc)
    except Exception as blad:  # jedna hipoteza nie może wywrócić całego runu
        wynik.blad = f"{type(blad).__name__}: {blad}"
        logger.warning("hipoteza %s/%s: %s", hipoteza.klasa_id, hipoteza.obiekt_id, wynik.blad)
        return wynik
    finally:
        wynik.wywolania_narzedzi = list(narzedzia_hipotezy.wywolania)
        wynik.przebieg_narzedzi = list(narzedzia_hipotezy.przebieg)
        biezace.pop("aktywne", None)
        # W `finally`, bo pomiar ma przeżyć także padniętą hipotezę — sesja
        # zerwana po trzech blokach rozumowania zapłaciła za te bloki tak samo.
        wynik.blokow_tekstu = len(bloki)
        wynik.znakow_finalnych = len(bloki[-1]) if bloki else 0
        wynik.znakow_wyrzuconych = sum(len(b) for b in bloki[:-1])

    if wynik.blad:
        # Błąd po stronie API. Nie próbujemy parsować odpowiedzi, której nie ma.
        logger.warning("hipoteza %s/%s: %s", hipoteza.klasa_id, hipoteza.obiekt_id, wynik.blad)
        return wynik

    try:
        rozstrzygniecie = _wyluskaj_json(bloki[-1] if bloki else "")
    except AgentError as blad:
        wynik.blad = str(blad)
        return wynik

    if rozstrzygniecie.get("rozstrzygniecie") == "odrzucona":
        wynik.odrzucona = {
            "klasa_id": hipoteza.klasa_id,
            "obiekt_id": hipoteza.obiekt_id,
            "powod": str(rozstrzygniecie.get("powod") or "brak powodu"),
        }
    else:
        wynik.finding = rozstrzygniecie.get("finding")
        if not isinstance(wynik.finding, dict):
            wynik.blad = "brak obiektu `finding` w rozstrzygnięciu"
    return wynik


def wpisy_do_maskowania(zestaw: Narzedzia) -> tuple[MaPII, ...]:
    """Znane osoby konta — dla DRUGIEJ siatki w trace'ach, nie dla modelu.

    `maskowanie.zamaskuj` od fazy 4 przyjmuje tę listę i podmienia znane
    imiona na pseudonimy tej samej osoby. Do review 2026-09-23 nikt jej nie
    podawał, więc obietnica z docstringu `maskowanie.py` była martwa w obu
    ścieżkach.

    Lista żyje w procesie i idzie wyłącznie do maskowania. Do modelu, do jego
    narzędzi ani do trace'u nie trafia — zakaz „tabela mapowania bez narzędzia
    dostępowego" dotyczy agenta, a to jest nasz kod.
    """
    return tuple(MapowanieOsob(zestaw.con, zestaw.client_id).wczytaj())


def _wyslij_slad(
    slad: Wysylka | None,
    wynik: WynikHipotezy,
    *,
    run_id: str,
    snapshot_id: int,
    model: str,
    prompt_hash: str,
    wpisy: tuple[MaPII, ...] = (),
) -> None:
    """Trace jednej hipotezy. NIGDY nie wywraca runu — ale nie milczy.

    Sama reguła („bezpiecznik maskowania głośno, awaria sieci cicho, audyt
    zawsze do końca") mieszka w `obserwowalnosc.wyslij_bezpiecznie`, bo nowa
    ścieżka (`cli_analiza`) potrzebuje dokładnie tej samej. Tutaj zostaje tylko
    to, co jest specyficzne dla starej ścieżki: jak z `WynikHipotezy` zrobić
    trace.
    """
    wyslij_bezpiecznie(
        slad,
        lambda: zbuduj_trace(
            wynik,
            run_id=run_id,
            snapshot_id=snapshot_id,
            model=model,
            prompt_hash=prompt_hash,
            wpisy=wpisy,
        ),
        opis=f"hipoteza {wynik.hipoteza.klasa_id}/{wynik.hipoteza.obiekt_id}",
    )


async def zbadaj_hipotezy(
    hipotezy: list[Hipoteza],
    *,
    zestaw: Narzedzia,
    rubryka: Rubryka,
    run_id: str,
    klucz_api: str,
    stawki: dict[str, Stawka] | None = None,
    model: str = MODEL,
    effort: str | None = None,
    sciezka_promptu: Path = SCIEZKA_PROMPTU,
    postep: Callable[[int, int, str], None] | None = None,
    slad: Wysylka | None = None,
) -> dict[str, Any]:
    """Bada wszystkie hipotezy i składa dokument D8. Nie waliduje — to `kontrakt`.

    `slad=None` (domyślnie) znaczy „bez trace'ów" i jest stanem poprawnym:
    audyt bez obserwowalności działa tak samo, traci tylko podgląd. Parametr
    zamiast odczytu konfiguracji w środku, z tego samego powodu co `klucz_api`
    w `zbuduj_opcje` — inaczej test nie ma jak sprawdzić, co poszło na zewnątrz.

    `postep(zbadanych, wszystkich, klasa_id)` jest wołane po KAŻDEJ hipotezie.
    `None` = cisza, więc CLI i testy nie muszą nic podawać.

    ZGŁOSZONE (Kuba, 2026-08-25): „patrzysz w to i nie wiesz, kiedy co się
    stanie, za ile się stanie". Pętla liczyła `[24/24]` od zawsze, ale wysyłała
    to tylko do logu — ekran dostawał JEDEN zapis stanu na całe dziewięć minut
    analizy. Ten sam wzorzec co `postep` w `MondayClient`: warstwa liczy,
    wywołujący decyduje, co z tym zrobić.

    Rozdzielenie jest celowe: pętla ma zwrócić to, co agent faktycznie
    powiedział, a walidacja ma to ocenić. Gdyby pętla poprawiała odpowiedzi
    w locie, odsetek odrzuconych — główna metryka etapu 4 — pokazywałby
    jakość naszych łatek, nie jakość agenta.
    """
    prompt = _tekst_promptu(sciezka_promptu)
    inwentarz = _inwentarz(zestaw)
    # Liczony RAZ, poza pętlą: ten sam prompt dla wszystkich hipotez runu,
    # a do trace'u idzie hasz zamiast treści (powód w `obserwowalnosc`).
    hasz_promptu = hash_promptu(sciezka_promptu)
    wpisy_sladu = wpisy_do_maskowania(zestaw) if slad is not None else ()
    biezace: dict[str, NarzedziaHipotezy] = {}
    serwer = _zbuduj_narzedzia(biezace)

    findings: list[dict[str, Any]] = []
    odrzucone: list[dict[str, Any]] = []
    bledy: list[dict[str, str]] = []
    # Zużycie PER HIPOTEZA, nie tylko suma. Do 2026-08-11 pętla sumowała
    # `wynik.zuzycie` i wyrzucała szczegóły — więc nie dało się powiedzieć, KTÓRE
    # KLASY są drogie, a od tego zależy każda decyzja o optymalizacji (etap 4).
    per_hipoteza: list[dict[str, Any]] = []
    zuzycie: dict[str, float] = {
        "wywolania": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "koszt_usd": 0.0,
    }

    for numer, hipoteza in enumerate(hipotezy, start=1):
        logger.info(
            "[%d/%d] %s %s (budżet %d)",
            numer,
            len(hipotezy),
            hipoteza.klasa_id,
            hipoteza.obiekt_id,
            hipoteza.budzet_wywolan,
        )
        # `monotonic`, nie `datetime.now`: zegar systemowy potrafi skoczyć w trakcie
        # godzinnego runu i dać czas ujemny.
        zaczeto = time.monotonic()
        # ── ŚCIEŻKA BEZ MODELU ────────────────────────────────────────────
        #
        # Klasa z `rola_agenta: brak` i szablonem nie wchodzi do sesji wcale.
        # ZMIERZONE na `ZOMBIE_ACCOUNT`: szablon daje trafność 1,000, fałszywki
        # 0,000 i rzeczowość 1,000 — te same liczby co model, za 0 USD zamiast
        # 0,357 USD na run. Model przepisywał `dowod` z faktów detektora bajt
        # w bajt i dokładał zdanie.
        #
        # Rozdzielenie idzie TUTAJ, nie w `zbadaj_hipoteze`: tamta funkcja jest
        # o prowadzeniu sesji, a nie o tym, czy sesja jest potrzebna.
        szablonowy = z_szablonu(hipoteza, rubryka.po_id[hipoteza.klasa_id])
        if szablonowy is not None:
            wynik = WynikHipotezy(hipoteza=hipoteza, finding=szablonowy)
        else:
            wynik = await zbadaj_hipoteze(
                hipoteza,
                zestaw=zestaw,
                rubryka=rubryka,
                prompt=prompt,
                inwentarz=inwentarz,
                serwer=serwer,
                biezace=biezace,
                klucz_api=klucz_api,
                stawki=stawki,
                model=model,
                effort=effort,
            )
        sekund = round(time.monotonic() - zaczeto, 3)
        _wyslij_slad(
            slad,
            wynik,
            run_id=run_id,
            snapshot_id=zestaw.snapshot_id,
            model=model,
            prompt_hash=hasz_promptu,
            wpisy=wpisy_sladu,
        )
        for klucz in ("tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_write"):
            zuzycie[klucz] += wynik.zuzycie.get(klucz, 0)
        zuzycie["koszt_usd"] += wynik.zuzycie.get("koszt_usd", 0.0)
        wywolan_hipotezy = sum(
            1 for w in wynik.wywolania_narzedzi if w.startswith(("probka_kolumn", "log_tablicy"))
        )
        zuzycie["wywolania"] += wywolan_hipotezy
        per_hipoteza.append(
            {
                "klasa_id": hipoteza.klasa_id,
                "obiekt_id": hipoteza.obiekt_id,
                "tokens_in": int(wynik.zuzycie.get("tokens_in", 0)),
                "tokens_out": int(wynik.zuzycie.get("tokens_out", 0)),
                "tokens_cache_read": int(wynik.zuzycie.get("tokens_cache_read", 0)),
                "tokens_cache_write": int(wynik.zuzycie.get("tokens_cache_write", 0)),
                "koszt_usd": float(wynik.zuzycie.get("koszt_usd", 0.0)),
                "sekund": sekund,
                "wywolan_narzedzi": wywolan_hipotezy,
                # Hipoteza ODRZUCONA też kosztuje — i to jest istotna liczba:
                # jeśli większość kończy się odrzuceniem, płacimy głównie za
                # dowiadywanie się, że czegoś NIE MA.
                "byl_finding": bool(wynik.finding),
                # Rozbicie wyjścia — patrz komentarz przy `WynikHipotezy`.
                # `tokens_out` minus (znaki / ~3,3) to tokeny myślenia, których
                # nie widać w żadnym bloku tekstu.
                "blokow_tekstu": wynik.blokow_tekstu,
                "znakow_wyrzuconych": wynik.znakow_wyrzuconych,
                "znakow_finalnych": wynik.znakow_finalnych,
            }
        )
        if wynik.blad:
            bledy.append(
                {
                    "klasa_id": hipoteza.klasa_id,
                    "obiekt_id": hipoteza.obiekt_id,
                    "blad": wynik.blad,
                }
            )
        elif wynik.finding:
            findings.append(wynik.finding)
        elif wynik.odrzucona:
            odrzucone.append(wynik.odrzucona)

        if postep is not None:
            # Po hipotezie, nie przed: „5 z 24" ma znaczyć „pięć zbadanych",
            # nie „zaczynam piątą". Wyjątek z hooka NIE może przerwać analizy,
            # za którą klient już zapłacił — raportowanie postępu jest
            # mniej ważne niż wynik.
            try:
                postep(numer, len(hipotezy), hipoteza.klasa_id)
            except Exception:
                logger.warning("hook postępu padł na hipotezie %d", numer, exc_info=True)

    if bledy:
        # Nie ukrywamy: hipoteza, której nie udało się zbadać, to inna rzecz
        # niż hipoteza odrzucona, i raport nie może ich zlewać.
        logger.warning("%d hipotez nie udało się rozstrzygnąć: %s", len(bledy), bledy)

    return {
        "run_id": run_id,
        "snapshot_id": zestaw.snapshot_id,
        "rubric_version": rubryka.wersja,
        "model": model,
        "findings": findings,
        "hipotezy_odrzucone": odrzucone,
        "hipotezy_nierozstrzygniete": bledy,
        # Stawki użyte w tym runie, z pochodzeniem. Kwota w raporcie klienta
        # bez widocznej stawki jest nieweryfikowalna.
        "parametry_wyceny": {n: s.do_snapshotu() for n, s in (stawki or {}).items()},
        "zuzycie": {**zuzycie, "koszt_usd": round(float(zuzycie["koszt_usd"]), 6)},
        # Rozbicie per hipoteza — do `zuzycie_hipotez` (migracja 010). Osobny klucz,
        # nie wewnątrz `zuzycie`, bo to lista wierszy, a nie sumy; wywołujący zapisuje
        # ją przez `przebieg.zapisz_zuzycie`.
        "per_hipoteza": per_hipoteza,
    }


# ── zapis do przeglądu przez człowieka ───────────────────────────────────

KATALOG_RAPORTOW = Path("raporty")


def zapisz_do_pliku(
    odpowiedz: dict[str, Any],
    wynik_walidacji: Any,
    *,
    katalog: Path = KATALOG_RAPORTOW,
) -> Path:
    """Wynik runu agenta jako czytelny plik tekstowy.

    To NIE jest renderer z 3.12 — ten produkuje raport dla klienta. To zapis
    do przeglądu przez człowieka na etapie budowy: co agent powiedział, co
    walidacja odrzuciła i za ile.

    Katalog jest w `.gitignore`: findingi zawierają nazwy tablic i kolumn
    klienta. Świadomie w repo, nie w katalogu tymczasowym — po pierwszym
    snapshocie okazało się, że `/private/tmp` jest niewidoczne w Finderze
    i nieodtwarzalne.
    """
    katalog.mkdir(parents=True, exist_ok=True)
    cel = katalog / f"agent_{odpowiedz['run_id']}.txt"

    linie: list[str] = [
        "=" * 72,
        f"RUN AGENTA: {odpowiedz['run_id']}",
        "=" * 72,
        f"snapshot       : {odpowiedz['snapshot_id']}",
        f"model          : {odpowiedz['model']}",
        f"rubryka        : {odpowiedz['rubric_version']}",
        "",
        "PARAMETRY WYCENY",
        "-" * 72,
    ]
    parametry = odpowiedz.get("parametry_wyceny") or {}
    if not parametry:
        # Nie usterka. Kwoty wychodzą puste i to jest poprawne — cena licencji
        # jest negocjowana i musi wejść jako parametr runu (O7).
        linie.append("  brak stawek — wszystkie kwoty powinny być null")
    for nazwa, stawka in sorted(parametry.items()):
        zrodlo = "podana dla klienta" if stawka.get("per_klient") else str(stawka.get("zrodlo"))
        wiek = stawka.get("dni_od_odswiezenia")
        linie.append(
            f"  {nazwa}: {stawka.get('wartosc')} "
            f"{stawka.get('waluta') or stawka.get('jednostka')}  ({zrodlo}"
            + (f", odczyt {wiek} dni temu" if wiek is not None else "")
            + ")"
            + ("" if stawka.get("wolno_liczyc") else "  PRZETERMINOWANA — kwoty odrzucane")
        )

    linie += [
        "",
        "ZUŻYCIE",
        "-" * 72,
    ]
    for klucz, wartosc in sorted(odpowiedz["zuzycie"].items()):
        linie.append(f"  {klucz:24} {wartosc:>10,}".replace(",", " "))

    linie += ["", "WALIDACJA", "-" * 72, f"  {wynik_walidacji.opis()}"]
    for odrzucony in wynik_walidacji.odrzucone:
        linie += [
            f"  ODRZUCONY  klasa={odrzucony.klasa_id}",
            f"    reguła : {odrzucony.regula}",
            f"    powód  : {odrzucony.powod}",
        ]

    linie += ["", f"FINDINGI PRZYJĘTE ({len(wynik_walidacji.przyjete)})", "=" * 72]
    for numer, finding in enumerate(wynik_walidacji.przyjete, start=1):
        linie += [
            "",
            f"[{numer}] {finding['klasa_id']}  "
            f"waga={finding['waga']}  pewność={finding['pewnosc']}  "
            f"kwota={finding['kwota_pln']}",
            "",
            "  OPIS",
            *_zawin(str(finding["opis"]), "    "),
            "",
            "  REKOMENDACJA",
            *_zawin(str(finding["rekomendacja"]), "    "),
            "",
            "  DOWÓD",
            *[
                f"    {k}: {json.dumps(v, ensure_ascii=False)}"
                for k, v in sorted(finding["dowod"].items())
            ],
            "-" * 72,
        ]

    linie += [
        "",
        f"HIPOTEZY ODRZUCONE PRZEZ AGENTA ({len(odpowiedz['hipotezy_odrzucone'])})",
        "=" * 72,
    ]
    for odrzucona in odpowiedz["hipotezy_odrzucone"]:
        linie += [
            f"  {odrzucona.get('klasa_id')} / {odrzucona.get('obiekt_id')}",
            *_zawin(str(odrzucona.get("powod")), "    "),
            "",
        ]

    nierozstrzygniete = odpowiedz.get("hipotezy_nierozstrzygniete") or []
    if nierozstrzygniete:
        linie += [
            "",
            f"HIPOTEZY NIEROZSTRZYGNIĘTE ({len(nierozstrzygniete)})",
            "=" * 72,
            "  To NIE to samo co odrzucone — tych agent nie zdołał zbadać.",
            "",
        ]
        for blad in nierozstrzygniete:
            linie.append(f"  {blad['klasa_id']} / {blad['obiekt_id']}: {blad['blad']}")

    linie += [
        "",
        "SUROWA ODPOWIEDŹ AGENTA (do porównania z walidacją)",
        "=" * 72,
        json.dumps(odpowiedz, ensure_ascii=False, indent=1),
    ]

    cel.write_text("\n".join(linie) + "\n", encoding="utf-8")
    return cel


def _zawin(tekst: str, wciecie: str, szerokosc: int = 68) -> list[str]:
    import textwrap

    return [wciecie + w for w in textwrap.wrap(tekst, szerokosc)] or [wciecie + "(puste)"]
