# Etap 4 — co zostało zrobione, jak i dlaczego

> Dokumentacja **wykonania** etapu 4. Specyfikacja jest w `04-test.md`, pomiary
> w `docs/BASELINE_ETAP4.md`, otwarte kwestie w `docs/OTWARTE.md` (O28–O33).
> Ten plik odpowiada na „co zdecydowaliśmy i na jakiej podstawie".

## Po co ten etap istniał

Pierwszy pełny audyt (2026-08-11) kosztował **7,09 USD i 62 minuty** — 86 hipotez,
27 znalezisk. Etap 4 miał to obniżyć, ale przy ~7 USD za każdy pomiar kontrolny
**zgadywanie było drogie**, a jakości nikt nie mierzył wcale. Dwa zadania naraz:
zbudować miarę i użyć jej do cięcia kosztu.

Kolejność nie była dowolna. **Bez miary jakości nie da się powiedzieć, czy tańszy
run jest równie dobry** — a bez rozbicia kosztu nie wiadomo, gdzie ciąć.

---

## Część 1 — miara jakości (bo nie było żadnej)

### Skąd wiadomo, co jest „dobrze"

Złoty zestaw: plik YAML z pozycjami, które agent **musi** znaleźć, i takimi,
których zgłoszenie jest **fałszywką**. Trzy sekcje, każda odpowiada na inne pytanie:

| sekcja | znaczenie | kto wypełnia |
|---|---|---|
| `oczekiwane` | jest w danych, agent musi to zgłosić | wyliczalne z danych |
| `niedopuszczalne` | jest w danych, ale rubryka każe odrzucić | wyliczalne z danych |
| `pominiete` | **czego w danych NIE MA**, a jest na koncie | **wyłącznie człowiek** |

Sekcja `niedopuszczalne` jest tym, co odróżnia zestaw od zapisu wyniku agenta.
Bez niej zestaw tylko **potwierdzałby** agenta i zawsze wychodziłoby 100%.

`pominiete` zostaje pusta i to jest zapisane w każdym pliku zestawu. Dopóki jest
pusta, **trafność mierzy zgodność z danymi snapshotu, nie z rzeczywistością konta.**
Miernik pisze to przy każdym wyniku, żeby nikt nie przeczytał 1,000 jako
„bezbłędnie".

### Dopasowanie faktów, nie sędzia LLM

`musi_zawierac` sprawdza obecność **liczby** (≥ progu, bo cisza rośnie z każdym
dniem) i **słów kluczowych**, z wariantami po `|`. `nie_powinno_zawierac` łapie
spekulację i PII wzorcem.

Powód odrzucenia sędziego LLM: mierzyłby własny gust i kosztował tyle, co sam
audyt. Ocena stylu opisu zostaje osobną warstwą z `04-test.md` i osobną decyzją.

### Cztery usterki w samym mierniku

Narzędzie do oceny jakości jest samo kodem i psuje się tak samo. Wszystkie cztery
znalezione przez konfrontację z prawdziwymi wynikami, nie przez testy:

| usterka | kierunek | skutek |
|---|---|---|
| zakres zakazu po **klasie**, nie po obiekcie | **zawyżała** | 12 poprawnych findingów jako fałszywki (0,444 zamiast 0,037) |
| `ł` nie przechodzi NFKD (U+0142, litera z kreską) | **zawyżała** | „osoba odeszła" nie łapała się na wzorzec — „bez przecieku" dla findingu, który spekulował wprost |
| `(kind member albo admin)` jako koniunkcja | **zaniżała** | fakt niespełnialny, bo konto jest albo jednym, albo drugim |
| brak wariantów sformułowań | **zaniżała** | agent pisał „konto typu member", zestaw wymagał „zajmuje płatne miejsce" |

**Miara zaniżająca jest równie zła jak zawyżająca** — kazałaby wydłużać opisy, żeby
trafić w sformułowania zestawu, a cel jest odwrotny: krótko i rzeczowo.

### Trzecia miara: rzeczowość

