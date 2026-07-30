# monday.com Account Audit — instrukcje dla Claude Code

## Zanim cokolwiek zrobisz

1. Przeczytaj `STATUS.md`.
2. Pracuj **wyłącznie** nad etapem oznaczonym jako `etap_biezacy`.
3. **Nigdy nie edytuj `STATUS.md`.** Ten plik należy do człowieka. Zmiana etapu
   to jego decyzja, nie twoja.
4. Nie zaczynaj kolejnego etapu, nawet jeśli bieżący wygląda na skończony.
   Zgłoś gotowość i zatrzymaj się.

## Co to jest

Wewnętrzne narzędzie CXLABS. Audytuje konto monday.com klienta i produkuje
raport ze znaleziskami. Odpalane ręcznie, jednorazowo per klient.
Nie SaaS, nie abonament, nie self-service.

Dwie warstwy:
- **Collector** — deterministyczny, spisuje wyczerpująco, czysty GraphQL
- **Agent** — bada hipotezy wzbudzone przez collector, wchodzi do monday
  tylko tam, gdzie coś nie pasuje

## Zakazy twarde

Naruszenie któregokolwiek = błąd krytyczny, zatrzymaj się i zapytaj.

- **Agent nie dostaje żadnego narzędzia zapisującego.** Nigdzie: ani do monday,
  ani do bazy, ani do plików. MCP monday odpalany zawsze z `--read-only`.
- **Nie schodzimy na poziom itemów** poza jawnie oznaczonym samplingiem
  w klasie `BOARD_OVERCOMPLEX`. `items_count` to granica.
- **Żadnych imion, nazwisk i e-maili w kontekście modelu.** Pseudonimizacja
  przed wywołaniem, tabela mapowania bez narzędzia dostępowego.
- **Token klienta nigdy w kontekście modelu.** Żyje w env procesu MCP.
- **Finding bez pola `dowod` nie przechodzi walidacji.** Bez wyjątków.
- **Nie dodawaj zależności bez pytania.** Szczególnie: Postgres, Redis,
  Celery, Langfuse. Każda była rozważona i odrzucona — powody w
  `docs/ARCHITEKTURA.md`.

## Stack

Python 3.12, `uv`, `httpx` (collector), Agent SDK (analityk), SQLite,
FastAPI, Caddy. Node 20 tylko jako podproces MCP. Bez frontu w v1.

## Gdzie co jest

| Plik | Kiedy czytać |
|---|---|
| `STATUS.md` | zawsze, pierwszy |
| `docs/etapy/0N-*.md` | pełna specyfikacja bieżącego etapu |
| `docs/ARCHITEKTURA.md` | decyzje z uzasadnieniami — **czytaj przed zmianą architektury** |
| `docs/OTWARTE.md` | założenia niepotwierdzone — nie traktuj ich jako faktów |
| `rubryka_znalezisk.yaml` | definicje klas znalezisk |
| `docs/PROMPT_AGENTA.md` | prompt agenta produkcyjnego (runtime, nie build) |

Skille (`.claude/skills/`) wczytują się same, gdy zadanie do nich pasuje.

## Zasada, gdy masz wątpliwość

Ten projekt jest budowany funkcja po funkcji, świadomie wolno.
Jeśli widzisz szybszą drogę, która pomija etap albo łączy dwie warstwy —
**napisz o niej i poczekaj.** Nie wykonuj.
