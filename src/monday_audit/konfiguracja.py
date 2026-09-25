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

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from monday_audit.zbieranie.osoby import MIN_DLUGOSC_SOLI, PseudonimizacjaError

logger = logging.getLogger(__name__)

DOMYSLNY_PLIK = Path(".env")
ZMIENNA_PLIKU = "MONDAY_AUDIT_ENV_FILE"
DOMYSLNA_BAZA = Path("monday_audit.db")

# Czym rozliczana jest pętla agenta.
#
# `klucz`       — `ANTHROPIC_API_KEY` idzie do środowiska podprocesu SDK; zużycie
#                 widać w konsoli platformy, a `koszt_usd` to faktyczny wydatek.
# `subskrypcja` — nie przekazujemy klucza, więc SDK spada na login w `~/.claude`.
#                 Runy nie obciążają karty, ale `koszt_usd` staje się wyceną
#                 teoretyczną — i panel musi to napisać, żeby nikt nie wyceniał
#                 usługi po liczbie, za którą nikt nie zapłacił.
ROZLICZENIE_KLUCZ = "klucz"
ROZLICZENIE_SUBSKRYPCJA = "subskrypcja"

# Klucz podany JEDNORAZOWO przez klienta w formularzu audytu. Koszt idzie na JEGO
# rachunek u Anthropic, więc `koszt_usd` przestaje być naszą fakturą — i dlatego
# jest to trzecia wartość, nie odmiana `klucz`.
#
# Bez tego rozróżnienia panel sumowałby kwoty z dwóch różnych rachunków w jedną
# liczbę, a po fakcie nie dałoby się powiedzieć, który run coś NAS kosztował.
# Ta sama zasada co przy `subskrypcja`: tam kwota jest wyceną teoretyczną, tu
# jest fakturą, tylko cudzą.
ROZLICZENIE_KLUCZ_KLIENTA = "klucz_klienta"

