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

**Trzy warstwy odcięcia zapisu**, bo wbudowane narzędzia SDK to `Write`,
`Edit` i `Bash` — czyli zapis do plików, którego zakaz twardy zabrania
wprost („ani do monday, ani do bazy, ani do plików"):

1. `allowed_tools` wymienia WYŁĄCZNIE nasze narzędzia
2. `disallowed_tools` wymienia wbudowane z nazwy — jawnie, nie licząc na to,
   że biała lista wystarczy
3. `can_use_tool` odrzuca w procesie wszystko, czego nie ma na naszej liście.
   To jedyna warstwa, którą kontrolujemy w całości i której model nie widzi

Do tego `setting_sources=[]`: agent nie wczytuje `CLAUDE.md` z tego repo ani
ustawień użytkownika. Bez tego jego zachowanie zależałoby od plików, które
zmieniamy przy każdym etapie, a 05-deploy wymaga, żeby run sprzed trzech
miesięcy był odtwarzalny.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    create_sdk_mcp_server,
    tool,
)

from monday_audit.detektory import Hipoteza
from monday_audit.narzedzia import Narzedzia, NarzedziaHipotezy, NarzedzieError
from monday_audit.rubryka import Klasa, Rubryka

logger = logging.getLogger(__name__)

# Model przypięty pełnym identyfikatorem. 05-deploy zakazuje aliasów typu
# `latest`, bo alias przesuwa się przy nowym wydaniu i wynik zmienia się bez
# zmiany kodu. D2: jeden model w całej pętli, bez routowania — routing przed
# pomiarem to zgadywanie, a pomiary przyjdą z etapu 6.
MODEL = "claude-sonnet-5"

SCIEZKA_PROMPTU = Path("docs/PROMPT_AGENTA.md")

# Nazwa serwera narzędzi w procesie. SDK prefiksuje nią nazwy narzędzi.
SERWER = "audyt"

# Wbudowane narzędzia SDK wymienione z nazwy. `Write`, `Edit` i `Bash` to
# zapis do plików, `WebFetch` i `WebSearch` to wyjście na zewnątrz z treścią
# klienta w zapytaniu. Nie polegamy na tym, że biała lista wystarczy.
WBUDOWANE_ZAKAZANE = (
    "Bash",
    "BashOutput",
    "Edit",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "KillShell",
    "NotebookEdit",
    "Read",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Write",
)

# Sufit obrotów na hipotezę. Nie zastępuje budżetu wywołań (ten liczy wejścia
# do monday), a chroni przed pętlą, w której model woła narzędzia snapshotu
# bez końca — te są darmowe, więc licznik budżetu ich nie zatrzyma.
MAKS_OBROTOW = 12


class AgentError(RuntimeError):
    """Pętla nie da się domknąć — brak promptu, brak klucza, zła odpowiedź."""


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
    zuzycie: dict[str, float] = field(default_factory=dict)
    blad: str | None = None


def _tekst_promptu(sciezka: Path = SCIEZKA_PROMPTU) -> str:
    """Wyciąga prompt z bloku ```` ``` ```` w `PROMPT_AGENTA.md`.

    Plik jest dokumentacją dla człowieka Z promptem w środku, nie samym
    promptem. Bierzemy zawartość pierwszego bloku kodu — nagłówki i uwagi
    („to NIE jest instrukcja dla Claude Code") nie mają prawa trafić do modelu.
    """
    if not sciezka.is_file():
        raise AgentError(f"nie ma pliku promptu: {sciezka.resolve()}")
    tresc = sciezka.read_text(encoding="utf-8")
    bloki = re.findall(r"```\n(.*?)```", tresc, flags=re.DOTALL)
    if not bloki:
        raise AgentError(f"{sciezka}: nie znalazłem bloku z promptem")
    return bloki[0].strip()


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
    }
    return json.dumps(sekcje, ensure_ascii=False, indent=1)


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
        },
        ensure_ascii=False,
        indent=1,
    )


