# Wybór zakresu audytu — stan na 2026-08-25

Funkcja zbudowana w jednej sesji, poza formalnym etapem (decyzja Kuby:
„podepniemy to pod build, ale pracuj nad tym"). Ten dokument opisuje, **co
stoi**, **co zostało zmierzone** i **co jest niedokończone** — z myślą o kimś,
kto wróci do tego po tygodniu.

---

## Po co to jest

Klient płaci za analizę własnym kluczem Anthropic (O36). Skoro rachunek jest
jego, musi widzieć **za co** płaci i móc zawęzić zakres, zanim koszt powstanie.

ZMIERZONE na koncie 27690228: pełny audyt workspace'u to 24 sygnały i ~1,5 USD
w 11 minut. Wybór jednej tablicy schodzi do ~1,0 USD — bo 22 z 24 sygnałów
dotyczy konta, nie tablic (patrz „Podłoga" niżej).

---

## Przepływ, jaki widzi klient

```
[ + Nowy audyt ]  ← osobny widok w sidebarze, nie nad starym audytem
        │
   1. klucz monday                          → [Dalej]        0,5 s
        │
   2. workspace (lista z wyszukiwaniem)     → klik           4,5 s
        │
   3. tablice (grupy zwinięte, opcjonalnie) → [Zbierz dane]
        │
   4. ▓▓▓▓▓░░░░  zbieranie                                   ~1 min
        │        „czytam tablice i kolumny", kroki 1–4
   5. dokładne widełki + klucz Anthropic    → [Zatwierdź]
        │
   6. ▓▓▓▓▓▓▓▓░  analiza                                     ~11 min
        │        „zbadano 7 z 24 sygnałów · zostało około 8 minut"
   7. raport
```

Dwie bramki, obie świadome: **przed zbieraniem** (zgrubny szacunek z liczby
tablic) i **przed analizą** (dokładne widełki z liczby sygnałów). Klucz
Anthropic podaje się dopiero w drugiej — czyli wtedy, gdy kwota jest znana.

---

## Co zostało zmierzone

### Podgląd jest tani i szybki

| co | czas | wywołania monday | koszt modelu |
|---|---|---|---|
| lista workspace'ów (`workspaces`) | **0,5–0,9 s** | 1 | 0 USD |
| tablice jednego workspace'u + kolumny | **3,6–5,7 s** | 2 | 0 USD |
| **razem do decyzji** | **~5 s** | **3** | **0 USD** |

Dla porównania pełne zbieranie: 167 s i ~130 wywołań, z czego dziennik
aktywności 47 s i próbkowanie kolumn 64 s — do wyboru zakresu niepotrzebne.

**Wcześniej twierdziłem, że zapytania `workspaces` nie ma.** Szukałem go
w naszym kodzie, nie w API monday. Jest, kosztuje 0,5 s i to ono odwróciło cały
projekt tego ekranu: wybór zakresu przeszedł z „po trzech minutach zbierania"
na „natychmiast po podaniu klucza".

### Konto jest większe, niż zakładaliśmy

- **100+ workspace'ów** (nie kilka) → lista musi mieć wyszukiwanie
- **500+ tablic** na koncie; pobranie wszystkich to 17 s i 2,5 mln complexity
- ze 124 obiektów jednego workspace'u tylko **59 to prawdziwe tablice** —
  reszta to podelementy (41), obiekty własne (22) i dokumenty (2)

### Limit monday nie jest wąskim gardłem

Audyt zużywa **~132 wywołania**. Plan `pro` daje 10 000 dziennie, czyli
**75 audytów**; nawet `free` (1 000) mieści siedem. To był argument za hamulcem
częstotliwości — nie utrzymał się (patrz „Zdjęty hamulec").

---

## Architektura

### Dwie fazy, pauza w środku

`web/run.py` rozdzielony na `_zbierz` i `_analizuj`. Między nimi zadanie stoi
w stanie **`czeka_na_zgode`** (migracja 012, przebudowa tabeli `zadania` —
`CHECK (stan IN …)` nie da się zmienić przez `ALTER`).

Nowe kolumny: `snapshot_id` (który snapshot zatwierdzono), `wybor` (JSON
z zakresem), `zgoda_do` (termin ważności, **12 godzin**).

### Trzy moduły, trzy różne rzeczy

| moduł | linii | rola |
|---|---|---|
| `podglad_zakresu.py` | 398 | szybki podgląd PRZED zbieraniem, bez bazy |
| `wybor_zakresu.py` | 671 | flagi ze snapshotu, filtr hipotez, widełki |
| `migracje/012_*.sql` | 72 | stan `czeka_na_zgode` |

**Agenta i detektorów nie tknięto.** `uruchom_detektory` nie ma filtra,
`zbadaj_hipotezy` przyjmuje gotową listę — filtr stoi **między nimi**, jako
czysta funkcja `odsiej_hipotezy`.

### Klucze nie przechodzą przez bazę

Faza druga dostaje oba klucze **ponownie z przeglądarki**. Świadomy kompromis
zamiast trzymania ich w pamięci procesu serwera: tam stan byłby niewidoczny,
przeżywałby wielu klientów i ginął przy restarcie. Test przeszukuje **każdą
kolumnę każdej tabeli** pod kątem wycieku.

---

## Flagi tablic

Cztery, wszystkie wyliczane z danych, **etykiety a nie rekomendacje** (decyzja
Kuby: nie piszemy „proponujemy pominąć").

| flaga | warunek | dostępna przed zbieraniem? |
|---|---|---|
| `nieuzywana_od_startu` | `updated_at − created_at < 24 h` | tak (z dat) |
| `raportowa` | ≥50% kolumn automatycznych | tak (+0,46 s na kolumny) |
| `cisza_90_dni` | jest w dzienniku, zero wpisów | **nie** — wymaga logów (47 s) |
| `nieprobkowana` | brak w dzienniku | **nie** — wymaga logów |

Dwie flagi wyglądają podobnie i znaczą **przeciwne** rzeczy:
`nieuzywana_od_startu` to nietknięty szablon (hałas), `cisza_90_dni` to
porzucony proces (najciekawsze znalezisko). Wykluczają się wzajemnie — bez tego
cisza trafiała na 30 tablic zamiast 3, bo szablon też nie ma wpisów.

**Flagi „mnóstwo pustych kolumn" NIE MA i nie będzie przed runem.** Snapshot zna
kolumnę jako `{id, title, type}`, bez wypełnienia (D5 zabrania schodzenia do
itemów). Sprawdzone: wszystkie hipotezy `BOARD_OVERCOMPLEX` mają puste
`kolumny_martwe` — wypełnia je agent próbkowaniem. Zastępnik: `raportowa`.

---

## Podłoga kosztu — rzecz, która najbardziej myliła

**22 z 24 sygnałów w typowym runie dotyczy KONTA, nie tablic:** martwe konta (7),
martwe automatyzacje (7), wygaszeni użytkownicy (5), goście, plan.

Wybór tablic ich nie dotyczy i nie może — nie są związane z żadną tablicą.
Dlatego:

- odznaczanie tablic zmienia kwotę tylko o część zmienną
- **podłoga ≈ 0,87 USD** jest widoczna na ekranie na stałe
- ekran mówi wprost: „24 sygnały: 2 z wybranych tablic oraz 22 dotyczących
  całego konta"

Bez tego zdania klient odznaczał tablice, patrzył na nieruchomą kwotę i pytał
o limit, którego nie ma.

---

## Zdjęty hamulec częstotliwości (2026-08-25)

`ODSTEP_DNI = 7` i `SUFIT_AUDYTOW = 4` **usunięte**, razem ze stałymi.

Powstały, gdy analiza szła na naszym kluczu (~1,71 USD za run) — cztery audyty
to było ~7 USD naszych pieniędzy. Po przejściu na klucz klienta ten argument
zniknął, a drugi (dzienny limit monday) nie przeszedł pomiaru: 132 wywołania
wobec 10 000 dziennie.

**Zostało** sprawdzenie „audyt tego konta już trwa" — to nie hamulec kosztu,
a ochrona spójności: dwa równoległe runy piszą do jednej bazy SQLite, a faza
druga czyta `snapshot_id` z zadania.

Gdyby wracać do limitowania, właściwym kluczem jest **`account_id` konta
monday**, nie `client_id`: `cxlabs` i `acme` dzielą `account_id=27690228`, więc
liczenie per klient mierzyło coś innego, niż zakładało.

---

## Usterki znalezione i naprawione w tej sesji

Wszystkie wyszły **z użycia na żywo albo ze zrzutu ekranu**, nie z testów.

| usterka | przyczyna | jak pilnowane |
|---|---|---|
| ekran zamarzał po „Zatwierdź" | polling robił `clearInterval` i nie wracał — `useEffect` zależał tylko od `zadanieId` | zależność od `czyObserwowac` |
| wyścig przy pobieraniu wyboru | `ustawStan` rozmontowywał efekt przed `await` | pobranie przed zmianą stanu |
| **nazwy tablic niewidoczne** | globalne `input { width: 100% }` łapało `checkbox` i rozpychało go na całą szerokość | reguła zawężona do pól tekstowych |
| wersaliki i marginesy w wierszach | globalne `label { text-transform: uppercase }` — a wiersz jest `<label>` | reguła zawężona do `label[for]` |
| kwota nie reagowała na odznaczanie | udział liczony tylko przy włączonym przełączniku | zaznaczenie jako jedno źródło prawdy |
| podłoga wyższa niż dolna granica | podłoga z mediany, dolna z p25 | ten sam percentyl + test |
| `TypeError` w hamulcu kosztu | porównanie daty ze strefą z datą bez | `_znacznik` (usunięty razem z hamulcem) |
| **raport z 97 tablic** przy wyborze 2 | front przy pełnym zaznaczeniu wysyłał listę wszystkich zamiast pustej | pusta lista = cały workspace + test |
| `graphql:*` i complexity na ekranie | `postep.opis()` jest dla konsoli | słownik polskich etapów, 9/9 pokrycia |
| surowe nazwy zdarzeń u ludzi | `update_column_value` prosto z API | słownik, 31/31 pokrycia |
| „OTWARTE.md O12" w raporcie | odnośnik do naszego pliku w tekście dla klienta | przepisane + test na przecieki |

**Cztery poprawki sprawdzone mutacją** (wstrzyknięcie błędu → test pada):
reaper nie zabija zgody, pauza między fazami istnieje, porzucenie nie blokuje,
hamulec znosi datę bez strefy.

---

## Co jest NIEDOKOŃCZONE

### 1. Metryki i ludzie nie idą za wyborem tablic

**Najważniejsza rzecz na tej liście.** Zawężenie po zebraniu działa **tylko na
hipotezach**. Sekcje „Znaleziska", „Ludzie" i metryki liczą się z **całego
snapshotu**, więc pokażą wszystkie tablice i wszystkie osoby, niezależnie od
tego, co klient zaznaczył na drugim ekranie.

Zgłoszone przez Kubę: „raport zawiera więcej tablic niż tylko te, co chciałem,
nie ma to pokrycia w tym, co wybierałem". To **projekt, nie usterka** — ale
sprzeczny z oczekiwaniem. Dwie drogi:

- zawężać **przy zbieraniu** (pierwszy ekran) — wtedy snapshot jest węższy
  i wszystko się zgadza, ale `DUPLICATE_STRUCTURE` traci materiał do porównań
- filtrować **pulpit** po `wybor` z zadania — snapshot zostaje pełny, ale
  trzeba przepisać `zbuduj_pulpit` i `zbuduj_ludzi`

Decyzja należy do Kuby.

### 2. Wybór wielu workspace'ów nieprzetestowany

Interfejs pozwala wybrać jeden. Backend (`Zakres.workspace(*ids)`) przyjmuje
wiele, ale nie było konta, na którym dałoby się to sprawdzić — snapshoty
testowe mają po jednym workspace. Do `OTWARTE.md` jako **O37**.

### 3. Powtarzalność nadal 0,797 przy progu 0,8

Liczba **przedawniona** — zmierzona przed filtrem `BOARD_GHOST`, który zszedł
z 30 hipotez do 3. Nie wiadomo, czy próg jest spełniony. Dwa runy to ~3 USD.

### 4. Zbieranie ginie przy restarcie serwera

Audyt w tle żyje w wątku procesu serwera — nie ma kolejki. Restart w trakcie
analizy niszczy ją bez śladu w bazie (zadanie zostaje na `analizuje`, dopóki
reaper go nie zwolni po 40 minutach). Zdarzyło się raz w tej sesji.

Docelowo: kolejka (etap 5, deploy). Doraźnie: **sprawdzać `zadania`, zanim
zrestartuje się serwer**.

### 5. Ekran nie był oglądany przez autora zmian

Wszystkie usterki wizualne z tabeli wyżej znalazł Kuba, nie testy. `tsc`, ruff
i pytest nie mają pojęcia o pustej przestrzeni ani o zerowej szerokości
elementu. Bez Playwrighta (świadomie nieinstalowanego — nowa zależność wymaga
decyzji) weryfikacja wizualna zostaje po stronie człowieka.

---

## Stan techniczny

- **803 testy** przechodzą (`+47` w tej sesji: 28 + 19 nowych plików)
- `ruff`, `mypy`, `tsc` — czyste
- `front/src/api.ts` wygenerowany i aktualny (`--sprawdz`)
- migracje: **12** (`012_zgoda_na_zakres.sql`)
- `STATUS.md` **nietknięty** — nadal `etap_biezacy: 3`, choć tabela mówi, że
  etap 3 zatwierdzony i etap 4 w toku. Plik jest niespójny sam ze sobą i należy
  do Kuby.

## Pliki

| plik | rola |
|---|---|
| `src/monday_audit/podglad_zakresu.py` | podgląd przed zbieraniem |
| `src/monday_audit/wybor_zakresu.py` | flagi, filtr, widełki |
| `src/monday_audit/migracje/012_zgoda_na_zakres.sql` | stan `czeka_na_zgode` |
| `front/src/komponenty/PodgladZakresu.tsx` | kreator: workspace + tablice |
| `front/src/komponenty/WyborZakresu.tsx` | bramka po zebraniu |
| `front/src/komponenty/Kroki.tsx` | etapy i prognoza czasu |
| `tests/test_podglad_zakresu.py` | 19 testów |
| `tests/test_wybor_zakresu.py` | 28 testów |
| `tests/test_web_zgoda.py` | 33 testy — przepływ dwufazowy |