Nie ma jej w `04-test.md`, bo powstała w tym etapie. Liczy, ile z **trafionych**
pozycji niesie wszystkie wymagane fakty i żaden zakaz. Bez niej nie dałoby się
powiedzieć, czy skrócenie odpowiedzi zabrało treść — a to było główne ryzyko
całej optymalizacji.

Liczona po trafionych, nie po wszystkich: pozycja nieznaleziona nie ma jak być
nierzeczowa, a wliczanie jej dwa razy karałoby za to samo.

### Dwie miary chroniące przed fałszywym alarmem

**`trafnosc_w_zasiegu`** — run z `--na-klase 2` widzi 2 z 7 kont, więc jego
maksymalna możliwa trafność to 0,29. Bez tego rozróżnienia raport pokazywałby
„poniżej progu" dla runu, który nie pomylił się ani raz.

**Pusty zestaw `oczekiwane` daje trafność 1,0, nie 0,0.** „Nie ma czego znaleźć"
nie jest tym samym co „nie znalazł". Zmierzone na `wygaszeni-s7`: agent odrzucił
obie hipotezy i oba odrzucenia były trafne.

---

## Część 2 — cięcie kosztu

### Gdzie realnie były pieniądze

Rozbicie rachunku 3,76 USD (run `ewal-tablice-s7`, 37 hipotez):

| pozycja | tokeny | koszt | udział |
|---|---|---|---|
| **wyjście** | 123 806 | 1,86 USD | **49%** |
| odczyt cache | 2 669 227 | 0,80 USD | 21% |
| zapis cache | 280 190 | 1,05 USD | 28% |
| wejście świeże | 60 236 | 0,18 USD | 5% |

**Wyjście to 49% rachunku przy 4% wolumenu tokenów.** Wejście było już wyciśnięte:
cache 88,7% oszczędza na tym runie ~6,9 USD, a zawężanie inwentarza odrzucono
**trzykrotnie** pomiarem — cache rośnie z rozmiarem runu (79% → 83% → 88,7%), więc
im większy run, tym mniej można tam ugrać.

### Krok 0 — rozdzielić wyjście, zanim się je tnie (0,21 USD)

`tokens_out` sklejał trzy rzeczy o **trzech różnych dźwigniach**: tokeny myślenia
(niewidoczne w `TextBlock`), wcześniejsze bloki tekstu wyrzucane przez pętlę,
i finalny JSON. Migracja 011 dołożyła trzy kolumny.

**Wynik: 74–76% to myślenie, wyrzuconych bloków ZERO.** Agent odpowiada jednym
blokiem, od razu JSON-em.

To unieważniło całą drogę „instrukcja w prompcie: nie rozpisuj rozumowania" —
walczyłaby o nic, a przy okazji zresetowałaby cache i porównywalność runów.
Krok kosztował 0,21 USD i **zaoszczędził próbę za ~0,6 USD**.

Sprawdzone przy okazji: **`max_tokens` nie istnieje w `ClaudeAgentOptions`.** Cała
pierwotna droga „ogranicz tokeny" była nierealizowalna. Obawa o obcięcie JSON-a
w środku dotyczyła `max_budget_usd` — którego dlatego nie ruszamy, bo zwraca błąd
zamiast findingu.

### Krok 1 — klasa, w której model nic nie wnosił (0 USD)

`ZOMBIE_ACCOUNT` ma `rola_agenta: brak` i `budzet_wywolan: 0`. Sprawdzenie:
wszystkie 7 findingów miało `dowod` **identyczny** z faktami detektora dla
wszystkich 6 pól rubryki. Model przepisywał JSON i dokładał zdanie.

Szablon w `src/monday_audit/szablony_findingow.py` daje **te same trzy metryki
(1,000 / 0,000 / 1,000) za 0,00 USD** zamiast 0,357 USD na run.

Rozdzielenie siedzi w `zbadaj_hipotezy`, nie w `zbadaj_hipoteze`: tamta funkcja
jest o prowadzeniu sesji, nie o tym, czy sesja jest potrzebna. `SZABLONY` to jawny
słownik, nie automat po `rola_agenta == "brak"` — klasa może mieć `brak` i nadal
wymagać zdania, którego nie umiemy napisać szablonem.

