# monday.com Account Audit — instrukcje dla Codex

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
  Celery. Każda była rozważona i odrzucona — powody w `docs/ARCHITEKTURA.md`.
- **Langfuse Cloud jest wdrożony** (D10 cofnięta 2026-09-22, faza 4). Reguły,
  które zostają w mocy — naruszenie każdej to wysyłka danych klienta:
  - **wszystko, co wychodzi poza serwer, przechodzi przez `maskowanie.py`.**
    Nie ma drugiej drogi na zewnątrz i nie wolno jej dorabiać,
  - **maskowanie zawodzi zamknięte.** `MaskowanieError` znaczy „trace nie
    wychodzi", nigdy „wyślij surowe". Nie łap go razem z błędami transportu,
  - **prompt systemowy nie wychodzi** — idzie sam hasz. Prompt niesie
    inwentarz, czyli nazwy tablic i workspace'ów klienta,
  - **trafienie wzorca to alarm, nie sukces.** Znaczy, że PII weszło do
    kontekstu modelu wyżej. Ma iść do logu, nie zniknąć w podmianie,
  - **trzy zmienne albo zero.** Konfiguracja połowiczna przerywa start, żeby
    biblioteka nie spadła na swój domyślny region.

## Stack

Python 3.12, `uv`, `httpx` (collector **i** narzędzia agenta), Agent SDK
(analityk), SQLite, FastAPI. Front: React 19 + Vite (D16) — budowany lokalnie,
na serwer idą gotowe pliki z `front/dist`.

**Node NIE jest potrzebny w produkcji** (sprawdzone 2026-08-25): Agent SDK wozi
własny plik wykonywalny `_bundled/claude` i sprawdza go przed szukaniem w PATH.
Node bierze udział tylko w `npm run build` na maszynie deweloperskiej.

**Caddy wypadł ze stacku** (2026-08-25): Mikr.us to kontener LXC bez portu
80/443, więc ACME nie ma jak przejść.

**HTTPS: przez nginx, który na serwerze już stoi** (D19, 2026-09-01). Serwer
docelowy nie jest pusty — dzielimy go z sześcioma cudzymi vhostami, dwiema
aplikacjami PM2 i n8n w Dockerze, a **oba przekierowane porty TCP są zajęte**.
TLS terminuje Cloudflare, panel wchodzi jako kolejny vhost proxujący na
`127.0.0.1:8000`. Subdomena `mikrus.cloud` i tunel Cloudflare to drogi zapasowe.
Szczegóły: `deploy/README.md` krok 2. **Bez Dockera** — D20, i to jest decyzja,
nie przypadek.

## Gdzie co jest

| Plik | Kiedy czytać |
|---|---|
| `STATUS.md` | zawsze, pierwszy |
| `docs/plan.md` | **fazy przebudowy i gdzie w nich jesteśmy** — czytaj przy „co dalej"; jedna faza na raz, nie przepisuj planu przy okazji pytania |
| `docs/HANDOFF_ETAP6.md` | **wejście do pracy nad wdrożeniem** — co stoi na produkcji, jak wdrażać, co zostało z kolejki zerowej, konfiguracja ręczna poza repo |
| `docs/PODSUMOWANIE.md` | stan projektu bez kodu — gdy ktoś pyta „na czym stoimy" |
| `docs/ZBUDOWANE.md` | **co już stoi i co zostało zmierzone** — zanim zaczniesz cokolwiek budować |
| `docs/WYBOR_ZAKRESU.md` | wybór zakresu audytu: dwie bramki, flagi, podłoga kosztu, co niedokończone |
| `docs/etapy/0N-*.md` | pełna specyfikacja bieżącego etapu |
| `docs/ARCHITEKTURA.md` | decyzje z uzasadnieniami — **czytaj przed zmianą architektury** |
| `docs/OTWARTE.md` | założenia niepotwierdzone — nie traktuj ich jako faktów |
| `rubryka_znalezisk.yaml` | definicje klas znalezisk |
| `docs/PROMPT_AGENTA.md` | prompt agenta produkcyjnego (runtime, nie build) |
| `docs/HANDOFF_PORTAL.md` | makieta frontu portalu: ekrany, stany interfejsu, zawartość wyniku. **Świadomie bez kontraktu API** |
| `docs/NOTATKA_PORTAL_DECYZJE.md` | decyzje do podjęcia przy wpinaniu audytu w portal — magazyn klucza, harmonogram, kolejka, baza. Należą do Kuby |

Skille (`.agents/skills/`) wczytują się same, gdy zadanie do nich pasuje.

## Zasada, gdy masz wątpliwość

Ten projekt jest budowany funkcja po funkcji, świadomie wolno.
Jeśli widzisz szybszą drogę, która pomija etap albo łączy dwie warstwy —
**napisz o niej i poczekaj.** Nie wykonuj.
