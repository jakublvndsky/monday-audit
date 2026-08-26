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
  ani do bazy, ani do plików. Narzędzia idą przez `MondayClient`, którego
  `przygotuj_zapytanie()` odrzuca `mutation` i `subscription` — w tej ścieżce
  kodu nie ma jak wysłać zapisu.
- **NIE używamy MCP monday.** Flaga `--read-only` nie działa: sprawdzone
  2026-08-03 na wersji 3.3.0, `create_board` i `all_api_write` z surową mutacją
  **przeszły do API**. Nie wracaj do MCP bez ponownego pomiaru — szczegóły w D4.
- **Nie schodzimy na poziom itemów** poza jawnie oznaczonym samplingiem
  w klasie `BOARD_OVERCOMPLEX`. `items_count` to granica.
- **Żadnych imion, nazwisk i e-maili w kontekście modelu.** Pseudonimizacja
  przed wywołaniem, tabela mapowania bez narzędzia dostępowego.
- **Token klienta nigdy w kontekście modelu ani w argv.** Żyje w konfiguracji
  procesu (D12), wczytywanej z `.env` albo ze środowiska.
- **Finding bez pola `dowod` nie przechodzi walidacji.** Bez wyjątków.
- **Nie dodawaj zależności bez pytania.** Szczególnie: Postgres, Redis,
  Celery, Langfuse. Każda była rozważona i odrzucona — powody w
  `docs/ARCHITEKTURA.md`.

## Stack

Python 3.12, `uv`, `httpx` (collector **i** narzędzia agenta), Agent SDK
(analityk), SQLite, FastAPI. Front: React 19 + Vite (D16) — budowany lokalnie,
na serwer idą gotowe pliki z `front/dist`.

**Node NIE jest potrzebny w produkcji** (sprawdzone 2026-08-25): Agent SDK wozi
własny plik wykonywalny `_bundled/claude` i sprawdza go przed szukaniem w PATH.
Node bierze udział tylko w `npm run build` na maszynie deweloperskiej.

**Caddy wypadł ze stacku** (2026-08-25): Mikr.us to kontener LXC bez portu
80/443, więc ACME nie ma jak przejść. HTTPS daje darmowa subdomena Mikrusa albo
tunel Cloudflare — `docs/etapy/05-deploy.md` i `deploy/README.md`.

## Gdzie co jest

| Plik | Kiedy czytać |
|---|---|
| `STATUS.md` | zawsze, pierwszy |
| `docs/PODSUMOWANIE.md` | stan projektu bez kodu — gdy ktoś pyta „na czym stoimy" |
| `docs/ZBUDOWANE.md` | **co już stoi i co zostało zmierzone** — zanim zaczniesz cokolwiek budować |
| `docs/WYBOR_ZAKRESU.md` | wybór zakresu audytu: dwie bramki, flagi, podłoga kosztu, co niedokończone |
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
