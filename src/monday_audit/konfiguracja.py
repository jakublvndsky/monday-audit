"""Jedno wejście do konfiguracji i sekretów (D12).

Precedencja, z góry na dół — pierwszy znaleziony wygrywa:

1. argument wywołania (`wczytaj(plik=...)`, flaga `--plik-env`)
2. **środowisko procesu** (`export`, `EnvironmentFile=` w systemd, `docker run -e`)
3. **plik `.env`**
4. wartość domyślna, o ile pole ją ma (sekrety jej nie mają)

Ta kolejność jest domyślną kolejnością `pydantic-settings` i jest jedynym
powodem, dla którego biblioteka tu jest: to samo ręcznie znaczy gałąź „a jeśli
w env już coś stoi", pisaną w każdym miejscu odczytu osobno.

**Aplikacja czyta `.env` i to jest normalne.** Wcześniej nie czytała, bo
pomyliłem dwie granice: zakaz dotyczy narzędzi Claude Code (`Read`/`Edit`/`Write`
na `.env` są zablokowane w `.claude/settings.json`), a nie programu. Na Mikrusie
worker leci jako proces jednorazowy z katalogu innego niż root repo
(05-deploy.md), więc `export` w cudzej sesji nie jest tam mechanizmem, którym
da się podać sekret.

Sekrety mieszkają w `SecretStr`. To nie jest ozdoba: `repr()` całego obiektu
`Ustawienia` trafia do komunikatów wyjątków i logów, a token klienta nigdy nie
może się w nich pojawić (D6). Wartość wyjmuje się jawnym `get_secret_value()`
i tylko w miejscu użycia.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from monday_audit.osoby import MIN_DLUGOSC_SOLI, PseudonimizacjaError

logger = logging.getLogger(__name__)

DOMYSLNY_PLIK = Path(".env")
ZMIENNA_PLIKU = "MONDAY_AUDIT_ENV_FILE"
DOMYSLNA_BAZA = Path("monday_audit.db")


class KonfiguracjaError(RuntimeError):
    """Konfiguracji nie da się zebrać. Komunikat NIGDY nie zawiera wartości."""


class UstawieniaPoczty(BaseSettings):
    """Poczta dla „nie pamiętam hasła" — OSOBNO od sekretów produkcyjnych.

    Klasa bazowa, nie duplikat: `Ustawienia` po niej dziedziczy, więc pola są
    zdefiniowane w jednym miejscu.

    Dlaczego osobno: aplikację webową da się zbudować bez `MONDAY_TOKEN`
    i `SOL_PSEUDONIMIZACJI` (testy granic tego korzystają, patrz `zbuduj_aplikacje`).
    Poczta potrzebuje wyłącznie własnych pól, więc wymaganie tam sekretów
    collectora znaczyłoby, że testu odzyskiwania hasła nie da się uruchomić bez
    produkcyjnych poświadczeń — a to najgorszy powód, żeby taki test pominąć.

    WSZYSTKIE pola są opcjonalne i to jest decyzja: brak konfiguracji SMTP nie
    może wywracać serwera ani zamykać drogi odzyskania hasła. Bez `smtp_host` link
    idzie do logu z ostrzeżeniem (tryb awaryjny — `poczta.py`).
    """

    model_config = SettingsConfigDict(
        env_file=DOMYSLNY_PLIK,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Przy Google Workspace `smtp_haslo` to **hasło aplikacji**, nie hasło do
    # konta: Google odrzuca logowanie zwykłym hasłem. Generuje się je raz
    # w ustawieniach konta Google.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_haslo: SecretStr | None = None
    # Adres w nagłówku „From". Gdy pusty, używamy `smtp_user` — przy Gmailu to
    # zwykle ten sam adres.
    smtp_nadawca: str | None = None
    # Adres, pod którym aplikacja jest widoczna dla odbiorcy. Wchodzi do LINKU
    # w mailu, więc `localhost` w mailu do kolegi po prostu nie zadziała.
    adres_publiczny: str = "http://127.0.0.1:8000"


class Ustawienia(UstawieniaPoczty):
    """Wszystko, co program bierze ze środowiska. Nic więcej nie czyta env.

    `extra="ignore"`, bo `.env` opisuje też sekrety etapów, które jeszcze nie
    istnieją (`ANTHROPIC_API_KEY` dla 3.11, `CXLABS_DOCS_KEY` dla 3.12).
    Wypełniony do przodu plik nie może wywracać runu collectora.
    """

    model_config = SettingsConfigDict(
        env_file=DOMYSLNY_PLIK,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    monday_token: SecretStr
    sol_pseudonimizacji: SecretStr
    # Nazwę narzuca Agent SDK i nie wolno jej zmieniać — SDK czyta ją ze
    # środowiska podprocesu sam. Trzymamy ją tutaj tylko po to, żeby brak
    # klucza przerwał run PRZED pierwszym wywołaniem modelu, a nie w połowie
    # pętli, po zapłaceniu za część hipotez.
    anthropic_api_key: SecretStr | None = None
    monday_audit_db: Path = DOMYSLNA_BAZA

    @field_validator(
        "monday_token",
        "sol_pseudonimizacji",
        "anthropic_api_key",
        "smtp_haslo",
        mode="after",
    )
    @classmethod
    def _bez_bialych_znakow(cls, wartosc: SecretStr | None) -> SecretStr | None:
        """Sekret skopiowany z panelu monday niesie ogon białych znaków.

        Walidator działa na `SecretStr`, nie na `str`, celowo: gdyby podniósł
        błąd na surowym wejściu, pydantic wstawiłby tę wartość do `input`
        w `ValidationError`, czyli sekret trafiłby do komunikatu.
        """
        if wartosc is None:
            return None
        return SecretStr(wartosc.get_secret_value().strip())

    @field_validator("sol_pseudonimizacji", mode="after")
    @classmethod
    def _sol_dosc_dluga(cls, wartosc: SecretStr) -> SecretStr:
        """Sól bez wartości domyślnej i nigdy losowana w locie.

        Sól inna w każdym runie dałaby inne hashe, czyli snapshoty tego samego
        klienta przestałyby być porównywalne — a to jest sens D7. Krótka sól
        nie chroni: identyfikatory monday to małe liczby, więc hash odwraca się
        tablicą tęczową.
        """
        dlugosc = len(wartosc.get_secret_value())
        if dlugosc == 0:
            raise ValueError("wartość jest pusta")
        if dlugosc < MIN_DLUGOSC_SOLI:
            raise ValueError(f"ma {dlugosc} znaków, wymagane minimum {MIN_DLUGOSC_SOLI}")
        return wartosc


def _sciezka_pliku(plik: Path | None) -> Path:
    """Argument → zmienna środowiskowa → `./.env`.

    Ścieżka jest rozwiązywana do absolutnej, bo domyślne `.env`
    w pydantic-settings jest relatywne do katalogu roboczego, a etap 5
    uruchamia workera spoza roota repo.
    """
    if plik is not None:
        return plik.expanduser().resolve()
    ze_srodowiska = os.environ.get(ZMIENNA_PLIKU, "").strip()
    if ze_srodowiska:
        return Path(ze_srodowiska).expanduser().resolve()
    return DOMYSLNY_PLIK.resolve()


def _ostrzez_o_prawach(sciezka: Path) -> None:
    """Sekrety czytelne dla całego systemu to wyciek soli, czyli deanonimizacja.

    Ostrzeżenie, nie błąd: na cudzej maszynie i w kontenerze prawa bywają
    ustawione poza naszą kontrolą, a przerwany run nie naprawia uprawnień.
    """
    tryb = sciezka.stat().st_mode
    if tryb & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            "%s jest czytelny poza właścicielem (%s) — wyciek soli pozwala "
            "zdeanonimizować tabelę mapowania; `chmod 600 %s`",
            sciezka,
            stat.filemode(tryb),
            sciezka,
        )


def _opis_bledu(blad: ValidationError) -> str:
    """Buduje komunikat WYŁĄCZNIE z nazw pól i powodów.

    Nigdy z `blad.errors()[i]["input"]` — tam siedzi wartość, którą pydantic
    odrzucił, czyli potencjalnie sam token. To ta sama zasada, którą trzyma
    walidator antyprzeciekowy w `osoby`: komunikat o błędzie nie może być
    drugim wyciekiem, tym razem do logów.
    """
    powody = []
    for szczegol in blad.errors():
        pole = ".".join(str(czesc) for czesc in szczegol["loc"]) or "?"
        if szczegol["type"] == "missing":
            powody.append(f"{pole.upper()}: brak")
        else:
            powody.append(f"{pole.upper()}: {szczegol['msg']}")
    return "; ".join(powody)


def wczytaj(plik: Path | None = None) -> Ustawienia:
    """Zbiera konfigurację. Jedyne publiczne wejście tego modułu.

    Loguje ŹRÓDŁO — ścieżkę pliku albo jego brak — i nigdy żadnej wartości.
    Bez tego logu nieznaleziony `.env` na serwerze wygląda dokładnie tak samo
    jak niewypełniona zmienna, a ten projekt nie ma cichych zachowań.
    """
    sciezka = _sciezka_pliku(plik)
    istnieje = sciezka.is_file()

    if istnieje:
        _ostrzez_o_prawach(sciezka)
        logger.info("konfiguracja: %s + środowisko procesu", sciezka)
    else:
        logger.info("konfiguracja: tylko środowisko procesu (brak %s)", sciezka)

    try:
        return Ustawienia(_env_file=sciezka if istnieje else None)
    except ValidationError as blad:
        # `from None`, nie `from blad`. Sprawdzone empirycznie: pydantic wkłada
        # do `input_value` SUROWE wejście pola, niezależnie od tego, że walidator
        # jest `mode="after"` i dostaje już `SecretStr`. Czyli `str(ValidationError)`
        # zawiera odrzucony sekret w jawnej postaci. Podpięcie przyczyny przez
        # `from blad` wypisałoby ją w tracebacku, więc łańcuch jest tu urwany
        # świadomie — nazwy pól i powody i tak niesie `_opis_bledu`.
        raise KonfiguracjaError(
            f"konfiguracja niekompletna [{_opis_bledu(blad)}] — uzupełnij {sciezka} "
            f"albo wyeksportuj zmienną w środowisku"
        ) from None


def sol_z_ustawien(ustawienia: Ustawienia) -> bytes:
    """Sól jako bajty do HMAC.

    Osobna funkcja, żeby `get_secret_value()` na soli miało jedno miejsce
    w kodzie — łatwiej sprawdzić `git grep`, gdzie sekret opuszcza `SecretStr`.
    Wyjątek jest typu `PseudonimizacjaError`, bo brak soli to naruszenie
    granicy PII, a nie zwykły błąd konfiguracji.
    """
    surowa = ustawienia.sol_pseudonimizacji.get_secret_value()
    if len(surowa) < MIN_DLUGOSC_SOLI:  # pragma: no cover — walidator już to odrzucił
        raise PseudonimizacjaError(
            f"sól ma {len(surowa)} znaków, wymagane minimum {MIN_DLUGOSC_SOLI}"
        )
    return surowa.encode("utf-8")


def klucz_anthropic(ustawienia: Ustawienia) -> str:
    """Klucz do Agent SDK. Brak PRZERYWA, zanim padnie pierwsze wywołanie.

    Puste znaczy tyle samo co brak: `ANTHROPIC_API_KEY=` w `.env` to linia,
    którą ktoś skopiował z szablonu i nie wypełnił, a nie świadoma decyzja.
    Sprawdzenie samego `is None` przepuściłoby ją i run wywrócił się dopiero
    przy modelu — po zapłaceniu za wywołania monday, których już nie odzyskamy.

    **SPROSTOWANIE 2026-08-05.** Ten docstring mówił wcześniej: „Agent SDK
    czyta zmienną ze środowiska podprocesu sam". **Nieprawda** — i to była
    usterka, nie tylko zła dokumentacja. `pydantic-settings` wczytuje `.env`
    do obiektu `Ustawienia`, a **nie do `os.environ`**; zmierzone:
    `"ANTHROPIC_API_KEY" in os.environ` jest `False` po `wczytaj()`.

    Podproces CLI nie widział więc klucza i spadał na własne poświadczenia
    (login subskrypcyjny w `~/.claude`). Runy działały, ale ich zużycia nie
    było w konsoli API, bo szło na subskrypcję.

    Zwracaną wartość trzeba przekazać do `ClaudeAgentOptions(env=...)` —
    i `agent.py` to robi. Ta funkcja nadal istnieje po to, żeby run przerwał
    się WCZEŚNIE i z czytelnym komunikatem, przed pierwszym wywołaniem monday.
    """
    surowy = ustawienia.anthropic_api_key
    wartosc = surowy.get_secret_value().strip() if surowy else ""
    if not wartosc:
        raise KonfiguracjaError(
            "brak ANTHROPIC_API_KEY — pętla agenta (3.11) go wymaga. Wpisz go "
            "do .env albo wyeksportuj w środowisku"
        )
    return wartosc