**Model nie został przy rekomendacji**, wbrew pierwotnemu kompromisowi. Powód
mocniejszy niż oszczędność: jedna z prawdziwych rekomendacji radziła *przenieść
konto na rolę bezpłatną (guest)* — czyli tworzyć problem, który ta sama rubryka
audytuje jako `GUEST_SPRAWL`, a złoty zestaw ma na to jawny zakaz. Model nie ma
jak wiedzieć, że inna klasa uznaje to za wadę; człowiek pisząc szablon raz — ma.

Co szablon liczy sam, zamiast deklarować: `pewnosc` z pola `podstawa` detektora
(`wysoka` wymaga dwóch niezależnych dowodów **i** ciszy ≥120 dni), `kwota_pln`
zawsze `None` (wzór jest na całe konto, findingi są per konto).

### Krok 2 — skrócenie wyjścia (1,58 USD)

Dźwignia wskazana krokiem 0: `effort`. Sprawdzone w kodzie SDK, że flaga faktycznie
dochodzi do argv CLI (`subprocess_cli.py:647`) — bez tego byłby to **trzeci**
przypadek „opcja jest, nie działa" po `--read-only` w MCP i `can_use_tool`.

| run | USD/hip. | out/hip. | trafność | rzeczowość |
|---|---|---|---|---|
| baseline | 0,1017 | 3346 | 1,000 | 1,000 |
| `effort=high` | **0,1280** | **4121** | **0,833** | 1,000 |
| `effort=medium` | 0,0771 | 1575 | 1,000 | **0,833** |
| medium + wymóg faktu | **0,0710** | **1236** | 1,000 | 1,000 |

`effort=medium` zbił wyjście, ale rzeczowość spadła — i to był **realny brak**.
Fakt o aktywności ginął **niesystematycznie**: 3 z 6 findingów miały go w dowodzie,
3 nie.

**Przyczyną nie był brak wysiłku, a brak wymogu.** Rubryka nie wymieniała
aktywności w polach `dowod`, więc walidacja jej nie pilnowała, a detektor jej nie
podawał. Agent czasem sam ją znajdował.

Naprawa u źródła, nie przez oddanie effortu:

1. detektor dokłada `aktywnosc_stron` — `LEFT JOIN`, żeby `None` znaczyło „nie
   wiem", nie „zero", bo te dwie rzeczy nie mogą się zlać;
2. rubryka **wymaga** tego pola w dowodzie → walidacja odrzuci finding bez niego;
3. dwa nowe warunki odrzucenia.

Skutek: agent **przestał szukać** faktu, który dostaje gotowy, i wyjście spadło
**jeszcze niżej** — 1575 → 1236. **Guardrail w kodzie wyszedł tańszy niż
w prompcie**, i to jest najważniejszy wniosek techniczny tego etapu.

`effort` **nie jest przypięty** na stałe. `high` wyszedł droższy od baseline'u
o 29% i zgubił jedną pozycję, więc „więcej wysiłku = lepiej" jest fałszem.
Wartość domyślna byłaby przypięciem liczby zmierzonej na jednej klasie i jednym
koncie demo.

### Krok 3 — nowa klasa `UZYTKOWNIK_WYGASZONY` (0,38 USD)

Między `ZOMBIE_ACCOUNT` i `ENGAGEMENT_DROP` była dziura. Pierwszy ma w SQL
`AND autorzy.user_hash IS NULL`, czyli bierze **wyłącznie** osoby nieobecne
w logach — przecięcie z `per_uzytkownik` jest puste **z konstrukcji** (zmierzone:
8 osób w logach, 7 kont zombie, 0 wspólnych). Drugi mierzy **grupy** i wzbudza
zero hipotez na snapshocie #7.

Osoba widoczna w logach, która przestała pracować, nie miała swojej klasy.

