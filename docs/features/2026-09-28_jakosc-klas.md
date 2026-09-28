# Jakość klas: mniej szumu, stabilniejsze rozstrzygnięcia

| | |
|---|---|
| **Data** | 2026-09-28 |
| **Projekt / klient** | monday.com Account Audit (CXLABS), odbiór na koncie CXLABS |
| **Faza planu** | 8. Jakość klas |
| **Zakres zmian** | 8 plików (`7cdd824..1bc4db6`); `detekcja/detektory.py`, `agent/sesja.py`, `stary_panel/wybor_zakresu.py`, `rubryka_znalezisk.yaml` 0.8→0.10 |

## Co się zmieniło

Na pełnym koncie detektory dawały 621 hipotez, z czego 398 to BOARD_OVERCOMPLEX
wzbudzony samą liczbą kolumn. Teraz ta klasa liczy tylko kolumny wypełniane
ręcznie, pomija tablice raportowe i prawie puste — zostaje 175, a wszystkich
hipotez 400. Grupa duplikatów ma krótki identyfikator zamiast złączonych ID
kilkudziesięciu tablic. Powody błędów automatyzacji, które znaczą „element nie
miał danych", są spisane decyzją Kuby, więc AUTOMATION_DEAD rozstrzyga je
jednakowo. Run odbiorczy: 76 uwag zamiast 59, zero ostrzeżeń o pokryciu.

## Dlaczego

Model widzi z każdej klasy 20 hipotez (sufit). Gdy detektor wzbudza szum,
sufit wybiera 20 złych: w BOARD_OVERCOMPLEX na górę szły tablice pełne formuł
i luster, których nikt nie wypełnia, więc przechodziły 4 uwagi na 20.
AUTOMATION_DEAD skakał między runami (11 / 1 / 7), bo „brak danych na elemencie"
model oceniał raz jako wadę, raz nie.

## Decyzje

- **BOARD_OVERCOMPLEX: więcej niż 15 kolumn ręcznych, bez tablic raportowych,
  co najmniej 5 elementów** (Kuba). Ręczne = bez `formula`, `mirror`, `lookup`,
  `dependency`, `progress`, `auto_number`, `creation_log`, `last_updated`,
  `item_id`; raportowa = co najmniej połowa kolumn automatycznych. Sufit
  rankingowany po kolumnach ręcznych. Wybrane po pomiarze wariantów na pełnym
  CXLABS (0 USD modelu). Odrzucone: próg 10 elementów (121 hipotez) — odciąłby
  największy kubełek tablic z 5–19 elementami; próg 20 lub 25 kolumn ręcznych
  (105 / 39) — za ostry, bez pomiaru, że gubi tylko szum.
- **Filtr raportowy liczony w detektorze, nie przez model** — to warunek
  odrzucenia z rubryki, a detektor ma typy kolumn. W praktyce zdejmuje mało
  (15 tablic), bo filtr kolumn ręcznych robi większość roboty.
- **`obiekt_id` grupy: `grupa-<najmniejsze ID>-<liczba tablic>`** — grupy są
  rozłączne, więc to wystarcza do unikalności. Najmniejsze liczbowo, nie
  tekstowo (ID monday mają różną długość). Odrzucone: hasz listy —
  nieczytelny w logu i w pokryciu.
- **Stary panel czyta tablice grupy z `board_ids`**, nie z rozcinania
  `obiekt_id` po `+`. Odrzucone: zostawienie formatu `a+b+…` dla zgodności —
  to on był problemem.
- **Złe dane wejściowe = brak pliku, za krótki tekst, pusta kolumna wejściowa**
  (Kuba, po spisie wszystkich powodów z konta). Wada procesu: błąd webhooka,
  „provide more informative instructions" (instrukcja kroku AI to
  konfiguracja), `invalid_person_assignment` (zdecydowane na zapas — nie było
  go w oknie statystyk). Detektor tylko ustawia fakt; odrzuca model,
  z uzasadnieniem, zgodnie z rubryką.

## Jak to działa

```
detekcja/detektory.py
  board_overcomplex   SQL: kolumn > 15 AND items_count >= 5
                      Python: kolumn_recznych > 15 AND udział automatycznych < 50%
  duplicate_structure obiekt_id = obiekt_grupy(board_ids)
  automation_dead     tylko_bledy_danych_wejsciowych = wszystkie powody
                      z POWODY_Z_DANYCH_WEJSCIOWYCH
agent/sesja.py        _SILA["BOARD_OVERCOMPLEX"] → kolumn_recznych
rubryka_znalezisk.yaml sygnał BOARD_OVERCOMPLEX i warunek odrzucenia AUTOMATION_DEAD
```

Model dostaje zmienione definicje klas w zadaniu, więc runy przed 0.9 i po
nie są wprost porównywalne dla tych dwóch klas.

## Konfiguracja ręczna

Brak — całość w kodzie i konfiguracji w repo.

## Jak zweryfikować

1. `uv run pytest -q` → 1144 testy zielone.
2. `uv run python -m monday_audit.cli.analiza --zakres cale_konto --tylko-szacunek`
   → `hipotez: 400`, `sufit BOARD_OVERCOMPLEX: 20 z 175 (najwięcej kolumn
   wypełnianych ręcznie)`, bez wołania modelu.
3. Run odbiorczy `analiza-20260928T091801Z` (2,87 USD, 17 min): 95 z 95
   hipotez rozstrzygniętych, bez ostrzeżenia o pokryciu; BOARD_OVERCOMPLEX
   15 uwag z 20 (było 4) z martwymi kolumnami ręcznymi; AUTOMATION_DEAD te same
   2 uwagi co w poprzednim runie.

## Znane ograniczenia

- **Miernik powtarzalności starej ścieżki** (`evals/mierz.py`) klucza uwagi
  o grupach po `a+b+…` z dowodu, a odrzucone po `obiekt_id` — dla grup się nie
  spotkają. Stara ścieżka nie robi nowych runów, więc zostawione.
- **Lista powodów z danych wejściowych jest zamknięta.** Nowy komunikat monday
  trafi do modelu jak dotąd; dopisanie wymaga decyzji.
- **Próg 15 kolumn ręcznych i 5 elementów** zmierzony na jednym koncie
  (CXLABS); na koncie klienta rozkład może być inny.
- **Wejście w kategorie raportu w panelu podglądu aplikacji Claude nie
  działa** — zgłoszone przez Kubę przy odbiorze. Panel przechwytuje przejście
  do kotwicy, na którym stoi przełączanie widoków (`:target`). W przeglądarce
  działa (sprawdzone). Raport otwierać w przeglądarce; przejście na `:checked`
  dopiero, gdyby to samo wyszło przy osadzeniu w portalu.

## Dla klienta

Audyt wskazuje teraz tablice, na których naprawdę są pola do uprzątnięcia —
wypełniane ręcznie i od dawna puste — zamiast tablic, które po prostu mają dużo
wyliczanych kolumn. Automatyzacje, które nie działają przez brak danych na
elemencie, nie są już zgłaszane jako zepsute, więc lista problemów z
automatyzacjami pokazuje to, co faktycznie wymaga naprawy konfiguracji.
