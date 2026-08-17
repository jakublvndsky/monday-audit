# Założenia niepotwierdzone

**Claude Code: to nie są fakty.** Nie buduj na nich logiki bez oznaczenia
miejsca jako tymczasowego. Jeśli implementacja zależy od któregoś z tych
punktów, zaimplementuj wykrywanie (discovery) i ścieżkę awaryjną, a nie
założenie zapisane na sztywno.

Zasada discovery-first pochodzi z briefu Artura i jest właściwa:
**najpierw wyślij zapytanie i sprawdź, co wraca — dopiero potem
implementuj fallback.** Loguj wyniki: `[DISCOVERY] ✅ pole X dostępne`.

---

## CZEKAJĄ NA DECYZJĘ CZŁOWIEKA

Skrót dla nowej sesji: to są miejsca, gdzie kod działa na moim założeniu,
a nie na twoim rozstrzygnięciu. Szczegóły w wskazanych pozycjach.

| Co | Gdzie | Stan tymczasowy w kodzie |
|---|---|---|
| Snapshot #1 przejrzany? | BRAMA w `03-build.md` | snapshot 1 zapisany, zakres = workspace 6576039; #2 na pełnym koncie **czeka na zatwierdzenie #1** |
| Kosz (`state: deleted`) w snapshocie | **O10** | zbieramy `all`, listujemy `active` + `archived`, kosz tylko liczony |
| Tablice `Subitems of ...` | **O14** | nieodfiltrowane; zafałszują `BOARD_GHOST` w 3.9 |
| Sandbox jako blokada `.env` | rozmowa 2026-07-31 | `permissions.deny` na `.env` i `.env.local`; polecenia Basha nadal mogą czytać plik |
| Sól pseudonimizacji | wygenerowana przeze mnie 2026-07-30 | w `.env`; jej zmiana unieważnia porównywalność snapshotów |
| `board.updated_at` jako sygnał życia | **O18** | zaniża o do 40 dni; rozstrzyga `najnowszy_at` z logu |
| **Śledzenie kredytów agentów monday** | **O20**, **O21** | sonda notuje dostępność; kredytów nie ma w stabilnym API, a Enterprise ich nie płaci — liczba musi przyjść od człowieka z panelu Admin |

Zamknięte: `pydantic-settings` jako źródło konfiguracji (**D12**, zgoda ustna
2026-07-31), prawa do pliku `.env` (`chmod 600`, 2026-08-01), przypięcie
wersji API (**O15**) i model użytkownika `kind`+`status` (**O17**) — oba
2026-08-01. Pozycja „`is_verified` w rekordzie użytkownika" zniknęła razem
z polem: patrz O17.

---

## O1. Czy API zwraca liczbę uruchomień automatyzacji

**Status: ROZSTRZYGNIĘTE 2026-07-30 — tak, na poziomie konta. Szczegóły w O12.**
**Blokuje:** `AUTOMATION_DEAD` — sygnał "0 uruchomień w 90 dni"

Odpowiedź: `account_trigger_statistics` zwraca `success`, `failure`, `total`
i **nie potrzebuje MCP** — czysty GraphQL, jedno wywołanie. Na koncie CXLABS:
1 226 udanych, 11 nieudanych, 1 237 razem.

**Ale sygnał jest węższy, niż zakładała rubryka.** Nie ma listy automatyzacji
per tablica, więc „0 uruchomień konkretnej automatyzacji" jest nieosiągalne;
osiągalne jest „ta tablica miała 0 zdarzeń automatyzacji w okresie".
`AUTOMATION_DEAD` musi być na tym oparte i wiedzieć o zwężeniu — collector
zapisuje `lista_automatyzacji_dostepna: false` w snapshocie.

Pełne ustalenia, w tym zepsuty filtr `board_id`: **O12**.

---

## O2. Zużycie kredytów AI — ROZSTRZYGNIĘTE: API tego nie oddaje

**Status: ROZSTRZYGNIĘTE 2026-08-04 — nie ma tego w wersji stabilnej.**
Szczegóły powierzchni agentowej w **O20**, cennik w `docs/CENNIK_AI.md`.

**Poprzedni status:** niepotwierdzone, z podejrzeniem co do przyczyny
**Blokuje:** cała klasa `AI_UNUSED` (oznaczona `status: do_weryfikacji`)
**Jeśli nie:** klasa zostaje wyłączona, ewentualnie zastąpiona ręcznym
sprawdzeniem w Admin panel klienta.
**Jak sprawdzić:** `account { usage { ai } }` plus introspekcja typu
`Account`.

**Wynik rozpoznania 2026-07-30:** `account { plan { tier period max_users } }`
zwróciło **`null`** na koncie CXLABS przy tokenie bez uprawnień admina.
Nie wiemy, czy to brak uprawnień, czy natura planu partnerskiego — ale jeśli
`plan` jest bramkowany rolą, to `usage` prawdopodobnie też. **Nie testuj O2
tokenem bez admina i nie wyciągaj z null-a wniosku, że pola nie ma.**
Rozstrzygnięcie wymaga porównania odpowiedzi tokena admina i członka.

**ROZSTRZYGNIĘCIE 2026-08-04.** Sprawdzone zapytaniem na koncie CXLABS,
nie samą introspekcją:

- korzeniowe `usage` zwraca `CampaignsUsage`, czyli metryki marketingowe
  (`email_sends`, `marketing_contacts`) — **nie ma tam AI**
- `Account` ma tylko `plan` i `tier`, żadnego pola o kredytach
- korzeń zapytania **nie ma ANI JEDNEGO pola** z `credit`, `usage` ani `agent`
  w wersji `2026-07` ani `2026-10`
- typy `AgentActivityRun.credits_used`, `ChatMessage.credits_used`
  i `VibeQueries.ai_credits_billing_cycle` **istnieją w schemacie, ale nic
  ich nie zwraca** — są nieosiągalne

Czyli null przy `plan` nie był kwestią uprawnień: kredytów po prostu nie ma
w publicznym API. `AI_UNUSED` zostaje `do_weryfikacji`, ale **z innego powodu
niż dotąd** — nie „nie wiemy", a „wiemy, że nie ma". Jedyne źródło to panel
Admin → AI governance → Credits, czyli droga ręczna, jak `koszt_licencji_mies`
w O7.

---

## O3. Kredyty AI przy zewnętrznych agentach — POTWIERDZONE u źródła

**Status: ROZSTRZYGNIĘTE 2026-08-04 co do strony deweloperskiej**
**Waga:** wysoka — to argument cenowy, nie tylko techniczny

Support monday twierdzi, że rozumowanie zewnętrznego agenta zużywa
kredyty dostawcy, nie monday AI. Dokumentacja deweloperska mówi
o podłączonych agentach, że konsumują kredyty AI rozliczane w dashboardzie
użycia konta.

Da się to pogodzić (rozumowanie u dostawcy, akcje AI po stronie monday
z kredytów monday), ale **nie prezentować jako argumentu cenowego
przed potwierdzeniem.**

