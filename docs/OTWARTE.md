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
| `is_verified` w rekordzie użytkownika | 3.4 mówi „WYŁĄCZNIE" i nie wymienia tego pola | pole JEST w snapshocie — sygnał dla `ZOMBIE_ACCOUNT` |
| Tablice `Subitems of ...` | **O14** | nieodfiltrowane; zafałszują `BOARD_GHOST` w 3.9 |
| Sandbox jako blokada `.env` | rozmowa 2026-07-31 | `permissions.deny` na `.env` i `.env.local`; polecenia Basha nadal mogą czytać plik |
| Sól pseudonimizacji | wygenerowana przeze mnie 2026-07-30 | w `.env`; jej zmiana unieważnia porównywalność snapshotów |
| **Prawa do pliku `.env`** | zmierzone 2026-07-31 | plik ma `-rw-r--r--`, czyli sól czyta każdy proces na maszynie; kod ostrzega, nie przerywa |

Zamknięte: `pydantic-settings` jako źródło konfiguracji — zgoda ustna
2026-07-31, szczegóły w **D12**.

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

## O2. Czy API zwraca zużycie kredytów AI

**Status:** niepotwierdzone, z nowym podejrzeniem co do przyczyny
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

---

## O3. Sprawa kredytów AI przy zewnętrznych agentach — SPRZECZNE ŹRÓDŁA

**Status:** sprzeczność w dokumentacji monday
**Waga:** wysoka — to argument cenowy, nie tylko techniczny

Support monday twierdzi, że rozumowanie zewnętrznego agenta zużywa
kredyty dostawcy, nie monday AI. Dokumentacja deweloperska mówi
o podłączonych agentach, że konsumują kredyty AI rozliczane w dashboardzie
użycia konta.

Da się to pogodzić (rozumowanie u dostawcy, akcje AI po stronie monday
z kredytów monday), ale **nie prezentować jako argumentu cenowego
przed potwierdzeniem.**

**Jak sprawdzić:** zmierzyć kredyty na koncie CXLABS przed i po jednym
runie agenta.

**Uwaga:** to nie dotyczy v1 audytu (nie jest agentem BYO), ale dotyczy
produktów, które pójdą tą ścieżką.

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

## O14. Tablice `Subitems of ...` zafałszują BOARD_GHOST

**Status:** zauważone w snapshocie #1, 2026-07-31
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
przepuściłby ją bez śladu. To przeważa sprawę na rzecz `Board.type` /
`hierarchy_type`: filtr po nazwie wymagałby listy tłumaczeń dla każdego języka
interfejsu klienta, a błąd byłby cichy — tablica techniczna po prostu
wylądowałaby w raporcie jako martwa.

**Dlaczego to ważne:** to dokładnie ten rodzaj fałszywki, którą rubryka
nazywa najgroźniejszą — klient sprawdzi takie znalezisko pierwsze
i zobaczy, że narzędzie nie rozumie jego konta.

---

## O15. Wersja API monday nie jest zapinana

**Status:** zmierzone 2026-07-31
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

**Do decyzji człowieka:** czy przypiąć wersję na sztywno (`MondayClient` ma już
parametr `wersja_api`, dziś nieużywany) i zapisywać ją w `meta` snapshotu.
Koszt to jedna stała i jedno pole. Cena zwłoki to niepowtarzalny audyt.

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
