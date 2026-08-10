# monday.com Account Audit

Wewnętrzne narzędzie CXLABS. Audytuje konto monday.com klienta i produkuje raport
ze znaleziskami. Odpalane ręcznie, jednorazowo per klient.
Nie SaaS, nie abonament, nie self-service.

> **Stan: etap 3 (Build), 3.1–3.12 zbudowane.** Collector, detektory, narzędzia
> agenta, pętla agenta, walidacja kontraktu i renderer raportu **działają na
> prawdziwym koncie**. Audyt kończy się dwoma dokumentami HTML; publikacja pod
> URL-em przechodzi do etapu 5.
>
> Postęp funkcja po funkcji: [`STATUS.md`](STATUS.md). Stan w pięć minut:
> [`docs/PODSUMOWANIE.md`](docs/PODSUMOWANIE.md). Szczegóły techniczne:
> [`docs/ZBUDOWANE.md`](docs/ZBUDOWANE.md).

## Jak to działa

Dwie warstwy, świadomie rozdzielone:

- **Collector** — deterministyczny, spisuje konto wyczerpująco, czysty GraphQL.
  Inwentaryzacja to robota kodu: lista rzeczy, które istnieją, jest skończona i znana.
- **Agent** — bada hipotezy wzbudzone przez detektory, wchodzi do monday tylko tam,
  gdzie coś nie pasuje. Dochodzenie to robota agenta: ścieżka nie jest znana z góry.

```
token klienta (read-only)
    → collector (httpx)         → snapshot w SQLite, niemutowalny
    → detektory (SQL, zero AI)  → lista hipotez
    → agent (Agent SDK, narzędzia własne na MondayClient)
    → walidacja kontraktu (kod) → finding bez `dowod` odpada
    → renderer                  → wersja wewnętrzna + wersja klientowa
```

Agent jest w środku przepływu, nie na końcu. Po nim są jeszcze dwie warstwy
deterministyczne. **Agent tylko czyta i tylko proponuje.**

## Wymagania