def _zbuduj_narzedzia(biezace: dict[str, NarzedziaHipotezy]) -> Any:
    """Narzędzia w procesie. `biezace` wskazuje zestaw aktywnej hipotezy.

    Domknięcie nad słownikiem, nie nad obiektem: SDK buduje serwer raz, a my
    przełączamy hipotezę między sesjami. Bez tego trzeba by stawiać serwer
    od nowa na każdą hipotezę.
    """

    def teraz() -> NarzedziaHipotezy:
        zestaw = biezace.get("aktywne")
        if zestaw is None:
            raise NarzedzieError("brak aktywnej hipotezy")
        return zestaw

    @tool(
        "pobierz_inwentarz",
        "Podsumowanie sekcji snapshotu. Zakres: konto, uzytkownicy, tablice, "
        "automatyzacje, aktywnosc. Nie zwraca pełnych list — po szczegół obiektu "
        "użyj zapytaj_snapshot.",
        {"zakres": str},
    )
    async def _inwentarz_narzedzie(args: dict[str, Any]) -> dict[str, Any]:
        wynik = teraz().pobierz_inwentarz(str(args["zakres"]))
        return {
            "content": [{"type": "text", "text": json.dumps(wynik.do_modelu(), ensure_ascii=False)}]
        }

    @tool(
        "zapytaj_snapshot",
        "Predefiniowane pytanie do snapshotu. Pytania: tablica, aktywnosc_tablicy, "
        "kolumny_tablicy, osoba, tablice_osoby, automatyzacja, tablice_workspace, "
        "podsumowanie. Wszystkie poza `podsumowanie` wymagają obiekt_id.",
        {"pytanie": str, "obiekt_id": str},
    )
    async def _snapshot_narzedzie(args: dict[str, Any]) -> dict[str, Any]:
        wynik = teraz().zapytaj_snapshot(
            str(args["pytanie"]), str(args.get("obiekt_id") or "") or None
        )
        return {
            "content": [{"type": "text", "text": json.dumps(wynik.do_modelu(), ensure_ascii=False)}]
        }

    @tool(
        "probka_kolumn",
        "Wypełnienie kolumn na próbce itemów tablicy. Zwraca WYŁĄCZNIE liczby "
        "wypełnionych pól, nigdy wartości. Kosztuje jedno wywołanie budżetu.",
        {"board_id": str},
    )
    async def _probka_narzedzie(args: dict[str, Any]) -> dict[str, Any]:
        wynik = await teraz().probka_kolumn(str(args["board_id"]))
        return {
            "content": [{"type": "text", "text": json.dumps(wynik.do_modelu(), ensure_ascii=False)}]
        }

    @tool(
        "log_tablicy",
        "Activity log tablicy w oknie czasowym, z rozkładem po dniach i autorami "
        "jako pseudonimami. Daty w ISO-8601. Kosztuje jedno wywołanie budżetu.",
        {"board_id": str, "od": str, "do": str},
    )
    async def _log_narzedzie(args: dict[str, Any]) -> dict[str, Any]:
        wynik = await teraz().log_tablicy(str(args["board_id"]), str(args["od"]), str(args["do"]))
        return {
            "content": [{"type": "text", "text": json.dumps(wynik.do_modelu(), ensure_ascii=False)}]
        }

    return create_sdk_mcp_server(
        name=SERWER,
        version="1.0.0",
        tools=[_inwentarz_narzedzie, _snapshot_narzedzie, _probka_narzedzie, _log_narzedzie],
    )


NASZE_NARZEDZIA = tuple(
    f"mcp__{SERWER}__{n}"
    for n in ("pobierz_inwentarz", "zapytaj_snapshot", "probka_kolumn", "log_tablicy")
)


