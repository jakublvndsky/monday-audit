# monday.com Account Audit — podsumowanie stanu

> **Dla kogo:** osoba nadzorująca projekt. Pięć minut czytania, bez kodu.
> **Stan na:** 2026-08-11. Etap 3 (Build) zbudowany, czeka na odhaczenie 3.12.
> Doszła **działająca aplikacja web** — panel dla zespołu i dla klienta, z dostępami
> i resetem haseł. Poza numeracją etapu 3, na wyraźne polecenie Kuby.
> **Pierwszy audyt uruchomiony w całości z panelu przez klienta: 2026-08-11.**
> **Szczegóły techniczne:** [`ZBUDOWANE.md`](ZBUDOWANE.md) · **decyzje:**
> [`ARCHITEKTURA.md`](ARCHITEKTURA.md) · **niepewności:** [`OTWARTE.md`](OTWARTE.md)

Wszystkie liczby niżej są **z pomiaru na prawdziwym koncie CXLABS**, nie
z szacunku. Gdzie czegoś nie zmierzyliśmy, jest to napisane wprost.

---

## Co to jest

Narzędzie audytuje konto monday.com klienta i produkuje raport ze znaleziskami:
co jest zepsute, dlaczego i co z tym zrobić. Odpalane ręcznie, jednorazowo per
klient. Nie SaaS, nie abonament.

**To raport, nie oferta.** Nie ma w nim zakresu naszych prac ani ceny za nasze
usługi. Kwoty w raporcie to oszczędności KLIENTA na jego licencjach monday,
policzone ze stawki, którą sam podał — i dokument zawsze pokazuje, skąd ta
stawka pochodzi i kiedy została zapisana.

## Jak to działa — pięć kroków

```
1. COLLECTOR    spisuje konto wyczerpująco, czysty GraphQL, zero AI
2. DETEKTORY    11 zapytań SQL wzbudza hipotezy — zero AI
3. AGENT        bada każdą hipotezę osobno, tylko czyta
4. WALIDACJA    kod odrzuca finding bez dowodu i kwotę bez podstawy
5. RENDERER     dwa dokumenty HTML: wewnętrzny i klientowy
```

**Agent jest w środku, nie na końcu.** Po nim dwie warstwy deterministyczne.
Sam nie publikuje niczego i nie ma żadnego narzędzia zapisującego — ani do
monday, ani do bazy, ani do plików.

Podział jest świadomy: **spis to robota kodu** (lista rzeczy, które istnieją,
jest skończona i znana), **dochodzenie to robota agenta** (ścieżka nie jest
znana z góry). Agent nie decyduje, CZY sprawdzić anomalię — decyduje JAK.

---

## Co zmierzyliśmy na koncie CXLABS

Workspace 6576039, snapshot z 2026-08-01.

### Collector — jeden przebieg

| | |
|---|---|
| wywołania do monday | **227** |
| complexity | 638 798 |
| czas | minuty |
| tablice | 105, wszystkie aktywne, wszystkie z właścicielem |
| kolumny | 902, maksymalnie 21 na tablicy |
| itemy | 559 — **tylko licznik**, treści nie zbieramy |
| użytkownicy | 95 |
| automatyzacje | 80, z tego 7 z błędami |
| wpisy activity log | 4 432 w oknie 90 dni |
| wyciek PII | **0** |

**Dwie liczby, które zmieniają rozmowę z klientem:**

**36 z 95 „kont" to agenci AI, nie ludzie.** Ponad jedna trzecia. Nie wyszłoby
to z samego `razem: 95` — i wprost dotyczy kosztu licencji.

**94 z 105 tablic jest zdominowanych jednym autorem.** Collector to liczy,
ale **żadna dzisiejsza klasa znalezisk tego nie używa.** Dane są w snapshocie,
klasy w rubryce nie ma. Kandydat na nowe znalezisko, nie usterka.

### Agent — pełny przebieg analityczny

19 hipotez, model `claude-sonnet-5`:

| | |
|---|---|
| koszt | **1,71 USD** |
| czas | ~17 minut |
| findingi przyjęte | **11** |
| odrzucone na walidacji | 0 |
| **hipotezy obalone przez agenta** | **8 z 19** |

**Odrzucenie 8 z 19 to najważniejsza liczba w tej tabeli.** Agent, który
potwierdza wszystko, jest bezużyteczny — kontrakt wyjściowy wymaga niepustej
listy odrzuceń, a run z jedną hipotezą dostaje ostrzeżenie w logu.

