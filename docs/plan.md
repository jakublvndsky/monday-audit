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

- [ ] **1. Pomiary API** — cztery zapytania rozstrzygające, co w ogóle da się
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
  - Ryzyko: liczba automatyzacji per tablica i właściciel automatyzacji zależą
    od wyniku pomiaru 2. Jeśli API ich nie oddaje, dwie pozycje wytycznych
    wypadają i trzeba to powiedzieć wprost, zamiast podstawiać przybliżenie.

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

- [ ] **4. Analiza: co to za konto** — model dostaje agregaty i produkuje cztery
  rzeczy: typ workspace'u z uzasadnieniem, sugestię produktu z nomenklatury
  (CRM albo Service, z pytaniem do klienta), przypadki użycia agentów, oraz
  uwagi krytyczne. Każde stwierdzenie z dowodem.
  - Ryzyko: fałszywe rozpoznania. „Ten workspace to CRM" postawione na nazwach
    kolumn bywa trafne i bywa mylące — potrzebny próg pewności i jawne „nie
    wiem" zamiast zgadywania. Przypadki użycia agentów zależą od pomiaru 1.

- [ ] **5. Przepływ: dwa kroki, jedno kliknięcie między nimi** — user story
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

- [ ] **6. Raport: przebudowa treści, HTML i PDF** — wejście w kafelek pogłębia
  do szczegółów, a sam raport jest **pogrupowany i opisany przez agenta**, nie
  wyliczany zdarzenie po zdarzeniu. Kwoty w dolarach.

  **Kształt grupowania jest do dopracowania** (decyzja 2026-09-21: „to będzie
  do dopracowania"). Wiadomo tylko, czego ma nie być: listy pojedynczych
  zdarzeń. Tej fazy nie da się domknąć, dopóki nie wiadomo, w co grupujemy —
  i to jest w porządku, bo materiału do grupowania dostarczają dopiero fazy 3
  i 4.
  - Ryzyko: **PDF to nowa zależność.** Headless Chrome oznacza powrót Node'a na
    produkcję, czego świadomie unikaliśmy; WeasyPrint to czysty Python za cenę
    bibliotek systemowych (pango, cairo). Decyzja przed fazą, nie w trakcie.

## Dziennik

<!-- Uzupełniany przy zamykaniu faz: data, faza, link do dokumentu
     w `docs/features/`, odchylenia od planu. -->

---

**`STATUS.md` należy do człowieka.** Ten plan niczego w nim nie przesuwa —
projekt jest formalnie w etapie 6 (Operate), a powyższe opisuje przebudowę,
która czeka na decyzję o rozpoczęciu.
