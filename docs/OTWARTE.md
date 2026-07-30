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

**Status:** niepotwierdzone
**Blokuje:** cała klasa `AI_UNUSED` (oznaczona `status: do_weryfikacji`)
**Jeśli nie:** klasa zostaje wyłączona, ewentualnie zastąpiona ręcznym
sprawdzeniem w Admin panel klienta.
**Jak sprawdzić:** `account { usage { ai } }` plus introspekcja typu
`Account`.

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
