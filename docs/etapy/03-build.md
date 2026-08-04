# Etap 3 — Build

**Stan: w toku.**

> **Claude Code: to jest etap bieżący.** Realizuj **jedną pozycję naraz**,
> w kolejności. Po każdej zgłoś gotowość i zatrzymaj się. Nie zaczynaj
> następnej, dopóki człowiek nie odhaczy jej w `STATUS.md`.
>
> Kolejność nie jest sugestią. Jest bramą.

---

## Dlaczego funkcja po funkcji

Kuszące jest zbudowanie collectora i agenta równolegle, a potem
"zobaczenie czy działa". **Nie.** Wtedy debugujesz dwie niesprawdzone
warstwy jednocześnie i nie wiesz, czy problem jest w danych, czy
w rozumowaniu.

Brama po 3.8 jest bezwzględna: **agent nie powstaje, dopóki nie ma
jednego prawdziwego snapshotu, przejrzanego ręcznie przez człowieka.**

---

## 3.1 Schemat SQLite + migracje

Schemat: `docs/ARCHITEKTURA.md` D7.

- Migracje jako numerowane pliki SQL, aplikowane w kolejności
- Tabela `_migracje` z zapisem zastosowanych
- Bez ORM-a. `sqlite3` ze standardu plus ręczne zapytania
- Indeksy: `findings(snapshot_id)`, `snapshots(client_id, run_at)`,
  `wywolania(run_id)`

**Gotowe gdy:** migracje aplikują się od zera i są idempotentne.

---

## 3.2 Klient GraphQL

Fundament wszystkiego. Nie idź dalej, dopóki nie jest solidny.

```python
class MondayClient:
    def __init__(self, token: str, budzet_wywolan: int = 400): ...
    async def query(self, gql: str, variables: dict) -> dict: ...
    async def paginate(self, gql: str, sciezka: str) -> AsyncIterator[dict]: ...
```

Wymagania:

- **Complexity w każdym zapytaniu.** Dodaj pole `complexity { query after }`
  i loguj. To jedyny sposób, żeby wiedzieć, ile faktycznie kosztujesz
  klienta.
- **Retry z wykładniczym backoffem i pełnym jitterem.** Nie stały delay.
- **Licznik wywołań** z twardym limitem — przekroczenie rzuca wyjątek,
  nie loguje ostrzeżenia
- **Każde wywołanie do tabeli `wywolania`**
- **Rozdziel błędy:** rate limit (retry) od błędu zapytania (nie retry,
  bo powtórzenie da to samo)

**Limity monday** (potwierdzone w dokumentacji, patrz skill `monday-graphql`):
- complexity 5 mln/min
- zapytania/min: 1 000 Free / 2 500 Pro / 5 000 Enterprise
- **dzienne wywołania: 1 000 / 10 000 / 25 000** ← to jest wiążące
- współbieżność: 40 / 100 / 250

**Gotowe gdy:** klient przechodzi test na koncie CXLABS, loguje complexity,
a wymuszony limit faktycznie przerywa działanie.

---

## 3.3 Collector — konto i plan

> **⚠️ IMPLEMENTACJA ROZJEŻDŻA SIĘ Z TYM ZAPISEM — patrz `docs/OTWARTE.md` O8.**
> Treść poniżej zostaje **celowo nietknięta** jako zapis pierwotnego zamiaru.
> Dwie zmiany, obie zatwierdzone przez Kubę 2026-07-30 i uzasadnione w O8:
> brama `is_admin` zamieniona na deklarowany zakres, a z zapytania wypadły
> `me { name }` i `me { id }` (PII przed pseudonimizacją z 3.4).
> Kod: `src/monday_audit/konto.py`.

```graphql
query { me { id name is_admin account { id name slug
  plan { period tier max_users } } } }
```

- **Sprawdź `is_admin`.** Jeśli false — przerwij z jasnym komunikatem.
  Token bez uprawnień admina da cichy, niepełny audyt, a to gorsze
  niż brak audytu.
- Zapisz `plan.tier` — determinuje dzienny limit wywołań dla reszty runu

**Gotowe gdy:** metadane konta w snapshocie, walidacja admina działa.

---

## 3.4 Collector — użytkownicy + pseudonimizacja

**To jest granica PII. Zaimplementuj ją tu, raz, poprawnie.**

```graphql
query { users(limit: 500, page: $p) { id name email enabled is_admin
  is_guest is_pending is_verified created_at last_activity title
  teams { id name } } }
```

Przetwarzanie:

1. Dla każdego użytkownika policz `user_hash` (stabilny, np. HMAC-SHA256
   z solą per klient)
