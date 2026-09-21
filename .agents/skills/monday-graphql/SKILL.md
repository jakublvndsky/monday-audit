---
name: monday-graphql
description: Odpytywanie monday.com API v2 (GraphQL) i MCP — limity, complexity, paginacja, uprawnienia tokenów, zasada discovery-first. Używaj przy pisaniu lub debugowaniu kodu odpytującego monday.com, konfiguracji serwera MCP monday, oraz przy każdym błędzie rate-limit lub uprawnień.
---

# monday.com API — fakty, nie wspomnienia

**Ostrzeżenie:** monday.com API jest aktywnie rozwijane. Twoje dane
treningowe zawierają nieaktualne szczegóły. Nie zakładaj, że pole
istnieje albo nie istnieje — **sprawdź empirycznie.**

---

## Zasada discovery-first

Dla każdego niepewnego pola:

1. Wyślij zapytanie i sprawdź, co wraca
2. Działa → implementuj pełną ścieżkę
3. Błąd / null / brak pola → **dopiero teraz** fallback
4. Zaloguj: `[DISCOVERY] ✅ pole X dostępne` / `[DISCOVERY] ❌ brak Y, fallback`

Dotyczy szczególnie: zużycia automatyzacji, kredytów AI, `account.usage`,
`users.last_activity`, i wszelkich nowych pól.

Niepotwierdzone założenia: `docs/OTWARTE.md`.

---

## Limity (potwierdzone)

| Limit | Wartość |
|---|---|
| Complexity | 5 mln punktów/min (20 mln dla agentów) |
| Zapytania/min | 1 000 Free / 2 500 Pro / 5 000 Enterprise |
| **Wywołania dziennie** | **1 000 / 10 000 / 25 000** |
| Współbieżność | 40 / 100 / 250 |

**Wiążący jest limit dzienny, nie complexity.** To najczęstszy błąd
w projektowaniu — complexity brzmi groźniej, ale przy audycie
metadanych nie zbliżysz się do niego.

**To limit konta klienta, nie nasz.** Przekroczenie spowalnia jego
integracje w środku dnia roboczego. Przerwij przy 50%.

### Mierz complexity

```graphql
query {
  complexity { query after reset_in_x_seconds }
  boards(limit: 25) { id name }
}
```

Odczyty i zapisy mają osobne budżety complexity.

---

## Paginacja

- `boards` — limit twardy 500, paginuj przez `page`.
  **Używaj ~25 na stronę**, nie 500 — complexity rośnie z liczbą
  zagnieżdżonych pól, więc duża strona z `columns` i `subscribers`
  bywa droższa niż kilka małych.
- `users` — `limit` + `page`
- `activity_logs` — per tablica, limit 100. **Nie ma logu na poziomie
  konta**, więc wyczerpujące zebranie to N zapytań. Zawsze samplinguj.

---

## Uprawnienia tokenów — mechanizm, nie konwencja

**Wszystkie wywołania wykonują się w kontekście uwierzytelnionego
użytkownika, ograniczone do jego uprawnień. Eskalacja jest niemożliwa.**

Konsekwencja praktyczna: token z dostępem do trzech tablic wyaudytuje
trzy tablice i **nie powie ci, że resztę pominął.** Dlatego walidacja
`me { is_admin }` jest obowiązkowa i przerywająca (krok 3.3).

| Typ tokena | Zakres |
|---|---|
| Personal | odzwierciedla uprawnienia użytkownika w UI |
| Aplikacji | jawne scope'y (`boards:read`), zalecany produkcyjnie |

W tym projekcie: token read-only od admina klienta (D11).

---

## MCP monday

### Lokalny serwer (używamy tego)

```bash
MONDAY_TOKEN=xxx npx @mondaydotcomorg/monday-api-mcp@latest --read-only
```

**Flagi:**
| Flaga | Znaczenie |
|---|---|
| `--token`, `-t` | token (albo przez env `MONDAY_TOKEN`) |
| `--read-only`, `-ro` | **tylko odczyt — wymuszone przez serwer** |
| `--version`, `-v` | wersja API |
| `--mode`, `-m` | `api` / `apps` / `atp` |

**Token zawsze przez env, nigdy w argv** — argv jest widoczne w `ps`.

`--read-only` to podstawa granicy zaufania (D6). Nie jest instrukcją
w prompcie, którą model może zignorować — to wymuszenie w procesie.
**Nigdy nie uruchamiaj MCP bez tej flagi w tym projekcie.**

Uwaga: dynamiczne narzędzia API (`--enable-dynamic-api-tools`) **nie są
kompatybilne** z read-only. Nie używamy ich.

### Hostowany (nie używamy)

`https://mcp.monday.com/mcp`, Streamable HTTP, OAuth 2.1 albo bearer
z personal tokenem. Przyjmuje token, ale **nie ma flagi read-only** —
dlatego wybraliśmy lokalny (D4).

### Narzędzia MCP przydatne w audycie

`list_automations`, `get_automation_runs`, `get_automation_statistics`,
`get_board_activity`, `get_board_info`, `get_column_type_info`,
`list_users_and_teams`, `all_api_read`.

`get_automation_runs` i `get_automation_statistics` rozstrzygają O1 —
przetestuj je w kroku 3.6.

---

## Zapytania w tym projekcie

Konto i plan:
```graphql
query { me { id name is_admin
  account { id name slug plan { period tier max_users } } } }
```

Użytkownicy — **wynik przechodzi przez pseudonimizację przed zapisem**:
```graphql
query { users(limit: 500, page: $p) { id name email enabled is_admin
  is_guest is_pending is_verified created_at last_activity title
  teams { id name } } }
```

Tablice — **bez itemów** (D5):
```graphql
query { boards(limit: 25, page: $p, order_by: created_at) {
  id name state board_kind workspace { id name }
  owners { id } subscribers { id } items_count
  columns { id title type } updated_at created_at } }
```

---

## Obsługa błędów

Rozdziel dwie kategorie — mieszanie ich to najczęstszy błąd:

- **Rate limit / complexity** → retry z wykładniczym backoffem
  i **pełnym jitterem**. Nie stały delay.
- **Błąd zapytania** (złe pole, brak uprawnień) → **nie retry.**
  Powtórzenie da ten sam wynik. Zaloguj i przejdź dalej albo przerwij.

Częste pułapki:
- Odpowiedź 200 z tablicą `errors` — GraphQL nie zwraca 4xx przy błędzie
  zapytania. Sprawdzaj `errors`, nie tylko status.
- `null` w polu opcjonalnym vs brak pola w schemacie — pierwsze jest
  danymi, drugie błędem discovery.
