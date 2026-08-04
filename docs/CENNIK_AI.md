# Cennik AI w monday.com — metodologia, źródła, granice pomiaru

> **Ten plik NIE jest cennikiem.** Obowiązujące liczby są w tabeli `cennik`
> w bazie, odświeżane komendą `cli_cennik --odswiez`. Tutaj jest opis: skąd
> pochodzą, na ile są pewne i czego zmierzyć się nie da.
>
> Powód rozdzielenia: cenniki się zmieniają, a liczb wklejonych w markdown
> nikt nie odświeża. Stara stawka w raporcie klienta przestaje być
> niedogodnością, gdy dochodzi front i publikacja na Marketplace — staje się
> błędem, który podważa cały raport.

```bash
uv run python -m monday_audit.cli_cennik --odswiez   # pobierz ze stron monday
uv run python -m monday_audit.cli_cennik --pokaz     # co obowiązuje i od kiedy
```

## Sprostowanie z 2026-08-04

Wcześniejsza wersja tego pliku twierdziła, że `support.monday.com` **zwraca
403** i że żadnej stawki nie da się potwierdzić u źródła. **To była nieprawda.**
Strony zwracają 200 przy zwykłym nagłówku `User-Agent`; bez niego 403. Część
liczb pochodzi więc dziś **wprost od monday** i jest pobierana automatycznie.

Konsekwencja: to, co poniżej oznaczone jako `zrodlo_pierwotne`, jest cytowane
z żywej strony — `cennik.surowy_fragment` trzyma fragment HTML-a, z którego
liczba została wyjęta, żeby dało się odróżnić „zmieniła się cena" od „zmienił
się układ strony".

## Co jest pobierane u źródła, a co nie

| Pozycja | Wiarygodność | Skąd |
|---|---|---|
| `ai_block_kredyty` | `zrodlo_pierwotne` | scraper, strona AI Credits |
| `ai_notetaker_kredyty_godzina` | `zrodlo_pierwotne` | scraper, ta sama strona |
| `agent_run_kredyty_min` / `_max` | `zrodlo_pierwotne` | scraper, tabela złożoności agentów |
| `kredyt_ai_usd` | `zewnetrzne` | **na stronach monday NIE WYSTĘPUJE** |
| `koszt_licencji_mies` | od klienta | **niescrapowalna** — negocjowana (O7) |

Dwie ostatnie pozycje są ważniejsze niż wygląda ich miejsce w tabeli:

**`kredyt_ai_usd`** to przelicznik kredytu na pieniądze i monday go nie
publikuje. Wartość `0,01 USD` pochodzi z analizy zewnętrznej, więc każda kwota
policzona przez ten przelicznik jest szacunkiem cudzego szacunku. Stąd sufit
w `PRZEDZIALY`: strona monday zawiera zarówno `$0.01`, jak i `$9` (cena planu
per user), a scraper, który wziąłby drugie, policzyłby klientowi kwotę 900 razy
za dużą.

**`koszt_licencji_mies`** wchodzi tylko ręcznie, przez `cli_agent
--koszt-licencji-mies` albo formularz we froncie. Publiczny cennik ceny
Enterprise nie zawiera, bo jest negocjowana; podstawienie ceny z listy dałoby
liczbę pewnie brzmiącą i błędną.

## Uwaga, która wyszła dopiero przy scraperze: dwie tabele złożoności

Strona AI Credits ma **dwie tabele o identycznym kształcie zdania**:

| | Simple | Intermediate | Complex | Extra complex |
|---|---|---|---|---|
| **monday agents** (za run) | ~10–50 | ~50–150 | ~150–250 | ~250+ |
| **monday sidekick** (za wiadomość) | ~10–30 | ~30–80 | ~80–150 | ~150+ |

Wzorzec bez kotwicy łapie tę, która na stronie stoi wyżej — czyli po
przestawieniu sekcji stawka podpisana „agent" mogłaby cicho pochodzić od
sidekicka. Dlatego wyrażenia w `cli_cennik.WZORCE` są zakotwiczone w słowie
`agents`, a test `test_wzorzec_agenta_nie_lapie_tabeli_sidekicka` tego pilnuje.

**Jeden run agenta to rząd wielkości od dziesięciu groszy do kilku złotych** —
przy przeliczniku, który sam jest źródłem zewnętrznym.

## Enterprise nie płaci za agentów — i to zmienia rozmowę

Rozliczanie agentów kredytami wystartowało **8–9 czerwca 2026** dla planów
Pro, Standard i Basic. **Enterprise jest zwolniony**, a termin przejścia
nie został ogłoszony.

Konsekwencja praktyczna, o której trzeba mówić wprost: na koncie
**CXLABS (Enterprise) kredyty za agentów będą zerowe** niezależnie od tego,
co API kiedyś odsłoni. Pomiar tej ścieżki wymaga konta klienta na Pro
albo niżej (O21).

## Gdzie klient widzi swoje zużycie

Panel administracyjny → **AI governance → Credits**. Widać zużycie w bieżącym
cyklu rozliczeniowym z rozbiciem na funkcje. Dla agentów widok pokazuje datę
uruchomienia, użyte aplikacje i liczbę kredytów.

**To jest dziś jedyna droga do tych liczb.** API ich nie oddaje (O2, O20),
więc jeśli liczba kredytów ma wejść do raportu, musi ją podać człowiek —
tym samym wzorcem, co `koszt_licencji_mies` w O7.

## Czego tutaj NIE MA

- **stawki per model** — wiadomo, że zużycie „zależy od modelu", ale nie ma
  tabeli, który model kosztuje ile
- **przelicznika kredyt → token** — monday go nie publikuje i nie udostępnia
  surowych tokenów. To odpowiada wprost na pytanie „ile tokenów zużywa agent":
  tej liczby nie ma i nie będzie, jedyną jednostką są kredyty
- **kosztu jednego runu KONKRETNEGO agenta** — tylko widełki po złożoności;
  dokładna liczba jest w panelu klienta, nie w cenniku
- **ceny w API** — `Plan` oddaje `max_users`, `period`, `tier`, `version`.
  Jedyne pola cenowe (`AppSubscriptionDetails.monthly_price`) dotyczą ceny
  aplikacji NA Marketplace, czyli tego, co klient płaciłby nam
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
| brak API do odczytu zużycia | ta sama strona + sonda `agenci.sonduj_agentow` na trzech wersjach API | **własny pomiar** |
| brak ceny w API | własna introspekcja typu `Plan` | **własny pomiar** |
| 8 kredytów za AI Block, 120 za godzinę Notetakera, widełki złożoności agentów i sidekicka | [monday: AI Credits](https://support.monday.com/hc/en-us/articles/29544502265746-AI-Credits) | **źródło pierwotne, pobierane przez `cli_cennik`, cytat w `surowy_fragment`** |
| 0,01 USD za kredyt, pakiety, przydziały dzienne | [Fruition — monday AI Pricing Model for 2026](https://www.fruitionservices.io/post/monday-ai-pricing-model-2026) | źródło zewnętrzne |
| Enterprise zwolniony, start 8–9 czerwca 2026 | [monday: AI Agents](https://support.monday.com/hc/en-us/articles/33347027353746-AI-Agents-on-monday-com), [monday: pricing model](https://support.monday.com/hc/en-us/articles/35277848309394-The-pricing-model-for-monday-AI-portfolio) | niepotwierdzone u źródła — treść z wyszukiwania, nie z pobrania |
| panel AI governance → Credits | [monday: AI Credits](https://support.monday.com/hc/en-us/articles/29544502265746-AI-Credits) | źródło pierwotne |
