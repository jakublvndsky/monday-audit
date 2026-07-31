# Założenia niepotwierdzone

**Claude Code: to nie są fakty.** Nie buduj na nich logiki bez oznaczenia
miejsca jako tymczasowego. Jeśli implementacja zależy od któregoś z tych
punktów, zaimplementuj wykrywanie (discovery) i ścieżkę awaryjną, a nie
założenie zapisane na sztywno.

Zasada discovery-first pochodzi z briefu Artura i jest właściwa:
**najpierw wyślij zapytanie i sprawdź, co wraca — dopiero potem
implementuj fallback.** Loguj wyniki: `[DISCOVERY] ✅ pole X dostępne`.

---

## O1. Czy API zwraca liczbę uruchomień automatyzacji

**Status:** niepotwierdzone
**Blokuje:** `AUTOMATION_DEAD` — sygnał "0 uruchomień w 90 dni"
**Jeśli nie:** sygnał zwęża się do `is_active = false` i klasa traci
około połowy wartości. Waga spada, bo nie odróżnisz śmiecia po testach
od procesu przeniesionego na ręce.
**Jak sprawdzić:** MCP udostępnia `get_automation_runs` i
`get_automation_statistics`. Przetestuj oba na koncie CXLABS.

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