Przykład realnego odrzucenia, słowami agenta: hipoteza „plan nieadekwatny do
użycia" upadła, bo *najnowsze konto utworzono 51 dni przed audytem, czyli
poniżej progu 60 dni — konto rośnie, a nadwyżka miejsc to zapas rekrutacyjny,
nie nadpłata*.

### Ile to kosztuje

**Pełny audyt to ~7 USD i ~godzina.** Zmierzone 2026-08-11 na drugim workspace'ie
konta CXLABS: 86 hipotez, 27 znalezisk, **7,09 USD, 62 minuty**.

Ta liczba zastępuje wcześniejsze „1,71 USD". Poprzednia była prawdziwa, ale
pochodziła z przebiegu na **19 hipotezach** — pierwszy audyt większego workspace'u
pokazał, że koszt i czas rosną liniowo z liczbą anomalii do zbadania, bo każda
hipoteza to osobna sesja modelu.

| przebieg | hipotez | znalezisk | koszt | czas |
|---|---|---|---|---|
| workspace 6576039 (pierwszy) | 19 | 11 | 1,71 USD | ~17 min |
| workspace 5610281 (2026-08-11) | 86 | 27 | **7,09 USD** | **62 min** |

Do wywołań monday to nadal ~227 z dziennego limitu klienta (Enterprise ma 25 000,
czyli niecały procent) — collector nie rośnie z liczbą hipotez.

**Czas jest do zoptymalizowania, koszt niekoniecznie.** 62 minuty to ~43 s na
hipotezę i wynika z sekwencyjnego badania; koszt wynika z liczby tokenów i spadnie
tylko przez zwężenie kontekstu albo tańszy model. Jedno i drugie należy do ewaluacji
(etap 4), nie do zgadywania teraz.

Dla porównania: jedno uruchomienie agenta monday to 10–250 kredytów, czyli rząd
wielkości od dziesięciu groszy do kilku złotych.

Koszt czytamy z tego, co raportuje Agent SDK, nie z mnożenia tokenów przez cennik
zaszyty u nas — ten rozjechałby się przy pierwszej zmianie cen. **Od 2026-08-10
zapisujemy też, CZYM run był rozliczony** (`runy.rozliczenie`): przy
`AGENT_ROZLICZENIE=subskrypcja` ta sama liczba jest wyceną teoretyczną, nie fakturą,
i panel to oznacza. Bez tego sumowanie kosztów mieszałoby wydatki z wycenami.

---

## Co produkuje

Dwa dokumenty HTML z jednego przebiegu — otwierają się z dysku, bez internetu,
drukują do PDF. Do tego **działającą aplikację web** (od 2026-08-06): panel
wewnętrzny CXLABS i panel dla klienta, widzący tylko siebie. Klient wchodzi hasłem,
wkleja **swój** klucz API monday, klika „Wygeneruj audyt" i widzi pasek postępu.

Aplikacja przeszła pełną ścieżkę na żywo 2026-08-11: klient odpalił audyt
z panelu, run zebrał dane, agent zbadał 86 hipotez, panel pokazał 27 znalezisk.

**Co ma zespół** (stan na 2026-08-11):

| widok | zawiera |
|---|---|
| **Klienci** (startowy) | tabela wszystkich klientów: audyty, znaleziska, oszczędność, data, dostęp; dodawanie klienta; resety haseł |
| **Audyt klienta** | kafle, sekcje metryk, znaleziska z dowodami, wybór wersji audytu, formularz uruchomienia |
| **Moje konto** | wyłącznie zmiana własnego hasła |

**Dostęp** — hasła generowane, w bazie tylko hash `scrypt`, więc nie da się ich
odczytać, tylko wydać nowe:

- klient dostaje hasło od CXLABS i **nie może zresetować go sam** (nie ma dla niego
  endpointu — nie „ma zablokowany"), bo hasło jest jedyną bramą do jego danych
  osobowych, a bez SSO nie mamy czym potwierdzić, kto o reset prosi;
- zespół zmienia własne hasło w panelu, a gdy je zgubi — **„nie pamiętam hasła"**
  wysyła jednorazowy link na skrzynkę `@cxlabs.digital` (ważny 30 minut);
- reset **nie wylogowuje**: otwarta sesja żyje do 12 h i interfejs to mówi wprost,
  żeby nikt nie uznał, że odciął dostęp. Prawdziwe „odetnij teraz" jest zapisane
  jako otwarta pozycja, nie dorobione po cichu do resetu.

Panel jest responsywny — sprawdzony zrzutami przy 390, 900 i 1440 px.

Panel docelowo **zastępuje raport** jako to, co dostaje klient — decyzja Kuby.
Dokument zostaje eksportem datowanej wersji, bo panel czyta bazę na żywo i nie
ma własności, którą raport miał: nie jest zamrożony.

| | wewnętrzny | klientowy |
|---|---|---|
| znaleziska oznaczone jako wewnętrzne | są | **nie ma** |
| trop rozmowy handlowej przy znalezisku | jest | **nie ma** |
| odrzucone hipotezy, koszt runu, dane odtwarzalności | są | nie ma |
| **nazwiska, kwoty, „czego nie widać"** | są | **są** |

Nazwiska są w OBU wersjach świadomie: raport mówiący „konto 05677b1a jest
martwe" jest niewykonalny, bo klient nie wie, o kogo chodzi.

**Sekcja „czego ten audyt nie widzi" też idzie do obu wersji.** Snapshot z 1
sierpnia ma dwa zastrzeżenia: token bez uprawnień admina widzi tylko część
konta, a statystyki automatyzacji są na poziomie konta, bo filtr po tablicy
w API monday jest zepsuty. Raport, który to ukrywa, sugeruje pokrycie,
którego nie ma — a pierwszy klient, który to sprawdzi, przestanie wierzyć
całej reszcie.

Wygląd z CXLABS Design System: paleta ink + lime, skala 8pt, podwójny szewron,
znak marki. Fontów marki **nie osadzamy w pliku** — licencja Clash Display tego
zabrania, szczegóły niżej.

---

## Granice, których narzędzie nie przekracza

Każda jest **mechanizmem, nie zasadą w dokumencie**. Obrona polega na odebraniu
możliwości, nie na filtrowaniu.

| Granica | Jak wymuszona |
|---|---|
| Agent nie zapisuje niczego | trzy niezależne warstwy odcięcia; w ścieżce kodu narzędzi nie ma jak wysłać zapisu |
| Żadnych nazwisk ani e-maili w kontekście modelu | pseudonimizacja przed wywołaniem; tabela PII bez narzędzia dostępowego |
| Nie schodzimy na poziom itemów | `items_count` to granica; jedyny wyjątek zwraca same liczniki, nigdy treść |
| Finding bez dowodu nie istnieje | walidacja w kodzie, bez wyjątków |
| Kwota bez stawki nie istnieje | walidacja sprawdza, czy run dostał każdą zmienną wzoru |
| Token klienta nigdy w kontekście modelu ani w argv | żyje w konfiguracji procesu |

**Maksymalna szkoda przy wrogiej treści w danych klienta to fałszywe znalezisko
w raporcie** — nie wyciek i nie modyfikacja konta.

---

## Usterki, które wyszły z pomiaru, nie z testów

To najważniejsza część tego dokumentu, bo mówi coś o procesie, a nie o funkcjach.

**Trzy razy zdarzyła się ta sama rzecz: mechanizm był udokumentowany, wyglądał
na działający i nie działał.** Za każdym razem testy były zielone, bo sprawdzały
element w izolacji, a nie to, czy jest PODŁĄCZONY.

1. **Flaga `--read-only` w oficjalnym MCP monday nie blokuje zapisu.** Zmierzone:
   przy włączonej fladze utworzenie tablicy i surowa mutacja **przeszły do API**.
   Ta flaga była w naszej dokumentacji opisana jako guardrail „wymuszony przez
   serwer, nie do obejścia". Zrezygnowaliśmy z MCP w całości.

2. **Nasz własny mechanizm odcinający narzędzia nigdy nie był wołany.** SDK
   ostrzegł o tym wprost przy pierwszym pełnym przebiegu. Test dawał fałszywą
   pewność, bo sprawdzał funkcję, a nie jej podpięcie. Po przepisaniu wyszło od
   razu, że na liście zakazanych brakowało jednego narzędzia — którego agent
   próbował użyć.

3. **Klucz API nie dochodził do modelu.** Biblioteka konfiguracyjna wczytuje
   `.env` do obiektu, a nie do środowiska procesu, więc podproces nie widział
   klucza i uwierzytelniał się loginem subskrypcyjnym. Runy działały, findingi
   wychodziły, koszt się liczył — tylko **zużycia nie było w konsoli API**.
   Wyszło z pytania „dlaczego nie widzę, żeby agent zużywał tokeny".

**Wniosek, który poszedł do architektury:** guardrail, w który się wierzy bez
pomiaru, jest gorszy od braku guardraila, bo zdejmuje czujność. Konstrukcja
sesji agenta jest dziś osobną, testowalną funkcją z czterema testami na samo
podłączenie.

**Czwarta sztuka tej samej klasy, i to trzykrotnie:** trzy pola istniały
w konfiguracji lub schemacie, kod ich nie czytał albo nie zapisywał, a efekt
był cichy — wzór wyceny (wszystkie kwoty wychodziły puste), trop rozmowy
(wersja wewnętrzna nie różniła się od klientowej) i hash promptu (pinowanie
niekompletne). Wszystkie trzy wyszły dopiero, gdy ktoś zapytał o konkret albo
gdy trzeba było napisać, że coś działa.

Do tego dwie rzeczy, które okazały się nieprawdą w naszej własnej dokumentacji
i zostały sprostowane z datą: strony pomocy monday **są** osiągalne (403 brało
się z braku zwykłego nagłówka przeglądarki), a pole `updated_at` na tablicy
zaniża wiek o **do 40 dni** i jest bezużyteczne jako sygnał świeżości.

**Piąta sztuka, przy aplikacji web: klient testowy nie odtwarzał warunku.**
Dwadzieścia testów granic świeciło na zielono, a panel w przeglądarce mówił
„nie ma jeszcze audytu tego konta" — bo `TestClient` obsługuje żądania po kolei,
w jednym wątku, a FastAPI wykonuje endpointy synchroniczne w puli wątków. Front
pyta o kilka endpointów równolegle i to wywracało połączenie z SQLite. Naprawa
odsłoniła drugą warstwę: transakcja, która najpierw czyta, a potem chce pisać,
dostaje `database is locked` **natychmiast**, bo SQLite nie czeka na podniesienie
blokady — czekanie groziłoby zakleszczeniem. `busy_timeout` tam nie działa
i wniosek jest prosty: **ścieżka odczytu nie pisze.** Test regresyjny strzela
16 równoległymi żądaniami, inaczej niczego nie sprawdza.

**Szósta: przy warstwie wizualnej trzeba patrzeć.** Brama logowania siedziała
w kolumnie 464 px zamiast na całym ekranie. Dwie pierwsze poprawki były
nietrafione, bo zgadywałem, który element jest wąski — winowajcą był `#korzen`,
div bez ani jednej reguły CSS. Ani `tsc`, ani `npm run build`, ani żaden test
HTTP tego nie widzi.

To wróciło jeszcze pięć razy: logo w ciemnym kolorze na ciemnym sidebarze, opis
zlepiony z procentem, formularz klucza zajmujący trzecią część szerokości, pasek
wypychający treść za krawędź telefonu i przycisk resetu łamiący się na pięć linii
(„Zreset uj hasło klient a"). **Za każdym razem kod przechodził wszystkie testy.**
Wniosek jest praktyczny, nie filozoficzny: zmiana w warstwie wizualnej kończy się
obejrzeniem zrzutu, a nie zielonym testem.

**Siódma, i to czwarty raz ten sam wzorzec: „wydałem nowe hasło" nie odbierało
starego dostępu.** Ponowne dodanie klienta nie zmieniało hasła — zakładało DRUGIE
konto, a stare hasło nadal wpuszczało, bo logowanie brało pierwszy pasujący wiersz
bez określonej kolejności. Zmierzone: klient `cxlabs` miał dwa aktywne konta i oba
hasła działały.

To dokładnie ta sama klasa co trzy pierwsze przypadki: mechanizm wyglądał na
działający i nie działał. Naprawa poszła w **dwie warstwy** — funkcja resetu
nadpisuje hasło na istniejącym koncie, a unikalny indeks w schemacie zamyka drogę,
którą duplikaty powstawały. Bez indeksu wróciłyby inną drogą.

**Ósma: dwie daty, które nie były datami.** W ścieżce, którą klient odpala audyt
z panelu, `started_at` i `finished_at` dostawały identyfikator runu zamiast
znacznika czasu. Kolumny są typu `TEXT`, więc baza przyjmowała to bez protestu.
Obie usterki przeżyły, bo **żaden run z panelu nie doszedł jeszcze do zapisu** —
ścieżka z terminala miała własny, poprawny kod. Wyszło przy przeglądaniu tego
kodu, nie z testu; potwierdził to pierwszy prawdziwy run z panelu (2026-08-11),
w którym daty są już datami.

---

## Czego narzędzie NIE potrafi i dlaczego

| Brak | Powód |
|---|---|
| **zużycie kredytów AI przez agentów monday** | API tego nie oddaje w żadnej sprawdzonej wersji. Jedyne źródło to panel klienta. Sondujemy trzy wersje API przy każdym runie, żeby wyłapać moment, gdy się pojawi |
| liczba uruchomień automatyzacji per tablica | filtr po tablicy w API monday jest zepsuty; statystyki są na poziomie konta i tak są opisane w raporcie |
| klasa „konto nie wykorzystuje AI" | nieaktywna, bo jej sygnał opierałby się na danych, których API nie oddaje |
| przelicznik kredyt → token | monday go nie publikuje. Tej liczby nie ma i nie będzie |
| publikacja raportu pod adresem URL | przeniesione do etapu 5, gdzie stoi serwer |

**Cena licencji klienta nie jest scrapowalna** — na Enterprise jest negocjowana,
więc wchodzi ręcznie jako parametr runu. Podstawienie ceny z publicznego cennika
dałoby liczbę pewnie brzmiącą i błędną.

---

## Jak audyt daje się powtórzyć

Snapshot jest niemutowalny, a każdy run zapisuje **sześć elementów pinowania**:
model (pełnym identyfikatorem, alias zakazany), wersję rubryki, hash promptu,
wersję collectora, **wersję API monday** i **wersję cennika**.

Hash promptu doszedł 2026-08-05, przy pisaniu tego dokumentu: kolumna istniała
od pierwszej migracji, raport ją pokazywał, specyfikacja wymieniała prompt jako
element pinowania — **a nic jej nie wypełniało.** Była NULL we wszystkich
jedenastu przebiegach. Wyszło, bo sprawdziłem twierdzenie, zanim je tu wpisałem.

Dwa ostatnie doszły z pomiarów, nie z projektu. Wersja API — bo `2026-10` usuwa
wszystkie flagi użytkownika, więc ta sama kwerenda zwróciłaby inne dane, cicho.
Wersja cennika — bo stawki odświeżają się same, więc ta sama kwota policzona
w lipcu i we wrześniu byłaby inna.

Bez tego audyt sprzed trzech miesięcy jest nieodtwarzalny, a etap 4 opiera
całą swoją wartość na przepuszczaniu **tych samych** snapshotów przez nową
rubrykę i nowy prompt.

---

## Stan i co dalej

**Zbudowane i przepuszczone przez prawdziwe konto: 3.1–3.12, plus aplikacja web
poza numeracją etapów.** 807 testów, kontrola typów i lintera przechodzą.

**Etap 5 wykonany 2026-09-01/02 — panel działa pod publicznym adresem.**
`https://audyt.cxlabs.digital`, dwa konta zespołu, dwa runy produkcyjne
(12 i 18 znalezisk, 1,54 i 2,29 USD). Szczyt RAM zmierzony pod obciążeniem:
452 MB przy 1130 MB rezerwy, co domyka O6 — i pokazuje, że pomiar z macOS-a
(280 MB) był zaniżony o 60%.

Wdrożenie wyciągnęło **dziesięć usterek**, z których żadna nie objawiała się
tam, gdzie powstawała: panel wystawiony na globalnym IPv6 z pominięciem
Cloudflare, `ProtectHome` odcinający `uv` od interpretera, baza czytelna dla
wszystkich, `wdroz.sh` restartujący usługę w trakcie analizy. Pełny wykaz
z mechanizmami: `docs/etapy/05-WYKONANE.md`.

Korpus 6 zamrożonych snapshotów gotowy na etap 4 (wymagane 3–5) — szósty doszedł
z drugiego workspace'u, na którym aplikacja przeszła pełną ścieżkę.

### Zostało w etapie 3

- **odhaczenie 3.12** — decyzja człowieka, nie narzędzia
- **front nie ma pozycji w `STATUS.md`** — na twoje wyraźne polecenie. Powstał
  poza numeracją etapów; pozycję wpisuje człowiek albo nie wpisuje wcale

### Czeka na decyzję, nic nie blokuje

1. **Pięć przebiegów wisi w statusie „w toku"** w bazie — artefakty po testach.
   Kod już takich nie tworzy; tych pięciu nie ruszaliśmy, bo to dane audytowe.
2. **Nazwa pliku raportu nie zawiera identyfikatora przebiegu**, więc dwa
   przebiegi z tego samego miesiąca i klienta nadpisują się. Dla produkcji
   poprawne, dla testów niewygodne.
3. **Clash Display nie jest zainstalowany na maszynie deweloperskiej**, więc
   nagłówki w dzisiejszych plikach lecą Avenirem — drugim krojem marki.
4. **Żadna kwota w panelu nie jest jeszcze prawdziwa.** Drugi workspace dał
   27 znalezisk i **ani jednej kwoty**, bo klient nie ma wpisanej stawki licencji —
   rubryka nie wycenia bez stawki, zamiast wymyślać liczbę. Jedyna stawka w bazie
   (100 PLN dla `cxlabs`) jest oznaczona jako testowa, więc **1200 PLN widoczne
   dziś w panelu to poprawne wyliczenie z nieprawdziwej liczby.** To jedna wartość
   do wpisania, nie zmiana w kodzie — ale trzeba wiedzieć, skąd ją brać (O28).
5. **Czas audytu: 62 minuty przy 86 hipotezach** (~43 s na hipotezę), bo hipotezy
   badane są po kolei. Do rozstrzygnięcia w ewaluacji (etap 4): zwężenie kontekstu,
   równoległość, tańszy model dla prostszych klas.
6. **Wysyłka maili nie jest skonfigurowana.** „Nie pamiętam hasła" działa, ale bez
   `SMTP_HOST` link ląduje w logu serwera, nie na skrzynce. Tryb awaryjny jest
   świadomy i głośny (log to mówi), lecz przed wystawieniem panelu trzeba wpisać
   dane SMTP — to konfiguracja, nie kod.

### Ryzyko do rozstrzygnięcia przed wystawieniem panelu

Raport był plikiem na dysku. Panel to **dane osobowe klienta pod adresem URL**.
Część z czterech pytań z **O23** aplikacja już zamknęła: hasła są hashowane
(`scrypt`), sesje wygasają, próby logowania są liczone i logowane, a limit
blokuje też **poprawne** hasło po pięciu nieudanych próbach. Otwarte zostaje to,
co nie jest kwestią kodu: kanał przekazania hasła klientowi, odbieranie dostępu
po zakończeniu relacji, i TLS — bo dziś aplikacja chodzi lokalnie, a nie pod URL-em.

**Reset haseł jest już zamknięty mechanizmem**, nie procedurą: klient nie ma drogi
do resetu, zespół odzyskuje hasło linkiem na firmową skrzynkę, a reset nie odbiera
dostępu natychmiast (sesja żyje do 12 h) — i interfejs mówi to wprost. „Odetnij
dostęp teraz" świadomie **nie istnieje**: zapisane jako otwarta pozycja, żeby nie
dorabiać tego do resetu i nie przerywać pracy przy każdym pomocniczym wydaniu hasła.

**Osobne ryzyko doszło z samym przepływem:** klient wkleja swój klucz API monday,
a klucz admina monday **nie jest read-only** — kto go ma, może usunąć każdą
tablicę. Nie zapisujemy go nigdzie (zmierzone, nie założone — patrz niżej),
formularz mówi o tym wprost i sugeruje unieważnienie klucza po audycie. Właściwym
rozwiązaniem jest OAuth z ograniczonym zakresem i to jest **warunek przed
wystawieniem panelu poza relację doradczą** — aneks do D11, granice pamięci
procesu w O25.

### Ryzyko do rozstrzygnięcia przed Marketplace

Stawki publiczne pobiera scraper ze stron monday, bo **cennika nie ma w API**.
Wewnętrzny skrypt odpalany raz na miesiąc to inny profil ryzyka niż komponent
opublikowanego produktu. Do wersji wewnętrznej jest w porządku; przed publikacją
trzeba zapytać monday o źródło maszynowe albo przejść na ręczne wprowadzanie
stawek — obsługa tego już jest w schemacie.

---

## Gdzie co jest

| Plik | Co zawiera |
|---|---|
| [`ZBUDOWANE.md`](ZBUDOWANE.md) | stan techniczny: moduły, detektory, narzędzia, pomiary |
| [`ARCHITEKTURA.md`](ARCHITEKTURA.md) | 14 decyzji z uzasadnieniami i warunkami unieważnienia |
| [`OTWARTE.md`](OTWARTE.md) | 22 niepewności z tym, co zmierzone, a co założone |
| [`CENNIK_AI.md`](CENNIK_AI.md) | metodologia stawek; liczby obowiązujące są w bazie |
| [`etapy/`](etapy/) | specyfikacje siedmiu etapów cyklu |
| [`../STATUS.md`](../STATUS.md) | postęp funkcja po funkcji; **należy do człowieka** |
