# Notatka: decyzje do podjęcia przy wpinaniu audytu w portal

**Dla:** Kuba. **Stan na:** 2026-09-17, kod na `main` (`feac935`).

To NIE jest specyfikacja ani plan etapu 7. Zbiór rzeczy, które wyszły przy
spisywaniu handoffu dla frontu i wymagają twojej decyzji, zanim ktokolwiek
napisze pod nie kod. Front ich nie potrzebuje, żeby ruszyć — dlatego siedzą
osobno (`docs/HANDOFF_PORTAL.md` został sam front).

---

## 1. Trzy tryby z portalu wobec tego, co stoi

| tryb | co z tego już jest | czego brakuje |
|---|---|---|
| **1. Jednorazowy zrzut, klucz przepada** | **Cała mechanika.** Klucz jedzie w ciele POST-a, leci jako argument zadania w tle i ginie z procesem. Nie ma na niego kolumny i to jest decyzja, nie przeoczenie (migracja 006) | **Ścieżki anonimowej.** Każdy endpoint wymaga sesji, a zadanie zakłada się na `client_id` i `konto_id` z tej sesji |
| **2. Abonament, klucz na stałe** | Nic. Dziś klucza nie da się zapisać nawet celowo | Magazynu klucza — **szyfrowanego, nie haszowanego** (§2). Plus rotacji i odwołania |
| **3. Audyt tygodniowy + czat** | Audyt: cały przepływ stoi, brakuje wyzwalacza czasowego. Czat: nie ma nic | Harmonogramu, workera (§4) i całego czatu (§5) |

Tryb 1 to w większości przepięcie istniejącego przepływu na sesję bez konta.
Tryb 2 to zmiana architektury bezpieczeństwa. Tryb 3 to nowy moduł. To nie są
trzy warianty tego samego ekranu.

**Dobra wiadomość o trybie 1:** faza 1 (zbieranie) kosztuje **0 USD modelu**
i ~132 wywołania monday. „Zrzut konta bez agenta" to dokładnie faza 1 zatrzymana
przed zgodą — nie trzeba pod to niczego liczyć ani nikogo obciążać.

---

## 2. „Klucz zahasowany i za solą" — tak się nie da

W trybie 2 klucz monday musi zostać **użyty** za tydzień. Hasz jest
jednokierunkowy: z `scrypt(klucz)` nie odzyskasz klucza i nie zawołasz nim
monday. To, co dziś jest haszowane z solą, to dwie inne rzeczy:

| co | jak | po co |
|---|---|---|
| hasła do panelu | `scrypt` + losowa sól per wiersz | weryfikacja logowania — hasła nigdy nie trzeba odzyskać |
| tożsamość ludzi klienta | HMAC-SHA256 z `SOL_PSEUDONIMIZACJI` | żeby imiona nie trafiły do modelu |

Żadne z nich nie jest wzorcem dla klucza API. Klucz trzeba **szyfrować
odwracalnie** (np. AES-GCM kluczem z konfiguracji procesu) albo oddać
menedżerowi sekretów. To zmiana kategorii, nie „dołóż kolumnę": pojawia się
klucz szyfrujący, jego rotacja i pierwszy w tym projekcie moment, w którym cudzy
sekret leży u nas trwale.

Migracja 006 ma na to jawne ostrzeżenie w komentarzu — kto dokłada taką kolumnę,
robi to **przeciw** zapisanej decyzji. To musi być świadome cofnięcie D11,
z datą i powodem.

**Do rozstrzygnięcia:** kto poza procesem audytu może użyć zapisanego klucza
i jak klient go odwołuje.

---

## 3. O23 blokuje samoobsługowe konta klientów

`docs/OTWARTE.md` §O23 jest otwarte, a `deploy/README.md` zaczyna się od zdania
„**nie zakładaj konta klienta**, dopóki O23 jest otwarte". Powód: panel niesie
imiona, nazwiska i e-maile pracowników klienta pod adresem URL. Bez odpowiedzi
na cztery pytania (wygasanie dostępu, kasowanie konta po zakończeniu relacji,
logi wejść, nazwiska pod URL-em) panel jest **tylko dla zespołu**.

Tryb 1 tego nie narusza — jednorazowy zrzut nie zakłada konta. Tryby 2 i 3 tak,
bo z definicji dają klientowi trwałe konto.

---

## 4. Kolejka, nie agent, jest największą różnicą makieta → produkt

