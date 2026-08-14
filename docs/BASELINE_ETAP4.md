# Baseline etapu 4 — z czego składa się koszt audytu

> **Stan na 2026-08-11.** Ten dokument jest punktem odniesienia dla **każdego**
> eksperymentu optymalizacyjnego. Do niego wracamy, zamiast pamiętać liczby.

## Punkt wyjścia: pierwszy pełny audyt z panelu

| | wartość |
|---|---|
| run | `acme-20260811T093330Z-agent` |
| snapshot | 6 (klient `acme`, workspace 5610281) |
| hipotez zbadanych | **86** |
| obalonych przez agenta | 30 (35%) |
| findingów zgłoszonych | 36 |
| **przyjętych po walidacji** | **27** |
| odrzuconych na walidacji | 9 (**0,25** — próg etapu 4 to ≤0,15) |
| **koszt** | **7,09 USD** (0,26 USD za znalezisko) |
| **czas** | **62 minuty** (~43 s na hipotezę) |
| rozliczenie | klucz API (koszt to faktyczny wydatek) |

## Co już wiadomo bez dodatkowego runu

**Z 62 minut tylko 40 sekund (1,1%) to wywołania do monday.** Policzone
z `wywolania.latency_ms`: 45 wywołań, średnio 894 ms. Pozostałe ~99% to czas
modelu — więc **czas i koszt to jedna robota**, oba w sesjach agenta.
Optymalizacja collectora nie da nic.

**Odsetek odrzuceń na walidacji przekracza próg etapu 4.** 0,25 wobec ≤0,15 —
znaczy to, że co czwarty finding zgłoszony przez agenta nie trzyma kontraktu D8.
To osobny problem od kosztu i **taniej go naprawić niż optymalizować**: każdy
odrzucony finding to zapłacona i wyrzucona praca modelu.

**Klasy trywialne to 8% hipotez.** Rubryka rozróżnia trudność przez `rola_agenta`:
`ZOMBIE_ACCOUNT` ma `brak` i budżet 0 wywołań (detektor już orzekł), a
`PROCESS_BYPASS` — „tu jesteś najbardziej potrzebny" i budżet 12. Na snapshocie 6:

| klasa | hipotez | rola agenta | budżet |
|---|---|---|---|
| BOARD_GHOST | 32 | jest | 4 |
| DUPLICATE_STRUCTURE | 21 | jest | 10 |
| BOARD_OVERCOMPLEX | 16 | jest | 8 |
| AUTOMATION_DEAD | 8 | jest | 5 |
| **ZOMBIE_ACCOUNT** | **7** | **brak** | **0** |
| GUEST_SPRAWL, PLAN_MISMATCH | 2 | jest | 3–5 |

Router modelu po `rola_agenta` da więc **najwyżej 8%**. Prawdziwe pieniądze siedzą
w `BOARD_GHOST` (32 hipotezy) i `DUPLICATE_STRUCTURE` (21).

## Czego JESZCZE nie wiadomo

Ten run jest **sprzed migracji 010**, więc nie ma rozbicia:

- `tokens_in`, `tokens_out`, `tokens_cache_read`, `tokens_cache_write` — **NULL**;
- brak wierszy w `zuzycie_hipotez`, czyli **brak kosztu per klasa**.

**Sumy 7,09 USD nie dzielimy po równo na 86 hipotez.** Liczba wyglądająca na pomiar
i nie będąca nim jest gorsza od jej braku — raport ewaluacji mówi „brak rozbicia"
i to jest poprawne zachowanie.

### Trzy pytania, na które odpowie następny run

1. **Czy prompt caching działa?** Przy 86 hipotezach na tym samym inwentarzu
   `cache_read` powinien być wielokrotnie większy od `tokens_in`. Jeśli nie jest,
   płacimy 86× za to samo wejście — i to jest wtedy najtańsze możliwe cięcie.
2. **Które klasy są drogie?** Czy `BOARD_GHOST` × 32 to 60% rachunku, czy 20%.
   Od tego zależy, czy eksperyment z tańszym modelem tam się zwróci.
3. **Ile płacimy za odrzucenia?** 30 hipotez obalonych plus 9 findingów odrzuconych
   na walidacji — to praca, za którą zapłaciliśmy i której nie widać w produkcie.

## Próba pomiaru 2026-08-12 — zatrzymana na braku środków

Uruchomiłem próbkę 8 hipotez `BOARD_GHOST` na snapshocie 6 (`proba-ghost-8`).
**Wszystkie 8 padło na `Credit balance is too low`** — konto platformy Anthropic ma
wyczerpane środki. Nic nie zostało policzone i nic nie zapłacone.

**Trzy pytania z tego dokumentu pozostają bez odpowiedzi.** Do ich zamknięcia
potrzebne jest doładowanie konta; sama próbka to ~1,2 USD.

### Co ten nieudany run jednak pokazał

**Instrumentacja z migracji 010 działa.** `zuzycie_hipotez` dostało 8 wierszy
z czasem per hipoteza (4,4 s, 2,4 s, 1,7 s…), zapisanych wspólną funkcją
`przebieg.zapisz_zuzycie`. Bez niej nie dałoby się nawet stwierdzić, że run doszedł
do modelu.