async def _pozwolenie(
    narzedzie: str, dane: dict[str, Any], kontekst: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    """Trzecia warstwa odcięcia — w procesie, poza zasięgiem modelu.

    Biała lista w opcjach i czarna lista wbudowanych to konfiguracja, którą
    przekazujemy podprocesowi. To jest kod, który wykonujemy sami, i on ma
    ostatnie słowo. Cokolwiek poza naszymi czterema narzędziami jest odrzucane
    z komunikatem, nie po cichu.
    """
    if narzedzie in NASZE_NARZEDZIA:
        return PermissionResultAllow()
    logger.warning("ODRZUCONE narzędzie %s — agent ma tylko narzędzia czytające", narzedzie)
    return PermissionResultDeny(
        message=(
            f"Narzędzie {narzedzie} nie jest dostępne. Masz wyłącznie narzędzia czytające: "
            f"{', '.join(n.split('__')[-1] for n in NASZE_NARZEDZIA)}."
        )
    )


ZADANIE = """\
Rozstrzygnij DOKŁADNIE JEDNĄ hipotezę.

## Definicja klasy z rubryki

{klasa}

## Hipoteza

{hipoteza}

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


def _wyluskaj_json(tekst: str) -> dict[str, Any]:
    """Wyciąga obiekt JSON z odpowiedzi modelu.

    Model bywa uprzejmy i dokłada zdanie przed albo blok ```json. Kontrakt
    mówi „sam obiekt", ale odrzucenie całej hipotezy za formatowanie byłoby
    marnowaniem zapłaconego wywołania — więc szukamy ostatniego nawiasu
    klamrowego zamiast się obrażać.
    """
    kandydat = tekst.strip()
    if "```" in kandydat:
        bloki = re.findall(r"```(?:json)?\s*(.*?)```", kandydat, flags=re.DOTALL)
        if bloki:
            kandydat = bloki[-1].strip()
    poczatek = kandydat.find("{")
    koniec = kandydat.rfind("}")
    if poczatek < 0 or koniec <= poczatek:
        raise AgentError("odpowiedź agenta nie zawiera obiektu JSON")
    try:
        dane = json.loads(kandydat[poczatek : koniec + 1])
    except ValueError as blad:
        raise AgentError(f"odpowiedź agenta nie jest poprawnym JSON-em: {blad}") from None
    if not isinstance(dane, dict):
        raise AgentError("odpowiedź agenta nie jest obiektem")
    return dane


async def zbadaj_hipoteze(
    hipoteza: Hipoteza,
    *,
    zestaw: Narzedzia,
    rubryka: Rubryka,
    prompt: str,
    inwentarz: str,
    serwer: Any,
    biezace: dict[str, NarzedziaHipotezy],
    model: str = MODEL,
) -> WynikHipotezy:
    """Jedna hipoteza, jedna sesja, budżet z rubryki."""
    klasa = rubryka.po_id[hipoteza.klasa_id]
    narzedzia_hipotezy = zestaw.dla_hipotezy(hipoteza)
    biezace["aktywne"] = narzedzia_hipotezy

    opcje = ClaudeAgentOptions(
        model=model,
        system_prompt=f"{prompt}\n\n## INWENTARZ (snapshot {zestaw.snapshot_id})\n\n{inwentarz}",
        mcp_servers={SERWER: serwer},
        allowed_tools=list(NASZE_NARZEDZIA),
        disallowed_tools=list(WBUDOWANE_ZAKAZANE),
        can_use_tool=_pozwolenie,
        max_turns=MAKS_OBROTOW,
        # Bez ustawień z repo ani od użytkownika: zachowanie agenta nie może
        # zależeć od plików, które zmieniamy przy każdym etapie.
        setting_sources=[],
        permission_mode="default",
    )
    zadanie = ZADANIE.format(
        klasa=_opis_klasy(klasa),
        hipoteza=json.dumps(hipoteza.do_zapisu(), ensure_ascii=False, indent=1),
        klasa_id=klasa.id,
        waga=klasa.waga,
        wysilek=klasa.wysilek_naprawy,
        typ_wyceny=klasa.typ_wyceny,
        dowod=", ".join(klasa.dowod),
    )

    wynik = WynikHipotezy(hipoteza=hipoteza)
    ostatni_tekst = ""
    try:
        async with ClaudeSDKClient(options=opcje) as klient:
            await klient.query(zadanie)
            async for wiadomosc in klient.receive_response():
                if isinstance(wiadomosc, AssistantMessage):
                    for blok in wiadomosc.content:
                        if isinstance(blok, TextBlock) and blok.text.strip():
                            ostatni_tekst = blok.text
                elif isinstance(wiadomosc, ResultMessage):
                    wynik.zuzycie = _zuzycie(wiadomosc)
    except Exception as blad:  # jedna hipoteza nie może wywrócić całego runu
        wynik.blad = f"{type(blad).__name__}: {blad}"
        logger.warning("hipoteza %s/%s: %s", hipoteza.klasa_id, hipoteza.obiekt_id, wynik.blad)
        return wynik
    finally:
        wynik.wywolania_narzedzi = list(narzedzia_hipotezy.wywolania)
        biezace.pop("aktywne", None)

    try:
        rozstrzygniecie = _wyluskaj_json(ostatni_tekst)
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


def _zuzycie(wiadomosc: ResultMessage) -> dict[str, float]:
    """Zużycie z `ResultMessage`. D8 wymaga go w `zuzycie`.

    Liczymy z `model_usage`, nie z `usage`. Powód jest zmierzony: pierwszy run
    pokazał `tokens_in: 10` przy prompcie systemowym rzędu pięciu tysięcy
    znaków, bo `usage.input_tokens` NIE obejmuje tokenów obsłużonych z cache —
    a przy D2 (caching na inwentarzu) to właśnie tam siedzi prawie całe
    wejście. Sumowanie samego `input_tokens` pokazywałoby koszt bliski zeru
    i uczyłoby nas fałszywej pewności.

    `costUSD` bierzemy z SDK, zamiast mnożyć tokeny przez cennik zaszyty
    u nas — cennik jest po stronie dostawcy i to on wie, ile policzył.
    """
    modele = getattr(wiadomosc, "model_usage", None) or {}
    zuzycie = {
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "koszt_usd": 0.0,
    }
    for uzycie in modele.values():
        if not isinstance(uzycie, dict):
            continue
        zuzycie["tokens_in"] += int(uzycie.get("inputTokens") or 0)
        zuzycie["tokens_out"] += int(uzycie.get("outputTokens") or 0)
        zuzycie["tokens_cache_read"] += int(uzycie.get("cacheReadInputTokens") or 0)
        zuzycie["tokens_cache_write"] += int(uzycie.get("cacheCreationInputTokens") or 0)

    # `total_cost_usd` jest wiarygodniejsze niż suma po modelach, bo obejmuje
    # też to, czego `model_usage` nie rozbija.
    calosc = getattr(wiadomosc, "total_cost_usd", None)
    zuzycie["koszt_usd"] = (
        float(calosc)
        if calosc
        else sum(float(u.get("costUSD") or 0) for u in modele.values() if isinstance(u, dict))
    )
    return zuzycie


async def zbadaj_hipotezy(
    hipotezy: list[Hipoteza],
    *,
    zestaw: Narzedzia,
    rubryka: Rubryka,
    run_id: str,
    model: str = MODEL,
    sciezka_promptu: Path = SCIEZKA_PROMPTU,
) -> dict[str, Any]:
    """Bada wszystkie hipotezy i składa dokument D8. Nie waliduje — to `kontrakt`.

    Rozdzielenie jest celowe: pętla ma zwrócić to, co agent faktycznie
    powiedział, a walidacja ma to ocenić. Gdyby pętla poprawiała odpowiedzi
    w locie, odsetek odrzuconych — główna metryka etapu 4 — pokazywałby
    jakość naszych łatek, nie jakość agenta.
    """
    prompt = _tekst_promptu(sciezka_promptu)
    inwentarz = _inwentarz(zestaw)
    biezace: dict[str, NarzedziaHipotezy] = {}
    serwer = _zbuduj_narzedzia(biezace)

    findings: list[dict[str, Any]] = []
    odrzucone: list[dict[str, Any]] = []
    bledy: list[dict[str, str]] = []
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
        wynik = await zbadaj_hipoteze(
            hipoteza,
            zestaw=zestaw,
            rubryka=rubryka,
            prompt=prompt,
            inwentarz=inwentarz,
            serwer=serwer,
            biezace=biezace,
            model=model,
        )
        for klucz in ("tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_write"):
            zuzycie[klucz] += wynik.zuzycie.get(klucz, 0)
        zuzycie["koszt_usd"] += wynik.zuzycie.get("koszt_usd", 0.0)
        zuzycie["wywolania"] += sum(
            1 for w in wynik.wywolania_narzedzi if w.startswith(("probka_kolumn", "log_tablicy"))
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
        "zuzycie": {**zuzycie, "koszt_usd": round(float(zuzycie["koszt_usd"]), 6)},
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
