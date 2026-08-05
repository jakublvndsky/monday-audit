"""Testy pętli agenta (etap 3.11), warstwa 1 z 04-test.md.

**Zero wywołań modelu.** Wszystko, co da się sprawdzić bez płacenia, jest tu:
odcięcie narzędzi zapisujących, wyłuskanie promptu, kształt inwentarza,
parsowanie odpowiedzi. Sam run na modelu to warstwa 2.

Najważniejszy test: `test_kazde_narzedzie_poza_nasza_lista_jest_odrzucone`.
Wbudowane narzędzia SDK to `Write`, `Edit` i `Bash`, czyli zapis do plików,
którego zakaz twardy zabrania wprost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from monday_audit.agent import (
    MAKS_OBROTOW,
    MODEL,
    NASZE_NARZEDZIA,
    SCIEZKA_PROMPTU,
    WBUDOWANE_ZAKAZANE,
    AgentError,
    _blad_api,
    _brama_narzedzi,
    _inwentarz,
    _tekst_promptu,
    _wyluskaj_json,
    zbuduj_opcje,
)

# ── odcięcie zapisu ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "narzedzie",
    ["Write", "Edit", "Bash", "Read", "WebFetch", "Task", "mcp__inny__cokolwiek"],
)
async def test_kazde_narzedzie_poza_nasza_lista_jest_odrzucone(narzedzie: str) -> None:
    """Trzecia warstwa odcięcia — hook `PreToolUse`, w procesie."""
    surowy = await _brama_narzedzi({"tool_name": narzedzie}, None, None)  # type: ignore[arg-type]
    wynik = cast("dict[str, Any]", surowy)

    decyzja = wynik["hookSpecificOutput"]
    assert decyzja["permissionDecision"] == "deny"
    assert narzedzie in decyzja["permissionDecisionReason"]


@pytest.mark.parametrize("narzedzie", NASZE_NARZEDZIA)
async def test_nasze_narzedzia_przechodza(narzedzie: str) -> None:
    surowy = await _brama_narzedzi({"tool_name": narzedzie}, None, None)  # type: ignore[arg-type]

    assert surowy == {}, "nasze narzędzia przechodzą bez decyzji hooka"


def test_brama_jest_podlaczona_jako_hook_a_nie_callback() -> None:
    """Test, którego brakowało i który kosztował nas fałszywą pewność.

    Pierwsza wersja podłączała odcięcie przez `can_use_tool`, a SDK ostrzegł
    przy pierwszym pełnym runie, że callback NIE jest wołany dla narzędzi
    z `allowed_tools`. Poprzedni test sprawdzał samą funkcję w izolacji, więc
    przechodził — mimo że w praktyce nic nie gatował.

    Ten test patrzy na WIRING: hook musi być w opcjach, a `can_use_tool` nie
    ma prawa tam wrócić.
    """
    from monday_audit import agent as modul

    zrodlo = Path(modul.__file__).read_text(encoding="utf-8")

    assert 'hooks={"PreToolUse"' in zrodlo, "brama musi być podłączona jako hook PreToolUse"
    assert "can_use_tool=" not in zrodlo, (
        "can_use_tool nie jest wołany przy allowed_tools — SDK ostrzega o tym wprost"
    )


def test_wbudowane_zapisujace_sa_na_czarnej_liscie() -> None:
    """Jawnie z nazwy, nie licząc na to, że biała lista wystarczy."""
    for zapisujace in ("Write", "Edit", "Bash", "NotebookEdit"):
        assert zapisujace in WBUDOWANE_ZAKAZANE


def test_nasze_narzedzia_to_dokladnie_cztery_czytajace() -> None:
    assert len(NASZE_NARZEDZIA) == 4
    nazwy = {n.split("__")[-1] for n in NASZE_NARZEDZIA}
    assert nazwy == {"pobierz_inwentarz", "zapytaj_snapshot", "probka_kolumn", "log_tablicy"}
    # Żadna nie brzmi jak zapis.
    zakazane = ("utworz", "zmien", "usun", "create", "update", "delete", "write")
    for nazwa in nazwy:
        assert not any(z in nazwa for z in zakazane)


# ── model przypięty, nie aliasowany ──────────────────────────────────────


def test_model_jest_przypiety_bez_aliasu() -> None:
    """05-deploy: alias typu `latest` w produkcji jest zakazany.

    Alias przesuwa się przy nowym wydaniu i wynik zmienia się bez zmiany kodu,
    czyli audyt sprzed trzech miesięcy przestaje być odtwarzalny.
    """
    assert "latest" not in MODEL
    assert MODEL == "claude-sonnet-5", "podniesienie modelu idzie przez bramę promocji (D2)"


def test_sufit_obrotow_istnieje() -> None:
    """Narzędzia snapshotu są darmowe, więc licznik budżetu ich nie zatrzyma."""
    assert 0 < MAKS_OBROTOW <= 30


# ── prompt ───────────────────────────────────────────────────────────────


def test_prompt_wyluskuje_sie_z_bloku_kodu() -> None:
    """Plik jest dokumentacją Z promptem w środku, nie samym promptem."""
    prompt = _tekst_promptu()

    assert prompt.startswith("Jesteś analitykiem audytowym CXLABS")
    # Nagłówki dokumentacji nie mają prawa trafić do modelu.
    assert "NIE jest instrukcja dla Claude Code" not in prompt
    assert "Wersja:" not in prompt


def test_prompt_nie_powoluje_sie_na_niedzialajaca_flage() -> None:
    """Prompt mówił, że twardą gwarancją jest `--read-only`. Nie jest (O19)."""
    prompt = _tekst_promptu()

    assert "--read-only" not in prompt


def test_prompt_opisuje_narzedzia_ktore_istnieja() -> None:
    prompt = _tekst_promptu()

    for nazwa in ("pobierz_inwentarz", "zapytaj_snapshot", "probka_kolumn", "log_tablicy"):
        assert nazwa in prompt, f"prompt nie wymienia narzędzia {nazwa}"


def test_brak_pliku_promptu_przerywa(tmp_path: Path) -> None:
    with pytest.raises(AgentError, match="nie ma pliku promptu"):
        _tekst_promptu(tmp_path / "nie-ma-mnie.md")


def test_plik_bez_bloku_kodu_przerywa(tmp_path: Path) -> None:
    plik = tmp_path / "prompt.md"
    plik.write_text("# Tylko nagłówek, zero bloku\n", encoding="utf-8")

    with pytest.raises(AgentError, match="nie znalazłem bloku"):
        _tekst_promptu(plik)


def test_sciezka_promptu_wskazuje_na_istniejacy_plik() -> None:
    """Hash tego pliku jest pinowany przy runie (05-deploy)."""
    assert SCIEZKA_PROMPTU.is_file()


# ── parsowanie odpowiedzi ────────────────────────────────────────────────


def test_json_wyluskuje_sie_z_bloku_kodu() -> None:
    """Model bywa uprzejmy i dokłada blok ```json.

    Odrzucenie hipotezy za formatowanie marnowałoby zapłacone wywołanie.
    """
    tekst = 'Oto rozstrzygnięcie:\n```json\n{"rozstrzygniecie": "odrzucona", "powod": "x"}\n```'

    assert _wyluskaj_json(tekst)["rozstrzygniecie"] == "odrzucona"


def test_json_wyluskuje_sie_ze_zdania_przed() -> None:
    tekst = 'Po sprawdzeniu: {"rozstrzygniecie": "odrzucona", "powod": "archiwum"}'

    assert _wyluskaj_json(tekst)["powod"] == "archiwum"


def test_zagniezdzony_json_zostaje_caly() -> None:
    """Ostatni nawias, nie pierwszy — inaczej urwalibyśmy zagnieżdżony `dowod`."""
    tekst = '{"finding": {"dowod": {"a": 1}}, "rozstrzygniecie": "finding"}'

    assert _wyluskaj_json(tekst)["finding"]["dowod"] == {"a": 1}


@pytest.mark.parametrize("tekst", ["", "nie ma tu nawiasów", "{{{", "{niepoprawny json"])
def test_odpowiedz_bez_json_przerywa_hipoteze(tekst: str) -> None:
    with pytest.raises(AgentError):
        _wyluskaj_json(tekst)


# ── inwentarz w kontekście ───────────────────────────────────────────────


class _ZestawAtrapa:
    """Atrapa `Narzedzia` — sam `wycinek`, bo tylko tego używa `_inwentarz`."""

    snapshot_id = 5

    def __init__(self, sekcje: dict[str, Any]) -> None:
        self.sekcje = sekcje

    def wycinek(self, sciezka: str) -> Any:
        return self.sekcje.get(sciezka)


def test_inwentarz_nie_zawiera_pelnych_list() -> None:
    """105 tablic w KAŻDEJ sesji to koszt bez wartości (D2, prompt caching).

    Szczegół agent bierze narzędziem, gdy jest mu potrzebny.
    """
    zestaw = _ZestawAtrapa(
        {
            "$.meta": {"okno_dni": 90},
            "$.tablice.podsumowanie": {"tablic": 105},
            "$.uzytkownicy.podsumowanie": {"razem": 95, "zajmujacych_miejsce": 19},
        }
    )

    tekst = _inwentarz(zestaw)  # type: ignore[arg-type]

    assert '"tablic": 105' in tekst
    # Ścieżek do pełnych list nawet nie pytamy.
    assert "$.tablice.tablice" not in zestaw.sekcje
    assert "board_id" not in tekst


def test_inwentarz_jest_stabilny_miedzy_wywolaniami() -> None:
    """Prompt caching działa tylko, gdy prefiks jest IDENTYCZNY (D2)."""
    zestaw = _ZestawAtrapa({"$.meta": {"a": 1}, "$.tablice.podsumowanie": {"b": 2}})

    assert _inwentarz(zestaw) == _inwentarz(zestaw)  # type: ignore[arg-type]


# ── uwierzytelnienie do modelu (regresja z 2026-08-05) ───────────────────


def _opcje(klucz: str = "sk-ant-testowy") -> Any:
    return zbuduj_opcje(
        prompt="prompt",
        inwentarz="{}",
        snapshot_id=5,
        serwer=object(),
        klucz_api=klucz,
    )


def test_klucz_api_trafia_do_srodowiska_podprocesu() -> None:
    """Regresja wprost: klucz NIE dochodził do CLI i run szedł na subskrypcję.

    `pydantic-settings` wczytuje `.env` do obiektu `Ustawienia`, a nie do
    `os.environ` — zmierzone. Podproces nie widział więc `ANTHROPIC_API_KEY`
    i spadał na login w `~/.claude`. Runy działały, ale ich zużycia nie było
    w konsoli API.

    493 testy były wtedy zielone. Ten test istnieje, bo obie usterki tej klasy
    (`can_use_tool` i klucz) siedziały w konstrukcji opcji i żadna nie dawała
    się złapać bez sprawdzenia PODŁĄCZENIA.
    """
    opcje = _opcje()

    assert opcje.env["ANTHROPIC_API_KEY"] == "sk-ant-testowy"


def test_env_nie_wywala_reszty_srodowiska() -> None:
    """SDK składa `{**inherited_env, ..., **options.env}`, więc PATH zostaje.

    Sprawdzamy, że NIE dokładamy niczego poza kluczem — gdyby ktoś wrzucił tu
    całe `os.environ`, sekrety monday poszłyby do podprocesu modelu bez powodu.
    """
    assert set(_opcje().env) == {"ANTHROPIC_API_KEY"}


def test_klucz_nie_wchodzi_do_promptu_ani_narzedzi() -> None:
    """Zakaz twardy: token i klucz nigdy w kontekście modelu (D6, D12)."""
    opcje = _opcje("sk-ant-SEKRET-DO-WYKRYCIA")

    assert "SEKRET-DO-WYKRYCIA" not in str(opcje.system_prompt)
    assert "SEKRET-DO-WYKRYCIA" not in str(opcje.allowed_tools)
    assert "SEKRET-DO-WYKRYCIA" not in str(opcje.disallowed_tools)
    assert "SEKRET-DO-WYKRYCIA" not in str(opcje.model)


def test_opcje_nadal_maja_trzy_warstwy_odciecia() -> None:
    """Wydzielenie `zbuduj_opcje` nie mogło zgubić żadnej z warstw."""
    opcje = _opcje()

    assert set(opcje.allowed_tools) == set(NASZE_NARZEDZIA)
    assert "Write" in opcje.disallowed_tools
    assert "ToolSearch" in opcje.disallowed_tools
    assert "PreToolUse" in opcje.hooks
    assert opcje.setting_sources == []


def test_blad_api_dostaje_czytelny_opis() -> None:
    """`is_error=True` przy `subtype='success'` — opieramy się na treści.

    Zmierzone na celowo złym kluczu: SDK zwraca `subtype='success'`,
    `is_error=True` i `result='Failed to authenticate. API Error: 401 API key
    is invalid.'`. Podtyp jest więc bezużyteczny jako sygnał, a treść nie.
    """

    class Udawana:
        subtype = "success"
        is_error = True
        result = "Failed to authenticate. API Error: 401 API key is invalid."

    opis = _blad_api(cast("Any", Udawana()))

    assert "401" in opis
    assert opis.startswith("błąd API")


def test_blad_api_bez_tresci_nadal_mowi_co_sie_stalo() -> None:
    """Pusty `result` nie może dać pustego komunikatu — wtedy nie wiadomo nic."""

    class Udawana:
        subtype = "error_max_turns"
        is_error = True
        result = None

    opis = _blad_api(cast("Any", Udawana()))

    assert "error_max_turns" in opis
