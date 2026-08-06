# monday.com Account Audit — podsumowanie stanu

> **Dla kogo:** osoba nadzorująca projekt. Pięć minut czytania, bez kodu.
> **Stan na:** 2026-08-05. Etap 3 (Build) zbudowany, czeka na odhaczenie 3.12.
> Doszła makieta frontu (D15) — poza numeracją etapu 3.
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

**Jeden audyt to ~1,71 USD za analizę** plus ~227 wywołań z dziennego limitu
klienta (Enterprise ma 25 000, więc niecały procent).

Dla porównania: jedno uruchomienie agenta monday to 10–250 kredytów, czyli
rząd wielkości od dziesięciu groszy do kilku złotych. **Nasz audyt kosztuje
tyle, ile 2–17 uruchomień ich agenta.**

Koszt czytamy z tego, co raportuje Agent SDK, nie z mnożenia tokenów przez
cennik zaszyty u nas — ten rozjechałby się przy pierwszej zmianie cen.

---

## Co produkuje

Dwa dokumenty HTML z jednego przebiegu — otwierają się z dysku, bez internetu,
drukują do PDF. Do tego **działającą aplikację web** (od 2026-08-06): panel
wewnętrzny CXLABS z drop-downem po klientach i panel dla klienta, widzący tylko
siebie. Klient wchodzi hasłem, wkleja **swój** klucz API monday, klika „Wygeneruj
audyt" i widzi pasek postępu — audyt trwa około kwadransa.

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
HTTP tego nie widzi. To trzeci raz w tym projekcie, gdy usterkę wizualną łapie
dopiero obejrzenie zrzutu.

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
poza numeracją etapów.** 553 testy, kontrola typów i lintera przechodzą.

Korpus 5 zamrożonych snapshotów gotowy na etap 4 (wymagane 3–5).

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
4. **Pełny przebieg ze stawką licencji** dałby 11 znalezisk **z kwotami**
   w jednym raporcie, zamiast dzisiejszego „11 bez kwot" albo „2 z kwotami".
   ~1,7 USD.

### Ryzyko do rozstrzygnięcia przed wystawieniem panelu

Raport był plikiem na dysku. Panel to **dane osobowe klienta pod adresem URL**.
Część z czterech pytań z **O23** aplikacja już zamknęła: hasła są hashowane
(`scrypt`), sesje wygasają, próby logowania są liczone i logowane, a limit
blokuje też **poprawne** hasło po pięciu nieudanych próbach. Otwarte zostaje to,
co nie jest kwestią kodu: kanał przekazania hasła klientowi, odbieranie dostępu
po zakończeniu relacji, i TLS — bo dziś aplikacja chodzi lokalnie, a nie pod URL-em.

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
