# Handoff dla frontu: audyt monday.com w portalu

**Dla kogo:** osoba, która buduje front modułu audytu w portalu.
**Stan na:** 2026-09-24 (faza 6, wariant A: pakiet dla portalu).
**Zastępuje:** wersję z 2026-09-17. Tamta opisywała wybór zakresu, zgodę na
widełki, wagi i kwoty w PLN. Nic z tego już nie istnieje (decyzje z 21.09
i 23.09 w `docs/plan.md`).

Opisuję, **co dziś jest i czego brakuje**, a nie wygląd. Wygląd jest sprawą
portalu. Decyzje architektoniczne (magazyn klucza, kolejka, baza) są
w `docs/NOTATKA_PORTAL_DECYZJE.md`, po stronie Kuby.

---

## 1. Jak audyt wchodzi do portalu

Kod audytu to **pakiet Pythona** z funkcjami wejściowymi w
`monday_audit.usluga`. Ekrany, sesje i magazyn klucza należą do portalu.

**Uwaga (2026-09-25):** portal nie ma backendu, który mógłby ten pakiet
zaimportować (statyczny JS + Make + nginx). Między frontem a pakietem powstanie
więc warstwa pośrednia — jaka, to decyzja przy wpinaniu
(`docs/NOTATKA_PORTAL_DECYZJE.md` §8). Kształt danych opisany niżej się nie
zmienia: to jest to, co warstwa pośrednia przekaże frontowi. Użytkownik
jest zalogowany w portalu, a klucz monday leży w bazie portalu, więc **nigdzie
go nie wpisuje**.

Przepływ ma **dwa kroki i jedno kliknięcie między nimi**:

| krok | funkcja pakietu | ile trwa | co kosztuje |
|---|---|---|---|
| 1. „Analizuj moje środowisko” | `przeglad_konta(klucz)` | kilka sekund | ok. 36 wywołań z dziennego limitu klienta, 0 USD |
| — szacunek kroku 2 | `szacuj_analize(przeglad)` | natychmiast | nic, liczy z wyniku kroku 1 |
| 2. „Chcę wykonać analizę” | `analiza_konta(klucz, …)` | kilkanaście–kilkadziesiąt minut | kilkaset wywołań i model AI (pełne CXLABS: 342 wywołania, 2,34 USD) |

Obie funkcje działają **w trybie pamięci** (faza 5c). Dane o osobach żyją tylko
w trakcie wywołania. Na dysk portalu trafia wyłącznie zapis minimalny,
czyli zamaskowane uwagi i liczby.

**Wyboru zakresu nie ma.** Skanujemy zawsze całe konto (decyzja 2026-09-21).

---

## 2. Co oddaje każda funkcja

Każdy wynik ma `do_json()` z kształtem pokazanym niżej. **Typy TypeScript
jeszcze nie istnieją** (krok 6-4). Nie pisz ich ręcznie, bo ręczne typy
rozjeżdżają się z backendem po cichu. Do tego czasu atrapa danych.

### Krok 1: `PrzegladKonta`

- `konto_nazwa`,
- `kafelki`: **sześć, w stałej kolejności**: `workspace`, `tablice`,
  `uzytkownicy`, `goscie`, `agenci_ai`, `licencja`. Każdy ma `klucz`,
  `etykieta`, `wartosc` i `szczegoly`. Przykłady szczegółów: podział
  workspace'ów na produkty, tablice w koszu i archiwum, rodzaje kont, okres
  licencji,
- `wywolan`: ile wywołań z limitu zużył ten krok,
- `zastrzezenia`: czego liczby nie obejmują.

Nazw workspace'ów w kafelkach świadomie **nie ma**, bo potrafią nieść nazwisko
(O50).

### Szacunek kroku 2: `SzacunekAnalizy`

- `wywolan_typowo`, `wywolan_maks`, `limit_dzienny`, `udzial_maks`,
- `przekracza_prog`: najgorszy przypadek zjada ponad **50% dziennego limitu**
  konta klienta,
