# Plan: audyt monday.com w nowej, uproszczonej postaci

**Cel:** podanie klucza API monday zwraca raport HTML i PDF opisujący konto —
inwentarz, typy workspace'ów, aktywność ludzi i agentów, uwagi krytyczne —
z szacowanym kosztem runu agenta w dolarach (znalezisk nie wyceniamy —
decyzja Kuby z 2026-09-23).

**Poza zakresem** (świadomie, nie z zapomnienia):

- **konta, logowanie, sesje, hasła** — wchodzą do innego systemu, z inną bazą;
  audyt dostaje tożsamość z zewnątrz i nie ma własnej,
- **panel zespołu**: lista klientów, przełączanie kontekstu, dwie role,
- **wybór zakresu audytu** — skanujemy zawsze całe konto, niezależnie od jego
  rozmiaru (decyzja 2026-09-21). Wypada z tym dwufazowa zgoda na koszt,
  widełki liczone ze snapshotu, flagi przy tablicach i podłoga kosztu,
- **rubryka z wagami, wysiłkiem i pewnością** oraz wycena w złotówkach —
  zastępuje je jedna kategoria „uwagi krytyczne", bez wyceny (2026-09-23),
- **MCP monday** — flaga `--read-only` nie działa (sprawdzone), zakaz zostaje,
- **harmonogram i cykliczność** — audyt jest odpalany na żądanie,
- **czat z agentem.**

Granice, które **zostają** mimo uproszczenia: agent nie ma narzędzia
zapisującego, dane osobowe nie wchodzą do kontekstu modelu, a uwaga krytyczna
bez dowodu nie przechodzi walidacji.

## Fazy

- [x] **1. Pomiary API** — cztery zapytania rozstrzygające, co w ogóle da się
  pobrać; wynik każdego wpisany do `docs/OTWARTE.md` jako `[DISCOVERY] ✅/❌`.
  Idzie pierwsza, bo trzy punkty wytycznych stoją dziś na „powinno się dać".
  Mierzymy na koncie CXLABS, nie klienta.
  1. czy `agents` oddaje już dane (sonda istnieje, chodzi przy każdym audycie),
  2. czy monday oddaje liczbę automatyzacji per tablica i ich właściciela,
  3. czy `Workspace` niesie jakiekolwiek pole o produkcie, czy `kind` to sama
     prywatność,
  4. ile kosztuje w wywołaniach i complexity zejście na itemy.
  - Ryzyko: **koszt itemów wobec limitu dziennego klienta** (1 000 free /
    10 000 pro / 25 000 enterprise, przerwanie przy 50%). Dziś cały audyt to
    ~132 wywołania. Jeśli konto CRM z dziesiątkami tysięcy leadów nie mieści
    się w limicie, sampling trzeba zaprojektować w fazie 3, a nie odkryć na
    koncie klienta.

Faza 2 podzielona 2026-09-22 na 2a i 2b: kafelki i tabela tablic mają różny
koszt i różne źródła, a sklejone dawałyby jeden wynik dopiero na końcu obu.

- [x] **2a. Sześć kafelków** — tani przekrój z punktu 2 wytycznych: workspace'y
  (z typem produktu), tablice, użytkownicy, goście, agenci AI, licencja.
  Bez itemów, bez modelu, bez danych osobowych — zapytanie o użytkowników
  świadomie nie pobiera `name` ani `email`, bo najtańszym sposobem na
  niewyciekanie danych jest ich nie pobrać.
  - Ryzyko było: „liczba agentów AI" to co innego niż agenci z API `agents`
    (O20). Rozstrzygnięte — kafelek liczy **konta agentowe** wszystkich trzech
    rodzajów (O44), a nie agentów z nieprzypiętej wersji API.

- [x] **2b. Tabela tablic i role** — punkt 4 wytycznych i reszta punktu 5:
  automatyzacje per tablica, tablice wg rodzaju, średnia userów i gości na
  tablicę, ostatnia aktywność. `owners` i `subscribers` tablicy są już zbierane,
  więc userzy i goście per tablica są policzalne bez nowego zapytania.
  - Ryzyko **rozstrzygnięte fazą 1**: właściciela automatyzacji w API **nie ma**
    (O42) — ta pozycja wytycznych wypada i trzeba to powiedzieć zamawiającemu.
    Liczba automatyzacji na tablicę jest osiągalna tylko jako „ile ich się
    URUCHOMIŁO" (O41), więc pomija te, które nigdy nie odpaliły — a w audycie
    to właśnie one są najciekawsze. Raport musi tę różnicę nazwać.

- [x] **3. Itemy i agregaty** — dla prawdziwego konta wychodzą liczby z punktu 3:
  leady, szanse sprzedaży, tickety, przyrost dzienny i zamknięcia dziennie.
  Zdjęcie zakazu D5 (`items_count` jako granica) jest tu świadome i zapisane.
  - Ryzyko: **dane osobowe osób trzecich.** Item niesie imię, nazwisko, telefon
    i mail leada — czyli klienta naszego klienta. Dzisiejsza pseudonimizacja
    chroni użytkowników konta, nie treść itemów. Granica do utrzymania: itemy
    czytamy, ale do modelu idą wyłącznie agregaty, nigdy treść.
  - Ryzyko drugie, wynikłe z decyzji o pełnym skanie: **sampling przestaje być
    opcją.** Skoro każde konto skanujemy w całości, to właśnie tutaj musi
    powstać reguła, która mieści pełny skan w budżecie wywołań klienta — a gdy
    się nie mieści, raport ma **powiedzieć wprost, czego nie objął**, zamiast
    milczeć. Do tego służy istniejące pole `zastrzezenia`.