Dziś audyt żyje **w wątku procesu aplikacji** (`BackgroundTasks`) — świadomie,
przez budżet RAM na Mikrusie (O6). Dlatego `wdroz.sh` odmawia wdrożenia, gdy
trwa run: restart niszczy go bezpowrotnie.

Przy cotygodniowych audytach dla wielu klientów to się rozpada. Potrzebny jest
prawdziwy worker. W CLAUDE.md stoi, że Celery i Redis były rozważone
i odrzucone — to była dobra decyzja **dla tamtych ograniczeń**. Portal ma inne
i tę decyzję trzeba podjąć na nowo, świadomie, a nie odziedziczyć przez to, że
nikt nie zajrzał, dlaczego tego tam nie ma.

---

## 5. Czat — pięć rzeczy bez odpowiedzi

Dziś nie ma nic: ani endpointu, ani tabeli na wiadomości, ani sesji rozmowy.
Agent jest procesem wsadowym — dostaje hipotezy z detektorów, bada je i kończy.

1. **Czego dotyczy rozmowa** — całego konta czy konkretnego audytu (`run_id`).
   Od tego zależy, czy czat czyta z zamrożonego snapshotu, czy odpytuje monday
   na żywo: dwa różne profile kosztu i dwa różne profile ryzyka.
2. **Jak wracają odpowiedzi** — strumieniowo czy w całości. To jedyna z tych
   pięciu, która realnie zmienia kod frontu, więc warto ją rozstrzygnąć pierwszą.
3. **Czy historia rozmowy jest trwała** i kto ją widzi.
4. **Kto płaci i jaki jest sufit.** Audyt ma zgodę na koszt z widełkami przed
   startem; rozmowa nie ma naturalnego momentu, w którym można o to zapytać.
5. **Czy czat może odpalić audyt** albo cokolwiek innego, co kosztuje.

Czego rozstrzygać nie trzeba: agent nie ma narzędzia zapisującego.
`MondayClient.przygotuj_zapytanie()` odrzuca `mutation` i `subscription`, więc
czat może o koncie opowiadać, ale nie może go zmienić. To warto zachować przy
przepisywaniu czegokolwiek — przy czacie ta granica jest **ważniejsza** niż przy
audycie wsadowym, bo użytkownik będzie agenta o rzeczy prosił, a „zmień status
tej tablicy" jest o jedno zdanie od „pokaż mi tę tablicę".

---

## 6. Zapisany zakres na koncie klienta

`Zakres` to już gotowy, walidowany model z trzema trybami (`cale_konto`,
`workspace`, `tablice`). Wybór jest zapisywany, ale **per zadanie**
(`zadania.wybor`) — tabeli z zakresem klienta nie ma. Trwały zakres to jedna
tabela trzymająca ten sam obiekt, nie nowe pojęcie.

Dwie pułapki:

- **Identyfikatory tablic się starzeją.** Tryby `cale_konto` i `workspace` leczą
  się same — nowa tablica wpada do audytu automatycznie. Tryb `tablice` nie
  i cichnie najgorzej: audyt tygodniowy zawęża się sam, a raport dalej wygląda
  kompletnie. Jeśli zapisujemy `tablice`, run musi mówić „z zapisanych 40 nie
  istnieje już 6, doszły 3 nieobjęte".
- **Dla audytu cyklicznego `tablice` to prawdopodobnie zły domyślny tryb** —
  świeżo założona tablica jest najbardziej prawdopodobnym źródłem nowego
  znaleziska, a właśnie ona nigdy nie trafi do zakresu zamrożonego miesiąc temu.
  Sensowniejszy domyślny to `workspace` z listą wyjątków zamiast listy wyborów.
  Tego w `Zakres` dziś nie ma.