- `usd_od`: **dolna granica** kosztu modelu. Dokładna kwota jest znana
  dopiero po zebraniu danych,
- `zastrzezenia`: m.in. „nieznany plan” zamiast procentu.

### Krok 2: `WynikAnalizy`

`analiza_konta` przyjmuje `przed_sesja` (funkcja zwrotna). Wywołuje ją **po
zebraniu danych, a przed modelem**, z dokładnym szacunkiem: ile hipotez,
ile idzie do modelu, ile z szablonu, szacowany koszt, które klasy przyciął
sufit. Dziś to jedyny sygnał pośredni. Pełnego raportowania postępu z pakietu
nie ma (§6).

`do_json()` zawiera:

| pole | co znaczy |
|---|---|
| `uwagi` | uwagi krytyczne w postaci **zamaskowanej**: osoba → `[OSOBA]`, daty → dni przed analizą. Każda ma `klasa_id`, `opis`, `rekomendacja`, `dowod` i `zrodlo` (`model` albo `szablon`) |
| `hipotez`, `do_modelu`, `z_szablonu` | ile sygnałów wzbudziły detektory i jak je rozdzielono |
| `poza_sufitem` | klasy przycięte limitem 20 na klasę: `{klasa: {zbadanych, wszystkich}}` |
| `pominietych`, `odrzuconych` | hipotezy odrzucone przez model / uwagi odrzucone przez walidację |
| `szacunek_usd`, `koszt_usd` | szacunek i faktyczny koszt modelu |
| `wywolan_monday` | wywołania z limitu klienta: collector plus narzędzia AI na żywo |
| `sekund`, `run_id` | czas i identyfikator analizy |
| `ma_raport` | czy powstał raport z nazwiskami |
| `blad_zapisu` | zapis minimalny padł, ale wynik jest (napis zamiast wyjątku) |

**Raport z nazwiskami (`raport_html`) NIE wchodzi do `do_json`.** Jest polem
obiektu w pamięci. Powstaje raz, w chwili zakończenia analizy, bo potem
mapowanie osób znika i złożyć go ponownie się nie da. Portal ma go oddać
człowiekowi, a nie przepuścić przez swoje API i logi.

### Błędy

- `UslugaError` ma komunikat dla człowieka, bez treści odpowiedzi API.
  Dziś są trzy: brak uprawnień admina, wyczerpany limit dzienny, klucz
  nie działa.
- `AnalizaError` znaczy, że odpowiedź modelu nie miała struktury. Sesja
  jest już opłacona. Surowa odpowiedź jest w pamięci obiektu błędu,
  pakiet nigdzie jej nie zapisuje.

---

## 3. Co dziś zawiera raport

**Raport z nazwiskami** (`raport_uwag.html.j2`) jest pogrupowany **po klasie
problemu**: sekcja na klasę, w niej tabela uwag z opisem, rekomendacją
i dowodem, a na górze zastrzeżenia. To **stan przejściowy**.

Docelowy kształt należy do fazy 7 i **nie jest zbudowany**. Ustalony z Kubą
2026-09-23 wygląda tak:

1. **Raport główny = cztery kategorie jako kafelki:** Workspace, Tablice,
   Użytkownicy, Agenci. Na kafelku liczba uwag i jedno zdanie o największym
   problemie.
2. **Kliknięcie kafelka PRZENOSI do osobnego raportu pogłębionego** tej
   kategorii, a nie rozwija treść na tej samej stronie.
3. **Przypisanie klas do kategorii jest propozycją do potwierdzenia**
   (`docs/plan.md`, faza 7):
   - Tablice ← `AUTOMATION_*`, `BOARD_*`, `DUPLICATE_STRUCTURE`, `PROCESS_BYPASS`,
   - Użytkownicy ← `ZOMBIE_ACCOUNT`, `GUEST_SPRAWL`, `PLAN_MISMATCH`,
     `UZYTKOWNIK_WYGASZONY`, `ENGAGEMENT_DROP`,
   - Agenci ← na razie żadna klasa, bo API nie oddaje danych (O20),
   - Workspace ← rollupy CRM/Service, bez klasy uwag.

