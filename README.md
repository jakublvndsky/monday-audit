# monday.com Account Audit

Wewnętrzne narzędzie CXLABS. Audytuje konto monday.com klienta i produkuje raport
ze znaleziskami. Odpalane ręcznie, jednorazowo per klient.
Nie SaaS, nie abonament, nie self-service.

> **Stan: etap 3 (Build), w toku.** Collector, detektory, agent i renderer jeszcze
> nie istnieją. Aktualny postęp funkcja po funkcji: [`STATUS.md`](STATUS.md).

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
    → agent (Agent SDK, MCP --read-only)
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
| Node 20 | podproces MCP monday oraz CLI Claude Code pod Agent SDK |

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
plik, a `MONDAY_AUDIT_ENV_FILE` albo `--plik-env` wskazuje go z innej lokalizacji:

```bash
uv run python -m monday_audit.cli --klient cxlabs --zakres workspace --id 6576039
```

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
[`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md) D5–D6.

- Agent nie ma żadnego narzędzia zapisującego. MCP zawsze z `--read-only`.
- Nie schodzimy na poziom itemów. `items_count` to granica.
- Żadnych imion, nazwisk i e-maili w kontekście modelu. Pseudonimizacja przed wywołaniem.
- Token klienta nigdy w kontekście modelu — żyje w env podprocesu MCP.
- Finding bez pola `dowod` nie przechodzi walidacji.

Obrona polega na **odebraniu możliwości**, nie na filtrowaniu. Maksymalna szkoda
przy prompt injection to fałszywe znalezisko w raporcie — nie wyciek, nie modyfikacja.

## Dokumentacja

| Plik | Kiedy czytać |
|---|---|
| [`STATUS.md`](STATUS.md) | zawsze, pierwszy |
| [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md) | decyzje D1–D11 z uzasadnieniami — przed zmianą architektury |
| [`docs/etapy/`](docs/etapy/) | pełna specyfikacja każdego etapu |
| [`docs/OTWARTE.md`](docs/OTWARTE.md) | założenia niepotwierdzone — nie fakty |
| [`docs/PROMPT_AGENTA.md`](docs/PROMPT_AGENTA.md) | prompt agenta produkcyjnego |