2. Do `osoby_mapowanie` zapisz: `user_hash` → imię, nazwisko, e-mail
3. **Do snapshotu zapisz WYŁĄCZNIE:** `user_hash`, `title`, nazwy zespołów,
   `enabled`, `is_admin`, `is_guest`, `is_pending`, `created_at`,
   `last_activity`

Snapshot nie może zawierać ani jednego imienia, nazwiska ani adresu e-mail.
**Napisz test, który to sprawdza** — skanuje payload snapshotu wzorcem
e-maila i listą imion z mapowania.

**Discovery:** zweryfikuj, czy `last_activity` zwraca timestamp
w aktualnej wersji API. Jeśli nie — fallback na activity logs (3.7).

**Gotowe gdy:** snapshot ma użytkowników bez PII, test antyprzeciekowy
przechodzi, mapowanie jest kompletne.

---

## 3.5 Collector — tablice i kolumny

```graphql
query { boards(limit: 25, page: $p, order_by: created_at) {
  id name state board_kind workspace { id name }
  owners { id } subscribers { id } items_count
  columns { id title type } updated_at created_at } }
```

- Paginacja po ~25, nie 500 — complexity rośnie z liczbą pól
- `owners` i `subscribers` **zmapuj na hashe** (te same co w 3.4)
- **NIE pobieraj itemów.** `items_count` to granica (D5)
- Zbierz **wszystkie** stany, nie tylko `active` — archiwizacja
  jest sygnałem

**Gotowe gdy:** wszystkie tablice w snapshocie, paginacja obsługuje
konto większe niż jedna strona.

---

## 3.6 Collector — automatyzacje

**Discovery-first.** Nie zakładaj, co jest dostępne.

Przetestuj i zaloguj wynik:
1. `automations` na boards
2. `account { usage { automations } }`
3. ~~Narzędzia MCP: `list_automations`, `get_automation_runs`,
   `get_automation_statistics`~~ — **odpada**, MCP nie jest już częścią
   architektury (D4). Discovery zamknęło się na punktach 1–2; wynik
   i faktyczne pola są w O1.

To jest najdroższy krok (~200 wywołań, liniowo per tablica).
Rozważ ograniczenie do tablic aktywnych.

**Blokuje O1** — jeśli liczba uruchomień jest niedostępna, oznacz to
w snapshocie polem `uruchomienia_dostepne: false`, żeby detektory
wiedziały, że mają zwężony sygnał.

**Gotowe gdy:** wynik discovery zalogowany, automatyzacje w snapshocie,
brak danych obsłużony jawnie a nie ciszą.

---

## 3.7 Collector — activity logs z samplingiem

**Nie pobieraj wyczerpująco.** To jest miejsce, gdzie łatwo spalić
dzienny limit klienta.

Sampling:
- top 30 tablic po `items_count`
- 20 losowych z ogona (żeby wychwycić martwe)
- limit 100 wpisów per tablica

Z logów wyciągnij **sygnały, nie treść**:
- kto (hash) zmieniał co i kiedy — do `ENGAGEMENT_DROP`, `PROCESS_BYPASS`
- rozkład typów akcji
- **czy ostatnie wpisy pochodzą od ludzi, czy od automatyzacji** —
  to kluczowy sygnał odróżniający żywą tablicę od pozornie żywej

**Nie zapisuj treści updateów.** Zapisuj fakt: "5 ostatnich zmian
pochodzi z automatyzacji, zero od ludzi".

**Gotowe gdy:** sampling działa, snapshot ma sygnały aktywności,
zero treści w payloadzie.

---

## 3.8 Zapis snapshotu + pierwszy prawdziwy run

Uruchom całość na koncie CXLABS. Zapisz snapshot.

**Wypisz raport z runu:** liczba wywołań, suma complexity, czas,
wyniki discovery, rozmiar snapshotu.

---

## 🚧 BRAMA — nie przekraczaj bez zgody człowieka

Zanim powstanie cokolwiek z warstwy agentowej:

- [ ] Snapshot istnieje w bazie
- [ ] **Człowiek przejrzał go ręcznie** i potwierdził, że dane
      odpowiadają rzeczywistości konta
- [ ] Test antyprzeciekowy PII przechodzi
- [ ] Liczba wywołań w granicach oczekiwań (~250)
- [ ] Wyniki discovery zapisane, `OTWARTE.md` zaktualizowany
- [ ] Zamrożone 3–5 snapshotów jako korpus na etap 4

**Ostatni punkt jest łatwy do pominięcia i najbardziej kosztowny.**
Bez zamrożonego korpusu nie ma na czym testować agenta, a wtedy etap 4
zamienia się w dopisywanie kryteriów pod to, co agent akurat wyprodukował.

---

## 3.9 Detektory deterministyczne

