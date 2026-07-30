---
name: audit-rubric
description: Rubryka znalezisk audytu monday.com — czym jest znalezisko, jaka waga, jak wyceniać, warunki odrzucenia, budżety wywołań. Używaj przy implementacji detektorów, walidacji kontraktu wyjściowego, pisaniu promptu agenta, testach i evalach oraz przy każdej zmianie definicji znaleziska.
---

# Rubryka znalezisk

Źródło prawdy: **`rubryka_znalezisk.yaml`** w katalogu głównym.
Ten skill zawiera zasady stosowania rubryki, nie same definicje klas.
Zawsze czytaj YAML — nie polegaj na tym, co pamiętasz z tego pliku.

---

## Dwa konsumenty jednej rubryki

To jest jedyny artefakt w projekcie z dwoma odbiorcami:

| Konsument | Do czego jej używa |
|---|---|
| **Claude Code** (build) | implementacja detektorów, walidacja kontraktu, przypadki testowe |
| **Agent produkcyjny** (runtime) | instrukcja rozumowania — co badać, kiedy odrzucić |

Zmiana rubryki wpływa na oba. Podnoś wersję (patrz niżej).

---

## Sześć zasad, których nie wolno naruszyć

### 1. Dowód obowiązkowy

Każdy finding wskazuje na **fakt deterministyczny ze snapshotu**,
w polu `dowod`. Klucze `dowod` muszą pokrywać pola wymagane
w definicji klasy.

Finding bez dowodu **nie przechodzi walidacji.** Bez wyjątków.
To jednocześnie obrona przed halucynacją i przed skutkami
prompt injection.

### 2. Fakty nie wychodzą z modelu

Liczby liczy SQL. Model interpretuje.

`ZOMBIE_ACCOUNT` w ogóle nie dotyka agenta (`budzet_wywolan: 0`) —
to zapytanie. `PROCESS_BYPASS` jest w całości jego.

Jedna zmyślona liczba w raporcie zabija produkt u pierwszego klienta,
który ją sprawdzi.

### 3. Wycena tylko tam, gdzie to mnożenie

Dopuszczalne typy: `oszczednosc_bezposrednia` (kwota) albo `ryzyko`
(bez kwoty, świadomie).

**Nie ma typu `czas_odzyskany`** — został usunięty, bo wymagał danych
na poziomie itemów, których nie zbieramy (D5). Wszystkie wzory
oparte na `itemy_mies` były niewykonalne.

Kwotę mają tylko `ZOMBIE_ACCOUNT` i `PLAN_MISMATCH`, bo `koszt_licencji_mies`
pochodzi **od klienta** (O7). Nie podany → finding bez kwoty z adnotacją.
**Nigdy nie zgaduj tej liczby.**

Zamiast wymyślonych godzin: `wysilek_naprawy` (niski/średni/wysoki).
Krytyczne + niski wysiłek = quick win na pierwszy slajd.

### 4. Warunki odrzucenia są równie ważne jak sygnał

Agent ma skłonność do potwierdzania wszystkiego, co mu podsuniesz.
`warunki_odrzucenia` są mechanizmem przeciw temu.

**`hipotezy_odrzucone` w wyjściu jest obowiązkowe.** Agent, który nie
odrzucił niczego, jest zepsuty — to metryka jakości, nie pole opcjonalne.

### 5. Budżet per hipoteza, nie per run

Każda wzbudzona hipoteza dostaje `budzet_wywolan` z rubryki.
Konto z trzema problemami jest tanie, z czterdziestoma drogie —
proporcjonalnie do pracy.

Bezpiecznik globalny 600/run to wyłącznik awaryjny, nie polityka.
Przekroczenie = błąd w logice.

### 6. Widoczność

`tylko_wewnetrzne`: `PROCESS_BYPASS`, `ENGAGEMENT_DROP`.

Oba mówią klientowi, że jego ludzie omijają system albo przestali go
używać. Raport zwykle czyta osoba, która to konto zbudowała.
Do rozmowy, nie do PDF-a.

Pole `trop` (na jaką usługę CXLABS wskazuje finding) **nigdy** nie idzie
do wersji klientowej.

---

## Kolejność w raporcie

Zastępuje Health Score, który został odrzucony — wagi 30/20/15/15/10/10
były arbitralne i nieobronne, a jedna liczba ukrywa konkrety.

Sortowanie: `waga` malejąco, potem `wysilek_naprawy` rosnąco.
Bez średnich, bez wag.

---

## Klasa `status: do_weryfikacji`

`AI_UNUSED` opiera się na danych, o których nie wiemy, czy API je zwraca
(O2). Klasa istnieje, ale **nie jest raportowana** — walidacja odrzuca
findingi klas z tym statusem.

Nie usuwaj jej. To najlepszy trop sprzedażowy pod główną linię CXLABS,
tylko zablokowany do rozstrzygnięcia discovery.

---

## Wersjonowanie

- Rubryka w gicie, review jak kod
- `rubric_version` przy każdym findingu w bazie
- **Zmiana `warunku_odrzucenia` = podniesienie wersji**
- Nowa klasa = podniesienie wersji
- Nigdy nie edytuj bez podniesienia wersji — stare findingi przestałyby
  być wytłumaczalne

Przy nowej wersji: przepuść zamrożone snapshoty przez nią i porównaj
z poprzednim wynikiem. Zero zapytań do monday, pełna informacja o skutku
zmiany. To główna korzyść z niemutowalnych snapshotów (D7).
