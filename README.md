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
```

Hasło klienta wypisuje się **raz, na konsolę** — w bazie leży tylko hash `scrypt`,
więc nie da się go odzyskać, tylko wygenerować nowe. Konto zespołowe wymaga
adresu w domenie `@cxlabs.digital`.

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
