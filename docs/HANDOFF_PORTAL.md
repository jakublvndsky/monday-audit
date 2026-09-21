# Makieta frontu: audyt monday.com w portalu

**Dla kogo:** osoba, która buduje front w portalu.
**Stan na:** 2026-09-17.
**Po co:** żeby dało się postawić szkielet ekranów, zanim domkniemy kształt
agenta i backendu.

**Świadomie nie ma tu kontraktu API** — ścieżek, ciał żądań ani kodów błędów.
Backend będzie jeszcze przerabiany, więc front zbudowany pod dzisiejsze endpointy
trzeba by poprawiać dwa razy. Buduj na **atrapie danych**, a podpięcie pod
prawdziwe wywołania zrobimy my.

Typy do TypeScriptu dostaniecie **wygenerowane z backendu** — nie piszcie ich
ręcznie, bo ręczne rozjeżdżają się po cichu.

Decyzje architektoniczne (magazyn klucza, harmonogram, baza, kształt agenta) są
w `docs/NOTATKA_PORTAL_DECYZJE.md`, po stronie Kuby, i **nie blokują budowy
szkieletu**.

---

## 1. Ekrany

Spis funkcji, nie układu graficznego — wygląd jest sprawą portalu.

| ekran | po co istnieje | co pokazuje | dane |
|---|---|---|---|
| Logowanie | wejście, dwie role: klient i zespół | formularz | **są** |
| Lista klientów | zespół przełącza kontekst; klient tego ekranu nie ma w ogóle | klient, liczba audytów, data ostatniego, suma kwot | **są** |
| Start audytu | podanie klucza monday i wskazanie zakresu | workspace'y i tablice do wyboru | **są** |
| Wybór zakresu i zgoda na koszt | **jedyna decyzja użytkownika w całym przepływie** | co można zawęzić + widełki kosztu | **są** |
| Postęp | dwa długie oczekiwania | etap, procent, co się teraz dzieje | **są** |
| Wynik audytu | to, po co cały produkt istnieje | findingi, metryki, kwoty | **są** |
| Historia audytów | powrót do starszej wersji | lista audytów z datami | **są** |
| Ludzie | kto pracuje na koncie, ludzie vs agenty AI | profile osób i tablic | **są** |
| Ustawienia konta klienta | zapisany klucz, zapisany zakres, harmonogram | — | **do dorobienia** |
| Czat | rozmowa z agentem | — | **do dorobienia** |

Osiem pierwszych ekranów ma już czym się zasilić. Dwa ostatnie czekają na
backend, więc na razie tylko szkielet.

---

## 2. Przepływ audytu jako stany interfejsu

Pięć stanów. **Dwa z nich to długie czekanie, a jeden w środku to decyzja
o pieniądzach** — i to jest cała trudność tego ekranu.

| stan | ile trwa | co widzi użytkownik |
|---|---|---|
| **Podgląd konta** | kilka sekund | lista workspace'ów, potem tablic w wybranym workspace. Nic jeszcze nie kosztuje |
| **Zbieranie** | minuty | pasek postępu z etapem. Nie da się w tym czasie zrobić nic innego z tym kontem |
| **Wybór zakresu i zgoda** | czeka na człowieka | co obejmie audyt, ile hipotez, **widełki kosztu w USD**. Tu klient zatwierdza albo rezygnuje |
| **Analiza** | kilkanaście minut | znowu pasek postępu, inny etap |
| **Wynik** | — | panel z findingami |

Co musi obsłużyć interfejs, bo inaczej się zablokuje:

- **Po zbieraniu ekran NIE jest skończony — on czeka na decyzję.** To dwa różne
  stany, które z zewnątrz wyglądają tak samo („nic się nie dzieje"), a mylenie
  ich daje ekran zatrzymany w połowie bez komunikatu. To najczęstszy błąd przy
  tym przepływie.
- **Odświeżenie strony nie może gubić audytu.** Zebranie danych zużywa dzienny
  limit klienta w monday, więc powrót do formularza po F5 znaczy „zapłać drugi
  raz". Backend umie powiedzieć, że jest zadanie czekające na decyzję — front ma
  do niego wrócić.
- **Zgoda na koszt ma termin ważności** (dziś 12 h). Po nim widełki przestają
  być obietnicą i trzeba zbierać od nowa. Ekran musi umieć to powiedzieć.
- **Jeden audyt naraz na konto.** Drugi start w trakcie ma być odbity
  komunikatem, nie drugim paskiem postępu.
- **Rezygnacja jest potrzebna.** Ktoś zobaczy kwotę i nie zechce — musi mieć
  wyjście inne niż zamknięcie karty.

---

## 3. Co zawiera wynik audytu

Panel dostaje **wszystko jednym kawałkiem** — nie ma doładowywania sekcji po
kolei, więc ekran nie musi obsługiwać częściowych danych.

| co | z czego się składa |
|---|---|
| **Findingi** | nazwa, waga, wysiłek wdrożenia, pewność, kwota w PLN (nie zawsze), opis, rekomendacja i **dowód** |
| **Metryki** | pogrupowane w sekcje; każda ma wartość, odniesienie („12 z 40"), udział procentowy i flagę „to wymaga uwagi" |
| **Zastrzeżenia** | czego ten audyt NIE obejmuje — ma być widoczne, nie schowane pod „więcej" |
| **Ludzie** | osoby i konta na koncie klienta, z podziałem na ludzi, agenty AI i konta nieznane, plus aktywność per tablica |
| **Historia** | lista poprzednich audytów i porównanie z poprzednim |
| **Nagłówek** | nazwa konta, zakres audytu, plan monday, data runu |

**Dowód przy findingu jest obowiązkowy** — znalezisko bez niego nie przechodzi
walidacji na backendzie. Interfejs, który go nie pokazuje, wyrzuca najmocniejszą
część raportu: to jest różnica między „macie bałagan w automatyzacjach" a „te
trzy automatyzacje nie odpaliły ani razu od kwietnia".

---

## 4. Czat

**Nie ma jeszcze nic** — ani kontraktu, ani historii rozmów. Kształt agenta jest
do przemyślenia, więc czat powstanie później.

**Co można zbudować teraz, nie tracąc pracy:** powłokę — lista wiadomości, pole
wejścia, stan „agent pracuje", przycisk przerwania — za modułem-atrapą z jedną
funkcją `wyslij(wiadomosc)`. Gdy kontrakt powstanie, podmienia się jeden plik.

Dwie rzeczy do uwzględnienia w tym szkielecie:

- **Nie wiadomo, czy odpowiedzi będą wracać strumieniowo, czy w całości.** To
  jedyna otwarta rzecz, która realnie zmienia kształt komponentu — zostawcie na
  nią miejsce.
- **Agent nie może niczego zmienić na koncie klienta** i to się nie zmieni.
  Klient GraphQL odrzuca zapisy na poziomie kodu, więc czat opowiada o koncie,
  ale go nie dotyka. Interfejs może to obiecać użytkownikowi wprost.

---

## 5. Reguły, które przetrwają zmiany backendu

Te rzeczy nie zależą od tego, jak przerobimy API:

- **Dwie role.** Klient widzi wyłącznie swoje konto. Zespół przełącza się między
  klientami. To nie jest przełącznik w widoku — to dwa różne zestawy danych.
- **Klient nie dostaje danych wewnętrznych.** Odrzucone hipotezy, koszt runu
  i rozliczenie są z jego wersji **usuwane**, a nie ukrywane w widoku. Payload
  w przeglądarce widać, więc „wyślij i schowaj" znaczyłoby „wyślij". Front ma
  działać przy braku tych pól, nie przy ich zerze.
- **Nie licz sam tego, co backend już policzył.** Liczniki w nagłówkach
  („3 osoby, 3 agenty AI") przychodzą gotowe po to, żeby nagłówek i lista pod nim
  nie mogły pokazać dwóch różnych liczb.
- **Klucz API nigdy do `localStorage`.** Tylko pamięć komponentu. Dziś klucz
  podaje się dwa razy w trakcie przepływu — jeśli to zostanie, ekran musi to
  obsłużyć bez proszenia użytkownika o przeklejanie.
- **Słownik wag, wysiłku i pewności żyje w rubryce po stronie backendu**, nie
  w kodzie frontu. Nie zaszywajcie listy wartości na sztywno — rubryka się
  zmienia i to jest jej zadanie.

---

## 6. Do potwierdzenia przed pierwszym commitem

1. Czy jednorazowy zrzut konta kończy się bez agenta? To decyduje, czy ekran
   startu w ogóle pyta o drugi klucz (Anthropic).
2. Czy panel klienta pokazuje zakładkę „Ludzie"? Niesie imiona i nazwiska
   pracowników klienta, więc to decyzja Kuby, nie domyślna.