Plan mówił „6 z 8 osób ma przesunięcie" — prawda o kubełkach, nieprawda o tym,
kogo klasa dotyczy. Po odsianiu zostało **dwoje**: 3 konta agentów AI
(`personal_agent_member`), 2 konta nieobecne na liście kont (w tym
**najaktywniejsze**, 205 akcji), 1 osoba nadal aktywna.

Oba warunki deterministyczne sprawdza **detektor**, nie agent — zostawienie ich
modelowi kosztowałoby to samo rozumowanie w każdej sesji (D1).

**Agent odrzucił oba przypadki i weryfikacja na danych potwierdziła oba
uzasadnienia.** Sekcja `oczekiwane` tego zestawu jest więc pusta: na snapshocie #7
nie ma ani jednego przypadku tej klasy. To nie wada detektora — on wzbudza
poprawnie, a agent stosuje warunki, których detektor sprawdzić nie może, bo
wymagają porównania aktywności osoby z aktywnością **całej tablicy** w czasie.

### Krok 4 — pętla jednosesyjna odrzucona pomiarem (1,00 USD)

Architektura „jedna sesja na hipotezę" miała trzy argumenty w docstringu
i **ani jednego pomiaru**. Skrypt `evals/petla_jednosesyjna.py` **poza ścieżką
produkcyjną** (do usunięcia jednym `rm`, gdyby wynik był inny) zamknął tę lukę.

| tryb | USD/hip. | out/hip. | cache |
|---|---|---|---|
| osobne sesje | **0,0710** | **1236** | **75,9%** |
| jedna sesja | 0,2000 | 2107 | 64,5% |

**Droższa o 182%.** Mechanizm widoczny w danych: `tokens_cache_read` per tura to
`[0, 34 579, 71 312, 110 232, 150 788]`. Historia narasta liniowo, więc suma
odczytów rośnie **kwadratowo**. Przy osobnych sesjach prefiks jest ten sam dla
wszystkich, odczyt stały — i dlatego cache wychodzi **wyżej**, choć intuicja mówi
odwrotnie.

Obawa o degradację potwierdzona liczbowo: długość opisu 459 → 508 → 473 → 359 →
342 znaków (nachylenie **−38,3**), podobieństwo leksykalne kolejnych odpowiedzi
0,36 → 0,14 → 0,49 → **0,667**.

Kontekst się **nie** urwał (`error_max_turns` milczało, `cache_read` rósł
monotonicznie). Ale przy 37 hipotezach odczyt szedłby w miliony tokenów — problem
przesuwa się z „urwie się" na „będzie drogie".

Jakość **nie spadła**, i to czyni tę odpowiedź użyteczną: gdyby spadła,
wiedzielibyśmy tylko „jest gorzej". Wiemy więcej — *że* jest drożej i *dlaczego*.

**Architektura zostaje bez zmian, ale przestaje być wyborem nieudokumentowanym.**

---

## Decyzje odrzucone i dlaczego

| pomysł | dlaczego odrzucony |
|---|---|
| **grupowanie hipotez tej samej klasy** | zmierzone: `BOARD_OVERCOMPLEX` użył 16 wywołań na 16 hipotez, czyli sprawdzał próbkę **za każdym razem**. W jednej sesji przestałby po piątej |
| **sub-agenci per klasa** | dałoby **7 cache'y zamiast jednego**, a zapis kosztuje 12,5× odczyt. Byłoby drożej |
| **router do Haiku** | `rola_agenta: brak` ma tylko `ZOMBIE_ACCOUNT` — ≤10% hipotez. Krok 1 usunął tę klasę z modelu w całości, więc router nie miałby czego routować |
| **`max_budget_usd`** | zwraca `error_max_budget_usd`, czyli utratę hipotezy zamiast findingu |
| **obniżenie `MAKS_OBROTOW`** | zmierzone 23 wywołania na 37 hipotez przy budżecie 338 — agent nie zbliża się do sufitu |
| **zawężanie inwentarza** | trzy pomiary przeciw; cache rośnie z rozmiarem runu, więc oszczędność maleje |
| **obniżenie progów `ENGAGEMENT_DROP`** | zespół z `u90d = 0,33` **nigdy nie używał**, więc nie „przestał". Zgłoszenie byłoby fałszywką, a te mają pierwszeństwo |
| **Langfuse** | D10 w `ARCHITEKTURA.md` plus tabela pokrycia: każda rzecz, którą chcieliśmy widzieć, jest już w schemacie. Jedyną lukę zamknął krok 0 za trzy kolumny — taniej niż postawienie ClickHouse'a |

