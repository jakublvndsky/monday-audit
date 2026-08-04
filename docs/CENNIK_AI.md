# Cennik AI w monday.com — kredyty, agenci, przeliczniki

> **Zebrane 2026-08-04.** Plik referencyjny dla rozmów o koszcie, nie
> specyfikacja i nie nasz pomiar.
>
> **Claude Code: to NIE są nasze dane.** Nie wpisuj tych liczb do kodu wyceny
> i nie wstawiaj ich do findingów. Klasy z `typ_wyceny: ryzyko` mają
> `kwota_pln: null` i walidacja z D8 tego pilnuje. Powód jest w rubryce:
> wymyślona kwota podważa cały raport u pierwszego klienta, który ją sprawdzi.

## Skąd te liczby i dlaczego to ważne

Strony `support.monday.com` **zwracają 403 przy pobieraniu**, więc poniższych
stawek nie udało się potwierdzić u źródła. Pochodzą z analiz zewnętrznych
(Fruition Services) i z podsumowań wyszukiwania. Traktuj je jako **rząd
wielkości do rozmowy, nie jako cennik do faktury.**

Jedyna rzecz potwierdzona u źródła pierwotnego (dokumentacja deweloperska
monday) to fakt, że podłączeni agenci zużywają kredyty monday rozliczane
w dashboardzie użycia konta — bez żadnego API do odczytu tego zużycia.

## Jednostka: kredyt

**1 kredyt ≈ 0,01 USD.**

monday **nie udostępnia surowych tokenów LLM** i nie publikuje przelicznika
kredyt → token. To odpowiada wprost na pytanie „ile tokenów zużywa agent":
tej liczby nie ma i nie będzie — jedyną jednostką są kredyty.

## Uruchomienie agenta

Zużycie zależy od złożoności zadania. Widełki podawane jako kierunkowe,
bo zależą od modelu, instrukcji i skilli agenta:

| Złożoność | Kredyty na run | Orientacyjnie |
|---|---|---|
| proste | 10–50 | 0,10–0,50 USD |
| średnie | 50–150 | 0,50–1,50 USD |
| złożone | 150–250 | 1,50–2,50 USD |
| bardzo złożone | 250+ | 2,50+ USD |

**Jeden run agenta to rząd wielkości od dziesięciu groszy do kilku złotych.**

## Pojedyncze akcje AI

| Funkcja | Kredyty |
|---|---|
| AI Block (jedna akcja) | 8 |
| AI Workflow | 8 |
| monday Vibe | 30 za wiadomość |
| AI Notetaker | 120 za godzinę |

## Przydziały w planach

| Plan | Dziennie na użytkownika | Minimum miesięczne |
|---|---|---|
| Basic | 50 | 1 000 (~10 USD) |
| Standard | 50 | 2 000 (~20 USD) |
| Pro | 50 | 3 000 (~30 USD) |
| **Enterprise** | **1 300** | 20 000 + 25 miejsc w pakiecie |

Pakiety dokupowane: 4 000 (40 USD), 8 000 (80 USD), 20 000 (200 USD).

## Enterprise nie płaci za agentów — i to zmienia rozmowę

Rozliczanie agentów kredytami wystartowało **8–9 czerwca 2026** dla planów
Pro, Standard i Basic. **Enterprise jest zwolniony**, a termin przejścia
nie został ogłoszony.

Konsekwencja praktyczna, o której trzeba mówić wprost: na koncie
**CXLABS (Enterprise) kredyty za agentów będą zerowe** niezależnie od tego,
co API kiedyś odsłoni. Pomiar tej ścieżki wymaga konta klienta na Pro
albo niżej.

## Gdzie klient widzi swoje zużycie

Panel administracyjny → **AI governance → Credits**. Widać zużycie w bieżącym
cyklu rozliczeniowym z rozbiciem na funkcje. Dla agentów widok pokazuje datę
uruchomienia, użyte aplikacje i liczbę kredytów.

**To jest dziś jedyna droga do tych liczb.** API ich nie oddaje (O2, O20),
więc jeśli liczba kredytów ma wejść do raportu, musi ją podać człowiek —
tym samym wzorcem, co `koszt_licencji_mies` w O7.

## Czego tutaj NIE MA

- **potwierdzenia od monday** — wszystkie stawki poza faktem zużywania
  kredytów pochodzą ze źródeł zewnętrznych, bo support blokuje pobieranie
- **stawki per model** — wiadomo, że zużycie „zależy od modelu", ale nie ma
  tabeli, który model kosztuje ile
- **przelicznika kredyt → token** — monday go nie publikuje i nie udostępnia
  surowych tokenów
- **kosztu jednego runu KONKRETNEGO agenta** — tylko widełki po złożoności;
  dokładna liczba jest w panelu klienta, nie w cenniku
- **terminu przejścia Enterprise na kredyty** — nieogłoszony

## Do porównania: koszt naszego audytu

Dla kontrastu, bo to **nasz pomiar**, nie cudzy cennik. Pełny run agenta
analitycznego na koncie CXLABS, 19 hipotez (run `agent-pelny-19`,
snapshot 5, model `claude-sonnet-5`):

| | |
|---|---|
| koszt | **1,71 USD** |
| tokeny wejścia | 29 146 |
| tokeny wyjścia | 36 684 |
| tokeny z cache | 758 113 |
| findingi | 11 przyjętych, 8 hipotez odrzuconych |
| czas | ~17 minut |

Czyli **jeden audyt kosztuje tyle, ile 2–17 uruchomień agenta monday**,
zależnie od ich złożoności. Ta liczba jest mierzona przy każdym runie
i zapisywana w `zuzycie.koszt_usd` — bierzemy ją z `total_cost_usd` podanego
przez Agent SDK, nie z mnożenia tokenów przez cennik zaszyty u nas.

## Źródła

| Co | Skąd | Wiarygodność |
|---|---|---|
| agenci zużywają kredyty monday, rozliczane w dashboardzie konta | [developer.monday.com — Build on monday.com with AI](https://developer.monday.com/api-reference/docs/build-on-monday-with-ai) | **źródło pierwotne, potwierdzone cytatem** |
| brak API do odczytu zużycia | ta sama strona + własna introspekcja schematu | **własny pomiar** |
| 0,01 USD za kredyt, widełki 10–250+ na run, pakiety, przydziały | [Fruition — monday AI Pricing Model for 2026](https://www.fruitionservices.io/post/monday-ai-pricing-model-2026) | źródło zewnętrzne |
| 8 kredytów za AI Block, 30 za Vibe, 120 za Notetaker, brak surowych tokenów | [Fruition — monday.com AI Token Usage in 2026](https://www.fruitionservices.io/post/monday-ai-credits-usage) | źródło zewnętrzne |
| Enterprise zwolniony, start 8–9 czerwca 2026 | [monday: AI Agents](https://support.monday.com/hc/en-us/articles/33347027353746-AI-Agents-on-monday-com), [monday: pricing model](https://support.monday.com/hc/en-us/articles/35277848309394-The-pricing-model-for-monday-AI-portfolio) (oba 403 — treść z wyszukiwania) | niepotwierdzone u źródła |
| panel AI governance → Credits | [monday: AI Credits](https://support.monday.com/hc/en-us/articles/29544502265746-AI-Credits) (403) | niepotwierdzone u źródła |
