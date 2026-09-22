# Plan: audyt monday.com w nowej, uproszczonej postaci

**Cel:** podanie klucza API monday zwraca raport HTML i PDF opisujący konto —
inwentarz, typy workspace'ów, aktywność ludzi i agentów, uwagi krytyczne —
z wyceną w dolarach.

**Poza zakresem** (świadomie, nie z zapomnienia):

- **konta, logowanie, sesje, hasła** — wchodzą do innego systemu, z inną bazą;
  audyt dostaje tożsamość z zewnątrz i nie ma własnej,
- **panel zespołu**: lista klientów, przełączanie kontekstu, dwie role,
- **wybór zakresu audytu** — skanujemy zawsze całe konto, niezależnie od jego
  rozmiaru (decyzja 2026-09-21). Wypada z tym dwufazowa zgoda na koszt,
  widełki liczone ze snapshotu, flagi przy tablicach i podłoga kosztu,
- **rubryka z wagami, wysiłkiem i pewnością** oraz wycena w złotówkach —
  zastępuje je jedna kategoria „uwagi krytyczne" i kwoty w dolarach,
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

- [ ] **2. Collector: inwentarz konta** — jedna komenda produkuje snapshot,
  z którego liczą się wszystkie pozycje punktów 2 i 4 wytycznych oraz role
  i aktywność z punktu 5. Większość już istnieje: `kind` użytkownika rozróżnia
  admina, gościa, członka, `view_only` i konto agentowe; `owners` i
  `subscribers` tablicy są zbierane, więc userzy i goście per tablica są
  policzalne bez nowego zapytania.
  - Ryzyko **rozstrzygnięte fazą 1**: właściciela automatyzacji w API **nie ma**
    (O42) — ta pozycja wytycznych wypada i trzeba to powiedzieć zamawiającemu.
    Liczba automatyzacji na tablicę jest osiągalna tylko jako „ile ich się
    URUCHOMIŁO" (O41), więc pomija te, które nigdy nie odpaliły — a w audycie
    to właśnie one są najciekawsze. Raport musi tę różnicę nazwać.

- [ ] **3. Itemy i agregaty** — dla prawdziwego konta wychodzą liczby z punktu 3:
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

- [ ] **4. Langfuse z maskowaniem PII** — trace'y wywołań modelu trafiają do
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
  - Ryzyko drugie: co dokładnie maskujemy. Mail i telefon są łatwe wzorcem;
    imię i nazwisko w nazwie tablicy albo w treści itemu **nie są** — i trzeba
    powiedzieć wprost, czego ta warstwa nie złapie.

- [ ] **5. Analiza: co to za konto** — model dostaje agregaty i produkuje trzy
  rzeczy: sugestię produktu z nomenklatury, przypadki użycia agentów oraz uwagi
  krytyczne. Każde stwierdzenie z dowodem.

  **Faza 1 zdjęła stąd jedną pozycję i zawęziła drugą.** Typ workspace'u to
  odczyt pola `account_product` (O40), nie wnioskowanie — model nie ma tu nic do
  roboty. Nomenklatura zostaje potrzebna wyłącznie tam, gdzie produkt **nie
  jest** ustawiony, a dane wyglądają, jakby powinien być: „macie leady
  w zwykłych tablicach, rozważcie monday CRM".
  - Ryzyko: fałszywe rozpoznania w tej zawężonej roli. „To wygląda na leady"
    postawione na nazwach kolumn bywa trafne i bywa mylące — potrzebny próg
    pewności i jawne „nie wiem" zamiast zgadywania.
  - Ryzyko drugie: przypadki użycia agentów opierają się na aktywności kont
    agentowych, bo `agent_runs` nie istnieje w żadnej wersji API (O20, pomiar
    z fazy 1), a `agents` działa dopiero w nieprzypiętej `2027-01`.

- [ ] **6. Przepływ: dwa kroki, jedno kliknięcie między nimi** — user story
  z 2026-09-21. Użytkownik jest już zalogowany w portalu, a klucz monday leży
  w tamtej bazie, więc nigdzie go nie wpisuje.

  1. **„Analizuj moje środowisko"** → sześć kafelków z punktu 2 wytycznych:
     workspace'y, tablice, użytkownicy, goście, agenci AI, rodzaj licencji.
     Krok **tani**: konto, workspace'y, tablice i użytkownicy to kilka zapytań,
     zero itemów, zero modelu. Ekran startowy dla **każdego** konta, nie tylko
     dużego.
  2. **„Chcę wykonać analizę"** → pełny skan całego konta plus model, na końcu
     raport.

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
  wyliczany zdarzenie po zdarzeniu. Kwoty w dolarach.

  **Kształt grupowania jest do dopracowania** (decyzja 2026-09-21: „to będzie
  do dopracowania"). Wiadomo tylko, czego ma nie być: listy pojedynczych
  zdarzeń. Tej fazy nie da się domknąć, dopóki nie wiadomo, w co grupujemy —
  i to jest w porządku, bo materiału do grupowania dostarczają dopiero fazy 3
  i 5.
  - Ryzyko: **PDF to nowa zależność.** Headless Chrome oznacza powrót Node'a na
    produkcję, czego świadomie unikaliśmy; WeasyPrint to czysty Python za cenę
    bibliotek systemowych (pango, cairo). Decyzja przed fazą, nie w trakcie.

## Dziennik

<!-- Uzupełniany przy zamykaniu faz: data, faza, link do dokumentu
     w `docs/features/`, odchylenia od planu. -->

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

---

**`STATUS.md` należy do człowieka.** Ten plan niczego w nim nie przesuwa —
projekt jest formalnie w etapie 6 (Operate), a powyższe opisuje przebudowę,
która czeka na decyzję o rozpoczęciu.