| | |
|---|---|
| Python | 3.12 |
| [uv](https://docs.astral.sh/uv/) | menedżer zależności i środowiska |
| Node 20 | CLI pod Agent SDK — **nie** pod MCP, podprocesu MCP nie ma (D4). Do zbudowania frontu też, ale tylko na maszynie deweloperskiej: na serwer idą gotowe pliki |

## Start

```bash
uv sync                      # środowisko + zależności z uv.lock
uv run pre-commit install    # bramka lint/typy/sekrety przed commitem
cp .env.example .env         # i wypełnij — opis każdego pola jest w środku
chmod 600 .env               # sól to klucz prywatny, nie plik konfiguracyjny
```

Albo jednym poleceniem: `make instalacja`.

Wypełniony `.env` wystarcza — program czyta go sam (D12), więc `export` przed
uruchomieniem nie jest potrzebny. Zmienna ustawiona w środowisku i tak przebija
plik, a `MONDAY_AUDIT_ENV_FILE` albo `--plik-env` wskazuje go z innej lokalizacji.

## Komendy

Trzy wejścia, świadomie osobne — bo to trzy różne koszty i trzy różne momenty:

```bash
# 1. Collector: spisuje konto do snapshotu. Kosztuje wywołania klienta.
uv run python -m monday_audit.cli --klient cxlabs --zakres workspace --id 6576039

# 2. Agent: bada hipotezy z zamrożonego snapshotu. Kosztuje pieniądze za model.
uv run python -m monday_audit.cli_agent --klient cxlabs --snapshot 5 \
    --koszt-licencji-mies 100 --zrodlo-stawki "faktura 07/2026"

# 3. Cennik: odświeża stawki ze stron monday. NIGDY w trakcie audytu.
uv run python -m monday_audit.cli_cennik --odswiez --pokaz

# 4. Raport: dwa dokumenty HTML z zapisanego runu. Darmowe i powtarzalne.
uv run python -m monday_audit.cli_raport --run-id agent-pelny-19

# 5. Dashboardy jako pliki HTML: szybki podgląd bez serwera.
uv run python -m monday_audit.cli_pulpit --json

# 6. Aplikacja web: jeden adres, dwa wejścia. Klient sam odpala audyt.
cd front && npm install && npm run build && cd ..   # raz, po zmianach we froncie
uv run python -m monday_audit.cli_web --dodaj-klienta acme        # wypisuje hasło
uv run python -m monday_audit.cli_web --dodaj-osobe jle@cxlabs.digital
uv run python -m monday_audit.cli_web --serwuj --port 8010

# Zgubione hasło — droga ratunkowa z terminala (codziennie robi się to z panelu)
uv run python -m monday_audit.cli_web --zresetuj-haslo jle@cxlabs.digital
uv run python -m monday_audit.cli_web --zresetuj-haslo acme
```

Hasło wypisuje się **raz, na konsolę** — w bazie leży tylko hash `scrypt`, więc nie
da się go odzyskać, tylko wydać nowe. Konto zespołowe wymaga adresu w domenie
`@cxlabs.digital`.

**Ponowne `--dodaj-klienta` dla istniejącego klienta odmawia.** Do 2026-08-10
zakładało drugie konto, a stare hasło nadal wpuszczało — więc „wydałem nowe hasło"
nie odbierało starego dostępu. Do wymiany hasła służy `--zresetuj-haslo`.

### Rozliczanie agenta

```bash
AGENT_ROZLICZENIE=klucz         # domyślnie: run obciąża ANTHROPIC_API_KEY
AGENT_ROZLICZENIE=subskrypcja   # run idzie z zalogowania Claude Code
```

Tryb `klucz` wymaga `ANTHROPIC_API_KEY` i przerywa run **przed** wywołaniami monday,
gdy go nie ma. Tryb `subskrypcja` klucza nie potrzebuje, ale wtedy `koszt_usd`
przestaje być fakturą — jest wyceną teoretyczną z SDK. `runy.rozliczenie` zapisuje,
którym trybem poszedł każdy run, a panel oznacza kwotę.

Do 2026-08-05 klucz nie dochodził do podprocesu SDK i runy szły na subskrypcję
niezależnie od konfiguracji. Dlatego domyślną wartością jest `klucz`: tryb
subskrypcyjny ma być decyzją, nie stanem, w który wpada się przez zapomnienie.

### Dodawanie klienta

Z panelu: **Moje konto → Dostępy klientów → Dodaj klienta**. Identyfikator musi
pasować do `^[a-z0-9][a-z0-9-]{1,49}$`, bo trafia do adresu panelu i do nazw plików
raportu. Hasło pokazuje się raz.

Z terminala, jako droga ratunkowa: `--dodaj-klienta acme`.

### Reset haseł

| Kto | Jak |
|---|---|
| Zespół, zna hasło | panel → **Moje hasło** (podaje obecne) |
| Zespół, **nie pamięta** | brama → **Nie pamiętam hasła** → link na skrzynkę |
| Klient | **tylko zespół**, z panelu → *Dostęp klienta* |
| Ratunek | `--zresetuj-haslo` z terminala |

Klient **nie może zresetować hasła sam** — nie ma dla niego endpointu. Hasło jest
jedyną bramą do jego danych osobowych, a bez maila w naszej domenie nie mamy czym
potwierdzić, że o reset prosi on.

Reset **nie wylogowuje**: otwarta sesja żyje do 12 h. Panel i CLI mówią, ile sesji
zostaje ważnych — „odetnij dostęp teraz" to osobna funkcja, której jeszcze nie ma
(O26).

### Poczta dla „nie pamiętam hasła" (opcjonalna)

Bez tych zmiennych **link resetu ląduje w logu serwera** z ostrzeżeniem — działa,
ale to tryb awaryjny, nie docelowy. `smtplib` jest w stdlib, więc żadnej nowej
zależności to nie wymaga.

```bash
SMTP_HOST=smtp.gmail.com          # Google Workspace
SMTP_PORT=587
SMTP_USER=jle@cxlabs.digital
SMTP_HASLO=<hasło aplikacji>      # NIE hasło do konta Google
SMTP_NADAWCA=jle@cxlabs.digital   # opcjonalnie, domyślnie SMTP_USER
ADRES_PUBLICZNY=https://audyt.cxlabs.digital   # TYLKO za odwrotnym proxy
```

`ADRES_PUBLICZNY` zostaw **puste**, dopóki serwer stoi lokalnie: link resetu bierze
wtedy host i port z żądania, więc `--serwuj --port 8010` daje link na `:8010`.
Wcześniej to pole miało stałą `:8000` i link prowadził na port, na którym nic nie
nasłuchiwało. Ustaw je dopiero za Caddy (etap 5), gdzie żądanie widzi adres
wewnętrzny, a odbiorca publiczny.

Przy Google Workspace potrzebne jest **hasło aplikacji**, nie zwykłe hasło do konta
— Google odrzuca logowanie zwykłym hasłem. Generuje się je raz w ustawieniach konta
Google. `ADRES_PUBLICZNY` musi być adresem widocznym dla odbiorcy: `127.0.0.1`
w mailu do kogokolwiek innego po prostu nie zadziała.

**Klucza API klienta nie zapisujemy nigdzie** — nie ma na niego kolumny
w schemacie. Klient wkleja go w formularzu, klucz jedzie w ciele POST-a jako
argument funkcji runu i ginie razem z procesem. O tym, co odbiorca widzi, decyduje
**sesja po stronie serwera**, nigdy parametr z przeglądarki (D16).

Rozdzielenie 1 i 2 nie jest kosmetyczne: etap 4 wymaga przepuszczania **tego
samego** snapshotu przez nową rubrykę i nowy prompt, więc analiza nie może
wymagać ponownego zbierania danych.

## Sprawdzenia

```bash
make            # lista celów
make sprawdz    # lint + typy + testy, zatrzymuje się na pierwszym błędzie
make lint       # ruff check
make format     # ruff format, ZAPISUJE zmiany
make typy       # mypy
make testy      # pytest
make hooki      # wszystkie hooki pre-commit na całym repo
```

Te same narzędzia odpalają się automatycznie przed każdym commitem.
Testy integracyjne (uderzające w prawdziwe monday) są domyślnie odznaczone —
`-m integracyjny` je włącza.

## Struktura

| Ścieżka | Co tam jest |
|---|---|
| [`src/monday_audit/`](src/monday_audit/) | kod aplikacji |
| [`tests/`](tests/) | testy — warstwy opisane w `docs/etapy/04-test.md` |
| [`docs/`](docs/) | decyzje architektoniczne i specyfikacje etapów |
| [`rubryka_znalezisk.yaml`](rubryka_znalezisk.yaml) | definicje klas znalezisk — jednocześnie specyfikacja, skill agenta i podstawa evali |
| [`STATUS.md`](STATUS.md) | stan etapów; **należy do człowieka** |

## Granice, których nie wolno przekroczyć

Pełna lista z uzasadnieniami: [`CLAUDE.md`](CLAUDE.md) i
[`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md) D4–D6.

- **Agent nie ma żadnego narzędzia zapisującego** — nigdzie: ani do monday, ani
  do bazy, ani do plików. Narzędzia idą przez `MondayClient`, którego
  `przygotuj_zapytanie()` odrzuca `mutation` i `subscription`.
- **Nie używamy MCP monday.** Flaga `--read-only` nie blokuje zapisu —
  zmierzone, nie założone (D4, O19).
- Nie schodzimy na poziom itemów. `items_count` to granica.
- Żadnych imion, nazwisk i e-maili w kontekście modelu. Pseudonimizacja przed wywołaniem.
- Token klienta nigdy w kontekście modelu ani w argv — żyje w konfiguracji procesu (D12).
- Finding bez pola `dowod` nie przechodzi walidacji.
- Kwota bez stawki albo na stawce przeterminowanej nie przechodzi walidacji (D13).

Obrona polega na **odebraniu możliwości**, nie na filtrowaniu. Maksymalna szkoda
przy prompt injection to fałszywe znalezisko w raporcie — nie wyciek, nie modyfikacja.

## Dokumentacja

| Plik | Kiedy czytać |
|---|---|
| [`STATUS.md`](STATUS.md) | zawsze, pierwszy |
| [`docs/PODSUMOWANIE.md`](docs/PODSUMOWANIE.md) | **stan projektu w pięć minut, bez kodu** — dla kogoś z zewnątrz |
| [`docs/ZBUDOWANE.md`](docs/ZBUDOWANE.md) | co faktycznie stoi, moduł po module, z pomiarami |
| [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md) | decyzje D1–D15 z uzasadnieniami — przed zmianą architektury |
| [`docs/etapy/`](docs/etapy/) | pełna specyfikacja każdego etapu |
| [`docs/OTWARTE.md`](docs/OTWARTE.md) | założenia niepotwierdzone — nie fakty |
| [`docs/PROMPT_AGENTA.md`](docs/PROMPT_AGENTA.md) | prompt agenta produkcyjnego |
| [`docs/CENNIK_AI.md`](docs/CENNIK_AI.md) | metodologia stawek; liczby są w tabeli `cennik`, nie tutaj |