Czego w raporcie **nie ma** i nie będzie: kwot, wag, wysiłku, pewności
(decyzja 2026-09-23). Jest jedna kategoria: „uwaga krytyczna”.

**PDF-a nie ma.** Wybór narzędzia (headless Chrome albo WeasyPrint) jest
decyzją przed fazą 7.

---

## 4. Rzeczy w danych, które front musi umieć pokazać

- **Dowód przy każdej uwadze.** Uwaga bez dowodu nie przechodzi walidacji na
  backendzie. Dowód to fakty z konta: identyfikatory tablic, liczby, daty,
  nakładanie kolumn, błędy automatyzacji.
- **Wartość `{"nie_zmierzone": "<powód>"}` w dowodzie** znaczy, że API tego
  nie oddaje. To nie jest zero ani puste pole. Dziś występuje w
  `GUEST_SPRAWL.tablice_dostepne` (zmiana O31 z 2026-09-24).
- **Zastrzeżenia** przychodzą w trzech miejscach: przegląd, szacunek i raport.
  Mówią, czego liczby nie obejmują, np. „AI zbadało 20 z 401 — reszta NIE
  jest w porządku, tylko niezbadana”.
- **Grupy duplikatów.** `DUPLICATE_STRUCTURE` to grupa tablic, nie para.
  Na pełnym CXLABS największa ma 91 tablic, więc listy w dowodzie bywają długie.
- **Dwie wersje uwagi:** z nazwiskami (tylko świeży `raport_html`)
  i zamaskowana (`uwagi` w `do_json`, zapis w bazie).

---

## 5. Reguły, które przetrwają zmiany backendu

- **Klucz API nigdy w przeglądarce** i nigdy w `localStorage`. W tym modelu
  front w ogóle go nie dotyka, bo leży w portalu.
- **AI nie może niczego zmienić na koncie klienta.** Klient GraphQL odrzuca
  zapisy w kodzie. Interfejs może to obiecać wprost.
- **Nie licz sam tego, co backend policzył.** Liczby w kafelkach, szacunku
  i nagłówkach przychodzą gotowe.
- **Jedna analiza naraz na konto** i **odświeżenie strony nie może jej
  zgubić**. Zbieranie zużywa limit klienta. Pakiet nie ma kolejki ani stanu
  zadania, więc to należy do portalu (§6).

---

## 6. Czego brakuje

| brak | gdzie jest rozstrzygnięcie |
|---|---|
| typy TypeScript z kontraktu i dokument wejścia dla zespołu portalu | krok 6-4, niezaczęty |
| raport w czterech kategoriach z przejściem do raportu pogłębionego | faza 7 |
| PDF | faza 7, decyzja o narzędziu przed fazą |
| raportowanie postępu z pakietu (etap, procent) w trakcie kroku 2 | brak; dziś jest tylko `przed_sesja`. Do rozstrzygnięcia |
| kolejka, stan zadania, „jedna analiza naraz”, powrót po odświeżeniu | po stronie portalu (`NOTATKA_PORTAL_DECYZJE.md`) |
| co widzi klient, a co zespół CXLABS (koszt, odrzucone hipotezy) | do rozstrzygnięcia. Pakiet oddaje wszystko w `do_json`, filtrowania ról nie ma |
| historia i porównanie analiz | dziś tylko zapis minimalny w bazie, bez funkcji pakietu do odczytu |
| czat z agentem | poza zakresem planu |

Panel na Mikrusie (`web/`) istnieje, ale **audyty są w nim wstrzymane**
(`AUDYTY_WSTRZYMANE`), a jego przepływ (wybór zakresu, zgoda na widełki)
jest starszy niż ten dokument. Nie jest wzorem dla portalu.