# `ROZLICZENIA` to wartości dopuszczalne w KONFIGURACJI procesu (`AGENT_ROZLICZENIE`).
# `klucz_klienta` tam NIE WCHODZI: nie jest trybem pracy narzędzia, a cechą
# jednego runu. Wpisanie go do `.env` nie miałoby sensu — nie ma tam klucza klienta.
ROZLICZENIA = (ROZLICZENIE_KLUCZ, ROZLICZENIE_SUBSKRYPCJA)


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
    # Adres, pod którym aplikacja jest widoczna dla odbiorcy — wchodzi do LINKU
    # w mailu resetu.
    #
    # PUSTY DOMYŚLNIE, i to jest poprawka usterki. Wcześniej stała `:8000`
    # rozjeżdżała się z `--serwuj --port 8010`: link prowadził na port, na którym
    # nic nie nasłuchiwało. Gdy pole jest puste, `web/api.py` bierze adres
    # Z ŻĄDANIA, czyli z tego, w co odbiorca kliknął — wtedy nie ma jak się
    # rozjechać.
    #
    # Ustawić trzeba TYLKO za odwrotnym proxy (Caddy, etap 5): żądanie widzi wtedy
    # `127.0.0.1:8000`, a odbiorca `https://audyt.cxlabs.digital`.
    adres_publiczny: str = ""


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

    # Czym rozliczamy pętlę agenta. **Domyślnie `klucz`** i to nie kosmetyka:
    # audyt klienta ma być rozliczalny, a tryb subskrypcyjny musi być DECYZJĄ,
    # nie stanem, w który wpada się przez zapomnienie. Dokładnie ta pomyłka
    # zdarzyła się już raz: do 2026-08-05 klucz nie dochodził do podprocesu,
    # runy szły na subskrypcję i `runy.koszt_usd` zapisywał zero.
    #
    # `subskrypcja` = SDK spada na login w `~/.claude`. Wtedy `total_cost_usd`
    # z SDK jest wyceną teoretyczną, nie fakturą — dlatego `runy.rozliczenie`
    # zapisuje, którym trybem run poszedł (migracja 009).
    # Czy klucz Anthropic KLIENTA jest wymagany w formularzu audytu.
    #
    # `True` (domyślnie, decyzja Kuby 2026-08-19): koszt modelu idzie CAŁKOWICIE
    # na klienta. Powód z O35: konto z czterema workspace'ami to ~17 USD za audyt,
    # a przy usłudze jednorazowej per klient to zjada marżę.
    #
    # `False` przywraca wariant opcjonalny — puste pole znaczy „rozliczamy my".
    # To ścieżka na później, „jak będziemy przechodzili na produkt": wtedy klucz
    # przestanie być barierą wejścia, bo koszt wejdzie w cenę subskrypcji.
    #
    # Przełącznik, nie usunięty kod: wariant opcjonalny jest ZBUDOWANY I PRZETESTOWANY,
    # więc przejście na produkt to zmiana jednej zmiennej, nie kolejna implementacja.
    klucz_modelu_od_klienta_wymagany: bool = True

    agent_rozliczenie: str = ROZLICZENIE_KLUCZ

    # ── Langfuse (plan, faza 4) ──────────────────────────────────────────
    #
    # Trace'y wychodzą POZA nasz serwer, do firmy trzeciej. To jedyne miejsce
    # w tej konfiguracji, gdzie ustawienie zmiennej środowiskowej powoduje, że
    # dane klienta opuszczają maszynę — stąd ostrzejsze reguły niż przy reszcie.
    #
    # Brak kluczy = wysyłki nie ma. Nie ma osobnej flagi `LANGFUSE_WLACZONY`,
    # bo druga furtka do tego samego wyłącznika to drugie miejsce, w którym
    # można się pomylić. Usunięcie klucza jest wyłącznikiem.
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    # BEZ WARTOŚCI DOMYŚLNEJ, choć Langfuse ma oczywistą (`cloud.langfuse.com`).
    # Domyślna oznaczałaby, że region przechowywania danych klienta wybiera się
    # sam — a między EU a US różnica nie jest techniczna. Ma być decyzją, tak
    # jak `agent_rozliczenie`. Walidator niżej pilnuje, żeby dało się o niej
    # zapomnieć tylko razem z całą wysyłką.
    langfuse_base_url: str | None = None

    @property
    def langfuse_wlaczony(self) -> bool:
        """Wysyłamy tylko przy komplecie.

        Sprawdzamy WSZYSTKIE trzy, choć walidator już pilnuje „trzy albo zero".
        Pierwsza wersja sprawdzała jedno pole przez `is not None` — i puste
        `LANGFUSE_PUBLIC_KEY=` z `.env.example` dawało `SecretStr('')`, czyli
        „włączone". Ta właściwość jest bramką wysyłki, więc nie opiera się na
        tym, że walidator zawsze zadziała tak, jak dziś.
        """
        return bool(
            self.langfuse_public_key and self.langfuse_secret_key and self.langfuse_base_url
        )

    @field_validator(
        "langfuse_public_key", "langfuse_secret_key", "langfuse_base_url", mode="before"
    )
    @classmethod
    def _puste_to_brak(cls, wartosc: object) -> object:
        """Puste albo same spacje = zmiennej nie ma.

        ZMIERZONE przy review 2026-09-23: wdrożenie robi `cp .env.example
        /etc/monday-audit.env`, a usługa wczytuje go przez `EnvironmentFile`.
        Puste linie `LANGFUSE_*=` trafiały wtedy do środowiska jako `""`, pydantic
        robił z nich `SecretStr('')`, a SDK — dostając pusty napis zamiast
        `None` — włączało się i spadało na domyślny `cloud.langfuse.com`.
        Dokładnie ta awaria, która wygląda jak sukces.

        Tryb `before` i żadnego wyjątku, więc wartość nie ma jak trafić do
        komunikatu `ValidationError` — to samo zastrzeżenie co przy
        `_bez_bialych_znakow`.
        """
        surowa = wartosc.get_secret_value() if isinstance(wartosc, SecretStr) else wartosc
        if surowa is None or (isinstance(surowa, str) and not surowa.strip()):
            return None
        return wartosc

    @field_validator(
        "monday_token",
        "sol_pseudonimizacji",
        "anthropic_api_key",
        "smtp_haslo",
        "langfuse_public_key",
        "langfuse_secret_key",
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

    @field_validator("agent_rozliczenie", mode="after")
    @classmethod
    def _znane_rozliczenie(cls, wartosc: str) -> str:
        """Literówka nie może cicho zmienić sposobu płacenia.

        `AGENT_ROZLICZENIE=subskrybcja` bez tego walidatora byłoby traktowane jak
        „nie klucz", czyli przeszłoby na subskrypcję — i nikt by nie zauważył,
        dopóki nie zabrakłoby kosztów w konsoli platformy.
        """
        czysta = wartosc.strip().lower()
        if czysta not in ROZLICZENIA:
            raise ValueError(f"nieznany tryb {wartosc!r}; dozwolone: {', '.join(ROZLICZENIA)}")
        return czysta

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

    @model_validator(mode="after")
    def _langfuse_w_komplecie(self) -> Ustawienia:
        """Trzy zmienne albo zero. Konfiguracja połowiczna przerywa start.

        Stan, przed którym to broni, jest konkretny: ktoś wkleja dwa klucze,
        zapomina `LANGFUSE_BASE_URL`, a biblioteka spada na swój domyślny
        region. Wtedy trace'y klienta z EU idą do US i **nikt się o tym nie
        dowie**, bo wszystko działa. Awaria, która wygląda jak sukces, jest
        gorsza od awarii.

        Dlatego nie ma tu wartości domyślnej ani ostrzeżenia — jest przerwanie.
        Ten sam kierunek, co maskowanie: przy wysyłce do firmy trzeciej
        zawodzimy zamknięte.
        """
        pola = {
            "LANGFUSE_PUBLIC_KEY": self.langfuse_public_key,
            "LANGFUSE_SECRET_KEY": self.langfuse_secret_key,
            "LANGFUSE_BASE_URL": self.langfuse_base_url,
        }
        brakujace = sorted(nazwa for nazwa, wartosc in pola.items() if not wartosc)
        if brakujace and len(brakujace) < len(pola):
            raise ValueError(
                "konfiguracja Langfuse jest niepełna — brakuje: "
                f"{', '.join(brakujace)}. Ustaw wszystkie trzy albo żadnej "
                "(brak wszystkich = wysyłki nie ma)"
            )
        return self


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

    **W trybie `subskrypcja` zwraca pusty napis.** Klucz jest wtedy niepotrzebny,
    więc wymaganie go blokowałoby tryb, który go nie używa. Wywołujący MUSI wtedy
    pominąć `env` — pusty klucz w środowisku podprocesu byłby gorszy niż jego brak,
    bo SDK zobaczyłby zmienną i nie spadł na login.
    """
    if ustawienia.agent_rozliczenie == ROZLICZENIE_SUBSKRYPCJA:
        logger.warning(
            "AGENT_ROZLICZENIE=subskrypcja — run NIE obciąży klucza API, a koszt_usd "
            "będzie wyceną teoretyczną, nie fakturą"
        )
        return ""

    surowy = ustawienia.anthropic_api_key
    wartosc = surowy.get_secret_value().strip() if surowy else ""
    if not wartosc:
        raise KonfiguracjaError(
            "brak ANTHROPIC_API_KEY — pętla agenta (3.11) go wymaga przy "
            "AGENT_ROZLICZENIE=klucz. Wpisz go do .env, wyeksportuj w środowisku "
            "albo ustaw AGENT_ROZLICZENIE=subskrypcja, jeśli świadomie chcesz "
            "płacić subskrypcją (wtedy koszt_usd nie jest fakturą)"
        )
    return wartosc
