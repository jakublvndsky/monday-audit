# Etap 5 — Deploy

**Stan: zablokowany do zamknięcia etapu 4.**

> Ten dokument opisuje **kryteria i mechanizmy**, nie implementację.
> Szczegóły wdrożenia domykamy po pierwszym działającym runie —
> pisanie ich teraz byłoby opisywaniem czegoś, czego nie znamy.

---

## Pinowanie wersji

Wszystkie **sześć** elementów poniżej musi być zapisane przy każdym runie.
Bez tego audyt sprzed trzech miesięcy jest nieodtwarzalny.

Rosło z czterech: dwa doszły z pomiarów, nie z projektu. **Wersja API** —
bo `2026-10` usuwa wszystkie flagi użytkownika, więc ta sama kwerenda na
innej wersji zwraca inne dane (O15). **Wersja cennika** — bo od chwili, gdy
stawki odświeżają się same, ta sama kwota policzona w lipcu i we wrześniu
będzie inna (D13).

| Element | Gdzie zapinane | Dlaczego |
|---|---|---|
| **Model** | `runy.model`, pełny identyfikator, nie alias | alias przesuwa się przy nowym wydaniu i wynik zmienia się bez zmiany kodu |
| **Rubryka** | `rubric_version` przy każdym findingu | umożliwia porównanie starego snapshotu z nową rubryką |
| **Prompt agenta** | hash pliku `PROMPT_AGENTA.md` | zmiana promptu zmienia wynik i musi być śledzona |
| **Collector** | `collector_ver` w snapshocie | zmiana zakresu zbierania zmienia znaczenie snapshotu |
| **Wersja API monday** | `meta.wersja_api` w snapshocie | nieprzypięta wersja to cicha zmiana schematu po stronie dostawcy (O15) |
| **Wersja cennika** | `runy.cennik_ver` | znacznik `pobrano_at` stawek UŻYTYCH w runie; run bez kwot zostaje z NULL, żeby nie pinować cudzej daty |

Alias modelu (typu `latest`) w produkcji jest zakazany. Podnoszenie
wersji modelu przechodzi przez bramę promocji jak każda inna zmiana.

---

## Brama promocji

Zmiana idzie na produkcję **tylko** po przejściu warstwy 3 z etapu 4.

Blokery bezwzględne:
- fałszywe trafienia > 0.1
- odsetek odrzuconych na walidacji > 0.15
- test antyprzeciekowy PII nie przechodzi
- test injection nie przechodzi
- którykolwiek `hipotezy_odrzucone` pusty

Regresja w trafności o więcej niż 0.05 wobec poprzedniego baseline'u
wymaga świadomej decyzji człowieka, nie automatycznego przejścia.

**Uruchamiaj evale na tym samym zamrożonym korpusie co poprzednio.**
Zmiana korpusu i zmiana promptu w jednym kroku = brak informacji,
co spowodowało różnicę.

---

## Sekrety

| Sekret | Gdzie | Uwagi |
|---|---|---|
| Klucz API Anthropic | env procesu workera | nigdy w repo |
| Token klienta monday | env procesu workera, **nie argv** | argv widoczne w `ps`. Podprocesu MCP nie ma (D4) — token wczytuje `konfiguracja.wczytaj()` i nie wychodzi poza `MondayClient` |
| Sól do hashowania osób | env, osobno per klient | wyciek soli = możliwość deanonimizacji |
| Klucz publishera docs | env | |

Po zakończeniu audytu **token klienta jest usuwany.** Nie przechowujemy
poświadczeń między runami (D11). Jeśli klient chce powtórki, daje token
ponownie.

---

## Wdrożenie na Mikrusa

**AKTUALIZACJA 2026-08-25 — trzy kroki niżej były nieaktualne.** Pełna,
wykonywalna instrukcja jest teraz w `deploy/README.md`; tutaj zostaje
uzasadnienie zmian, bo to ono jest wiedzą, a nie same komendy.

Kolejność, każdy krok weryfikowalny osobno:

1. ~~Node 20~~ **Python 3.12 i `uv`. Node NIE jest potrzebny na serwerze.**
   `claude-agent-sdk 0.2.128` wozi własny plik wykonywalny
   (`_bundled/claude`, 246 MB), a jego `_find_cli()` sprawdza go PIERWSZY,
   przed szukaniem `claude` w PATH. Sprawdzone w kodzie SDK. Ryzyko przenosi się
   z „zainstaluj Node" na „upewnij się, że `uv sync` wziął wheel `manylinux`" —
   pilnuje tego krok w CI.
2. ~~Caddy + `Caddyfile`~~ **HTTPS bez Caddy.** Mikr.us to kontener LXC
   z *przekierowanymi portami*, bez własnego IPv4 i bez portu 80/443 — wyzwanie
   ACME nie ma jak przejść. Dwie drogi: darmowa subdomena
   `serwer-port.mikrus.cloud` (HTTPS automatyczny, aplikacja słucha na IPv6)
   albo **tunel Cloudflare** na `audyt.cxlabs.digital`, bez otwartych portów.
   Domena `cxlabs.digital` już stoi na Cloudflare (sprawdzone), a subdomena
   `audyt` jest wolna. Wpływ na D9 i aneks D16: `ADRES_PUBLICZNY` nadal jest
   potrzebny za tunelem, tylko proxy nie jest nasze.