---

## Wzorzec, który powtórzył się trzy razy

**Agent czytał dane uważniej niż złoty zestaw.** Za każdym razem poprawiony został
zestaw, nie agent:

1. **`BOARD_OVERCOMPLEX`** — zestaw zakładał, że przewaga kolumn formuł znaczy
   „tablica raportowa, nie zgłaszaj". Agent zszedł na próbkę 19 itemów (100%
   populacji) i wykazał **34 martwe kolumny z 45**, w tym 13 formuł miesięcznych
   zwracających zero. Zestaw stał na *typie* kolumny, agent sprawdził jej *stan*.
2. **`DUPLICATE_STRUCTURE`** — odrzucił parę z uzasadnieniem, którego zestaw nie
   przewidywał: obie tablice powstały **w tej samej sekundzie**, ten sam
   właściciel, brak wzorca „jedna aktywna, reszta cichnie". I dodał: „workspace
   nazywa się `CRM_PL_Demo`". Sprawdzenie potwierdziło — 124 ze 124 tablic (O33).
3. **`UZYTKOWNIK_WYGASZONY`** — wykrył, że aktywność ustała u **wszystkich**
   autorów jednocześnie, czyli to zamknięcie projektu, nie wygaszenie osoby.

Wniosek metodyczny: **złoty zestaw jest hipotezą o danych, nie prawdą.** Trzeba go
konfrontować z wynikiem, a nie tylko wynik z nim.

---

## Co ten etap dał poza liczbami

**Zakładka „Ludzie" w panelu** (poza planem, na prośbę Kuby). Dane `per_uzytkownik`
istniały od kroku 1 i szły **wyłącznie** do inwentarza agenta — panel ich nie
pokazywał, więc marnowaliśmy to, co zebraliśmy. Odpowiada na „kto z czego korzysta,
jak i kiedy", czego znaleziska nie robią: finding mówi „tu jest problem, zrób X",
a to jest obraz stanu.

Dwie usterki panelu złapane przy okazji:

* **panel nie otwierał się dla ACME** — `zbuduj_pulpit` czytał snapshot przez
  tabelę `findings`, więc run o zerze wierszy dawał `TypeError`. Skutek był
  nieproporcjonalny: dziewięć audytów stawało się niewidocznych, bo lista wersji
  jedzie w tym samym payloadzie. Wyzwolił to skrypt eksperymentalny z kroku 4;
* **run bez snapshotu był w drop-downie** — `agent-pelny-19` z lipca miał
  `findingow = 11` i `snapshot_id = NULL`, a jego wybór dawał 404.

---

## Granice, które etap 4 utrzymał

Żadna z granic z `CLAUDE.md` nie została naruszona ani rozluźniona:

* agent nadal nie ma narzędzia zapisującego — trzy warstwy odcięcia bez zmian;
* zero imion, nazwisk i e-maili w kontekście modelu; zakładka „Ludzie"
  deanonimizuje **po** stronie panelu, tą samą `Deanonimizacja` co raport;
* `items_count` nadal jest granicą — jedyne zejście na itemy to sampling
  w `BOARD_OVERCOMPLEX`, jawnie oznaczony w rubryce;
* każdy finding ma `dowod`; walidacja nie została złagodzona dla szablonu —
  przechodzi ten sam kontrakt D8;
* zero nowych zależności.

Jedna granica została **zaostrzona**: `DUPLICATE_STRUCTURE` dostało dwa warunki
odrzucenia i wymóg `aktywnosc_stron` w dowodzie, co wycięło 16 z 21 hipotez tej
klasy jako par martwych po obu stronach.