- [x] **4. Langfuse z maskowaniem PII** — trace'y wywołań modelu trafiają do
  Langfuse, a przed wysłaniem przechodzą przez warstwę maskującą: zamiast maila
  w trace widać `[E-MAIL]`, zamiast telefonu `[TELEFON]`, i tak dalej.
  Idzie **przed** analizą, bo faza 5 jest pierwszą, w której model pracuje na
  prawdziwych danych — a obserwowalność dostawiona po fakcie nie pokaże tego,
  co się działo, gdy była potrzebna najbardziej.

  **To jest świadome cofnięcie D10** („obserwowalność własna, nie Langfuse").
  Tamta decyzja stała na trzech nogach i dwie się przewróciły: ClickHouse nie
  mieścił się w RAM-ie Mikrusa, a wdrożenie idzie teraz do portalu; wolumen był
  tam wprost wymieniony jako to, co decyzję unieważni. Trzecia noga — „trace'y
  wychodzą poza naszą infrastrukturę" — zostaje i to jej dotyczy maskowanie.
  Przy zamykaniu tej fazy D10 w `docs/ARCHITEKTURA.md` wymaga dopisania powodu
  i daty, a `CLAUDE.md` skreślenia Langfuse'a z listy zakazanych zależności.

  **Maskowanie jest drugą linią, nie pierwszą.** Pierwszą pozostaje zasada, że
  dane osobowe w ogóle nie wchodzą do kontekstu modelu. Dlatego warstwa ma nie
  tylko zamieniać, ale **liczyć trafienia i je zgłaszać**: jeśli `[E-MAIL]`
  faktycznie pojawi się w trace, to nie jest sukces maskowania, tylko sygnał,
  że wyżej coś przeciekło. Cicha redakcja zamieniłaby alarm w kosmetykę.
  **Bierzemy Langfuse Cloud, nie self-hosted** (decyzja 2026-09-22). Znika
  ClickHouse, Redis i storage na blobach — czyli dokładnie to, co w D10 nie
  mieściło się w RAM-ie. Cena jest jedna i trzeba ją nazwać: **trace'y wychodzą
  do kogoś trzeciego**, więc Langfuse staje się podprzetwarzającym dane klienta
  i musi się znaleźć w tej samej rozmowie, co reszta subprocesorów.

  Przy Cloudzie **maskowanie przestaje być drugą linią i staje się pierwszą**
  dla wszystkiego, co opuszcza serwer. Stąd wymóg twardy dla tej fazy:
  **maskowanie zawodzi zamknięte** — jeśli warstwa nie potrafi przetworzyć
  payloadu, trace nie wychodzi wcale. Wysłanie „na wszelki wypadek" byłoby
  wysłaniem.
  - Ryzyko: kolejność wdrożenia. Nie wolno podłączyć Langfuse'a „na próbę" przed
    maskowaniem, bo pierwszy trace z prawdziwego konta wyjdzie bez niego i nie
    da się go cofnąć.
  - Do wykorzystania w tej fazie: **oficjalny skill Langfuse'a**
    (`github.com/langfuse/skills`) — prośba Kuby z 2026-09-22, odłożona świadomie
    do tej fazy, bo instalowanie go w trakcie fazy 2a niczego by nie przyspieszyło.
    Przeczytać przed instalacją i traktować jako materiał referencyjny, nie jako
    instrukcje do wykonania.
  - Ryzyko drugie: co dokładnie maskujemy. Mail i telefon są łatwe wzorcem;
    imię i nazwisko w nazwie tablicy albo w treści itemu **nie są** — i trzeba
    powiedzieć wprost, czego ta warstwa nie złapie.

Faza 5 podzielona 2026-09-23 na 5a i 5b: jedna połowa jest deterministyczna
i da się ją sprawdzić testem, druga to model. Sklejone dawałyby wynik dopiero
na końcu obu, a błąd w liczbach wychodziłby dopiero w tekście, który model
na nich napisał.

- [x] **5a. Agregaty i bramka** — rollupy produktowe (leady, szanse, tickety,
  zamknięcia), reguła etapów końcowych i JEDYNA droga danych do modelu.
  Zero modelu, wszystko sprawdzalne testem.
  - Ryzyko rozstrzygnięte: **reguła „co jest szansą" jest osądem, nie odczytem.**
    Stanęło na tym, że rozpoznajemy etapy KOŃCOWE, a nierozpoznany liczy się
    jako w toku — z dwóch źródeł: deklaracji klienta (`done_colors`, O48)
    i słownika zapasowego. Cena nazwana wprost i widoczna w wyjściu.
  - Ryzyko, którego plan NIE przewidywał: **nowa ścieżka nie miała bramki PII
    w ogóle.** Stara ma ją w `przebieg.py`; nowa szła do modelu bez niczego.
    Zamknięte przez `wejscie_analizy.py`, ale z dziurą na nazwiska (O50).

- [x] **5b. Analiza modelem: uwagi krytyczne** — zastąpienie rubryki 13 klas
  jedną kategorią i szacowany koszt runu. Każde stwierdzenie z dowodem.
  (Pierwotnie także wycena w dolarach i przypadki użycia agentów — pierwsza
  odpadła decyzją 2026-09-23, druga wyniesiona, bo blokuje ją API, O20.)

  **Zakres zawężony przez pomiary, nie przez decyzję.** Sugestia produktu
  z nomenklatury **odpada**: faza 1 zawęziła ją do workspace'ów bez ustawionego
  `account_product` (O40), a pomiar pokazał, że na CXLABS takich nie ma ani
  jednego ze 136. Budowanie jej teraz byłoby pisaniem na wyobrażonym kliencie.
  Wraca, gdy trafi się konto, które jej potrzebuje.

  **ROZSTRZYGNIĘTE z Kubą 2026-09-23:**
  1. **detektory zostają** — deterministycznie wykrywają, model tylko orzeka.
     Z rubryki umiera metadana OCENIAJĄCA (waga, wysiłek, pewność, wycena),
     a katalog wykrywania (progi, budżety, `rola_agenta`) zostaje,
  2. **jedna sesja na całe konto** zamiast sesji na hipotezę,
  3. **nie wyceniamy znalezisk** — tylko szacowany koszt runu agenta,
  4. **stara ścieżka zostaje na razie** równolegle, nietknięta.

  Dwa potoki współistnieją celowo: nowy (inwentarz → itemy → zestawienia)
  to tani pierwszy ekran, stary (snapshot → detektory → model) — głęboka
  analiza, bo detektory potrzebują zamrożonego snapshotu.

  **Stan kroków:**
  - [x] **5b-1** — kształt uwagi krytycznej (`uwagi.py`): cztery pola zamiast
    dziewięciu, `klasa_id` jako POCHODZENIE, reguła dowodu współdzielona
    z `kontrakt.sprawdz_dowod`, nie skopiowana.
  - [x] **5b-2** — jedna sesja (`analiza.py`, `PROMPT_ANALIZY.md`). Pierwszy
    prawdziwy run miał 41% odrzuceń, bo przeniosłem hydraulikę starej ścieżki
    bez jej wiedzy: szablony i `dowod_wymagany`. Po poprawce: 24/24
    rozstrzygnięte, 0% odrzuceń, 0,63 USD (`analiza-20260923T085706Z`).
  - [x] **5b-3** — szacowany koszt runu (`koszt.py`) i spięcie w `cli_analiza`.
    Pierwsza wersja liczyła tokeny z długości tekstu i zaniżała 12× (0,05 wobec
    0,63), bo sesja z narzędziami czyta kontekst na nowo przy każdym obrocie.
    Teraz: koszt na hipotezę z historii analiz, celowo zawyżany w małych runach.
  - [x] Langfuse dla nowej ścieżki — trace sesji, także z runu, który padł;
    obraz konta idzie jako hasz, nie treść.
  - [x] **definicje klas w zadaniu** (`analiza.definicje_klas`). Plan mówił
    „próg `AUTOMATION_DEAD` 0,05 łapie automatyzacje, które w większości
    działają" — i to była **zła diagnoza**. Próg niczego nie odcina: sygnał to
    `failure > 0 OR … OR udział > 0,05`, a udział powyżej zera wymaga
    `failure > 0`. Prawdziwa przyczyna: nowa ścieżka podawała modelowi samo
    `klasa_id`, bez `rola_agenta` i `warunki_odrzucenia`, więc model rozumiał
    klasę z nazwy — i na `analiza-20260923T103802Z` odrzucił trzy
    automatyzacje „bo nie jest martwa", choć rubryka definiuje klasę jako
    „uruchamia się i nie działa". Trzecia regresja tej samej klasy co
    szablony i `dowod_wymagany`: hydraulika przeniesiona, wiedza zgubiona.
  - [x] rerun `analiza-20260923T105716Z`: definicje dochodzą (żaden powód nie
    brzmi już „nie jest martwa"), ale model zastosował warunek „dane wejściowe
    od człowieka" do błędów bloków AI — także do automatyzacji z 0 sukcesów.
    Przyjętych `AUTOMATION_DEAD` 5 zamiast 11. „Priorytetowo" nie pojawiło się
    w żadnym z dwóch runów; jedyne stopniujące zdanie jest w NASZYM szablonie
    `ZOMBIE_ACCOUNT` dla adminów.
  - [x] **przebieg automatyzacji z błędami** (O52, `automatyzacje.przebieg_automatyzacji`):
    historia z 365 dni i ostatnie nieudane uruchomienie krok po kroku — trigger,
    padający krok, błąd. W przypiętej `2026-07`, ~2 wywołania na automatyzację,
    sufit 30. Na żywo CXLABS: 32 wywołania, 14 z 14. Obraz: bloki AI na
    „item created" z ZEREM sukcesów w roku (40, 49, 16 błędów) obok
    automatyzacji, które przy dobrym wejściu działają (10/3, 5/1, 4/1).
  - [x] rerun z przebiegiem w faktach, `analiza-20260923T115710Z`: 11 z 14
    `AUTOMATION_DEAD` przyjętych — dokładnie te z zerem sukcesów w roku, każda
    z triggerem i padającym krokiem w opisie („every time period → create
    item"). Trzy pominięte to te, które przy dobrym wejściu działają (10/3,
    5/1, 4/1), z powodem „dane wejściowe od człowieka". **Zmiana warunku
    w rubryce okazała się niepotrzebna** — modelowi brakowało faktów, nie
    reguły. 0,47 USD, 0 trafień maskowania.
  - **Wyniesione z 5b** (decyzja Kuby 2026-09-23): **przypadki użycia
    agentów.** Blokuje je API, nie kod — `agent_runs` nie istnieje w żadnej
    wersji (O20), `agents` działa dopiero w nieprzypiętej `2027-01`. Zostaje
    liczba kont agentowych (kafelek z 2a). Wraca, gdy przypniemy wersję API,
    która oddaje dane agentów.
  - Ryzyko drugie: **zakaz z `CLAUDE.md` o dowodzie zostaje w mocy.** Rubryka
    znika, ale „finding bez pola `dowod` nie przechodzi walidacji" jest zakazem
    twardym, nie elementem rubryki. Cokolwiek zastąpi `kontrakt.py`, musi to
    egzekwować.

- [ ] **5c. Minimalne przechowywanie** — po runie na dysku nie zostaje nic, co
  dotyczy konkretnej osoby. Decyzja Kuby z 2026-09-23: ograniczyć przechowywanie
  do minimum, żeby nie trzymać niczyich danych i nie musieć ich prawnie
  zabezpieczać. „5c", a nie nowa szóstka, żeby nie przenumerowywać faz, do
  których odwołuje się kod.

  **Mechanizm:** cały run nowej ścieżki idzie na bazie SQLite W PAMIĘCI
  (`:memory:`). Snapshot i tabela `osoby_mapowanie` (prawdziwe imiona, nazwiska
  i maile) istnieją tylko przez czas procesu. Detektory zostają bez zmian, bo
  dalej robią SQL — tylko po bazie w RAM-ie. Sprawdzone: collector
  (`wykonaj_run`), detektory i narzędzia agenta biorą połączenie parametrem.

  **Co zostaje na dysku — i nic poza tym:**
  - metadane runu: data, `client_id`, model, hasze promptu i obrazu konta,
  - koszt, tokeny, czas (zasilają estymator),
  - liczby: uwag w każdej klasie, pominiętych, odrzuconych na walidacji,
  - **uwagi krytyczne PO MASKOWANIU** (prośba Kuby z 2026-09-23): opis,
    rekomendacja i dowód z liczbami, ale identyfikator osoby zastąpiony
    znacznikiem, lista identyfikatorów — liczbą, a dane kontaktowe tak jak
    w trace'ach.

  **Co NIE zostaje:** snapshot, tabela mapowania, surowa odpowiedź modelu,
  dowody z pseudonimami, raport w plikach. Raport generujemy i oddajemy.

  - Ryzyko: **pseudonim to nadal dana osobowa.** Hasz liczony stałą solą da się
    odwrócić, mając dostęp do konta. Dlatego maskowanie przed zapisem musi
    usuwać pseudonimy, a nie tylko maile i telefony — i test po pełnym runie
    ma sprawdzać, że w trwałej bazie nie ma ani jednego.
  - Ryzyko drugie: **daty aktywności konkretnego konta** („ostatnio aktywny
    2026-06-09") są quasi-identyfikatorem na koncie z kilkunastoma
    użytkownikami. Na start zostają jako liczby dni; do potwierdzenia.
  - Ryzyko trzecie: **nazwiska w nazwach tablic** (O50) — maskowanie wzorcem
    ich nie złapie, a uwaga może wymienić tablicę z nazwy.
  - Otwarte: **obraz konta z pliku** (`--wejscie`) to plik z nazwami tablic,
    który operator trzyma na dysku. Minimum znaczy liczyć go w tym samym
    procesie — za cenę ~1100 wywołań monday przy każdej analizie.
  - Otwarte: **stara ścieżka na serwerze** dalej zapisuje snapshoty i tabele
    mapowania, a kopie w `/var/backups` trzymają wszystko, co już zebrano.
    Sprzątanie jest nieodwracalne — tylko na wyraźną decyzję Kuby.
  - Przy zamykaniu: D7 w `docs/ARCHITEKTURA.md` (snapshot trwały
    i niemutowalny) wymaga zapisu, co z niej cofamy i dlaczego.

  **Stan kroków (2026-09-23):**
  - [x] krok 1 — `przechowanie.py` i migracja 014 (`uwagi_zapisane`,
    `statystyki_runow`): pseudonim → `[OSOBA]`, lista → `[OSOBY: n]`, data →
    liczba dni przed runem, statystyki = same liczby. Bramka przerywa zapis,
    gdy po maskowaniu został pseudonim albo adres.
  - [x] krok 2 — `cli_analiza --zakres …` zbiera do SQLite w pamięci; na dysk
    idzie tylko to, co przeszło przez `przechowanie.py`. Test przegląda CAŁĄ
    trwałą bazę po runie. Na żywo (collector, bez modelu): snapshotów 1 → 1,
    mapowania 100 → 100.
  - [x] lokalne dane z sesji 2026-09-22/23 (snapshot CXLABS, 100 wierszy
    mapowania, odpowiedzi modelu, pliki robocze) przeniesione do Kosza —
    nie usunięte trwale; opróżnienie Kosza po stronie Kuby.
  - [x] code review 19 commitów przed pełnym runem — trzy blokery, dwa z nich
    w tej fazie: klucze słowników (nazwy grup, etykiety) omijały maskowanie
    i trafiały do `statystyki_runow` wprost. Poprawione w `36c21a3`.
  - [x] pełna analiza z modelem w trybie pamięci, `analiza-20260923T103802Z`
    (workspace 7465500): 20 uwag zapisanych zamaskowanych, 0 snapshotów,
    0 wierszy mapowania, w treści trwałej bazy ani pseudonimu, ani adresu,
    ani daty. 39 wywołań monday, 0,30 USD. Trace w Langfuse: 0 trafień
    maskowania, obraz konta jako hasz. `statystyki_runow` na żywo NIESPRAWDZONE
    — run szedł bez `--wejscie`, a obraz konta to ~1100 wywołań; pokrywa to
    test `test_tresc_klienta_w_kluczach_nie_zostaje_na_dysku`,
  - [x] **raport z nazwiskami — wariant A z D** (decyzja Kuby 2026-09-23).
    A: `cli_analiza --raport PLIK` składa raport Z NAZWISKAMI w trakcie runu,
    póki mapowanie żyje w bazie w pamięci; plik od razu z prawami 600, kopii
    na serwerze nie ma, bez flagi raport nie powstaje wcale. D: zapis
    w `uwagi_zapisane` zostaje zamaskowany, ale z atrybutami, po których
    klient odnajdzie konto (rodzaj, dni bez aktywności, plan). Wygląd
    tymczasowy — grupowanie i PDF to faza 7. Na żywo jeszcze NIE uruchomione.
  - [x] Langfuse bez pseudonimów: `[OSOBA]` / `[IMIĘ] [NAZWISKO]` (`b57da37`).
  - [x] **panel: nowe audyty WSTRZYMANE** (decyzja Kuby 2026-09-23, droga 1
    z trzech). Przełączenie panelu na tryb pamięci to w praktyce faza 6 —
    pulpit, API i front stoją na snapshotach i `findings` — więc zamiast
    przełączać stary panel i zaraz go przepisywać, `AUDYTY_WSTRZYMANE` w
    `web/api.py`: przycisk dostaje powód, `POST /api/audyt` i `/zgoda` dają
    503. Dotychczasowe audyty da się przeglądać. Stała w kodzie, nie zmienna
    środowiskowa — włączenie z powrotem przez commit i review. Na produkcji
    dopiero po wdrożeniu.
  - [x] **inwentarz danych na serwerze** (odczyt, 2026-09-23): `/var/backups`
    to w większości kopie systemu (dpkg, apt) — nasz jest tylko
    `monday-audit/`, 14 dziennych kopii bazy (10–23.09). Baza: jeden klient
    (`cxlabs`), 3 snapshoty, 101 wierszy mapowania (użytkownicy konta monday
    CXLABS, w tym 13 gości — mogą być spoza firmy), 6 runów, 47 findingów;
    osobno dane logowania do panelu (5 kont). Raportów w plikach brak.
    Serwer stoi na 12 migracjach — kod sprzed faz 4–5c.
  - [ ] dane na serwerze i w `/var/backups` — **zostają na razie, „w razie w"**
    (decyzja Kuby 2026-09-23). Nowe nie dochodzą, bo audyty z panelu są
    wstrzymane i wdrożone (`decb5ea`); kopie dzienne dalej rotują co 14 dni,
    ale kopiują tę samą, już niezmienianą zawartość. Sprzątanie tylko na
    wyraźną decyzję — do rozstrzygnięcia: metadane runów zostają czy nie,
  - [ ] stara ścieżka na serwerze dalej zapisuje snapshoty,
  - [x] D7 w `docs/ARCHITEKTURA.md` — sekcja „Faza 5c: snapshot przestaje być
    trwały w nowej ścieżce".

  Faza zostaje NIEODHACZONA: lokalnie rezultat jest osiągnięty, ale na serwerze
  stara ścieżka dalej zapisuje snapshoty i mapowanie — a to są dwie pozycje do
  decyzji Kuby, nie do zamknięcia kodem.

- [x] **6. Przepływ: dwa kroki, jedno kliknięcie między nimi** — user story
  z 2026-09-21. Użytkownik jest już zalogowany w portalu, a klucz monday leży
  w tamtej bazie, więc nigdzie go nie wpisuje.

  1. **„Analizuj moje środowisko"** → sześć kafelków z punktu 2 wytycznych:
     workspace'y, tablice, użytkownicy, goście, agenci AI, rodzaj licencji.
     Krok **tani**: konto, workspace'y, tablice i użytkownicy to kilka zapytań,
     zero itemów, zero modelu. Ekran startowy dla **każdego** konta, nie tylko
     dużego.
  2. **„Chcę wykonać analizę"** → pełny skan całego konta plus model, na końcu
     raport.

  **Wariant A — pakiet dla portalu** (decyzja Kuby 2026-09-23). Zgodnie
  z notatką z 2026-09-17 kod audytu idzie do repo portalu jako importowany
  pakiet, a ekrany, sesje i magazyn klucza pisze portal. W tym repo powstają
  więc DWIE FUNKCJE WEJŚCIOWE o jasnym kontrakcie, obie w trybie pamięci
  (5c), bez ekranów:

  - `przeglad_konta(klucz)` → sześć kafelków (krok 1),
  - `analiza_konta(klucz, …)` → uwagi krytyczne, zapis minimalny
    i raport z nazwiskami zwrócony w pamięci, nie zapisany (krok 2).

  Panel na Mikrusie zostaje z wstrzymanymi audytami — portal go zastąpi.

  **Wariant A w tej postaci nie zadziała** (ustalone 2026-09-25 z informacji od
  zespołu portalu). Portal nie ma backendu, który mógłby zaimportować pakiet:
  to statyczny JS bez frameworka, nginx serwujący pliki i webhooki Make jako
  cała logika, a bazą są tablice monday. Nie ma też TypeScriptu, więc typy TS
  nic nie dają. Pakiet zostaje bez zmian — brakuje PROCESU, który go uruchomi.
  Drogi i pytania: `docs/NOTATKA_PORTAL_DECYZJE.md` §8.

  **Kroki:**
  - [x] **6-1** — `usluga.przeglad_konta`: sześć kafelków (`Kafelek` ze
    stałym `klucz`, `wartosc`, `szczegoly`) pod publicznym kontraktem, bez bazy
    i bez modelu; błędy konta jako `UslugaError` bez treści odpowiedzi API.
    Na żywo CXLABS: 36 wywołań, liczby 1:1 z `cli_inwentarz` (138 workspace'ów,
    1316 tablic, 19 użytkowników, 13 gości, 41 agentów, enterprise).
    `cli_inwentarz` NIE przepięty — to narzędzie rozszerzone (tablice, itemy,
    obraz dla modelu), a nie same kafelki. Nazwy workspace'ów świadomie poza
    kontraktem (O50); pogłębienie to faza 7.
  - [x] **6-2** — `usluga.analiza_konta` (i `analizuj_snapshot` dla snapshotów
    sprzed 5c): całe sedno przeniesione z `cli_analiza.uruchom`, CLI jest
    cienką nakładką. Kontrakt `WynikAnalizy`: `uwagi` zamaskowane (wolno
    pokazać i przechować), `raport_html` z nazwiskami poza `do_json`,
    `blad_zapisu` zamiast wyjątku (zapis, który padł, nie zabiera wyniku),
    `AnalizaError` z surową odpowiedzią w pamięci, `przed_sesja` — szacunek
    przed modelem. Sól, `client_id`, klucze i baza parametrami. **Przy okazji
    naprawiona usterka sprzed 6-2:** szablony doklejane do `uwagi` przed
    walidacją maskowały odpowiedź modelu bez struktury jako „zero uwag".
    Na żywo `--tylko-szacunek` przez pakiet: 24 hipotezy, 0 runów, 0 snapshotów,
    0 mapowań w bazie.
  - [x] **6-3** — szacunek przed krokiem 2 (wywołania monday i USD) liczony
    z danych kroku 1 — zamiast progu „pięciu workspace'ów". `szacuj_analize`:
    na pełnym koncie CXLABS 332 wywołania wobec zmierzonych 334. Pomiar
    ujawnił dwie rzeczy: detektory stały godzinami na pełnym koncie (naprawione,
    0,2 s) i 6224 hipotezy, z czego 5707 par DUPLICATE_STRUCTURE (~245 USD) —
    założenie 5b „jedna sesja na całe konto" nie trzymało się. Stąd 6-3b.
  - [x] **6-3b** — hipotez tyle, ile jedna sesja uniesie (decyzja Kuby
    2026-09-24). DUPLICATE_STRUCTURE: grupa zamiast pary (spójna składowa,
    rubryka 0.5). Sufit 20 najsilniejszych na klasę przed modelem, reszta
    w zastrzeżeniach raportu z liczbą i w `poza_sufitem`. Pełne konto po
    zmianie: 615 hipotez (96 grup zamiast 5707 par; największa ma 91 tablic),
    do modelu 99, z szablonu 8, szacunek 3,91 USD. Sufit przyciął BOARD_OVERCOMPLEX
    (20 z 401), DUPLICATE_STRUCTURE (20 z 96), BOARD_NO_OWNER (20 z 65),
    BOARD_GHOST (20 z 26). Sesja na pełnym koncie (2026-09-24): 2,34 USD przy
    szacunku 3,91, 17 min, 22 uwagi przyjęte. Ujawniła lukę PII (samo imię
    w nazwie tablicy — `7d687ec`) i cztery usterki, poprawione w jednym
    commicie: BOARD_GHOST bez `wpisow_w_oknie` w rubryce (16/16 odrzuceń),
    brak klienta monday w narzędziach pakietu (20/20 BOARD_OVERCOMPLEX),
    tablice bez właściciela poza próbką logów (20/20 BOARD_NO_OWNER),
    GUEST_SPRAWL z jawnym „nie zmierzone" (zmiana O31). Rubryka 0.6.
    Druga sesja padła na parsowaniu odpowiedzi w kilku blokach (`f428bad`).
    Trzecia (`analiza-20260924T114549Z`): 89 uwag, 2,88 USD, 17 min,
    369 wywołań monday. Po niej budżet narzędzi 30 → 50 i IBAN tylko od
    15 znaków (koniec fałszywego alarmu co run).
  - [~] **6-4** — ~~typy dla portalu generowane z kontraktu i dokument
    wejścia~~ — **przeniesione do wpinania w portal** (decyzja Kuby
    2026-09-25). Nic w fazie 7 od tego nie zależy, a kształt zależy od
    odpowiedzi, których dziś nie ma (gdzie działa usługa, skąd klucz, jak
    sprawdzić użytkownika). Przy wpinaniu treść kroku to: **cienkie API HTTP
    nad `usluga`** (start, status, wynik, raport jednorazowo) z zadaniem w tle
    i odpytywaniem statusu, plus opis kształtu JSON dla frontu w JS.

  Rozdzielenie jest tu mechanizmem, nie ozdobą: pierwszy krok kosztuje
  sekundy i nic nie zużywa, drugi kosztuje minuty i budżet wywołań klienta.
  - Ryzyko: **co się dzieje powyżej pięciu workspace'ów** — historyjka opisuje
    ścieżkę „maks 5". Skanujemy wtedy tak samo wszystko (decyzja 2026-09-21),
    więc różnicą może być co najwyżej ostrzeżenie „to potrwa". Do potwierdzenia.
  - Ryzyko drugie: próg pięciu workspace'ów jest liczbą z decyzji, nie z pomiaru.
    Po fazie 1 może się okazać, że właściwą miarą jest **liczba itemów**: konto
    z trzema workspace'ami i czterdziestoma tysiącami leadów trwa dłużej niż
    osiem pustych.

- [ ] **7. Raport: przebudowa treści, HTML i PDF** — wejście w kafelek pogłębia
  do szczegółów, a sam raport jest **pogrupowany i opisany przez agenta**, nie
  wyliczany zdarzenie po zdarzeniu. Bez kwot przy znaleziskach (2026-09-23).

  **Kształt grupowania — ustalony z Kubą 2026-09-23**, po obejrzeniu
  pierwszego raportu uwag („strasznie nieprzejrzyste"):

  1. **Raport główny = cztery kategorie z wytycznych jako kafelki:**
     Workspace, Tablice, Użytkownicy, Agenci. Na kafelku liczba uwag
     krytycznych w tej kategorii i jedno zdanie agenta o największym problemie.
  2. **Kliknięcie PRZENOSI do osobnego, pogłębionego raportu tej kategorii**
     — osobny widok, nie rozwinięcie treści na tej samej stronie. Tam tabele,
     dowody, nazwiska, rekomendacje.
  3. **Przypisanie klas do kategorii** (propozycja, do potwierdzenia przy
     fazie): Tablice ← `AUTOMATION_*`, `BOARD_*`, `DUPLICATE_STRUCTURE`,
     `PROCESS_BYPASS`; Użytkownicy ← `ZOMBIE_ACCOUNT`, `GUEST_SPRAWL`,
     `PLAN_MISMATCH`, `UZYTKOWNIK_WYGASZONY`, `ENGAGEMENT_DROP`; Agenci ←
     `AI_UNUSED` (dziś zablokowana, O20); Workspace ← rollupy CRM/Service
     z 5a, na razie bez klasy uwag.

  Stan przejściowy do tej fazy: `raport_uwag.html.j2` grupuje po klasie
  (sekcja na klasę, tabela wierszy) — lepsze niż karta na uwagę, ale to nie
  jest docelowy kształt.
  - Ryzyko: **PDF to nowa zależność.** Headless Chrome oznacza powrót Node'a na
    produkcję, czego świadomie unikaliśmy; WeasyPrint to czysty Python za cenę
    bibliotek systemowych (pango, cairo). Decyzja przed fazą, nie w trakcie.

## Dziennik

<!-- Uzupełniany przy zamykaniu faz: data, faza, link do dokumentu
     w `docs/features/`, odchylenia od planu. -->

**2026-09-24 — krok 6-3b zamknięty** (faza 6 trwa, zostaje 6-4). Pełne konto
CXLABS jedną sesją: `analiza-20260924T114549Z` — 629 hipotez, 99 do modelu,
**89 uwag przyjętych, 1 odrzucona walidacją**, 17 odrzuconych przez model
z uzasadnieniem, 2,88 USD przy szacunku 3,91 USD, 17 min, 369 wywołań monday.

Co poszło inaczej, niż zakładał plan:

- **Założenie 5b „jedna sesja na całe konto" padło na pierwszym pełnym
  koncie** — 6224 hipotezy, w tym 5707 par jednej klasy. Uratowały je dwie
  zmiany deterministyczne (grupa zamiast pary, sufit na klasę), nie model.
  Sufit zmienia pokrycie: model nie orzeka o każdej tablicy, a raport musi to
  mówić liczbą — i mówi.
- **Detektory stały godzinami** na pełnym koncie (JSON SQLite bez
  `MATERIALIZED`). Na małych snapshotach tego nie było widać.
- **Pierwsza sesja na pełnym koncie ujawniła lukę PII**: samo imię
  w nazwie tablicy przechodziło do modelu i do Langfuse (`7d687ec`).
  Redakcja po imionach była też O(osób × napisów) — >10 min przy 1000 osobach.
- **Cztery klasy nie domykały się wcale** (BOARD_GHOST, BOARD_OVERCOMPLEX,
  BOARD_NO_OWNER, GUEST_SPRAWL) z czterech różnych powodów, żaden widoczny
  na małym koncie. Ta sama lekcja co w 5b: dopiero prawdziwy run mówi prawdę.
- **O31 zmienione decyzją Kuby**: GUEST_SPRAWL przechodzi z jawnym
  „nie zmierzone" zamiast być odrzucany.
- **Tracing narzędzi w Langfuse** (argumenty, wynik, błąd) dołożony w trakcie,
  na prośbę Kuby. Granica „nic, czego nie mamy u siebie" przestała obowiązywać;
  zapisane w `obserwowalnosc.py`.
- **Otwarte na później:** oś czasu w Langfuse jest płaska (SDK nie przyjmuje
  czasu startu — wysyłka na żywo to przebudowa); BOARD_OVERCOMPLEX to wciąż
  401 hipotez na koncie — sygnał „> 15 kolumn" mało wybiórczy (faza 7).

**2026-09-23 — faza 5b zamknięta.** `uwagi.py`, `analiza.py`,
`PROMPT_ANALIZY.md`, `koszt.py`, `cli_analiza.py`, przebieg automatyzacji
w `automatyzacje.py`. Ostatni run: `analiza-20260923T115710Z` — 24 hipotezy,
20 uwag, 1 odrzucona zgodnie z O31, 0,47 USD.

Co poszło inaczej, niż zakładał plan:

- **Trzy razy ta sama regresja: hydraulika przeniesiona, wiedza zgubiona.**
  Szablony, `dowod_wymagany` i definicje klas — stara ścieżka dawała je
  modelowi od zawsze, nowa za każdym razem zaczynała bez nich. Każdą wykrył
  dopiero prawdziwy run. Wniosek dla dalszych faz: przy przenoszeniu z starej
  ścieżki sprawdzać, co ona podaje modelowi, a nie tylko, jak go woła.
- **„Próg 0,05" był złą diagnozą.** Nigdy niczego nie odcinał. Model odrzucał
  automatyzacje, bo znał samą nazwę klasy, a potem — z definicją — bo nie
  miał faktów o triggerze i padającym kroku. Po dołożeniu przebiegu (O52)
  rozdziela konfigurację od złego wejścia sam; zmiana rubryki niepotrzebna.
- **Wycena odpadła, estymator kosztu runu został** — i dwa razy musiał być
  poprawiany pomiarem: model tokenów zaniżał 12×, a sama historia zaniżałaby
  2×, bo koszt sesji waha się od obrazu konta i narzędzi (0,30–0,63 USD przy
  tych samych 16 hipotezach). Stąd podłoga z pomiaru startowego.
- **Wyniesione:** przypadki użycia agentów (O20). **Otwarte na później:**
  pełny spis automatyzacji z twórcą i konfiguracją czeka na przypięcie
  `2026-10` (O52); zmienność wyniku między runami na tych samych danych.

**2026-09-22 — faza 1 zamknięta.** Wyniki: `docs/OTWARTE.md` O40–O43.
Trzynaście wywołań na koncie CXLABS, plus jedenaście w dwóch dopytaniach.

Co poszło inaczej, niż zakładał plan:

- **Typ workspace'u jest w API.** `Workspace.account_product { id kind }` zwraca
  `core` / `crm` / `service` / `software` (O40). Planowałem to jako wnioskowanie
  z nomenklatury i było to **moje błędne założenie** — faza 5 traci przez to
  jedną pozycję, a analiza nomenklatury zostaje tylko tam, gdzie produkt nie
  jest ustawiony.
- **Właściciela automatyzacji nie da się pobrać** (O42). Pozycja wytycznych
  wypada; trzeba to powiedzieć zamawiającemu, a nie podstawić przybliżenie.
- **Liczba automatyzacji na tablicę tylko jako „ile się uruchomiło"** (O41).
  Automatyzacja, która nigdy nie odpaliła, jest niewidoczna — a to ona jest
  w audycie najciekawsza.
- **Koszt itemów zmierzony na za małej próbce** (O43): najgrubsza tablica na
  CXLABS ma 103 itemy, więc konto nie pokazuje przypadku, o który chodzi.
  Ekstrapolacja: 40 000 leadów ≈ 400 wywołań, czyli 40% dnia na planie `free`.
  Regułę samplingu z fazy 3 trzeba ustalić na prawdziwym koncie CRM.

**2026-09-22 — faza 2a zamknięta.** `inwentarz.py`, `cli_inwentarz.py`, typ
`Inwentarz` w `front/src/api.ts`. Sprawdzone na żywo: CXLABS, 36 wywołań.

Trzy rzeczy wyszły dopiero na pełnym koncie i każda zmieniła kod:

- **136 workspace'ów.** `pobierz_workspace` brało jedną stronę po 100, więc
  kafelek pokazałby `100` i nie powiedziałby, że urwał. Paginacja dopisana —
  przypadek opisany w teście jako hipotetyczny okazał się faktem przy pierwszym
  uruchomieniu.
- **3268 obiektów, z czego 1206 w koszu.** Kafelek pokazuje teraz aktywne
  (2016), a rozbicie po stanach zostaje obok. Suma była prawdziwa i bezużyteczna.
- **Trzy rodzaje kont agentowych, nie jeden** (O44). Przy okazji wyszła usterka
  w działającym kodzie: `pulpit.py` trzymał drugą kopię literału
  `personal_agent_member` i pokazywał cztery konta agentów zewnętrznych
  w zakładce „Ludzie" jako ludzi. Naprawione jednym źródłem prawdy
  (`RODZAJE_AGENTOW` w `osoby.py`).

Odchylenie od planu: faza miała produkować **snapshot**, a produkuje odczyt
na żywo. Tak wychodzi z user story — pierwszy ekran ma odpowiedzieć w sekundy
i nie zakładać runu. Snapshot zostaje tam, gdzie był: przy pełnym audycie.

**2026-09-22 — faza 2b zamknięta.** `przeglad_tablic.py` plus flaga `--tablice`.
Punkt 4 okazał się listą metryk, nie specyfikacją tabeli, więc powstały agregaty
plus krótka lista, a nie tabela na 2000 wierszy.

Trzy rzeczy poszły inaczej, niż zakładał plan:

- **`trigger_events` nie ma stronicowania**, choć przyjmuje `nextPageOffset`:
  każda wartość powyżej zera daje po stronie monday błąd serwera, a strona
  urywa się na 200 (O41). Zamiast stronicować **kroimy okno na tygodnie**,
  a tydzień pełny na dni; dzień dalej pełny trafia do zastrzeżeń z datą.
  Dzięki jawnemu oknu 90 dni wyszły **23 tablice z żywymi automatyzacjami**,
  a nie 7 jak przy domyślnym oknie API.
- **Kafelek „liczba tablic" był zawyżony o połowę.** Z 2017 aktywnych obiektów
  tylko 1315 to `type: board`; reszta to kontenery podelementów, dokumenty
  i obiekty własne. Poprawione w obu miejscach, bo dwie różne liczby o tej samej
  nazwie w jednym wyjściu to ten sam rozjazd, przed którym bronią testy.
- **Gości na tablicach nie widać** (O45): 12 aktywnych gości i zero wystąpień
  w 1000 tablicach. Nie dowód, więc metryka zostaje, ale z zastrzeżeniem, że
  zera nie należy traktować jako zmierzonego.

**2026-09-22 — faza 4 zamknięta.** `maskowanie.py`, `obserwowalnosc.py`,
`wysylka_langfuse.py`, plus wpięcie w `zbadaj_hipotezy`. Trzy kroki,
w kolejności wymuszonej przez plan: warstwa maskująca → odbiorca → wpięcie.

Cztery rzeczy poszły inaczej, niż zakładał plan:

- **Model chodzi w PODPROCESIE, więc nie ma czego opakować.**
  `ClaudeSDKClient` uruchamia CLI osobnym procesem, a `ClaudeAgentOptions` nie
  ma żadnego pola o telemetrii (sprawdzone: 44 pola). Standardowa droga „owiń
  klienta dekoratorem" w tej architekturze **nie istnieje**. Trace składamy
  sami z `WynikHipotezy` — czyli z tego, co i tak zapisujemy do własnej bazy.
  Wyszło to na nasze: Langfuse nie dostaje niczego, czego nie mamy u siebie.
- **Trzy czwarte maskowania już istniało** w `osoby.py` (`zredaguj_pii`,
  `waliduj_brak_pii` — już zawodzące zamknięte, `policz_podejrzenia_pii`).
  Brakowało wyłącznie wzorców na ludzi, których NIE znamy: leadów, czyli
  klientów naszego klienta. Nowy moduł komponuje istniejące, zamiast pisać
  drugą implementację.
- **Tabela mapowania PII okazała się w tej ścieżce zbędna** — i to jest
  lepsza odpowiedź niż planowana. Snapshot jest pseudonimizowany i twardo
  walidowany PRZED zapisem, więc agent nigdy nie widzi prawdziwych nazwisk.
  Ciągnięcie prawdziwego PII do kodu, którego jedynym zadaniem jest wysyłka
  na zewnątrz, byłoby odwrotnością celu tej fazy.
- **Wzorzec telefonu wymagał pomiaru, nie intuicji.** Pierwsza wersja
  zamieniała `2026-09-22 12:00` w `2026-[TELEFON]:00`. Identyfikatory monday
  to gołe ciągi 9-10 cyfr, więc maskowanie, które je zjada, zostanie
  wyłączone przez pierwszego człowieka czytającego trace — a wtedy nie
  maskuje już nic. Wzorzec jest wąski świadomie i test pilnuje tego mocniej
  niż tego, czy w ogóle trafia.

**Skill Langfuse'a** (`github.com/langfuse/skills`, prośba Kuby z 2026-09-22)
przeczytany i **nieprzydatny w tej fazie**: dotyczy odpytywania Langfuse'a
przez API — trace'y, prompty, datasety — a nie instrumentacji. Wróci, gdy
będziemy chcieli czytać trace'y z powrotem. Nie instalowany.

**Zależność kosztowała 15 pakietów** (OpenTelemetry, protobuf, requests,
wrapt, backoff) przy wybranej drodze oficjalnego SDK. Alternatywa bez ani
jednego nowego pakietu istniała — Langfuse przyjmuje OTLP także w JSON-ie,
więc wystarczyłby `httpx` — i została **świadomie odrzucona przez Kubę**
2026-09-22 na rzecz wsparcia producenta. Auto-instrumentacja `httpx`,
`requests` i `urllib3` jest zablokowana, żeby SDK nie wysyłało tego, czego mu
nie daliśmy.

**Sprawdzone na żywo przez PEŁNĄ ścieżkę** (2026-09-22): snapshot 1 zawężony
do jednego workspace'u CXLABS (`CRM DEMO 28.08`), run `agent-20260922T124624Z`
na trzech hipotezach `AUTOMATION_DEAD` — 2 findingi przyjęte, 1 hipoteza
odrzucona przez agenta, 0,42 USD. W Langfuse wylądowało **sześć obserwacji**:
trzy korzenie z `run_id`, `snapshot_id` i rozstrzygnięciem oraz trzy generacje
z modelem, zużyciem i kosztem.

**`trafien_maskowania: 0` na wszystkich trzech** — i to jest właściwy wynik,
a nie brak wyniku. Znaczy, że pierwsza linia obrony (dane osobowe nie wchodzą
do kontekstu modelu) utrzymała się, a warstwa maskująca nie miała czego łapać.
Wcześniejsza próba z danymi WYMYŚLONYMI pokazała, że gdy ma co złapać, to
łapie: po stronie Langfuse leżało wtedy `[E-MAIL]` i `[TELEFON]`, adresu nie
było. Dwie próby razem pokrywają oba przypadki.

**Zadanie dla człowieka, nie do domknięcia kodem:** Langfuse jest teraz
**podprzetwarzającym dane klienta** i musi się znaleźć w tej samej rozmowie,
co reszta subprocesorów.

**2026-09-22 — faza 3 zamknięta.** `itemy.py` plus flaga `--itemy`. Zakaz D5
zdjęty świadomie i tylko tutaj. Pełny przebieg na CXLABS: **1109 wywołań**,
954 tablice objęte planem, zero pominiętych przez budżet.

Cztery rzeczy poszły inaczej, niż zakładał plan:

- **`items_count` nie jest obietnicą, że itemy da się pobrać** (O47).
  Dwanaście tablic deklaruje itemy i oddaje zero — bez błędu, GraphQL zwraca
  200 i pustą stronę. Największa z nich mówi `7076`. Kolejne dwie oddają mniej,
  niż deklarują. To ~1,5% konta, więc „ile itemów jest" i „ile umiemy opisać"
  są od teraz dwiema osobnymi liczbami i obie są widoczne w wyjściu.
  Rozstrzygnięcie przyczyny wymaga wejścia do panelu monday — zadanie dla
  człowieka, nie dla zapytania.
- **Ekstrapolacja kosztu z O43 była zaniżona 2,3×** — 476 wobec zmierzonych
  1109. Powód: nie objętość danych, tylko **podłoga jednego wywołania na
  tablicę**. Konto z tysiącem małych tablic kosztuje więcej niż konto z jedną
  wielką. Liczba powtórzyła się co do jednego w drugim przebiegu, więc jest
  przewidywalna, a nie przypadkowa. 1109 to 8,9% budżetu `enterprise`, ale
  **dwukrotność całego limitu planu `free`**.
- **Goły `status` nie jest lejkiem** (O46). Pierwsza wersja reguły robiła lejek
  sprzedaży z dowolnej tablicy, bo `status` to domyślne id pierwszej kolumny
  statusu wszędzie. Stąd trzy stopnie rozpoznania zamiast dwóch: kolumna
  kanoniczna, grupy, „nie rozpoznano" — i stopień jest nazwany w wyjściu, żeby
  nikt nie wziął podziału na grupy za etapy lejka.
- **Sampling okazał się niepotrzebny na tym koncie**, ale reguła zostaje, bo na
  planie `free` to warunek wykonalności, nie optymalizacja. `zaplanuj_pobranie`
  bierze najmniejsze tablice najpierw: dwadzieścia małych mówi o koncie więcej
  niż jedna wielka, a wielka i tak ma policzone itemy z licznika.

Odchylenie od zakresu: **rollupy produktowe** (leady, szanse, tickety,
zamknięcia dziennie) **przeniesione do fazy 5** — decyzja Kuby z 2026-09-22.
Dane pod nie są zebrane; brakuje reguły, co jest szansą i co zamknięciem,
a ta reguła jest osądem i należy do fazy analizy.

---

**`STATUS.md` należy do człowieka.** Ten plan niczego w nim nie przesuwa —
projekt jest formalnie w etapie 6 (Operate), a powyższe opisuje przebudowę,
która czeka na decyzję o rozpoczęciu.