**POTWIERDZENIE 2026-08-04.** Dokumentacja deweloperska monday (źródło
pierwotne, [Build on monday.com with AI](https://developer.monday.com/api-reference/docs/build-on-monday-with-ai))
mówi wprost, że podłączeni agenci „consume AI credits tracked under the
account's usage dashboard". Strona dev z pierwotnej sprzeczności jest więc
potwierdzona cytatem; twierdzenie supportu pozostaje nierozstrzygnięte
i pogodzenie obu (rozumowanie u dostawcy, akcje AI z kredytów monday) nadal
jest tylko hipotezą.

**Ale ta sama strona NIE dokumentuje żadnego API do odczytu tego zużycia** —
co jest zgodne z pomiarem z O2.

**Czego nie da się zmierzyć na CXLABS:** konto jest na Enterprise, a tam
agenci są darmowi (**O21**). Pomiar „kredyty przed i po runie agenta" da zero
niezależnie od tego, co się stanie. Walidacja tej ścieżki wymaga konta
na Pro albo niżej.

---

## O4. Czy `items_count` wystarcza

**Status:** założenie projektowe do walidacji
**Blokuje:** ocenę, czy D5 (nie schodzimy do itemów) się utrzyma

Rubryka zakłada, że objętość tablicy da się ocenić po `items_count`.
Ale kilka klas byłoby mocniejszych z **napływem** (nowe itemy/miesiąc).
Napływ wymaga zejścia na poziom itemów.

**Jeśli okaże się niezbędny dla więcej niż jednej klasy:** decyzja D5
do przemyślenia od nowa, świadomie, z akceptacją kosztu PII i objętości.
Nie obchodź tego po cichu.

---

## O5. Ile itemów na próbkę w BOARD_OVERCOMPLEX

**Status:** nieokreślone
**Blokuje:** wykonalność klasy `BOARD_OVERCOMPLEX`

Jedyna klasa, która przecina zasadę D5. Potrzebuje próbki itemów,
żeby ocenić, które kolumny są martwe.

**Do ustalenia empirycznie:** ile itemów daje wiarygodną ocenę.
Jeśli wyjdzie powyżej ~50 na tablicę, klasa istotnie podnosi koszt runu
i **wypada pierwsza** przy cięciu zakresu.

---

## O6. Co jeszcze chodzi na Mikrusie

**Status:** do sprawdzenia przez Kubę
**Blokuje:** budżet RAM

Założenie: ~360 MB w spoczynku, ~720 MB w szczycie runu. Przy 2 GB
dzielonych z innymi aplikacjami CXLABS trzeba znać realną rezerwę.

**Jeśli rezerwa < 800 MB:** worker musi działać jako proces jednorazowy
(nie demon), a sampling activity logs trzeba zawęzić.

**Aktualizacja 2026-08-06 — doszedł serwer web, i to zmierzone.** Front
z D16 mógł podnieść ten budżet o proces uvicorna i o Node. Zmierzone:

| | |
|---|---|
| uvicorn + FastAPI w spoczynku | **12 MB RSS** |
| zbudowany front (`front/dist`) | **236 KB** — dwa pliki statyczne |
| Node na serwerze | **niepotrzebny** |

Node bierze udział tylko w `npm run build` na maszynie deweloperskiej; na serwer
idą gotowe pliki, które FastAPI oddaje jako statyki. Więc web **nie zmienia
rzędu wielkości** tego budżetu — nadal decyduje szczyt runu, a nie panel.

Co nadal wymaga pomiaru Kuby: realna rezerwa na Mikrusie. Pomiar powyżej jest
z macOS-a i mówi tylko, że web jest tani, nie ile zostaje.

---

## O7. Koszt licencji u klienta

**Status:** zmienna wejściowa, nie do odgadnięcia
**Blokuje:** wycenę w `ZOMBIE_ACCOUNT` i `PLAN_MISMATCH`

To jedyne dwie klasy z kwotą. Kwota wymaga `koszt_licencji_mies`,
który **trzeba pobrać od klienta**, nie szacować.

**Implementacja:** parametr wejściowy runu. Jeśli nie podany —
finding raportowany bez kwoty, z adnotacją "wycena po podaniu
kosztu licencji". Nigdy nie zgaduj tej liczby.

---

# Wyniki rozpoznania — konto CXLABS, 2026-07-30

Zebrane przy 3.2, tokenem Kuby (`MONDAY_TOKEN`), ~180 wywołań read-only.
**To są pomiary, nie założenia** — ale pomiary z **jednego** konta,
i to konta partnerskiego pełnego demówek. Jako proxy typowego klienta
jest złe, jako test obciążeniowy dobre.

| Zmierzone | Wartość |
|---|---|
| workspace'y widoczne dla tokena | 128 (116 `open`, 12 `closed`) |
| tablice, `state: all` | 3 171 |
| — `active` / `deleted` / `archived` | 1 909 / 1 216 / 46 |
| tablice `private` | 5, wszystkie z Kubą jako subskrybentem |
| `me { is_admin }` | **false** |
| `account { plan }` | **null** (patrz O2) |
| waga inwentarza (pełne pola z 3.5) | 1 403 B/tablicę, ~13 kolumn |
| complexity strony 25 tablic | ~128 tys. |
| complexity pełnego przelotu 1 909 tablic | ~9,7 mln |

---

## O8. Zakres tokena — POTWIERDZONE, unieważnia bramę z 3.3

**Status:** potwierdzone empirycznie
**Blokuje:** 3.3 (walidacja admina), 3.5 (kompletność snapshotu)

Potwierdzone:

- **Token żyje w jednym koncie.** Nie ma technicznej możliwości dosięgnięcia
  konta innego klienta. Audyt klienta wymaga tokena z konta klienta (D11).
- **Widzi wszystkie workspace'y, do których użytkownik ma dostęp** — także
  `closed`, jeśli jest ich członkiem. Nie tylko jeden workspace.
- **Prywatne tablice tylko tam, gdzie jest subskrybentem** (5 z 5). Token nie
  informuje, że coś pominął — to jest dokładnie ten cichy niepełny audyt,
  którego boi się 3.3.
- **`boards(workspace_ids: [...])` filtruje poprawnie** — sprawdzone, zero
  tablic z obcych workspace'ów. To otwiera audyt zawężony do workspace'u,
  wykonalny tokenem bez admina.
- **`page` działa** na `boards` i `workspaces`, przy `limit` 3, 25 i 100;
  paginacja kończy się pustą stroną.

**Niepotwierdzone i wymagające tokena admina:** czy admin widzi prywatne
tablice innych osób. Przy `is_admin: false` nie da się tego rozstrzygnąć.
Test rozstrzygający: ktoś inny tworzy prywatną tablicę bez Kuby, admin
odpytuje `boards(board_kind: private)`.

**Konsekwencja dla 3.3:** brama „`is_admin` false → przerwij" zabija audyt
CXLABS na pierwszym kroku, a Kuba nie będzie miał uprawnień admina.

**DECYZJA KUBY (2026-07-30): deklarowany zakres zamiast bramy binarnej.**
Wiążąca dla 3.3, zastępuje literalny zapis z `03-build.md`:

- run przyjmuje jawny `zakres`: całe konto (**wymaga** `is_admin`) albo
  lista `workspace_ids` (nie wymaga)
- snapshot zapisuje `is_admin`, liczbę workspace'ów, liczbę tablic
  i flagę `pokrycie_pelne`
- przerwanie **tylko** wtedy, gdy ktoś prosi o całe konto tokenem bez admina
- raport mówi wprost, co było audytowane

Intencja 3.3 zostaje nienaruszona — cichy niepełny audyt nadal niemożliwy,
ale realizowany przez jawność zakresu, nie przez odmowę.

---

## O9. Okno complexity jest większe, niż mówi dokumentacja

**Status:** zmierzone, sprzeczne z dokumentacją
**Blokuje:** tempo collectora w 3.5 i 3.7

Dokumentacja monday i skill `monday-graphql` mówią **5 mln/min**.
Na koncie CXLABS zaobserwowany zapas (`complexity.after`) sięga
**~9,87 mln**, a okno resetuje się w trakcie runu.

Pomiar: 45 stron po 25 tablic z pełnym zestawem pól = 5,69 mln complexity
w 45 wywołaniach, **bez ani jednego `ComplexityException`** i bez ani jednej
pauzy hamulca. Zapas w trakcie runu spadł do 7,4 mln i wrócił do 9,7 mln.

Wnioski:

1. **Wiążący jest limit dzienny wywołań, nie complexity** — dokładnie tak,
   jak mówi skill. Przy zapytaniach wysyłanych sekwencyjnie okno resetuje się
   szybciej, niż jesteśmy w stanie je opróżnić.
2. **Hamulec complexity w kliencie (3.2) jest ubezpieczeniem, nie wąskim
   gardłem.** Mechanizm jest pokryty testami jednostkowymi, ale **nie odpalił
   się ani razu na żywym API** — nie mam dowodu z produkcji, że działa
   w prawdziwym scenariuszu wyczerpania. Chroni przed kontem z mniejszym
   oknem (Free?) i przed droższymi zapytaniami, nie przed tym, co widzimy.
3. Nie zakładaj 5 mln ani 10 mln na sztywno. Klient czyta `after`
   i `reset_in_x_seconds` z każdej odpowiedzi i nie potrzebuje tej stałej.
4. **Pusta strona domykająca paginację kosztuje 0 complexity** (zmierzone
   przy 3.5). Nie żądaj więc dodatniego kosztu od każdego wywołania —
   konwencja z 3.2 pozostaje: `NULL` w `wywolania.complexity` znaczy próbę
   nieudaną, a `0` to poprawna odpowiedź bez wyników.

---

## O10. `state: all` wciąga kosz — 38% tablic to `deleted`

**Status:** zmierzone, wymaga decyzji projektowej
**Blokuje:** 3.5 i wszystkie detektory liczące tablice

1 216 z 3 171 tablic (38%) ma `state: deleted`. To kosz, nie archiwum.

3.5 każe zebrać **wszystkie** stany, bo „archiwizacja jest sygnałem" — i to
jest słuszne dla `archived` (46 tablic). Ale `deleted` to śmieci: bez
rozdzielenia tych dwóch stanów każda liczba w raporcie klienta jest zawyżona
o zawartość kosza, a `BOARD_GHOST` policzy tablice, których nikt już nie ma.

**Do decyzji człowieka:** czy `deleted` wchodzi do snapshotu jako osobna
kategoria (moja rekomendacja: tak, zbieramy i oznaczamy, ale detektory
liczą tylko `active` + `archived`), czy odfiltrowujemy je już w collectorze.
Pierwsze jest droższe o ~12 wywołań, ale kosz sam potrafi być znaleziskiem —
1 216 usuniętych tablic mówi coś o higienie konta.

**Zaimplementowane w 3.5 jako domyślne, do potwierdzenia:** jeden przelot
`state: all`, do listy w snapshocie wchodzą `active` i `archived`, a `deleted`
jest **tylko liczony** (`usunietych_pominietych` w podsumowaniu). Parametr
`zbieraj_usuniete=True` odwraca decyzję bez zmiany kodu. Powód takiego
domyślnego ustawienia: przy 38% kosza każda liczba w raporcie klienta byłaby
zawyżona o zawartość śmietnika, a `BOARD_GHOST` liczyłby tablice, których
nikt już nie ma.

---

## O11. Użytkownicy — POTWIERDZONE, plus trzy pułapki

**Status:** zmierzone przy 3.4 na koncie CXLABS (95 użytkowników, 1 strona)
**Blokuje:** `ZOMBIE_ACCOUNT`, `ENGAGEMENT_DROP`

Pełne zapytanie z 3.4 **działa w całości** — wszystkie pola wracają:
`id name email enabled is_admin is_guest is_pending is_verified created_at
last_activity title teams { id name }`.

| Zmierzone | Wartość |
|---|---|
| użytkowników | 95 |
| `enabled: true` | 95 (wszyscy) |
| adminów / gości / oczekujących | 10 / 12 / 1 |
| `is_verified: false` | 1 |
| `last_activity` wypełnione | **58 z 95** |
| bez `title` | 50 |
| bez zespołu | 86 |
| koszt | 1 wywołanie, ~1 750 complexity |

**Pułapka 1: `last_activity` jest timestampem, ale u 37 osób jest `null`.**
Format to ISO-8601 ze strefą (`2026-07-30T20:25:49Z`), zakres na koncie
CXLABS: od marca 2025 do dziś. **`null` znaczy „nie wiem", nie „nieaktywny
od zawsze".** `ZOMBIE_ACCOUNT` nie może liczyć tych 37 kont jako martwych
na podstawie tego pola — dla nich sygnał musi przyjść z activity logs (3.7).
Collector zapisuje liczbę braków w snapshocie, żeby detektor nie mógł
tego przemilczeć.

**Pułapka 2: wszyscy użytkownicy są `enabled`.** Nie wiadomo, czy
`users` w ogóle zwraca konta dezaktywowane — być może domyślnie je pomija.
Ma to znaczenie dla wyceny: konto dezaktywowane nie zużywa licencji, więc
`ZOMBIE_ACCOUNT` powinien liczyć tylko aktywne. **Do sprawdzenia:**
argument `kind` w `users` (wartości do introspekcji) na koncie, które ma
kogoś dezaktywowanego.

**Pułapka 3: skan PII po pojedynczych tokenach daje fałszywki.**
Pierwsza wersja walidatora antyprzeciekowego przerywała run przy każdym
trafieniu tokenu z pola `name` w tekstach pisanych przez klienta (`title`,
nazwy zespołów). Na CXLABS dało to **54 trafienia z 3 tokenów** — wszystkie
fałszywe: konta serwisowe mają w `name` nazwę firmy albo produktu (jeden
taki token występuje w 28 nazwach zespołów), a nie imię osoby.

Rozstrzygnięcie zapisane w kodzie (`osoby.py`):

- **twardo przerywa run:** cokolwiek w formacie adresu e-mail oraz **pełne**
  imię i nazwisko jako ciągły napis (nazwy jednowyrazowe nie liczą się jako
  imię i nazwisko)
- **liczy i raportuje, nie przerywa:** pojedyncze tokeny — wynik ląduje
  w snapshocie jako `podejrzenia_pii_w_tekstach` i w logu jako ostrzeżenie,
  do ręcznego przejrzenia przy BRAMIE po 3.8

Powód rozdzielenia: `title` i nazwy zespołów pisze klient, nie my. Wyciekiem
jest skopiowanie przez nas pola `name` albo `email` — i to jest wykluczone
konstrukcyjnie (snapshot budowany z listy dozwolonych pól). Przerywanie
audytu na tym, że klient nazwał zespół słowem, które ktoś ma w nazwie konta,
byłoby blokadą bez powodu.

---

## O12. Automatyzacje w API — specyfikacja 3.6 opisuje pola, których nie ma

**Status:** rozstrzygnięte introspekcją schematu 2026-07-30
**Blokuje:** `AUTOMATION_DEAD`, `AUTOMATION_ABSENT`, kształt 3.6
**Zakres rozpoznania:** konto CXLABS, workspace 6576039, ~40 wywołań
(introspekcja kosztuje 0 complexity)

### Czego NIE ma, wbrew zapisowi 3.6

| Zapis w 3.6 | Rzeczywistość |
|---|---|
| `automations` na `boards` | **nie istnieje** — w 39 polach typu `Board` nie ma nic o automatyzacjach ani workflow |
| `account { usage { automations } }` | **nie istnieje** — `Account` ma 15 pól i `usage` nie jest jednym z nich |
| korzeniowe `usage` | istnieje, ale to `CampaignsUsage` (e-maile marketingowe), nie automatyzacje |

W API monday automatyzacje nazywają się **triggerami** i mają osobne zapytania
w korzeniu schematu. Bez introspekcji nie było tego jak znaleźć.

### Co działa

1. **`account_trigger_statistics` → `{success, failure, total}`.**
   Jedno wywołanie, całe konto. To odpowiedź na O1.
2. **`account_triggers_statistics_by_entity_id(run_status:)`** → mapa
   `automation_id → {total, powód_błędu: liczba}`. Kluczem jest
   **automatyzacja**, nie tablica. Enum `TriggerEventState`:
   `success`, `failure`, `exhausted`.
3. **`trigger_events(filters: {boardId: String, dateRange: {startDate, endDate}})`**
   → pojedyncze zdarzenia, 200 na stronę. Jedyna ścieżka per tablica.

### Pułapka: filtr `board_id` jest zepsuty

`AccountTriggerStatisticsFiltersInput.board_id` i
`AccountTriggersByEntityIdFiltersInput.board_id` są typu **`Int`**, a GraphQL
`Int` to 32 bity ze znakiem (maks 2 147 483 647). **Wszystkie identyfikatory
tablic na koncie CXLABS mają 10 cyfr** (np. 5097387646) i przekraczają ten
zakres. API odpowiada:

```
Int cannot represent non 32-bit signed integer value: 5097387646
```

Czyli statystyk triggerów **nie da się zawęzić do tablicy ani workspace'u**.
Za to `TriggerEventsFiltersInput.boardId` jest **Stringiem** i przyjmuje pełny
identyfikator — dlatego sonda per tablica idzie tą ścieżką.

**Konsekwencja dla zakresu audytu:** trzy liczby uruchomień są z natury
na poziomie konta. Zapytanie nie wylicza ani nie ujawnia żadnych tablic
ani workspace'ów — zwraca trzy liczniki. Atrybucja per tablica wymaga
osobnego wywołania na tablicę, i to jest jedyne miejsce w 3.6, które ma
wolumen. Domyślny sufit: 10 tablic, a liczba pominiętych ląduje
w snapshocie (`tablic_pominietych`).

### Efekt uboczny: `account.tier` ratuje budżet z 3.3

Introspekcja `Account` pokazała pole **`tier`** obok `plan`. Na koncie CXLABS:

```
account.plan = null        ← to samo co w O2
account.tier = 'enterprise'
account.active_members_count = 19
```

Czyli tier konta **jest** dostępny, tylko nie tam, gdzie 3.3 go szukało.
Poprawione: `konto.py` czyta `plan.tier`, a gdy null — `account.tier`,
i zapisuje w snapshocie, z którego pola wziął (`zrodlo_tieru`). Bez tego
budżet wywołań zostawał na 400 przy koncie z limitem 25 000 dziennie.

**`active_members_count = 19` przy 95 użytkownikach** to sygnał dla
`ZOMBIE_ACCOUNT` i `PLAN_MISMATCH` — do wykorzystania w 3.9. Nie wiem,
czy liczy licencje, czy aktywność; **do potwierdzenia**.

---

## O13. Activity logs — dwie pułapki i brak znacznika automatu

**Status:** zmierzone przy 3.7, 2026-07-30
**Blokuje:** `ENGAGEMENT_DROP`, `PROCESS_BYPASS`, `BOARD_GHOST`
**Zakres rozpoznania:** workspace 6576039, 2 tablice, ~4 wywołania

### Kształt API

`Board.activity_logs(column_ids, from, group_ids, item_ids, limit, page, to, user_ids)`
→ `ActivityLogType` z **siedmioma** polami:
`account_id`, `created_at`, `data`, `entity`, `event`, `id`, `user_id` (wszystkie `String`).

`from` i `to` są typu `ISO8601DateTime` — okno 90 dni działa. Nie ma logu
na poziomie konta, więc każda tablica to osobne wywołanie. Koszt: ~12,6 tys.
complexity na tablicę przy `limit: 25`.

### Pułapka 1: `created_at` nie jest datą

Log zwraca **`17830789794688296`** — liczbę jednostek 100 ns od epoki Unixa,
nie ISO-8601, mimo że typ pola to `String`. Sprawdzone przez porównanie
z `board.updated_at` tej samej tablicy: `17830789794688296 / 10^7 = 1783078979 s`
→ 2026-07-03, zgodnie z `updated_at = 2026-07-03T11:44:39Z`.

Naiwne `fromisoformat` albo porównanie stringów dają śmieci — a na tym polu
stoi okno 90 dni w `ENGAGEMENT_DROP`. Konwersja siedzi w `logi.na_iso()`
i ma test na prawdziwym znaczniku.

### Pułapka 2: `data` to treść klienta

Pole `data` (JSON w stringu) zawiera realne wartości. Zaobserwowane klucze:

```
action_record_uuid, board_id, board_name, column_id, column_title,
column_type, group_color, group_id, group_title, is_column_with_hide_permissions,
is_rollup_column, is_top_group, is_undo_action, parent_board_id, parent_item_id,
previous_textual_value, previous_value, pulse_id, pulse_name, value
```

`value`, `previous_value`, `previous_textual_value` i `pulse_name` to wartości
kolumn i nazwy itemów, czyli dokładnie to, czego zabrania D5 i granica PII.
**Nie pobieramy tego pola wcale** — nie ma go w zapytaniu, żeby nie polegać
na tym, że ktoś je potem odfiltruje. Test pilnuje treści zapytania.

### Brak znacznika „to zrobiła automatyzacja"

Żadne z siedmiu pól nie mówi, czy autorem wpisu był człowiek. A to jest
**kluczowy sygnał z 3.7** — ten, który odróżnia tablicę żywą od pozornie
żywej. Rozwiązanie: porównanie `user_id` z listą użytkowników konta z 3.4.
Autor nieobecny na liście to najpewniej system, bot albo konto usunięte.

**To heurystyka i jest tak oznaczona w snapshocie**
(`rozroznienie_czlowiek_automat: "heurystyka: user_id nieobecny na liście konta"`).
Bez listy użytkowników na wejściu każdy autor wyjdzie jako nieznany, więc
sygnał byłby bezwartościowy — collector loguje wtedy ostrzeżenie.

**Do sprawdzenia:** czy monday używa stałych, rozpoznawalnych identyfikatorów
dla akcji systemowych (np. ujemnych). Na dwóch zbadanych tablicach był
tylko jeden autor, więc nie było na czym tego zobaczyć.

### Rozbudowa warstwy aktywności (2026-07-31)

Pierwsza wersja agregowała trzy osie osobno — kto (zbiór autorów), co
(liczniki typów), kiedy (min i maks) — i **wyrzucała powiązania między nimi**.
Snapshot #1 pokazał, że to za cienko na health score: „jedna osoba zrobiła 90%
zmian trzy miesiące temu" i „pięć osób zmienia coś co tydzień" dawały
identyczny zestaw liczb.

Doszły cztery sygnały, **żaden nie kosztuje dodatkowego wywołania** — te dane
były już w odpowiedzi API:

| Sygnał | Pole | Po co |
|---|---|---|
| ostatnia zmiana OD CZŁOWIEKA | `najnowszy_od_znanego_at` | `updated_at` tablicy zmienia też automat; to jest data, która mówi o ludziach |
| udział autorów | `udzial_autorow`, `udzial_najaktywniejszego` | jeden autor na 90% zmian to ryzyko bus factor, nie zdrowie |
| kubełki czasowe 0-30/31-60/61-90 | `kubelki_dni` | `ENGAGEMENT_DROP` widać w kształcie rozkładu, nie w sumie |
| podział zdarzeń | `po_klasie` | `subscribe` i `set_entity_board_role` to zmiana DOSTĘPU, nie używanie. Na zbadanej tablicy 32 ze 100 wpisów — wrzucone do „operacyjnych" zawyżałyby sygnał życia o jedną trzecią |

Nierozpoznane typy zdarzeń lądują w klasie `inne`, a ich nazwy w
`discovery.nieznane_zdarzenia` — do sklasyfikowania w następnym runie.
Świadomie bez heurystyki po podłańcuchu nazwy: dawałaby ciche pomyłki,
a `po_event` i tak trzyma pełne liczniki.

**Paginacja logu.** Sto wpisów na stronę to nie „tyle jest" — 3 z 15 tablic
w snapshocie #1 miały log urwany. Teraz paginujemy do `maks_stron` (domyślnie
5, czyli 500 wpisów), z **deduplikacją po `id` wpisu**. Dedup jest
zabezpieczeniem, nie optymalizacją: gdyby `page` był ignorowany — a w tym API
zdarzył się już zepsuty filtr `board_id` (O12) — naiwna paginacja policzyłaby
te same zdarzenia po kilka razy i zawyżyła każdą metrykę. Powtórzona strona
przerywa pętlę i ląduje w `discovery.paginacja_logow_dziala: false`.

**ROZSTRZYGNIĘTE 2026-07-31 — `page` stronicuje.** Run na tablicy 5097387646
zwrócił `discovery.paginacja_logow_dziala: true`, czyli kolejna strona przyniosła
inne `id` wpisów, a nie powtórzone. Obecność argumentu w schemacie niczego nie
gwarantowała — filtr `board_id` też jest w schemacie i jest zepsuty (O12) —
więc dedup po `id` zostaje jako zabezpieczenie, teraz już z potwierdzeniem,
że w normalnym przypadku nic nie odsiewa.

**Sufity próbki wrócone do liczb ze specyfikacji:** top 30 + 20 z ogona.
Wcześniejsze 10 + 5 dawało health score liczony na 14% tablic workspace'u.

---

### Odstępstwo od specyfikacji: ogon deterministyczny, nie losowy

3.7 mówi „20 **losowych** z ogona". Zaimplementowane deterministycznie:
tablice o **najmniejszej** liczbie itemów. Powód: celem ogona jest wychwycenie
martwych tablic, a losowanie łamałoby powtarzalność — 04-test.md wymaga,
żeby dwa runy na tym samym koncie dały snapshoty różniące się **tylko
znacznikami czasu**. Przy losowaniu różniłyby się też próbką.

Sufity zmniejszone z „top 30 + 20" na **top 10 + 5 z ogona** na życzenie
przy zawężeniu do jednego workspace. Zmiana zakresu, nie zasady — liczba
tablic pominiętych ląduje w snapshocie.

---

## O14. Tablice podelementów i dokumenty — ROZSTRZYGNIĘTE przez `Board.type`

**Status: ROZSTRZYGNIĘTE 2026-08-01 — collector zbiera `type`, detektory filtrują**
**Blokuje:** jakość `BOARD_GHOST`, pośrednio `DUPLICATE_STRUCTURE`

W snapshocie #1 (workspace 6576039) osiem tablic ma `items_count = 0`, czyli
wyglądają na kandydatów na `BOARD_GHOST`. Jedna z nich to
**`Subitems of ⚠️ Flagi Konfliktów`** — tablica techniczna, którą monday
tworzy automatycznie dla podelementów. Nie jest martwa, tylko służebna,
i nikt jej nigdy nie „używa" bezpośrednio.

**Do zrobienia w 3.9:** detektor `BOARD_GHOST` musi odfiltrować tablice
podelementów, zanim policzy cokolwiek. Rozpoznanie po nazwie
(`Subitems of `) jest kruche — do sprawdzenia, czy `Board.type` albo
`hierarchy_type` (oba są w schemacie, patrz O12) odróżniają je wprost.
Jeśli tak, collector powinien te pola zbierać w 3.5.

**ZMIERZONE 2026-07-31 — nazwa jest LOKALIZOWANA.** Sondując workspace 6576039
trafiłem na tablicę **„Elementy podrzędne tablicy Lista pomysłów Agentów AI"**,
czyli polski odpowiednik `Subitems of ...`. Filtr po angielskim przedrostku
przepuściłby ją bez śladu.

**ROZSTRZYGNIĘCIE 2026-08-01: `Board.type`.** Pole rozwiązuje sprawę bez
zgadywania po nazwie. Zmierzone na workspace 6576039 (snapshot #4):
`board` 97, `document` 5, `sub_items_board` 3 — czyli **8 obiektów ze 105,
które `boards` zwraca, nie jest tablicą**. Sprawdzone: polska „Elementy
podrzędne tablicy ..." ma poprawnie `sub_items_board`.

**Druga klasa fałszywek, o której nie wiedzieliśmy: DOKUMENTY.** Pięć obiektów
ma `type = document`. Dokument nie ma itemów ani kolumn w sensie tablicy, więc
w `BOARD_GHOST` wyglądałby na porzucony, a w `BOARD_OVERCOMPLEX` na pusty.
Nikt tego nie przewidział w etapie 1, bo nikt nie zakładał, że `boards` zwraca
coś, co tablicą nie jest.

Collector zbiera `type` (3.5), rozkład idzie do `discovery.po_typie`, a rubryka
0.2 ma w `BOARD_GHOST` warunek `type = board` i warunek odrzucenia. Wartości
`hierarchy_type` (wszędzie `classic`) i `board_kind` (wszędzie `public`) NIE
odróżniają tych obiektów — sprawdzone, są bezużyteczne do tego celu.

**Dlaczego to ważne:** to dokładnie ten rodzaj fałszywki, którą rubryka
nazywa najgroźniejszą — klient sprawdzi takie znalezisko pierwsze
i zobaczy, że narzędzie nie rozumie jego konta.

---

## O15. Wersja API monday — PRZYPIĘTA 2026-08-01

**Status: ROZSTRZYGNIĘTE — `klient.WERSJA_API = "2026-07"`, zapisywana w `meta` snapshotu**
**Blokuje:** odtwarzalność snapshotów (D7), stabilność collectora

API monday jest wersjonowane (`2024-01`, `2024-10`, …) i wybiera się je
nagłówkiem `API-Version`. Collector **nie wysyła tego nagłówka**, więc dostaje
**wersję domyślną konta**, a tę monday przesuwa w czasie.

To nie jest teoretyczne. Zmierzone na koncie CXLABS: w wersji `2024-10` pole
`Board.created_at` **nie istnieje** — zapytanie kończy się `Cannot query field
"created_at" on type "Board"`. W wersji domyślnej konta istnieje i collector
z niego korzysta (`tablice.py`). Czyli ta sama komenda na tym samym koncie
przestanie działać, gdy monday przestawi domyślną wersję.

Dwa różne skutki i drugi jest gorszy:
- pole **usunięte** → twardy błąd, widoczny natychmiast
- semantyka pola **zmieniona** → run przechodzi, snapshot jest inny, a różnica
  wygląda jak zmiana na koncie klienta

`05-deploy.md` wymaga zapinania czterech rzeczy (model, rubryka, prompt,
collector), żeby audyt sprzed trzech miesięcy był odtwarzalny. Wersja API jest
piątą i jej brak podkopuje pozostałe cztery: bez niej nie da się odpowiedzieć,
czy różnica między snapshotem #1 i #4 to zmiana u klienta, czy u monday.

**Rozstrzygnięcie (zgoda ustna 2026-08-01):** przypięte do `2026-07`, czyli do
wersji domyślnej w dniu przypięcia — jedynej, przeciwko której cokolwiek tu
zwalidowano. Wersja idzie do `meta.wersja_api` każdego snapshotu. Flaga
`--wersja-api` służy WYŁĄCZNIE do porównania snapshotów przed podniesieniem
stałej; podnoszenie przechodzi przez bramę promocji jak każda inna zmiana.

Wersje widziane 2026-08-01: `2025-04`…`2026-04` maintenance, **`2026-07` current**,
`2026-10` i `2027-01` release_candidate, `dev`. Monday trzyma wersję
w maintenance około roku, więc ta stała ma termin ważności.

**Przypięcie natychmiast się opłaciło — patrz O17.**

---

## O16. Głębokość warstwy aktywności — gdzie jest sufit API

**Status:** zmierzone 2026-07-31, po podniesieniu pokrętła próbki

Introspekcja `ActivityLogType` daje **dokładnie 7 pól**: `id`, `event`,
`entity`, `created_at`, `user_id`, `account_id`, `data`. Bierzemy 5. Pomijamy
`account_id` (stały, bezużyteczny) i `data`, opisane w schemacie jako
„the item's column values in string form" — czyli treść tablic klienta, wprost
pod zakaz PII i D5.

Po zmianie z 2026-07-31 (`--wszystkie-logi`, sufit stron 5 → 10) snapshot #2
na workspace 6576039: **105 tablic ze 105, 4431 wpisów, zero tablic z urwanym
logiem**. Czyli w oknie 90 dni mamy KAŻDY wpis KAŻDEJ tablicy w zakresie.
Koszt: 131 wywołań, 629k complexity z okna ~9,87M, 68 sekund. Same logi to
109 wywołań i tylko 45k complexity — najdroższe jest `boards` (580k).

**Sufit, którego nie ruszy żadne pokrętło:** `activity_logs` to log **zapisów**.
Rejestruje zmiany, nie użycie. W API nie ma nic, co mówi „kto tę tablicę
otwierał" — pole `Board.views` to konfiguracja widoków (Tabela, Kanban),
nie licznik wyświetleń (sprawdzone: na tablicy 5097387646 puste).

**Konsekwencja dla rubryki, nie dla collectora:** tablica czytana codziennie
przez dwadzieścia osób, której nikt nie edytuje, jest nieodróżnialna od
martwej. `BOARD_GHOST` musi więc mówić „nic się nie zmieniło w 90 dni",
a nie „nikt tego nie używa" — drugie zdanie jest twierdzeniem bez dowodu,
a rubryka wymaga `dowod`.

**Czego NIE udało się zmierzyć:** retencji logów. Cały workspace 6576039
powstał ~1 czerwca 2026, więc najstarsza tablica ma dwa miesiące i jej pełna
historia siedzi w oknie 90 dni. Dla CXLABS okno pokrywa 100% istnienia konta.
Dla klienta z tablicami trzyletnimi retencja jest **nieznana** — sondowanie
`from` sprzed 1, 3 i 6 lat musi wejść do discovery przed pierwszym audytem.

**Klasyfikacja zdarzeń do dokończenia:** przy pełnej próbce lista
`nieznane_zdarzenia` wzrosła z 3 do 11 pozycji (m.in. `archive_pulse`,
`board_view_added`, `move_pulse_from_group`, `change_column_settings`).
Wszystkie są policzone w `po_event`, ale żadne nie trafia do klasy, więc
`po_klasie` zaniża sygnał. Do rozstrzygnięcia w 3.9.

---

## O17. Flagi użytkownika — PRZEPISANE na `kind` + `status` 2026-08-01

**Status: ROZSTRZYGNIĘTE — zgoda ustna, model przepisany, collector działa na `2026-07`, `2026-10` i `2027-01`**
**Blokuje:** 3.3 (rozpoznanie zakresu), 3.4 (użytkownicy), `ZOMBIE_ACCOUNT`
**Pilność:** `2026-10` jest już `release_candidate`

Pierwszy run po przypięciu wersji, odpalony dla porównania na `2026-10`,
przerwał się na `Cannot query field "is_admin" on type "User"`. Sprawdzone
pole po polu:

| Pole | 2026-07 | 2026-10 |
|---|---|---|
| `enabled` | OK | **BRAK** |
| `is_admin` | OK | **BRAK** |
| `is_guest` | OK | **BRAK** |
| `is_pending` | OK | **BRAK** |
| `is_verified` | OK | **BRAK** |
| `created_at`, `last_activity`, `title` | OK | OK |
| `kind`, `status`, `is_deleted`, `is_email_confirmed`, `became_active_at` | OK | OK |

Czyli **wszystkie pięć flag, na których stoi 3.4**, znika w następnej wersji —
a zamienniki działają już dziś, w wersji przypiętej. Migracja nie wymaga
czekania na cokolwiek.

**Pułapka, o której trzeba wiedzieć:** introspekcja `__type(name: "User")` NIE
pokazuje `is_admin` ani żadnej z tych flag — w **żadnej** z obu wersji. Pola są
w 2026-07 nieudokumentowane, ale działają. Wniosek na przyszłość: introspekcja
nie jest tu wiarygodnym źródłem prawdy o dostępności pola, a brak pola
w introspekcji nie znaczy, że go nie ma. Sprawdzaj zapytaniem.

**Zmierzone rozkłady zamienników (95 użytkowników CXLABS, 2026-10):**

- `kind`: `admin` 10, `member` 9, `guest` 12, `view_only` 28, `personal_agent_member` 36
- `status`: `ACTIVE` 94, `PENDING` 1
- `is_deleted`: `False` 95

**To rozwiązuje starą zagadkę `active_members_count = 19` przy 95
użytkownikach:** 19 = `admin` (10) + `member` (9). Pozostałe 76 to goście,
konta tylko do podglądu i **36 kont agentów AI**. Dla `ZOMBIE_ACCOUNT` to
zmienia wszystko — liczenie „nieaktywnych użytkowników" po 95 rekordach
zawyżałoby wynik czterokrotnie i wystawiłoby klientowi rachunek za konta,
które nie zajmują płatnych miejsc ani nie są ludźmi.

**Rozstrzygnięcie:** przepisane. `Osoba` stoi na `kind` + `status` +
`is_deleted` + `is_email_confirmed` + `became_active_at`, z właściwościami
`jest_adminem`, `jest_gosciem`, `jest_agentem` i `zajmuje_miejsce`.
`rozpoznaj_konto` pyta o `me { kind }`, nie o flagi.

**Weryfikacja:** ten sam run na `2026-07`, `2026-10` i `2027-01` daje identyczny
wynik — `{admin: 10, guest: 12, member: 9, personal_agent_member: 36,
view_only: 28}`, `zajmujacych_miejsce: 19 z 95`. Podniesienie wersji API jest
teraz zmianą jednej stałej, z dowodem, że przechodzi. Snapshot #3 na workspace
6576039 jest już w nowym modelu.

**`zajmujacych_miejsce: 19` zgadza się co do jednego z `active_members_count`
zwracanym przez API** — niezależne potwierdzenie, że mapowanie jest poprawne.

**Czego świadomie NIE ma: `is_verified`.** Pole istnieje w `2026-07` i ginie
w `2026-10`, a `is_email_confirmed` **nie jest** jego zamiennikiem (zmierzone:
58 z 95 osób ma `is_verified=True` przy `is_email_confirmed=False`). To
rezygnacja, nie przemianowanie, i była decyzją: sygnał był prawdziwy u 94 z 95
rekordów, czyli nie nosił informacji, a actionable przypadek („zaproszony, nie
wszedł") łapie `status == PENDING`. Utrzymanie go blokowałoby cały collector na
następnej wersji API za jedno pole bez wartości. Fakt utraty siedzi
w `discovery.is_verified_porzucone`, żeby raport nie udawał, że
„niezweryfikowanych" po prostu nie było.

**Konsekwencja dla D7:** sekcja `uzytkownicy` zmieniła kształt, więc snapshoty
#1–#2 nie są wprost porównywalne z #3 i późniejszymi. `meta.collector_ver`
i `meta.wersja_api` pozwalają to rozpoznać maszynowo.

---

## O18. `board.updated_at` systematycznie zaniża aktywność

**Status:** zmierzone 2026-08-01 na 105 tablicach
**Blokuje:** `BOARD_GHOST`, `ENGAGEMENT_DROP`

Porównanie `board.updated_at` z najnowszym wpisem w activity logu tej samej
tablicy, workspace 6576039:

| | tablic |
|---|---|
| log **nowszy** niż `updated_at` | **94** |
| zgodne do minuty | 11 |
| log starszy niż `updated_at` | **0** |

Największa rozbieżność: **40,6 dnia**. Kierunek jest jednostronny, więc to nie
szum — `updated_at` śledzi zmiany metadanych tablicy, a nie pracę na itemach.

**Konsekwencja dla 3.9:** detektor `BOARD_GHOST` oparty o `updated_at` uznałby
za martwe tablice, na których pracowano jeszcze wczoraj, myląc się o ponad
miesiąc. Sygnałem rozstrzygającym jest `najnowszy_at` z activity logu;
`updated_at` wolno użyć najwyżej jako sygnału pomocniczego dla tablic **poza**
próbką logów — a przy `--wszystkie-logi` poza próbką nie ma nikogo.

---

## O19. Flaga `--read-only` w MCP monday nie blokuje zapisu

**Status:** zmierzone 2026-08-03 na `@mondaydotcomorg/monday-api-mcp@3.3.0`
**Skutek:** D4 przepisane, MCP wypadło z architektury (etap 3.10)

Pierwotne D4 wybierało lokalny MCP nad hostowanym **wyłącznie** z powodu tej
flagi i opisywało ją jako „mechanizm, nie polityka — model nie ma go jak
obejść, nawet przy prompt injection". Sprawdzone protokołem MCP, z atrapą
tokena:

| Sprawdzenie | Wynik |
|---|---|
| `tools/list` z `--read-only` | **92 narzędzia, identycznie jak bez flagi** |
| wśród nich | `create_item`, `create_board`, `delete_item`, `change_item_column_values`, `all_api_write`, `execute_code` |
| `tools/call create_board` | **serwer zbudował `mutation createBoard` i wysłał do api.monday.com** |
| `tools/call all_api_write` z mutacją | **wysłane do api.monday.com** |

Oba wywołania zakończyły się `401 Not authenticated` **tylko dlatego, że token
był atrapą**. Z prawdziwym tokenem powstałaby tablica.

Trzech innych narzędzi zapisujących nie rozstrzygnięto — odrzuciła je walidacja
argumentów (`-32602`), zanim doszło do warstwy read-only. Nie zmienia to
wniosku: jedno z dwóch potwierdzonych to `all_api_write`, czyli przepustka
na dowolną mutację.

**Czego to NIE znaczy:** że MCP monday jest bezużyteczny. Znaczy, że nie jest
mechanizmem bezpieczeństwa i nie wolno na nim oprzeć zakazu twardego.

**Ścieżka odtworzenia** (gdyby trzeba było zgłosić monday albo sprawdzić po
aktualizacji): uruchom serwer z `--read-only`, w `initialize` → `tools/list`
policz narzędzia, potem `tools/call` na `create_board` z atrapą tokena
i sprawdź, czy w odpowiedzi jest `mutation createBoard` oraz status 401.
Obecność mutacji w odpowiedzi jest dowodem, że zapytanie wyszło.

**Uwaga wdrożeniowa:** `isolated-vm`, zależność natywna MCP, nie kompiluje się
na Node 25 (`node-gyp` przerywa). Zbudowało się na Node 22. Gdyby MCP kiedyś
wracał, Mikrus potrzebuje Node 20–22, nie najnowszego.

---

## O20. Powierzchnia agentowa jest w wersjach przedprodukcyjnych

**Status:** zmierzone 2026-08-04
**Blokuje:** śledzenie kredytów agentów, `AI_UNUSED`
**Sonda w kodzie:** `monday_audit.agenci.sonduj_agentow`

Sprawdzone **zapytaniem**, nie introspekcją — bo introspekcja monday nie jest
wiarygodnym źródłem prawdy o dostępności pola (O17):

| Wersja | `agents` | `agent_runs` |
|---|---|---|
| `2026-07` (przypięta) | ❌ brak pola | ❌ brak pola |
| `2026-10` | ❌ brak pola | ❌ brak pola |
| `2027-01` (release candidate) | ✅ **działa**, 50 agentów na CXLABS | ❌ brak pola |
| `dev` | ✅ działa | ⚠️ **`Internal server error`** |

W `dev` istnieje pełna powierzchnia: `agent_runs`, `agent_run_event`,
`agent_skills_catalog`, `agent_knowledge`, `agent_artifacts`, `custom_agents`,
`external_provider_agents`, `agent_triggers_catalog`, `agent_active_triggers`.

**Typ `AgentRun` w `dev` to komplet pod storytelling kosztowy:** `total_cost`,
`total_tokens`, `duration_ms`, `steps_count`, `tool_calls_count`, `models_used`,
`capabilities_used`, `mcp_servers_used`, `title`/`summary`/`short_summary`,
`outcome_status`/`outcome_reason`, `feedback_rating`/`feedback_count` oraz
`triggered_by_user_id`, `owner_user_id`, `agent_user_id`, `executed_as_user_id`.

**Ale dziś nie da się z tego skorzystać.** `agent_runs` zwraca ISE przy każdym
zestawie pól, jaki sprawdzono (minimalny `run_id status total_cost` też).

**Dlaczego NIE budujemy na `dev`:** wersja zmienia się bez ostrzeżenia,
a 05-deploy wymaga przypiętej wersji, żeby audyt sprzed trzech miesięcy dał
się odtworzyć. Audyt na `dev` byłby nieodtwarzalny z definicji. Sonda ma prawo
tam zaglądać jako rozpoznanie, a jej wynik jest oznaczony
`zrodlo_nieprzypiete: true` i `NIE_DO_FINDINGOW` — pilnują tego testy.

**Ścieżka odtworzenia:** `query { agents (limit: 5) { id } }` z nagłówkiem
`API-Version: 2027-01`, potem `query { agent_runs (agent_id: "<id>", limit: 5)
{ run_id status total_cost } }` z `API-Version: dev`. Sonda robi to sama przy
każdym runie i zapisuje wynik do `agenci.dostepnosc_api` w snapshocie, więc
pierwszy run po wypuszczeniu tego przez monday powie nam o tym bez pytania.

**Uwaga na PII przy wdrożeniu:** `AgentRun` niesie CZTERY surowe identyfikatory
osób. Wszystkie muszą przejść przez `policz_hash` z 3.4. Pola `title`,
`summary`, `trigger_context` i `outputs` to treść pisana przez klienta, czyli
i PII, i wektor prompt injection — sonda ich nie pobiera i nie wolno ich
dodać bez `waliduj_brak_pii` oraz sufitu długości.

---

## O21. Enterprise nie płaci za agentów — pomiar kredytów na CXLABS da zero

**Status:** ustalone 2026-08-04 ze źródeł zewnętrznych
**Sprostowanie 2026-08-04:** wcześniejsza wersja tej pozycji mówiła, że support
monday zwraca 403 i jest nieosiągalny. **Nieprawda** — zwraca 200 przy zwykłym
nagłówku `User-Agent`. Część liczb o kredytach jest więc dostępna u ŹRÓDŁA
i pobiera je `cli_cennik` (potwierdzone: `AI blocks 8 credits per action`,
`AI Notetaker 120 credits per meeting hour`, widełki złożoności agentów
10–50 / 50–150 / 150–250 / 250+). Zwolnienie Enterprise nadal opiera się na
źródłach zewnętrznych.
**Waga:** wysoka — przesądza, na jakim koncie da się cokolwiek zmierzyć

Rozliczanie agentów kredytami wystartowało **8–9 czerwca 2026** dla planów Pro,
Standard i Basic. **Enterprise jest zwolniony** i przejdzie na model kredytowy
później; termin nie został ogłoszony.

**Konsekwencja praktyczna:** konto CXLABS jest na Enterprise (`tier:
enterprise`, zmierzone), więc kredyty za agentów będą tam **zerowe niezależnie
od tego, co API kiedyś odsłoni**. Zero w tym miejscu NIE znaczy „agenci nie
działają" ani „API nie działa" — i raport nie może tego pomylić. Sonda
rozdziela te przypadki: `runy_dostepne` mówi o osiągalności, a
`kredyty_dostepne` o obecności kwoty; test pilnuje, żeby jedno nie było
wnioskiem z drugiego.

**Do walidacji tej ścieżki potrzebne jest konto klienta na Pro albo niżej.**
Dopóki go nie ma, kod jest napisany na podstawie schematu, nie pomiaru.

**Skąd te liczby:** część u źródła (strona support, pobierana przez
`cli_cennik`), część z analiz zewnętrznych — stawka `0,01 USD` za kredyt na
stronach monday **nie występuje** i jest oznaczona jako `zewnetrzne`.
Obowiązujące wartości są w tabeli `cennik`, nie w markdownie; metodologia
i lista źródeł: `docs/CENNIK_AI.md`.


---

## O22. Scrapowanie stron dostawcy przez aplikację z Marketplace

**Status:** otwarte, do rozstrzygnięcia PRZED wystawieniem na Marketplace
**Waga:** średnia teraz, wysoka przy publikacji
**Dotyczy:** `monday_audit.cli_cennik`

Stawki publiczne pobiera scraper ze stron monday, bo cennika **nie ma
w API**: `Plan` odsłania `max_users`, `period`, `tier` i `version`, a jedyne
pola cenowe (`AppSubscriptionDetails.monthly_price`) dotyczą ceny aplikacji
NA Marketplace, czyli tego, co klient płaciłby nam.

**Czego nie wiemy:** czy regulamin monday dopuszcza, żeby aplikacja
z ich Marketplace regularnie pobierała ich własne strony pomocy. Wewnętrzny
skrypt odpalany ręcznie raz na miesiąc to inny profil ryzyka niż komponent
opublikowanego produktu.

**Co to łagodzi już teraz:** komenda jest osobna i nigdy nie chodzi w trakcie
audytu, więc jedno żądanie na stronę na odświeżenie, nie na klienta.

**Rozstrzygnięcie:** zapytać monday o źródło maszynowe (endpoint albo plik
cennika) i dopiero na tej podstawie zdecydować. Do wersji wewnętrznej scraper
zostaje; przed publikacją albo jest zgoda, albo wchodzi ręczne wprowadzanie
stawek przez front (`sposob = 'reczna'` jest już w schemacie i obsłużone).


---

## O23. Panel z danymi osobowymi klienta pod hasłem na publicznym hostingu

**Status:** otwarte, do rozstrzygnięcia PRZED wystawieniem panelu
**Waga:** wysoka — dotyczy danych osobowych ludzi klienta
**Dotyczy:** `monday_audit.pulpit`, decyzja D15

Raport z 3.12 był **plikiem na dysku**: leżał u nas, wysyłaliśmy go świadomie,
a odbiorca dostawał kopię. Panel to inny profil ryzyka: **dane osobowe klienta
leżą pod adresem URL**, dostępne dla każdego, kto ma link i hasło.

Kuba wybrał wzorzec z Docs Publishera — jedno hasło na folder klienta,
kopiowane przyciskiem. Zgodne z D11 (bez OAuth w v1) i klienci go już znają.
**Czego to nie rozstrzyga:**

1. **Hasło krąży mailem.** Link żyje dłużej niż powinien, a hasło raz wysłane
   zostaje w skrzynkach. Do rozważenia: data ważności dostępu (np. 90 dni od
   audytu), po której panel przestaje odpowiadać.
2. **Co się dzieje po zakończeniu relacji z klientem.** D11 mówi wprost:
   „token wygasa albo jest usuwany po audycie". Panel ma żyć dalej, więc
   trzeba powiedzieć, jak długo i kto go kasuje.
3. **Logi wejść.** Docs Publisher liczy wizyty; panel audytu z danymi osobowymi
   tym bardziej powinien wiedzieć, kto i kiedy wchodził — i to samo trzeba
   powiedzieć klientowi.
4. **Nazwiska w panelu.** Decyzja z 3.12 (nazwiska w obu wersjach, bo raport
   z hashami jest niewykonalny) przenosi się na panel. Słuszna, ale w pliku
   znaczyła co innego niż pod URL-em.

**Czego to NIE blokuje:** makiety. Dziś panel to statyczne pliki na dysku,
bez serwera i bez hostingu, więc ryzyko jest zerowe. Pozycja istnieje, żeby
nie wystawić go bez odpowiedzi na te cztery pytania.

**Powiązane:** O22 (scrapowanie stron dostawcy przez aplikację z Marketplace) —
oba dotyczą tego samego przejścia z narzędzia wewnętrznego na produkt.

---

## O24. SSO na domenę zamiast haseł per osoba

**Status:** świadomy skrót, nie przeoczenie
**Blokuje:** nic dzisiaj; wraca przy trzeciej osobie w zespole

Logowanie CXLABS to dziś **hasło per osoba**, hashowane `scrypt`, konto zakładane
komendą `--dodaj-osobe` z wymogiem adresu `@cxlabs.digital`. Wybrane, bo działa
od razu i nie wymaga zależności.

Czego ten skrót nie robi:

- **odejście z firmy nie odbiera dostępu** — trzeba pamiętać o skasowaniu konta
- hasła krążą kanałem, którym je przekazujemy (dziś: ustnie/komunikator)
- nie ma drugiego czynnika

**Kiedy to przestanie wystarczać:** przy trzeciej osobie albo pierwszym odejściu.
Wtedy SSO Google na domenę: wygaśnięcie konta w Google Workspace odbiera dostęp
do panelu bez naszego udziału, co jest całą wartością tej zmiany.

---

## O25. Klucz admina klienta w pamięci procesu

**Status:** granica przyjęta świadomie, dwa pytania niesprawdzone
**Blokuje:** wystawienie panelu poza relacją doradczą

Klucz API klienta nie trafia na dysk — sprawdzone 2026-08-06 znacznikiem
w kształcie JWT, który przeszedł POST → collector → 401 z monday i nie pojawił
się ani w zrzucie bazy, ani w logu serwera, ani w argv procesów. Jest na to
test regresyjny na najgorszej ścieżce, czyli na **błędzie** runu, bo to wyjątki
cytują nagłówki żądania.

Czego pomiar NIE obejmuje:

1. **Zrzut pamięci przy awarii.** Jeśli proces dostanie SIGSEGV albo system
   zapisze core dump, klucz jest w tym zrzucie. Na Mikrusie trzeba sprawdzić
   `ulimit -c` i czy systemd-coredump nie zbiera zrzutów do `/var/lib`.
2. **Swap.** Strona pamięci z kluczem może wylądować na dysku, jeśli maszyna
   zacznie swapować w trakcie runu — a run to najcięższy moment.

Oba są poza tym, co da się załatwić kodem aplikacji. Właściwa odpowiedź to
OAuth z ograniczonym zakresem (aneks do D11), nie kolejna warstwa ostrożności
wokół klucza o pełnych uprawnieniach.

## O26 — „odetnij dostęp teraz" nie istnieje (2026-08-10)

Reset hasła **nie wylogowuje**: otwarta sesja klienta żyje do `GODZIN_SESJI = 12`
od zalogowania, bo `sesje.konto_id` jest niezależne od hasła. To świadoma decyzja
Kuby — reset ma wydać nowe hasło, nie przerywać komuś pracy w połowie audytu.

Konsekwencja: **nie mamy narzędzia do natychmiastowego odcięcia dostępu.** Gdy
zajdzie taka potrzeba (rozstanie z klientem, podejrzenie wycieku), właściwą
odpowiedzią jest osobna akcja: `aktywne = 0` na koncie plus `DELETE` z `sesje`.
Nie dorabianie tego do resetu — bo wtedy każdy reset pomocniczy przerywałby pracę.

Do rozstrzygnięcia, gdy się pojawi: czy w panelu, czy tylko z CLI, i czy odcięcie
ma zostawiać ślad w `proby_logowania`, żeby było widać, kiedy nastąpiło.

Dziś interfejs mówi wprost, ile sesji zostaje ważnych — żeby nikt nie uznał, że
reset odciął dostęp. To jedyny sposób, w jaki ta luka jest dziś „obsłużona".

## O27 — czas audytu rośnie liniowo z liczbą hipotez (2026-08-11)

**Zmierzone, nie oszacowane.** Dwa pełne przebiegi agenta:

| workspace | hipotez | znalezisk | koszt | czas | na hipotezę |
|---|---|---|---|---|---|
| 6576039 | 19 | 11 | 1,71 USD | ~17 min | ~54 s |
| 5610281 | 86 | 27 | 7,09 USD | 62 min | ~43 s |

Koszt i czas skalują się z liczbą anomalii, bo **każda hipoteza to osobna sesja
modelu**, badana po kolei. Przy koncie z 300 anomaliami to ~3,5 godziny i ~25 USD.

Czego nie wiemy:

- **czy równoległość jest bezpieczna** — hipotezy są niezależne, ale narzędzia
  agenta dzielą jedno połączenie z monday i jeden rejestr wywołań; limit API
  klienta jest wspólny;
- **ile da zwężenie kontekstu** — dziś każda sesja dostaje pełny inwentarz
  w prompcie systemowym (D2, prompt caching), więc koszt wejścia jest stały
  niezależnie od tego, jak wąska jest hipoteza;
- **czy tańszy model wystarczy dla prostszych klas** — rubryka ma klasy oczywiste
  (martwe konto) i wymagające rozumowania (obejście procesu); dziś wszystkie idą
  tym samym modelem.

Do rozstrzygnięcia w **etapie 4 (ewaluacja)**, na korpusie 6 snapshotów. Nie
zgadujemy teraz, bo optymalizacja bez pomiaru jakości może po cichu obniżyć
trafność znalezisk — a to jedyna rzecz, której klient nie sprawdzi.

### Wynik pomiaru (2026-08-11)

**Z 62 minut tylko 40 sekund (1,1%) to wywołania do monday** — 45 wywołań, średnio
894 ms, z `wywolania.latency_ms`. Reszta to czas modelu, więc czas i koszt siedzą
w tym samym miejscu i optymalizacja collectora nie da nic.

**Architektura jest już rozbita na sesje**: `zbadaj_hipoteze` to jedna hipoteza,
jedna sesja, własny budżet — 86 hipotez to 86 sesji, nie jeden agent. Rozbijanie na
„paru agentów" nie ma więc czego rozbić.

**Router modelu po `rola_agenta` da ≤8%**: tylko 7 z 86 hipotez to klasy, w których
agent nic nie ustala (`ZOMBIE_ACCOUNT`). Prawdziwe pieniądze są w `BOARD_GHOST` (32)
i `DUPLICATE_STRUCTURE` (21), a te wymagają rozstrzygania.

**Rozbicie per klasa zmierzone 2026-08-12** (run `ewal-4klasy`, 8 hipotez, 0,82 USD)
i wynik jest odwrotny do oczekiwanego:

- **caching działa**: 79,2% wejścia z cache (wyliczenie z rachunku dawało ~90% —
  kierunek dobry, precyzja nie);
- **budżet wywołań NIE przewiduje kosztu**: najdroższa klasa ma budżet 4, najtańsza 0;
- **koszt idzie za długością wyjścia**: `BOARD_GHOST` produkuje 2,7× więcej tokenów
  wyjścia niż `ZOMBIE_ACCOUNT` i kosztuje 4,6× więcej. Uszeregowanie klas po koszcie,
  wyjściu i czasie jest identyczne.

Wniosek dla optymalizacji: **skrócenie wyjścia obniża koszt I czas jednocześnie**, bo
oba wynikają z tej samej przyczyny. Router modelu po `rola_agenta` zwróci ≤10%.
Szczegóły: [`BASELINE_ETAP4.md`](BASELINE_ETAP4.md).

### Nowa obserwacja: odrzucenia na walidacji przekraczają próg

**0,25 wobec progu ≤0,15** (9 z 36 zgłoszonych findingów). To osobna sprawa od
kosztu, ale **tańsza do naprawienia niż optymalizacja**: każdy odrzucony finding to
praca modelu, za którą zapłaciliśmy i której nie ma w produkcie. Prompt nie trzyma
kontraktu D8 w co czwartym przypadku.

## O28 — żaden audyt nie ma stawki licencji (2026-08-11)

Drugi workspace dał **27 znalezisk i zero kwot**. To poprawne zachowanie —
walidacja odrzuca kwotę bez stawki, zamiast wymyślać liczbę — ale znaczy, że
**raport nie pokazuje oszczędności**, czyli tego, co sprzedaje audyt.

W bazie jest **jeden** wiersz w `stawki_klienta`: `cxlabs / koszt_licencji_mies =
100.0`, z adnotacją w polu źródła „liczba wymyślona na potrzeby testu". Znaczy to,
że **1200 PLN oszczędności widoczne dziś w panelu przy `cxlabs` też jest wartością
testową** — poprawnie policzoną z nieprawdziwej stawki. Klient `acme` stawki nie ma
wcale, więc jego 27 znalezisk jest bez kwot.

Naprawa to wpisanie wartości, nie zmiana kodu; pytanie otwarte jest inne: **skąd
bierzemy stawkę dla konkretnego klienta.** Cennik publiczny monday
pobiera scraper, ale klient na Enterprise ma cenę wynegocjowaną, której nie znamy
i której nie wolno zgadywać. Trzy drogi, nierozstrzygnięte:

1. pytamy klienta o kwotę z faktury (najdokładniejsze, wymaga rozmowy);
2. używamy ceny publicznej z zastrzeżeniem w raporcie („liczone po cenniku
   publicznym, twoja cena może być niższa");
3. pokazujemy oszczędność w licencjach, nie w złotówkach („zwolnisz 12 płatnych
   miejsc"), i kwotę zostawiamy klientowi.

Trzecia droga jest odporna na brak danych i nie da się jej podważyć — ale słabiej
sprzedaje. Decyzja handlowa, nie techniczna.

## O29 — poczta „nie pamiętam hasła" nieskonfigurowana (2026-08-11)

Mechanizm działa i jest przetestowany, ale bez `SMTP_HOST` link ląduje **w logu
serwera**, nie na skrzynce. Tryb awaryjny jest świadomy i głośny (log mówi wprost,
że to nie stan docelowy), bo brak konfiguracji nie może znaczyć „nikt nigdy nie
odzyska hasła".

Do zamknięcia przed wystawieniem panelu: `SMTP_HOST`, `SMTP_USER`, `SMTP_HASLO`
(przy Google Workspace **hasło aplikacji**, nie hasło do konta) oraz
`ADRES_PUBLICZNY`, gdy aplikacja stanie za odwrotnym proxy.

Ryzyko, dopóki to nie jest zrobione: **link resetu widzi każdy, kto ma dostęp do
logów serwera.** Lokalnie to nikt poza właścicielem maszyny; po wdrożeniu na
Mikrusa (etap 5) — każdy z dostępem do systemd journal.

## O30 — konto platformy Anthropic bez środków (2026-08-12)

Próbka ewaluacyjna (8 hipotez) padła na `Credit balance is too low`. **Etap 4 jest
zablokowany do doładowania konta** — bez wywołań modelu nie ma czego mierzyć.

Do rozstrzygnięcia niezależnie od doładowania: **czy `AGENT_ROZLICZENIE=subskrypcja`
jest właściwym trybem dla pomiarów ewaluacyjnych.** Runy kontrolne w etapie 4 nie są
pracą dla klienta, więc obciążanie karty za nie jest wątpliwe — ale wtedy `koszt_usd`
przestaje być fakturą i porównania kosztów tracą sens (D17). Napięcie realne, nie
rozstrzygnięte.

Wnioskiem praktycznym z tej próby jest coś innego: **brak środków objawiał się jako
run „zakończony" z zerem znalezisk.** Naprawione (status `przerwany`), ale warto
pamiętać, że każda awaria po stronie modelu wygląda w danych jak zdrowe konto,
dopóki ktoś tego nie odróżni.

## O31 — GUEST_SPRAWL nierozstrzygalny bez tokena admina (2026-08-14)

Klasa wymaga w dowodzie `tablice_dostepne[]` — do czego goście mają dostęp. **API
nie zwraca tego dla kont gościa przy tokenie bez uprawnień admina.** Zmierzone na
runie `ewal-uzytkownicy-s7`: zapytanie `tablice_osoby` zwróciło 0 tablic dla
sprawdzonych kont, przy 124 tablicach w workspace.

Agent zachował się poprawnie: zgłosił finding o 11 z 12 gości bez aktywności ponad
180 dni, rozpoznał model agencyjny i **nie** potraktował samej liczby gości jako
anomalii (warunek odrzucenia z rubryki), a puste pole opisał wprost — „dostęp poza
zakresem jest nieznany, nie potwierdzony jako zerowy".

Walidacja odrzuciła to za puste `tablice_dostepne[]`. **Decyzja (Kuba, 2026-08-14):
zostawiamy odrzucenie.** Bez wiedzy o dostępie finding nie mówi nic o skali ryzyka,
a klasa jest o ryzyku, nie o liczbie kont. Nie rozluźniamy walidacji i nie zmieniamy
rubryki, żeby dopasować ją do tego, co akurat widać.

Skutek: `GUEST_SPRAWL` **nie wchodzi do złotego zestawu** — pozycja usunięta, powód
zapisany w pliku zestawu. Klasa wróci, gdy audyt dostanie token z uprawnieniami
admina. Do tego czasu każdy run na koncie bez takiego tokena będzie tę klasę
odrzucał, i to jest zachowanie zamierzone, nie usterka.

Warto odnotować napięcie: to najlepszy finding tego runu pod względem rozumowania,
odrzucony na formalności. Ale formalność jest dobra — alternatywą jest raport, który
mówi „11 gości to ryzyko" bez umiejętności powiedzenia, czym to ryzyko jest.

## O32 — ENGAGEMENT_DROP domknięty danymi, nierozstrzygnięty jakością (2026-08-14)

Klasa była **nierozstrzygalna**: rubryka wymaga `data_zwrotu` i
`zdarzenie_towarzyszace`, a snapshot niósł kubełki czasowe tylko per tablica — nie
dało się powiedzieć, KTO przestał działać. Domknięte w kroku 1 etapu 4:
`aktywnosc.per_uzytkownik` daje rozkład akcji per osoba (`kubelki_dni`, `po_event`,
lista tablic), bez ani jednego nowego wywołania monday.

Widać w danych sygnaturę, o którą ta klasa pyta. Ze snapshotu #7:

    103df6a8b444ee7c   153 akcje, 4 tablice, WSZYSTKIE w kubełku 31-60 dni
    3f9dfd32fdf376b6    77 akcji,  2 tablice, 76 w kubełku 61-90, 1 w 8-30

Pierwszy przypadek to ktoś, kto pracował intensywnie i zniknął. Drugi — ktoś, kto
niemal zniknął. To jest `data_zwrotu` w rozdzielczości kubełka.

**Czego NADAL nie ma:** `zdarzenie_towarzyszace` — co działo się na tablicach tej
osoby, gdy przestała działać. Dane są (`aktywnosc_tablic` niesie `po_event` i daty),
ale zestawienia „spadek osoby X wobec zdarzeń na jej tablicach" nie robimy. Agent
musiałby to złożyć sam, czyli płacić rozumowaniem — a to jest robota collectora (D1).

**Klasa nie została jeszcze zmierzona.** Run `ewal-uzytkownicy-s7` badał
`ZOMBIE_ACCOUNT`, `GUEST_SPRAWL` i `PLAN_MISMATCH`; `ENGAGEMENT_DROP` nie miał
hipotezy, bo detektor go nie wzbudził na tym snapshocie. Do rozstrzygnięcia
przy następnym runie: czy detektor milczy słusznie (8 osób w logach to mało na
„grupę", o której mówi rubryka), czy próg detektora jest za wysoki.

Do tego czasu klasa **nie wchodzi do złotego zestawu** — nie mamy ani jednej
pozycji, którą dałoby się uzasadnić dwoma niezależnymi dowodami.

## O33 — cały workspace 5610281 to `CRM_PL_Demo` (2026-08-17)

**124 ze 124 tablic snapshotu #7 leży w workspace o nazwie `CRM_PL_Demo`.** To dane
demonstracyjne albo szablonowe, nie produkcyjne.

Znalezione nie przez przegląd danych, a przez **agenta przy `effort=high`**, który
odrzucił hipotezę `DUPLICATE_STRUCTURE` z uzasadnieniem: „Workspace nazywa się
`CRM_PL_Demo`, co wskazuje na materiał demo/szablonowy, nie produkcyjny rozjazd.
[…] brak charakterystycznego dla tej klasy wzorca »jedna aktywna, reszta cichnie«".
Dodał obserwację, której nie miałem w zestawie: obie tablice powstały **w tej samej
sekundzie** (2026-03-18T11:13:30Z), z tym samym właścicielem i subskrybentem —
sygnatura jednorazowego scaffoldingu z szablonu, nie niezależnego zakładania kopii.

**Skutek dla złotego zestawu tablic:** `acme_snapshot7_tablice.yaml` opisuje
32 duplikaty, które z dużym prawdopodobieństwem są szablonem CRM rozstawionym
dwujęzycznie, a nie rozjazdem procesu u klienta. Trafność 1,000 na tym zestawie
znaczy „agent zgadza się ze mną co do danych demo", nie „agent dobrze audytuje
konto produkcyjne".

**Skutek dla pomiarów kosztu:** liczby kosztu i czasu ZOSTAJĄ ważne — 0,1017 USD
na hipotezę to koszt pracy modelu niezależnie od tego, czy dane są produkcyjne.
Ważność tracą wyłącznie metryki JAKOŚCI dla klas o tablicach.

**Do rozstrzygnięcia przez człowieka:** czy `DUPLICATE_STRUCTURE` powinno mieć
warunek odrzucenia „workspace nazwany jako demo/sandbox/test". Argument za: agent
i tak to rozpoznaje przy wysokim wysiłku, więc warunek jawny byłby tańszy.
Argument przeciw: nazwa workspace to heurystyka, a klient może trzymać produkcję
w workspace nazwanym „Demo" po nieudanym pilocie. **Nie zmieniam rubryki bez
decyzji** — to zmiana definicji klasy, a te należą do człowieka.

Klasy o użytkownikach są tym NIEDOTKNIĘTE: `ZOMBIE_ACCOUNT` mówi o kontach na
poziomie konta monday (94 osoby, 19 płatnych miejsc), nie o tablicach w workspace.