3. SQLite + migracje → sprawdź, czy aplikują się od zera *(bez zmian; migracje
   aplikuje `przygotuj_baze()` przy starcie `cli_web`)*
4. FastAPI jako usługa systemd → sprawdź `/health` — **endpointu nie było,
   dopisany 2026-08-25.** Publiczny (czytają go systemd, skrypt wdrożenia
   i monitoring, żaden nie ma sesji), mówi tylko o stanie procesu i numerze
   migracji. Otwiera WŁASNE połączenie, nie przez zależność FastAPI: przy
   uszkodzonej bazie zależność wywala 500 z całej aplikacji, a kontrola zdrowia
   ma tę awarię rozpoznać, nie w niej tonąć.
5. Worker jako proces jednorazowy wywoływany przez FastAPI —
   **nie demon** (O6, budżet RAM)
6. Pierwszy run produkcyjny na koncie CXLABS, potem **brama promocji**
   (`evals/brama.py`) przed czymkolwiek dla klienta

**Sprawdź realną rezerwę RAM przed krokiem 5** (O6). Jeśli poniżej 800 MB,
zawęź sampling activity logs.

### Budżet dysku — dopisany 2026-08-25, wcześniej nie było go nigdzie

| co | rozmiar |
|---|---|
| środowisko produkcyjne (`uv sync --frozen --no-dev`) | **~275 MB** |
| z tego `claude_agent_sdk/_bundled/claude` | **246 MB** (jeden plik) |
| baza przy 12 snapshotach | 3,7 MB |
| `front/dist` | 300 KB |
| kopia zapasowa po `gzip` | ~600 KB |

Plan Mikrus 1.0 (5 GB, 384 MB RAM) jest ciasny: środowisko zajmuje 6% dysku,
a szczyt runu (~280 MB zmierzone) nie mieści się w RAM z zapasem. **2.1
(10 GB, 1 GB RAM) daje margines.**

### RAM — pierwszy pomiar, nie szacunek

`02-design.md` budżetuje ~720 MB w szczycie. Zmierzone (macOS, `ru_maxrss`):
aplikacja web z detektorami **71 MB**, podproces `claude` w trakcie analizy
**130–210 MB**, czyli szczyt **~280 MB**. To 2,5× mniej niż budżet — ale pomiar
jest z macOS-a i **nie mówi, ile zostaje na Mikrusie**. O6 pozostaje otwarte.

---

## Kopie zapasowe

Snapshoty są niemutowalne i są jedynym źródłem case studies z liczbami.
Ich utrata jest nieodwracalna — nie da się odtworzyć stanu konta klienta
z przeszłości.

- Codzienny `.backup` SQLite poza Mikrusa
- Test odtworzenia raz, ręcznie, przed pierwszym audytem klienta

---

## Definition of Done — etap 5

- [ ] Sześć elementów pinowania zapisywanych przy runie
- [x] **Brama promocji jako skrypt** — `evals/brama.py`, kody wyjścia 0/1/2,
      metryki przez `evals/mierz.py` (bez drugiej implementacji progów).
      Sprawdzona na prawdziwym runie: trafność 0,857, blokery działają
- [x] **Sekrety w env, token klienta nie w argv** — `EnvironmentFile` w jednostce
      systemd (nie `Environment=`, bo `systemctl show` je pokazuje),
      `.env.example` dokumentuje wszystkie 12 pól `Ustawienia`
- [ ] Run produkcyjny na koncie CXLABS przechodzi
- [x] **Kopia zapasowa i test odtworzenia** — `deploy/backup.sh`, `.backup`
      SQLite (nie `cp` — kopiowanie w trakcie zapisu daje uszkodzony plik bez
      ostrzeżenia). Test sprawdza integralność **i zawartość**: pusta, poprawna
      baza kończy się kodem 1, bo wygląda na kopię, a nią nie jest.
      **Odtworzenie nadal do wykonania na serwerze** — lokalnie zweryfikowane
      na kopii prawdziwej bazy (12 snapshotów, 42 runy)

**Dodane 2026-08-25, czego ta lista nie miała:**

- [x] **CI po każdym push** — `.github/workflows/sprawdz.yml`, trzy joby na
      `ubuntu-latest`: backend (ruff, mypy, 806 testów), front (`tsc`, build,
      kontrakt `api.ts`), pre-commit ze skanerami sekretów. Bez sekretów, bo
      `addopts` wyklucza testy integracyjne
- [x] **`/health`** — wymagany przez krok 4, w kodzie go nie było
- [ ] **Rezerwa RAM na Mikrusie** (O6) — jedyny pomiar, którego nie da się
      zrobić poza serwerem