**Zgoda na koszt wygasa po 12 h**, więc automat tygodniowy jej nie użyje.
Naturalne uogólnienie: zgoda z pułapem („audytuj co tydzień w tym zakresie,
dopóki szacunek nie przekracza X USD; jak przekroczy — zatrzymaj się i zapytaj").
Wtedy zapisany zakres i zapisana zgoda są jednym obiektem, a nie dwoma, które się
rozjeżdżają.

---

## 7. Co brać do portalu, a co pisać od nowa

Przy założeniu, że agent jest rozwijany, a nie pisany od zera:

| warstwa | co z nią |
|---|---|
| collector, detektory, rubryka, kontrakt findingu, pseudonimizacja, `MondayClient`, renderer | **zostaje i jest importowane** — to jest pakiet pod `uv`, portal bierze go jako zależność |
| sesje, konta, hasła, kolejka, ekrany | **portal pisze po swojemu** — ta warstwa powstała pod jednodostawcowy panel wewnętrzny |
| harmonogram, czat, magazyn klucza, pakiety | **nowe**, nie ma czego przenosić |

**Ustalone 2026-09-17:** kod audytu idzie do **repo portalu i jednym
wdrożeniem** razem z nim. Nie ma osobnego serwisu, więc wariant „audyt za
wewnętrznym tokenem" odpada, a z nim cały problem CORS-u i ciasteczek
międzydomenowych — front i API są tym samym originem z definicji.

Dwie konsekwencje do rozstrzygnięcia później:

- **Baza.** Audyt ma dziś własne SQLite z własnym runnerem migracji
  (ponumerowane `.sql`, tabela `_migracje`, tabele `STRICT`, WAL). Przy wspólnym
  wdrożeniu albo trzyma dalej swój plik obok bazy portalu, albo wchodzi do bazy
  portalu — a to drugie znaczy przepisanie warstwy składowania, bo schemat
  i runner są pisane pod SQLite, a snapshoty to niemutowalne bloby (D7).
- **`deploy/` w tym repo przestaje być ścieżką produkcyjną.** Jednostki systemd,
  `wdroz.sh` i kontrola zdrowia opisują wdrożenie tej makiety na Mikrusie.
  Zostają jako takie, ale ktoś nie może ich wziąć za instrukcję dla portalu.

---

**STATUS.md:** projekt jest w etapie 6 (Operate). Ta notatka niczego nie
przesuwa i nic w kodzie nie zmienia.

---

## 8. Portal nie ma backendu — pakiet nie ma gdzie się zaimportować (2026-09-25)

**Skąd to wiemy:** opis architektury od zespołu portalu (Kuba, 2026-09-25).
Portal to statyczny JS bez frameworka (jeden `portal_shell.html` + szablony
widoków), nginx oddaje pliki i pilnuje `/admin/`, **całą logikę robi Make**
(logowanie hasłem i Google, profil, klucze API, zgłoszenia) wołany z przeglądarki
webhookami, a **bazą są tablice monday** (Userzy, Klienci, Zgłoszenia). Python
jest tylko w buildzie i w cronie co 15 minut. Na serwerze `pomoc.cxlabs.digital`
nie nasłuchuje nic poza nginksem.

**Co z tego wynika:** wariant A z fazy 6 zakładał, że portal zaimportuje
`monday_audit.usluga`. Nie ma czego, co by importowało. Pakiet jest gotowy
i niezależny od sposobu wywołania — brakuje procesu, który uruchomi analizę
(~20 min) po stronie serwera.

**Trzy drogi. Do twojej decyzji, przy wpinaniu — nie wcześniej:**

| droga | jak | co za nią | co przeciw |
|---|---|---|---|
| 1. Nasza usługa HTTP obok portalu | cienkie API (FastAPI, już w stacku) nad `usluga`; nginx portalu proxuje np. `/audyt/`, front JS woła jak każdy adres | FastAPI i wdrożenie już są; długie zadanie + status to zwykły wzorzec; raport z nazwiskami idzie wprost do przeglądarki | pierwszy proces serwerowy w ekosystemie portalu; tożsamość i klucz do rozwiązania |
| 2. Make jako pośrednik | front → Make → nasze API | pasuje do dzisiejszego wzorca portalu | Make odpytuje przez ~20 min; **uwagi i raport z nazwiskami przechodzą przez Make**, który trzyma dane w historii scenariuszy — kolejny podprzetwarzający |
| 3. Panel osobno, portal linkuje | bez integracji | najmniej pracy | nie spełnia „jestem zalogowany w portalu i nic nie wpisuję" |

**Pytania, na które kod nie odpowie** (dotyczą drogi 1, częściowo 2):

1. **Gdzie działa usługa** — Mikrus (tam stoi panel, audyty wstrzymane), czy
   serwer portalu (tam według opisu chodzą już inne procesy Pythona)?
2. **Skąd usługa bierze klucz monday klienta** — portal trzyma klucze w tablicy
   monday przez Make. Klucz nie może przejść przez przeglądarkę, więc albo
   usługa czyta go sama po stronie serwera, albo dostaje od Make.
3. **Jak usługa sprawdza, kim jest użytkownik** — logowanie robi Make; jaki
   token/sesję wydaje przeglądarce i czy da się go zweryfikować poza Make?

**Czego to NIE blokuje:** fazy 7 (raport w czterech kategoriach, PDF) —
działa na wyniku analizy, nie na tym, kto ją uruchomił.
