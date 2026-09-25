"""Rdzeń sesji agenta w Agent SDK — wspólny dla nowej i starej ścieżki.

Wydzielony z `agent.py` (2026-09-25, podział repo): nowa ścieżka (`agent.sesja`)
i stara pętla per hipoteza (`stary_panel.agent`) stoją na tych samych opcjach,
narzędziach i bramce. Usunięcie starego panelu nie może zabrać granicy zapisu.

**Trzy warstwy odcięcia zapisu**, bo wbudowane narzędzia SDK to `Write`,
`Edit` i `Bash` — czyli zapis do plików, którego zakaz twardy zabrania
wprost („ani do monday, ani do bazy, ani do plików"):

1. `allowed_tools` wymienia WYŁĄCZNIE nasze narzędzia
2. `disallowed_tools` wymienia wbudowane z nazwy — jawnie, nie licząc na to,
   że biała lista wystarczy
3. **hook `PreToolUse`** odrzuca w procesie wszystko, czego nie ma na naszej
   liście. To jedyna warstwa, którą kontrolujemy w całości i której model
   nie widzi

   Pierwotnie tą warstwą był `can_use_tool` i **to nie działało**. SDK ostrzegł
   wprost przy pierwszym pełnym runie: „an allowed_tools entry that allows
   a whole tool auto-approves it before the callback is consulted". Czyli
   callback NIE był wołany dla narzędzi, które faktycznie się wykonują —
   dokładnie ta sama klasa błędu co flaga `--read-only` w MCP (O19):
   udokumentowany mechanizm, który nie chodzi. Test też tego nie wyłapał, bo
   sprawdzał samą funkcję w izolacji, a nie to, czy jest podłączona

Do tego `setting_sources=[]`: agent nie wczytuje `CLAUDE.md` z tego repo ani
ustawień użytkownika. Bez tego jego zachowanie zależałoby od plików, które
zmieniamy przy każdym etapie, a 05-deploy wymaga, żeby run sprzed trzech
miesięcy był odtwarzalny.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookContext,
    HookInput,
    HookMatcher,
    ResultMessage,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import SyncHookJSONOutput

from monday_audit.narzedzia import NarzedziaHipotezy, NarzedzieError

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
    # Dopisane po tym, jak hook złapał je na żywo — nie było ich na liście,
    # a agent próbował ich użyć. Dowód, że trzecia warstwa nie jest ozdobą.
    "ToolSearch",
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


def hash_promptu(sciezka: Path = SCIEZKA_PROMPTU) -> str:
    """SHA-256 promptu WYSŁANEGO do modelu — trzeci element pinowania (05-deploy).

    Hashujemy **wyciągnięty blok**, nie cały plik. `PROMPT_AGENTA.md` to
    dokumentacja z promptem w środku: poprawka literówki w nagłówku albo
    dopisanie sprostowania nie zmienia zachowania modelu, więc nie ma prawa
    zmieniać hasha. Zmiana treści promptu — ma.

    Do 2026-08-05 `runy.prompt_hash` był NULL we WSZYSTKICH runach, bo nic go
    nie zapisywało. Kolumna istniała od migracji 001, renderer ją pokazywał,
    a 05-deploy wymieniał prompt jako element pinowania. Trzecia luka tej samej
    klasy, po `wzor` i `trop_sprzedazowy`: pole jest, kod go nie wypełnia.
    """
    return hashlib.sha256(_tekst_promptu(sciezka).encode("utf-8")).hexdigest()[:16]


async def wykonaj_narzedzie(
    zestaw: NarzedziaHipotezy,
    nazwa: str,
    args: dict[str, Any],
    wywolanie: Callable[[NarzedziaHipotezy], Any],
) -> dict[str, Any]:
    """Wywołanie narzędzia z zapisem przebiegu — JEDNO miejsce dla wszystkich.

    Trace w Langfuse pokazywał same nazwy narzędzi, bez argumentów i wyników
    (zgłoszone 2026-09-24): nie dało się ocenić, czy model zapytał o właściwą
    rzecz i co dostał. Wynik zapisujemy w postaci `do_modelu()`, czyli
    dokładnie to, co widział model — nie więcej.
    """
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    zaczeto = time.monotonic()
    wpis: dict[str, Any] = {"narzedzie": nazwa, "argumenty": dict(args), "start": start}
    try:
        wynik = wywolanie(zestaw)
        if hasattr(wynik, "__await__"):
            wynik = await wynik
        dane = wynik.do_modelu()
    except Exception as blad:
        wpis["blad"] = f"{type(blad).__name__}: {blad}"[:500]
        raise
    else:
        wpis["wynik"] = dane
    finally:
        wpis["ms"] = round((time.monotonic() - zaczeto) * 1000)
        zestaw.przebieg.append(wpis)
    return {"content": [{"type": "text", "text": json.dumps(dane, ensure_ascii=False)}]}


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

    async def wykonaj(
        nazwa: str, args: dict[str, Any], wywolanie: Callable[[NarzedziaHipotezy], Any]
    ) -> dict[str, Any]:
        return await wykonaj_narzedzie(teraz(), nazwa, args, wywolanie)

    @tool(
        "pobierz_inwentarz",
        "Podsumowanie sekcji snapshotu. Zakres: konto, uzytkownicy, tablice, "
        "automatyzacje, aktywnosc. Nie zwraca pełnych list — po szczegół obiektu "
        "użyj zapytaj_snapshot.",
        {"zakres": str},
    )
    async def _inwentarz_narzedzie(args: dict[str, Any]) -> dict[str, Any]:
        return await wykonaj(
            "pobierz_inwentarz", args, lambda z: z.pobierz_inwentarz(str(args["zakres"]))
        )

    @tool(
        "zapytaj_snapshot",
        "Predefiniowane pytanie do snapshotu. Pytania: tablica, aktywnosc_tablicy, "
        "kolumny_tablicy, osoba, tablice_osoby, automatyzacja, tablice_workspace, "
        "podsumowanie. Wszystkie poza `podsumowanie` wymagają obiekt_id.",
        {"pytanie": str, "obiekt_id": str},
    )
    async def _snapshot_narzedzie(args: dict[str, Any]) -> dict[str, Any]:
        return await wykonaj(
            "zapytaj_snapshot",
            args,
            lambda z: z.zapytaj_snapshot(
                str(args["pytanie"]), str(args.get("obiekt_id") or "") or None
            ),
        )

    @tool(
        "probka_kolumn",
        "Wypełnienie kolumn na próbce itemów tablicy. Zwraca WYŁĄCZNIE liczby "
        "wypełnionych pól, nigdy wartości. Kosztuje jedno wywołanie budżetu.",
        {"board_id": str},
    )
    async def _probka_narzedzie(args: dict[str, Any]) -> dict[str, Any]:
        return await wykonaj(
            "probka_kolumn", args, lambda z: z.probka_kolumn(str(args["board_id"]))
        )

    @tool(
        "log_tablicy",
        "Activity log tablicy w oknie czasowym, z rozkładem po dniach i autorami "
        "jako pseudonimami. Daty w ISO-8601. Kosztuje jedno wywołanie budżetu.",
        {"board_id": str, "od": str, "do": str},
    )
    async def _log_narzedzie(args: dict[str, Any]) -> dict[str, Any]:
        return await wykonaj(
            "log_tablicy",
            args,
            lambda z: z.log_tablicy(str(args["board_id"]), str(args["od"]), str(args["do"])),
        )

    return create_sdk_mcp_server(
        name=SERWER,
        version="1.0.0",
        tools=[_inwentarz_narzedzie, _snapshot_narzedzie, _probka_narzedzie, _log_narzedzie],
    )


NASZE_NARZEDZIA = tuple(
    f"mcp__{SERWER}__{n}"
    for n in ("pobierz_inwentarz", "zapytaj_snapshot", "probka_kolumn", "log_tablicy")
)


async def _brama_narzedzi(
    wejscie: HookInput, narzedzie_id: str | None, kontekst: HookContext
) -> SyncHookJSONOutput:
    """Trzecia warstwa odcięcia — hook `PreToolUse`, w procesie.

    Hook, a NIE `can_use_tool`. Pierwsza wersja używała callbacka i SDK
    ostrzegł, że nie zostanie wywołany: wpis w `allowed_tools` zatwierdza
    narzędzie, zanim callback dojdzie do słowa. Hook `PreToolUse` widzi
    KAŻDE wywołanie, także to z białej listy.

    Zwracamy `deny` dla wszystkiego poza naszymi czterema narzędziami,
    z komunikatem, nie po cichu — model ma się dowiedzieć, czym dysponuje,
    zamiast próbować w kółko.
    """
    narzedzie = str(dict(wejscie).get("tool_name") or "")
    if narzedzie in NASZE_NARZEDZIA:
        return {}
    logger.warning("ODRZUCONE narzędzie %s — agent ma tylko narzędzia czytające", narzedzie)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Narzędzie {narzedzie} nie jest dostępne. Masz wyłącznie narzędzia "
                f"czytające: {', '.join(n.split('__')[-1] for n in NASZE_NARZEDZIA)}."
            ),
        }
    }


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


def zbuduj_opcje(
    *,
    prompt: str,
    inwentarz: str,
    snapshot_id: int,
    serwer: Any,
    klucz_api: str,
    model: str = MODEL,
    effort: str | None = None,
) -> ClaudeAgentOptions:
    """Opcje jednej sesji. Wydzielone, ŻEBY DAŁO SIĘ SPRAWDZIĆ PODŁĄCZENIE.

    ## Dwa tryby rozliczenia

    `klucz_api` niepusty → idzie do `env` podprocesu, run obciąża klucz API
    i `koszt_usd` jest faktycznym wydatkiem.

    `klucz_api` pusty → `env` zostaje PUSTE, więc SDK spada na login w `~/.claude`
    i run idzie z subskrypcji. `konfiguracja.klucz_anthropic` zwraca pusty napis
    właśnie w trybie `AGENT_ROZLICZENIE=subskrypcja`.

    Klucz zostaje PARAMETREM, nie odczytem z konfiguracji w środku — inaczej test
    nie ma jak sprawdzić, co naprawdę poszło do SDK. To lekcja z dwóch usterek
    tej klasy, opisanych niżej.

    Poprzednia usterka tej klasy — `can_use_tool`, który nigdy nie był wołany —
    przeszła przez testy, bo sprawdzały samą funkcję, a nie to, czy jest
    podpięta. To samo powtórzyło się z kluczem API: 493 testy były zielone,
    a klucz nie dochodził do podprocesu. Skoro obie usterki siedziały
    w konstrukcji opcji, konstrukcja musi być osobno testowalna.

    ## `effort` — jedyna dźwignia na wyjście, wskazana pomiarem

    ZMIERZONE (run `instr-011`, migracja 011): wyjście to 49% rachunku, a w nim
    **74-76% to tokeny MYŚLENIA**. Wyrzuconych bloków tekstu jest ZERO — agent
    odpowiada jednym blokiem, od razu JSON-em. Czyli instrukcja w prompcie
    („nie rozpisuj rozumowania") walczyłaby o zero, a `max_tokens` w SDK nie
    istnieje wcale.

    Zostaje `effort`. Zaleta wobec zmiany promptu: prompt jest prefiksem cache'u
    (88,7% odczytu), więc jego zmiana zresetowałaby cache i `prompt_hash`, czyli
    porównywalność z poprzednimi runami. `effort` jest poza prefiksem — jedna
    flaga, odwracalna, bez kosztu przy pierwszym runie.

    Ryzyko do zmierzenia, nie do założenia: `effort` cina jakość rozumowania,
    a nie samą długość. `DUPLICATE_STRUCTURE` rozstrzyga (porównuje tablice
    między sobą), nie przepisuje — więc każde obniżenie mierzymy złotym zestawem
    i cofamy przy spadku rzeczowości.

    `None` znaczy „nie przekazuj wcale", nie „domyślny" — brak pola w opcjach
    zostawia decyzję SDK, a jawna wartość domyślna przypięłaby nas do liczby,
    której nie zmierzyliśmy.
    """
    dodatkowe: dict[str, Any] = {"effort": effort} if effort else {}
    return ClaudeAgentOptions(
        model=model,
        # KLUCZ MUSI TU BYĆ JAWNIE. `pydantic-settings` wczytuje `.env` do
        # obiektu `Ustawienia`, a NIE do `os.environ` — więc podproces CLI go
        # nie widział i spadał na własne poświadczenia (login subskrypcyjny
        # w `~/.claude`). Runy działały, ale ich zużycia nie było w konsoli API,
        # bo szło na subskrypcję. Zmierzone 2026-08-05.
        #
        # `options.env` DOKŁADA się do odziedziczonego środowiska (SDK:
        # `{**inherited_env, ..., **options.env}`), więc PATH i reszta zostają.
        # Klucz idzie do env podprocesu, NIE do argv — argv widać w `ps` (D12).
        # PUSTY słownik w trybie subskrypcyjnym, nie `{"ANTHROPIC_API_KEY": ""}`.
        # Pusta zmienna byłaby GORSZA niż jej brak: SDK zobaczyłby ją i nie spadł
        # na login w `~/.claude`, więc run wywróciłby się na uwierzytelnianiu
        # zamiast pójść z subskrypcji. Test pilnuje obu trybów.
        env=({"ANTHROPIC_API_KEY": klucz_api} if klucz_api else {}),
        system_prompt=f"{prompt}\n\n## INWENTARZ (snapshot {snapshot_id})\n\n{inwentarz}",
        mcp_servers={SERWER: serwer},
        allowed_tools=list(NASZE_NARZEDZIA),
        disallowed_tools=list(WBUDOWANE_ZAKAZANE),
        # Hook widzi KAŻDE wywołanie, także to z białej listy — inaczej niż
        # `can_use_tool`, który przy `allowed_tools` nie jest wołany wcale.
        hooks={"PreToolUse": [HookMatcher(hooks=[_brama_narzedzi])]},
        max_turns=MAKS_OBROTOW,
        # Bez ustawień z repo ani od użytkownika: zachowanie agenta nie może
        # zależeć od plików, które zmieniamy przy każdym etapie.
        setting_sources=[],
        permission_mode="default",
        **dodatkowe,
    )


def _blad_api(wiadomosc: ResultMessage) -> str:
    """Czytelny opis błędu z `ResultMessage`, bez zgadywania.

    `subtype` bywa `success` także wtedy, gdy `is_error` jest `True` — więc
    opieramy się na treści `result`, a nie na podtypie.
    """
    tresc = str(getattr(wiadomosc, "result", "") or "").strip()
    return f"błąd API: {tresc}" if tresc else f"błąd API (subtype={wiadomosc.subtype})"


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