**Znalazł usterkę, której nie szukałem: run bez ani jednej zbadanej hipotezy
zapisywał się jako `zakonczony` z zerem findingów** — czyli **wyglądał jak audyt
konta bez problemów**. To najgroźniejsza możliwa pomyłka w tym narzędziu: cisza
udająca czyste konto. Naprawione — taki run dostaje status `przerwany`.

## Pomiar 2026-08-12 — pierwsza udana ewaluacja (8 hipotez, 0,82 USD)

Run `ewal-4klasy`: cztery klasy × dwie hipotezy, snapshot 6. **Wszystkie trzy pytania
zamknięte, jedno z odpowiedzią odwrotną do oczekiwanej.**

### 1. Prompt caching DZIAŁA — 79,2% wejścia z cache

| | tokenów |
|---|---|
| wejście świeże | 13 023 |
| **z cache (odczyt)** | **435 399** |
| zapis do cache | 101 388 |
| wyjście | 20 114 |

Wyliczenie z rachunku dawało ~90%; odczyt z SDK mówi **79,2%**. Rozbieżność
11 punktów pokazuje, ile warte są wyliczenia z sumy — kierunek był dobry, precyzja
nie. **Zwężanie inwentarza definitywnie odrzucone**: 4/5 wejścia idzie po dziesiątej
części ceny.

### 2. Koszt NIE idzie za budżetem narzędzi — korelacja jest ODWROTNA

| klasa | budżet | użyto | USD/hip. | wyjście/hip. | s/hip. |
|---|---|---|---|---|---|
| `BOARD_GHOST` | 4 | 2 | **0,1965** | 3 372 | 49,6 |
| `DUPLICATE_STRUCTURE` | 10 | 5 | 0,0978 | 2 970 | 42,9 |
| `BOARD_OVERCOMPLEX` | 8 | 2 | 0,0753 | 2 444 | 37,2 |
| `ZOMBIE_ACCOUNT` | **0** | 0 | **0,0424** | 1 270 | 20,6 |

Najdroższa klasa ma budżet 4, najtańsza 0, a klasa z budżetem 10 jest w środku.
**Budżet wywołań nie przewiduje kosztu.**

### 3. Koszt idzie za DŁUGOŚCIĄ WYJŚCIA i tylko za nią

Uszereguj tabelę powyżej po którejkolwiek kolumnie — kolejność jest identyczna:
koszt, wyjście i czas rosną razem. `BOARD_GHOST` produkuje 2,7× więcej tokenów
wyjścia niż `ZOMBIE_ACCOUNT` i kosztuje 4,6× więcej.

**To jest jedyny cel optymalizacji, jaki ma sens**: krótsze wyjście obniża koszt
I czas jednocześnie, bo oba wynikają z tego samego.

### 4. Poprawka `wpisow: 0` zadziałała

**Odsetek odrzuceń na walidacji: 0,00** (było 0,25 wobec progu ≤0,15). Siedem z ośmiu
hipotez dało finding, żaden nie odpadł na kontrakcie. `BOARD_GHOST` — klasa, która
generowała wszystkie 9 wcześniejszych odrzuceń — przeszła bez problemu.

### Czego ten pomiar NIE mówi

**Czy `ZOMBIE_ACCOUNT` na tańszym modelu dałby ten sam wynik.** Wiemy, że kosztuje
0,0424 USD/hip. i że agent nic tam nie ustala (`rola_agenta: brak`), ale to 10,3%
rachunku tej próbki. Router po `rola_agenta` zwróci więc niewiele — prawdziwe
pieniądze są w skróceniu wyjścia we wszystkich klasach.

**Nic o jakości.** Złoty zestaw nadal niewypełniony, więc 7 znalezisk z 8 hipotez to
liczba, nie ocena. Wysoki odsetek (88% wobec 31% w pełnym runie) wynika z tego, że
próbka wzięła po dwie PIERWSZE hipotezy każdej klasy, a nie losowe.

**Koszt na hipotezę wzrósł, nie spadł**: 0,0825 → 0,1030 USD. Przy zmienionym
cenniku nie da się rozdzielić, ile z tego to nowe stawki, a ile inny skład klas.
Dlatego raport porównuje też **tokeny wyjścia na hipotezę** — miarę odporną na ceny.

## Jak odtworzyć pomiar

```bash
# Powtórzenie runu na ZAMROŻONYM snapshocie — po to istnieje D7.
uv run python -m monday_audit.cli_agent --klient acme --snapshot 6

# Raport HTML z rozbiciem
uv run python -m monday_audit.cli_ewaluacja --run <nowy-run-id>

# Porównanie z baseline
uv run python -m monday_audit.cli_ewaluacja --run <nowy> --wobec acme-20260811T093330Z-agent
```

Koszt jednego powtórzenia: **~7 USD**. Dlatego każdy run kontrolny musi mieć
z góry ustalone, na jakie pytanie odpowiada.

## Czego ten dokument NIE zawiera

**Celu liczbowego.** Kuba świadomie go nie postawił, dopóki nie wiadomo, co da się
uciąć — a to wie się dopiero z rozbicia per klasa.

**Miary jakości.** Trafność i fałszywe trafienia wymagają złotego zestawu
(`evals/zloty_zestaw/`), czyli ręcznego przejścia konta przez człowieka. Bez niego
każda optymalizacja jest niemierzalna: tańszy agent, który gubi trafność, wygląda
jak sukces w kolumnie kosztów.