Czysty SQL po JSONB. **Zero AI.**

Dla każdej klasy z rubryki (poza `status: do_weryfikacji`) zaimplementuj
sygnał wzbudzający. Wyjście: lista hipotez.

```python
@dataclass
class Hipoteza:
    klasa_id: str
    obiekt_id: str | int      # board_id, user_hash, workspace_id
    fakty: dict               # to, co wzbudziło sygnał
    budzet_wywolan: int       # z rubryki
```

Uwaga na klasy z sygnałem złożonym — `PROCESS_BYPASS`
i `DUPLICATE_STRUCTURE` wymagają porównywania tablic między sobą
(nakładanie kolumn, nakładanie subskrybentów, okna czasowe).
To nadal SQL, tylko trudniejszy.

**Gotowe gdy:** detektory na zamrożonym snapshocie zwracają hipotezy,
a wynik jest powtarzalny (ten sam snapshot → ta sama lista).

---

## 3.10 Narzędzia agenta

**Tylko czytające. Bez wyjątków.**

Wszystkie **własne**, wszystkie w `monday_audit.narzedzia`. Dwa czytają
snapshot, dwa wchodzą do monday — i te dwa idą przez ten sam `MondayClient`,
którego używa collector:

```python
pobierz_inwentarz(zakres: str) -> dict   # ze snapshotu
zapytaj_snapshot(pytanie: str) -> dict   # 8 predefiniowanych pytań, nie surowy SQL
probka_kolumn(board_id: str) -> dict     # do monday: SAME LICZNIKI wypełnienia
log_tablicy(board_id: str) -> dict       # do monday: activity log, autorzy pseudonimizowani
```

> **ZMIENIONE 2026-08-03.** Ta sekcja mówiła wcześniej „MCP monday, podproces
> per run: `npx @mondaydotcomorg/monday-api-mcp@latest --read-only`". **Nie
> rób tego.** Flaga nie blokuje zapisu — zmierzone na wersji 3.3.0, `create_board`
> i surowa mutacja przez `all_api_write` przeszły do API (D4, O19). Podprocesu
> MCP nie ma już w architekturze.

Zasady:
- **Każde narzędzie przycina wyjście.** Surowa odpowiedź API do kontekstu
  to główna przyczyna, dla której agenci przestają rozumować.
- **Licznik per hipoteza.** Wyczerpanie budżetu = narzędzie zwraca
  komunikat o wyczerpaniu, nie błąd. Agent ma domknąć hipotezę
  z tym, co ma.
- Token **nigdy** w kontekście modelu

**Gotowe gdy:** agent nie ma technicznej możliwości zapisu,
budżety działają, wyjścia są przycięte.

---

## 3.11 Pętla agenta + walidacja

Prompt systemowy: `docs/PROMPT_AGENTA.md`.
Kontrakt wyjściowy: `ARCHITEKTURA.md` D8.

Przepływ: hipotezy → agent bada każdą w ramach budżetu → JSON →
**walidacja** → baza.

Walidacja odrzuca:
- finding bez `dowod` albo z niepełnym `dowod`
- `klasa_id` nieistniejące w rubryce
- klasę ze `status: do_weryfikacji`
- `kwota_pln` przy `typ_wyceny: ryzyko`
- brak `hipotezy_odrzucone`

**Odrzucenie loguj, nie ukrywaj.** Odsetek odrzuconych findingów
to twoja główna metryka jakości w etapie 4.

**Gotowe gdy:** pełny run na zamrożonym snapshocie daje zwalidowany
JSON, a przynajmniej jedna hipoteza jest odrzucona z podanym powodem.

---

## 3.12 Renderer + publikacja

- Szablon HTML z Claude Design w CXLABS Design System
- Wstrzyknięcie JSON (wzorzec z Proposal Engine)
- **Deanonimizacja dopiero tutaj** — renderer czyta `osoby_mapowanie`
- Dwa wyjścia: wewnętrzne (pełne + `trop`), klientowe
  (bez `tylko_wewnetrzne`)
- Publikacja przez skill `cxlabs-docs-publisher`,
  nazwa `RRRR-MM_audyt_konta.html`

**Gotowe gdy:** oba warianty wychodzą jako linki, wersja klientowa
nie zawiera ani jednego findingu oznaczonego `tylko_wewnetrzne`.

---

## Definition of Done — etap 3

- [ ] Wszystkie pozycje 3.1–3.12 odhaczone przez człowieka
- [ ] Pełny run end-to-end na koncie CXLABS
- [ ] Korpus 3–5 zamrożonych snapshotów
- [ ] Test antyprzeciekowy PII przechodzi
- [ ] `OTWARTE.md` zaktualizowany wynikami discovery
