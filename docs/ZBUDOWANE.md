# Co jest zbudowane — stan na 2026-08-05

> **Ten plik opisuje TERAŹNIEJSZOŚĆ.** Specyfikacje w `docs/etapy/` mówią, co
> ma być; ten dokument mówi, co stoi i **co zostało zmierzone, a nie założone**.
> Kolejność etapów i zatwierdzenia: [`STATUS.md`](../STATUS.md) — należy do
> człowieka.
>
> Zasada obowiązująca w całym pliku: **liczby są z pomiaru.** Jeśli czegoś nie
> zmierzyliśmy, jest napisane, że nie.

Etap 3, pozycje **3.1–3.12 zbudowane i przepuszczone przez prawdziwe konto**.
Audyt kończy się dwoma dokumentami HTML, a od 2026-08-05 także **makietą
dashboardów** (D15). Publikacja pod URL-em przechodzi do etapu 5, gdzie TLS
terminuje Cloudflare, a origin obsługuje nginx już stojący na serwerze (D19;
Caddy wypadł ze stacku w D18) — ryzyko danych osobowych pod URL-em opisuje O23.

---

## Przepływ, tak jak faktycznie działa

```
                       .env / środowisko procesu (D12)
                                   │
   ┌───────────────────────────────┴─────────────────────────────┐
   │  cli.py            collector: 5 modułów + sonda, 227 wywołań │
   │  cli_agent.py      agent: 1 sesja na hipotezę               │
   │  cli_cennik.py     scraper stawek — NIGDY w trakcie audytu  │
   │  cli_raport.py     dokument HTML · cli_pulpit.py  dashboardy │
   └───────────────────────────────┬─────────────────────────────┘
                                   ▼
  KROK 1 ── collector (httpx, zero AI)
  konto → osoby → tablice → automatyzacje → sonda agentów → logi
                                   │
                     walidacja antyprzeciekowa PII
                     na ZŁOŻONYM payloadzie
                                   ▼
                    snapshots (SQLite, niemutowalny)
                    osoby_mapowanie (bez narzędzia dostępowego)
                                   │
  KROK 2 ── detektory (11 zapytań SQL, zero AI)
  progi liczone od `meta.okno_od`, NIE od zegara
                                   │
                          lista hipotez
                                   ▼
  KROK 3 ── agent (Agent SDK, Sonnet) — osobna sesja PER HIPOTEZA
  4 narzędzia, wszystkie tylko czytające, budżet z rubryki
                                   │
  KROK 4 ── walidacja kontraktu (kod, D8)
  finding bez `dowod` odpada; kwota bez stawki odpada
                                   ▼
                 findings + findings_odrzucone + runy
                                   │
  KROK 5 ── renderer (jinja2, autoescape) — deanonimizacja DOPIERO TUTAJ
  raport: dwa pliki HTML · panel: dashboardy + payload JSON dla frontu w JS
  granica odbiorcy jest STRUKTURALNA, nie wizualna (D15)
```

**Agent jest w środku, nie na końcu.** Po nim dwie warstwy deterministyczne.

---

## Moduły

| Moduł | Za co odpowiada | Rzecz, która nie jest oczywista |
|---|---|---|
| `konfiguracja.py` | sekrety i ścieżki (D12) | pydantic wkłada **surowe** wejście pola do `ValidationError`, więc komunikat budujemy z samych nazw pól i urywamy łańcuch przez `from None` |
| `klient.py` | GraphQL, paginacja, complexity, retry | `przygotuj_zapytanie()` odrzuca `mutation` i `subscription` — pierwsza warstwa odcięcia zapisu. `WERSJA_API = "2026-07"` przypięta |
| `konto.py` | konto, plan, zakres | token bez admina widzi tylko część konta i to **wchodzi do snapshotu jako zastrzeżenie**, nie ginie |
| `osoby.py` | użytkownicy + pseudonimizacja | granica PII. Model `kind`/`status`; `is_verified` świadomie porzucone |
| `tablice.py` | tablice, kolumny, właściciele | `items_count` to granica — niżej nie schodzimy (D5) |
| `automatyzacje.py` | automatyzacje i ich uruchomienia | filtr `board_id` w API jest zepsuty (O12), więc statystyki są **na poziomie konta** i tak są opisane |
| `logi.py` | activity logs z samplingiem | każdy sufit zapisuje liczbę POMINIĘTYCH obiektów — „no silent caps" |
| `agenci.py` | sonda pól agentowych AI | pyta **zapytaniem**, nie introspekcją (O17), na trzech wersjach API |
| `przebieg.py` | składanie snapshotu, otwarcie i domknięcie runu | walidacja PII idzie **przed** insertem, bo snapshot jest niemutowalny |
| `detektory.py` | 11 detektorów, czysty SQL | progi z `meta.okno_od`, nie z `datetime.now()` — inaczej ten sam snapshot dawałby inne wyniki w różnych dniach |
| `rubryka.py` | wczytanie i walidacja rubryki | niespójna rubryka **zatrzymuje run na starcie**, nie po godzinie |
| `narzedzia.py` | 4 narzędzia agenta | każde przycina wyjście; `probka_kolumn` zwraca **same liczniki** |
| `agent.py` | pętla, jedna sesja na hipotezę | trzy warstwy odcięcia zapisu, opisane niżej |
| `kontrakt.py` | walidacja D8 | odrzucenie **zachowuje treść** findingu w `findings_odrzucone` — bez tego etap 4 nie miałby czego mierzyć |
| `cennik.py` | stawki z pochodzeniem (D13) | stawka to nie `float`: niesie wiek, źródło i wiarygodność |
| `cli_cennik.py` | scraper stawek | wzorce zakotwiczone w **zdaniach**, nie w HTML-u; porażka nic nie nadpisuje |
| `deanonimizacja.py` | hash → nazwisko (3.12) | jedyny czytnik PII poza walidacją z 3.8; hashe siedzą też w KLUCZACH dowodu i w wolnym tekście |
| `raport.py` | zebranie danych i render | filtrowanie odbiorcy w **SQL**, nie w szablonie — szablon nie może być ostatnią linią obrony |
| `pulpit.py` | dashboardy: agregaty kontowe + kontrakt JSON (D15) | granica odbiorcy jest **strukturalna**: payload klienta nie ZAWIERA kluczy wewnętrznych, a nie tylko ich nie pokazuje |
| `baza.py` | połączenie, migracje, rejestr wywołań | `polacz()` włącza klucze obce, więc kolejność zapisów nie jest dowolna |
| `postep.py` | wskaźnik postępu | run collectora trwa minuty i cisza jest nieodróżnialna od zawieszenia |

---

## Trzy warstwy odcięcia zapisu

Zakaz z `CLAUDE.md` brzmi: **agent nie dostaje żadnego narzędzia zapisującego.**
Realizują to trzy niezależne mechanizmy — a nie jeden, bo jeden już raz okazał
się pozorny:

1. **`allowed_tools`** wymienia wyłącznie nasze cztery narzędzia.
2. **`disallowed_tools`** wymienia wbudowane z nazwy, jawnie.
3. **Hook `PreToolUse`** odrzuca w procesie wszystko, czego nie ma na liście.

Do tego `setting_sources=[]` — agent nie wczytuje `CLAUDE.md` z tego repo ani
niczyich ustawień.

**Dlaczego trzy, a nie jedna.** Warstwa 3 była pierwotnie zrobiona przez
`can_use_tool` i **nie działała**: SDK ostrzegł, że callback nie zostanie
wywołany, bo wpis w `allowed_tools` zatwierdza narzędzie, zanim callback dojdzie
do słowa. Mój test dawał fałszywą pewność, bo sprawdzał funkcję w izolacji, a nie
jej podłączenie. Po przepisaniu na hook wyszło od razu, że na liście zakazanych
brakowało `ToolSearch`. Test sprawdza teraz **podłączenie**, nie samą funkcję.

**Osobno: nie używamy MCP monday.** Flaga `--read-only` nie blokuje zapisu —
zmierzone 2026-08-03 na `@mondaydotcomorg/monday-api-mcp@3.3.0`: `create_board`
i surowa mutacja przez `all_api_write` **przeszły do API** (D4, O19).

---

## Narzędzia agenta

| Narzędzie | Źródło | Co zwraca |
|---|---|---|
| `pobierz_inwentarz` | snapshot | spis konta, przycięty |
| `zapytaj_snapshot` | snapshot | 8 predefiniowanych pytań, **nie surowy SQL** |
| `probka_kolumn` | monday | **same liczniki** wypełnienia; kolumna tytułu wykluczona, `waliduj_brak_pii` na wyjściu |
| `log_tablicy` | monday | activity log, autorzy pseudonimizowani |

Limity: 25 itemów w próbce, 40 wpisów logu, 30 tablic i 30 osób w odpowiedzi.
Wyczerpanie budżetu **nie jest błędem** — narzędzie mówi o wyczerpaniu, a agent
domyka hipotezę z tym, co ma.

`probka_kolumn` to jedyne miejsce, gdzie schodzimy poniżej `items_count`, i to
tylko w klasie `BOARD_OVERCOMPLEX`. Zwraca liczniki, nigdy treść.

---

## Detektory

11 z 12 klas rubryki ma detektor. Bez detektora jest **`AI_UNUSED`** —
`status: do_weryfikacji`, bo API nie oddaje zużycia kredytów AI (O2, O20).

| Detektor | Wzbudzeń na snapshocie #5 |
|---|---|
| `ZOMBIE_ACCOUNT` | 7 |
| `AUTOMATION_DEAD` | 7 |
| `BOARD_OVERCOMPLEX` | 2 |
| `AUTOMATION_ABSENT` | 1 |
| `GUEST_SPRAWL` | 1 |
| `PLAN_MISMATCH` | 1 |
| `ENGAGEMENT_DROP`, `BOARD_GHOST`, `BOARD_NO_OWNER`, `DUPLICATE_STRUCTURE`, `PROCESS_BYPASS` | 0 |

**Zero wzbudzeń nie znaczy „detektor nie działa".** Konto CXLABS ma 105 tablic,
wszystkie aktywne, wszystkie z właścicielem — więc `BOARD_NO_OWNER` i
`BOARD_GHOST` po prostu nie mają na czym się wzbudzić. Testy jednostkowe
sprawdzają je na danych syntetycznych.

Detektory liczą progi od `meta.okno_od` i `meta.run_at`, **nie od zegara**.
Inaczej ten sam zamrożony snapshot dawałby inne wyniki w zależności od dnia
uruchomienia — a etap 4 opiera się na powtarzalności.

---

## Wycena kwot

Klasa `oszczednosc_bezposrednia` ma `wzor` i listę `zmienne_od_klienta`; klasa
`ryzyko` ma `kwota_pln: null`. Dziś wzory mają dwie klasy:

| Klasa | Wzór | Zmienna od klienta |
|---|---|---|
| `ZOMBIE_ACCOUNT` | `liczba_kont * koszt_licencji_mies * 12` | `koszt_licencji_mies` |
| `PLAN_MISMATCH` | `nadwyzka_miejsc * koszt_licencji_mies * 12` | `koszt_licencji_mies` |

**Kwota bez podstawy jest odrzucana mechanicznie.** Nie „prompt o to prosi" —
`REGULA_KWOTA_BEZ_PODSTAWY` sprawdza, czy run dostał każdą zmienną wzoru i czy
na tej stawce **wolno** było liczyć. To D6 w praktyce: odebranie możliwości,
nie prośba.

Kolejność reguł jest częścią kontraktu: precyzyjna `REGULA_KWOTA_PRZY_RYZYKU`
idzie **przed** ogólną „brak podstawy". Inaczej eval z etapu 4 pokazywałby jeden
powód zamiast dwóch i nie dałoby się odróżnić agenta, który wymyśla kwoty, od
takiego, który myli typ wyceny.

Cena licencji **nie jest scrapowalna** — na Enterprise jest negocjowana, więc
wchodzi ręcznie przez `--koszt-licencji-mies` (O7). Stawki publiczne pobiera
`cli_cennik` ze stron monday i mają przedziały rozsądku, datę ważności
i cytat źródłowy (D13).

---

## Pinowanie: sześć elementów

| Element | Gdzie |
|---|---|
| model | `runy.model`, pełny identyfikator, nigdy alias |
| rubryka | `rubric_ver` przy każdym findingu |
| prompt agenta | `runy.prompt_hash` — SHA-256 **wyciągniętego bloku**, nie całego pliku; otoczka dokumentacyjna nie zmienia hasha |
| collector | `meta.collector_ver` w snapshocie |
| **wersja API monday** | `meta.wersja_api` — bo `2026-10` usuwa wszystkie flagi użytkownika (O15) |
| **wersja cennika** | `runy.cennik_ver` — bo stawki odświeżają się same (D13) |

Dwa ostatnie doszły **z pomiarów, nie z projektu**. Run bez kwot zostaje
z `cennik_ver = NULL`, żeby nie pinować daty, która nie miała wpływu na wynik.

---

## Pomiary z prawdziwego konta

Konto CXLABS, workspace 6576039, snapshot **#5** z 2026-08-01.

### Collector

| | |
|---|---|
| wywołania monday | **227** |
| complexity | **638 798** |
| tablice | 105 (wszystkie aktywne, wszystkie z właścicielem) |
| kolumny | 902, maksymalnie 21 na tablicy |
| itemy | 559 — **tylko licznik**, treści nie zbieramy |
| użytkownicy | 95, z tego **36 to agenci AI**, 10 adminów, 12 gości, 28 tylko-podgląd |
| miejsc zajętych | 19 |
| automatyzacje | 80 widzianych, 7 z błędami, 7 z wyczerpaniem |
| wpisy activity log | 4 432 w oknie 90 dni |
| zredagowanych PII | **0** |

**Ponad jedna trzecia „kont" to agenci, nie ludzie** (O17). To jedna z liczb,
która zmienia rozmowę o koszcie licencji, i nie wyszłaby z samego `razem: 95`.

**94 z 105 tablic zdominowanych jednym autorem.** Sygnał, który collector
liczy, a którego żaden dzisiejszy detektor jeszcze nie używa.

### Agent

Run `agent-pelny-19`, 19 hipotez, `claude-sonnet-5`:

| | |
|---|---|
| koszt | **1,71 USD** |
| tokeny wejścia / wyjścia | 29 146 / 36 684 |
| tokeny z cache | 758 113 |
| findingi | **11 przyjętych, 0 odrzuconych na walidacji** |
| hipotezy odrzucone przez agenta | **8 z 19** |
| czas | ~17 minut |

Koszt czytamy z `total_cost_usd` z Agent SDK, **nie** z mnożenia tokenów przez
cennik zaszyty u nas. Pierwsza wersja liczyła `usage.input_tokens`, co pomija
cache — i moja ekstrapolacja z małej próby była wtedy **o 63% za niska**.

Run `agent-312-demo` (3 hipotezy, ze stawką 100 PLN) dał **2 findingi po
1200 PLN i jedno odrzucenie**, za 0,31 USD — i to on posłużył do sprawdzenia
renderera na prawdziwych danych.

**Odrzucenie 8 z 19 hipotez to sygnał, że pętla pracuje.** Agent, który
potwierdza wszystko, jest bezużyteczny — D8 wymaga niepustego
`hipotezy_odrzucone`, a run z jedną hipotezą dostaje ostrzeżenie w logu.

---

## Renderer raportu

Dwa pliki HTML z jednego runu, w `raporty/` (katalog jest w `.gitignore` — po
deanonimizacji dokument zawiera prawdziwe imiona, e-maile i nazwy tablic).

```bash
uv run python -m monday_audit.cli_raport --run-id agent-312-demo
```

Osobna komenda, nie doklejona do `cli_agent`: renderowanie jest darmowe i musi
dać się powtórzyć na zapisanym runie, bo etap 4 przepuszcza te same runy przez
nowy szablon.

**Trzy granice pilnowane testami**, każda na danych syntetycznych — bo na
snapshocie #5 wszystkie findingi są `widocznosc: klient`, więc prawdziwy run
tych granic NIE sprawdza:

1. żaden finding `tylko_wewnetrzne` w wersji klientowej
2. żadna treść `trop` w wersji klientowej
3. **żaden surowy hash w którejkolwiek wersji**

Granica 3 złapała usterkę, której nie złapał test jednostkowy: `tablice_dostepne`
w dowodzie `GUEST_SPRAWL` to mapa `user_hash → lista tablic`, czyli hash jest
**kluczem** słownika. Pierwsza wersja rekurencji schodziła tylko po wartościach
i przepuściła dziewięć hashy do obu plików.

**Filtrowanie odbiorcy jest w SQL, nie w szablonie.** Szablon jest edytowany
przy każdej zmianie wyglądu, przez osobę patrzącą na układ strony — warstwa,
którą rusza się najczęściej, nie może być ostatnią linią obrony.

`autoescape` jest włączony **jawnie**, bo jinja domyślnie go nie ma, a dokument
niesie nazwy tablic pisane przez klienta. Na tę jedną flagę jest test.

**Wygląd z CXLABS Design System** (D14): paleta ink + lime, skala 8pt, promienie,
podwójny szewron, znak marki osadzony jako `data:` URI. **Fontów nie osadzamy** —
licencja Clash Display zabrania osadzania w formie, z której da się je wyjąć,
a plik HTML to tekst. Stos schodzi na Avenira, czyli drugi krój marki. Pełna
zgodność = wydruk do PDF na maszynie z zainstalowanym fontem; instrukcja
w `szablony/fonty/README.md`, dwa testy strażnicze pilnują, żeby nikt tego nie
„poprawił".

## Dashboardy — makieta HTML (poprzedni krok, nadal działa)

Trzy statyczne pliki z jednego polecenia, linkowane relatywnie — klika się jak
aplikacja, a jest zwykłym HTML-em:

```bash
uv run python -m monday_audit.cli_pulpit --json
```

```
pulpity/index.html              panel wewnętrzny: lista klientów + drop-down
pulpity/<klient>/wewnetrzny.html   pełny widok
pulpity/<klient>/klient.html       to, co widzi klient
```

Układ z Docs Publishera: ciemny sidebar w ink, jasna treść, karty 16 px, duże
liczby, sekcje zwijane. Cztery sekcje agregatów **z samego snapshotu** — ludzie
i licencje, tablice, automatyzacje, aktywność — bez ani jednego nowego zapytania
do monday.

**Python zostaje przy danych, prezentacja jest wymienna (D15).** `pulpit.do_json()`
zwraca payload, który zobaczy front w JS; szablony jinja2 są jednym z jego
konsumentów, nie jedynym. Dzięki temu przejście na React albo komponenty
Docs Publishera jest podmianą widoku, nie przepisaniem logiki.

**Granica odbiorcy jest strukturalna.** Payload klienta **nie zawiera** kluczy
wewnętrznych (`pinowanie`, `koszt_usd`, `hipotezy_odrzucone`, …) — nie „nie
wyświetla ich". Przy froncie w JS to jedyny wariant, który cokolwiek znaczy,
bo szablon jest u odbiorcy. Osiem testów pilnuje tego na obu warstwach: HTML
i JSON.

**Bez serwera i bez interaktywności.** Filtry, sortowanie po kliknięciu
i porównania między audytami czekają na front w JS — w jinja2 zrobilibyśmy je
dwa razy. Makieta pokazuje układ i treść.

Dwie usterki wyszły dopiero ze **zrzutu ekranu**, nie z testów: znak marki
w wersji ink był niewidoczny na ciemnym sidebarze (HTML zawierał `<img>`, tylko
nikt go nie widział), a opis metryki zlewał się z procentem w jedno zdanie
(„99.0% z 105 statystyki są na poziomie konta"). Dowód, że przy warstwie
wizualnej trzeba patrzeć, nie tylko grepować.

Makieta została **zaakceptowana jako poziom docelowy** i na tym skończyła rolę:
dalej stoi aplikacja niżej, a te pliki zostają jako szybki podgląd bez serwera.

## Aplikacja web — jeden adres, dwa wejścia

```bash
uv run python -m monday_audit.cli_web --dodaj-klienta acme      # wypisuje hasło
uv run python -m monday_audit.cli_web --dodaj-osobe jle@cxlabs.digital
uv run python -m monday_audit.cli_web --serwuj --port 8010
```

Realna aplikacja: React 19 + Vite + TypeScript na froncie, FastAPI z tyłu,
sesje w SQLite. Klient wchodzi hasłem, wkleja **swój** klucz API monday, klika
„Wygeneruj audyt" i widzi pasek postępu. CXLABS wchodzi tym samym adresem, ale
e-mailem z domeny, i ma drop-down po wszystkich klientach.

**Granicę wyznacza sesja po stronie serwera, nigdy parametr z przeglądarki (D16).**
`GET /api/pulpit` nie przyjmuje `client_id` — bierze go z ciasteczka. Sesja
klienta pytająca o cudzego klienta dostaje **404, nie 403**, bo 403 potwierdza,
że taki klient u nas jest.

| Endpoint | Kto | Co zwraca |
|---|---|---|
| `POST /api/sesja/klient` | hasło klienta | ciasteczko `HttpOnly`, rola `klient` |
| `POST /api/sesja/zespol` | e-mail `@cxlabs.digital` + hasło | ciasteczko, rola `zespol` |
| `GET /api/pulpit` | z sesji | `pulpit.do_json()` — bez kluczy wewnętrznych dla klienta |
| `GET /api/klienci` | tylko zespół | lista do drop-downu (klient: 404) |
| `POST /api/audyt` | z sesji | id zadania; klucz API **w ciele**, nie w URL-u |
| `GET /api/audyt/<id>` | z sesji | stan, etap, postęp |

**Wybór wersji audytu.** Drop-down w pasku przełącza audyty tego samego klienta
po dacie („3 sierpnia 2026 — 11 znalezisk"); lewa strona wybiera klienta, drop-down
wersję. Klik w klienta rozwija pod nim sekcje otwartego audytu, a klik w sekcję
przewija do niej — rozwijając ją, jeśli była zwinięta.

`run_id` przychodzi z przeglądarki, więc serwer **sprawdza właściciela**
(`pulpit.run_nalezy_do`): obcy albo nieistniejący run daje 404 na oba przypadki.
Inaczej niż przy `client_id`, którego po prostu nie honorujemy — tu parametru nie
wolno zignorować, bo klient ma prawo obejrzeć swój starszy audyt.

Domyślnie panel pokazuje **najnowszy** zakończony audyt. Do 2026-08-06 pokazywał
najobszerniejszy — obejście chroniące przed naszymi runami diagnostycznymi, które
przy jawnym wyborze wersji zaczęło tylko zaskakiwać (dane z 1 sierpnia przy audycie
z 5 sierpnia). Szczegóły w aneksie do D16.

**Reset haseł.** Zespół resetuje swoje hasło w panelu („Moje hasło", wymaga
obecnego) i hasło klienta („Dostęp klienta" → „Zresetuj hasło klienta"). **Klient
nie może sam** — nie ma dla niego endpointu, a sesja klienta dostaje 404 na oba.
Droga ratunkowa z terminala: `--zresetuj-haslo EMAIL_LUB_CLIENT_ID`, na wypadek
zgubienia wszystkich haseł zespołu.

**Panel główny „Klienci"** — widok startowy zespołu: tabela wszystkich klientów
(audyty, znaleziska, oszczędność, data, dostęp), dodawanie klienta i resety haseł
w jednym miejscu. „Moje konto" ma odtąd TYLKO własne hasło.

**Kolejność sekcji z jednej funkcji** (`kolejnoscSekcji`) — sidebar i treść brały ją
z dwóch miejsc i rozjeżdżały się: nawigacja stawiała Znaleziska pierwsze, strona
ostatnie. Sortowanie `localeCompare("pl")`, bo „Aktywność"/„Automatyzacje" różnią się
na trzeciej literze, a polskie znaki w porównaniu bajtowym lądują po `z`.

**Nawigacja mobilna** w sidebarze (menu „widok") — pod 900 px `sidebar nav` jest
ukryty, więc bez niej na telefonie nie dało się przełączyć klienta.

**Dodawanie klienta z panelu** — „Moje konto" → „Dostępy klientów": identyfikator
i wygenerowane hasło pokazane raz. Identyfikator waliduje serwer wzorcem
`^[a-z0-9][a-z0-9-]{1,49}$`, bo trafia do adresów i nazw plików raportu. CLI
(`--dodaj-klienta`) zostaje jako droga ratunkowa.

**Przełącznik rozliczeń** `AGENT_ROZLICZENIE=klucz|subskrypcja` (domyślnie `klucz`).
`runy.rozliczenie` zapisuje, czym run był rozliczony — bez tego `koszt_usd` znaczy
raz wydatek, raz wycenę teoretyczną. Panel oznacza kwotę przy runach
subskrypcyjnych. Szczegóły w D17.

**Panel administracyjny „Moje konto"** (sidebar, tylko zespół) — własne hasło
i tabela dostępów wszystkich klientów: kto ma hasło, kto nie może się zalogować,
reset i nadanie dostępu. Lista klientów to suma tych z AUDYTAMI i tych z KONTEM;
do 2026-08-10 powstawała tylko z runów, więc panel ukrywał klienta z wydanym
hasłem, ale bez audytu, ORAZ klienta z audytem, który nie mógł się zalogować.

**„Nie pamiętam hasła"** (brama, zakładka zespołu) — jedyna droga hasła BEZ sesji,
i tak musi być: kto zgubił hasło, zalogować się nie może. Link na skrzynkę
`@cxlabs.digital`, ważny 30 minut, działa raz. Odpowiedź jest identyczna dla konta
istniejącego i nie, żeby brama nie zdradzała, które adresy są prawdziwe. Klient tej
drogi nie ma — brama mówi mu, żeby napisał do osoby prowadzącej audyt.

Poczta: `smtplib` ze stdlib, **bez nowej zależności**. Bez `SMTP_HOST` link idzie do
logu serwera z ostrzeżeniem (tryb awaryjny, nie docelowy).

Reset **nie wylogowuje**: otwarta sesja żyje do 12 h, więc panel i CLI mówią, ile
sesji zostaje ważnych. „Odetnij dostęp teraz" nie istnieje — O26.

Do 2026-08-10 ponowne `--dodaj-klienta` nie zmieniało hasła, tylko zakładało
drugie konto, a stare hasło nadal wpuszczało (czwarty guardrail bez pomiaru).
Migracja 007 dokłada unikalny indeks częściowy, a `utworz_konto` odmawia.

**Klucz API klienta nie ma kolumny w schemacie** i nie zostawia śladu. Zmierzone
2026-08-06 znacznikiem w kształcie JWT: przeszedł POST → collector → 401 z monday
i nie pojawił się ani w zrzucie bazy, ani w logu serwera, ani w argv procesów.
Test regresyjny pilnuje **ścieżki błędu**, bo to wyjątki cytują nagłówki żądania.

**Hamulec kosztu jest w bazie, nie w interfejsie:** odstęp 7 dni i sufit 4 audytów
na klienta, liczone w endpointcie — odświeżenie strony ani `curl` go nie obchodzą.
Osierocone zadania (proces padł w trakcie) zwalniają się po 40 minutach, i to
**przed** liczeniem odstępu — inaczej jeden zawieszony run blokowałby klienta
na tydzień.

**Limit prób logowania** blokuje na 15 minut po 5 nieudanych próbach, i blokuje
też **poprawne** hasło — gdyby odrzucało tylko złe, nie byłoby żadną blokadą.
Sprawdzone przez HTTP, nie tylko jednostkowo.

**Typy frontu są generowane z Pythona:**

```bash
uv run python -m monday_audit.generuj_typy            # zapisuje front/src/api.ts
uv run python -m monday_audit.generuj_typy --sprawdz  # jak `--check` w formatterze
```

Ręcznie pisane typy po obu stronach rozjechałyby się przy pierwszej zmianie pola
i to **cicho** — objawiłoby się `undefined` w interfejsie klienta, nie błędem
u nas. Test pilnuje aktualności pliku.

**Marka w jednym miejscu (D14):** `front/src/marka.css` jest kopią `_marka.css.j2`,
bo Vite nie czyta jinja — więc test porównuje tokeny obu arkuszy. Fontów nie
osadzamy ani tu, ani w raporcie: licencja Clash Display na to nie pozwala,
a CSS i bundle JS to tekst, z którego font da się wyjąć.

**Trzy usterki współbieżności, których 20 zielonych testów nie widziało.**
Wszystkie wyszły z uruchomienia prawdziwej przeglądarki na żywym serwerze —
panel mówił „nie ma jeszcze audytu tego konta", a `curl` w tej samej chwili
dostawał 200:

1. `sqlite3.ProgrammingError: SQLite objects created in a thread…` — FastAPI
   wykonuje endpointy synchroniczne w puli wątków. `TestClient` tego nie łapie,
   bo obsługuje żądania **po kolei, w jednym wątku**.
2. `database is locked` — front pyta o kilka endpointów równolegle.
3. Ta sama blokada **wracała** po włączeniu WAL i `busy_timeout`. Przyczyna była
   głębsza: transakcja, która najpierw **czyta**, a potem chce **pisać**, musi
   podnieść blokadę, a SQLite odrzuca takie podniesienie **natychmiast**, żeby
   nie doprowadzić do zakleszczenia. `busy_timeout` w tym miejscu nie działa.
   Wniosek, który został w kodzie: **ścieżka odczytu nie pisze.**

Test regresyjny strzela 16 żądaniami przez `ThreadPoolExecutor`, bo szeregowy
klient testowy nie odtwarza warunku, w którym usterka istnieje.

**Responsywność obejrzana, nie założona:** zrzuty przy 390, 900 i 1440 px. Przy
900 px sidebar zwija się w pasek u góry. Dwie pierwsze poprawki układu bramy były
nietrafione, bo zgadywałem, który element jest wąski, zamiast to zmierzyć —
winowajcą był `#korzen`, div bez ani jednej reguły CSS, zwężony jako element
flex. Komentarz w `aplikacja.css` o tym mówi.

## Wybór zakresu audytu — dwie bramki przed kosztem

Zbudowane 2026-08-25. **Pełny opis: `docs/WYBOR_ZAKRESU.md`** — tu tylko to,
co trzeba wiedzieć, zanim się coś w tym ruszy.

Klient wybiera zakres **przed** zbieraniem danych, nie po. Kolejność wymuszona
pomiarem: lista workspace'ów to 0,5 s i jedno wywołanie, tablice jednego
workspace'u 4,5 s i dwa — razem **~5 s i 0 USD**, bo model w tym nie
uczestniczy. Pełne zbieranie to 167 s, więc wybór po nim był wyborem, na który
trzeba czekać trzy minuty.

```
klucz monday → workspace → tablice → [Zbierz dane] → widełki + klucz Claude → [Zatwierdź]
     0,5 s        4,5 s     opcja        ~1 min          dokładna kwota           ~11 min
```

**Dwie bramki, obie świadome.** Pierwsza pokazuje zgrubny szacunek z liczby
tablic, druga — dokładne widełki z liczby sygnałów. Klucz Anthropic podaje się
dopiero w drugiej, czyli gdy kwota jest znana.

**Stan `czeka_na_zgode`** (migracja 012) to pauza między fazami. Zadanie stoi
w nim do 12 godzin; reaper 40-minutowy go **nie** dotyczy (osobny warunek na
`zgoda_do`) — pilnuje tego test podmieniający `zaczeto` w bazie.

**Agenta i detektorów nie tknięto.** Filtr `odsiej_hipotezy` stoi między nimi
jako czysta funkcja. `uruchom_detektory` nie ma parametru zawężającego
i mieć nie będzie.

**Podłoga kosztu ≈ 0,87 USD.** ZMIERZONE: 22 z 24 sygnałów w typowym runie
dotyczy KONTA (martwe konta, automatyzacje, wygaszeni użytkownicy, goście,
plan), nie tablic. Żaden wybór tablic ich nie usuwa, więc ekran mówi to wprost —
bez tego zdania odznaczanie tablic wygląda na zepsute.

**Czego wybór NIE obejmuje:** metryki i sekcja „Ludzie" liczą się z całego
snapshotu, więc pokazują wszystkie tablice niezależnie od zaznaczenia. To
**O38** — otwarte, z dwiema drogami wyjścia i ceną każdej.

## Czego jeszcze nie ma

| Brak | Gdzie opisany |
|---|---|
| **publiczny URL panelu** | **usługa stoi od 2026-09-01, vhost dla `audyt.cxlabs.digital` gotowy, rekord DNS w OVH jest** (`05-WYKONANE.md`). Brakuje **podpięcia hosta u operatora Mikrusa** — bez niego Cloudflare nie ma certyfikatu dla tej nazwy i TLS się zrywa — oraz konta zespołu. Ryzyko danych osobowych pod URL-em: O23 |
| **kopie zapasowe poza Mikrusem** | `deploy/backup.sh` gotowy, ale `CEL_ZDALNY` nie ma na co wskazywać. Kopia niesie `osoby_mapowanie` bez szyfrowania |
| **OAuth zamiast klucza wklejanego przez klienta** | warunek przed wystawieniem poza relację doradczą — aneks do D11, granice pamięci: O25 |
| **SSO na domenę `@cxlabs.digital`** | dziś hasło per osoba; O24 |
| wybór wielu workspace'ów naraz | backend potrafi, interfejs oferuje jeden — O37 |
| metryki i „Ludzie" zawężone do wybranych tablic | dziś z całego snapshotu — O38 |
| podział znalezisk po workspace'ach | tylko 2 z 11 znalezisk niesie `board_id`; wraca przy audycie całego konta |
| zużycie kredytów AI | API tego nie oddaje w żadnej sprawdzonej wersji — O2, O20 |
| liczba uruchomień automatyzacji per tablica | filtr `board_id` zepsuty w API — O12 |
| `AI_UNUSED` | klasa nieaktywna, `status: do_weryfikacji` |
| przelicznik kredyt → token | monday go nie publikuje — `docs/CENNIK_AI.md` |
| detektor na „tablica zdominowana jednym autorem" | dane są w snapshocie, klasy w rubryce nie ma |

---

## Rzeczy, na które trzeba uważać

Nie „ciekawostki" — każda kosztowała czas albo prawie weszła do produkcji.

**`updated_at` na tablicy jest bezużyteczne jako sygnał świeżości.** Zaniża
wiek o **do 40 dni** wobec activity logu (O18). Detektory `BOARD_GHOST`
i `ENGAGEMENT_DROP` liczą z logu.

**Introspekcja schematu monday kłamie o dostępności pól.** Pole może być
w schemacie i zwracać błąd, albo działać, choć go nie widać. Dlatego discovery
idzie **zapytaniem** (O17).

**Wersja API `2026-10` usuwa wszystkie flagi użytkownika.** Ten sam kod na
nieprzypiętej wersji zwróciłby inne dane, cicho (O15). Stąd przypięcie
i szósty element pinowania.

**Strony support monday zwracają 403 bez nagłówka `User-Agent`, a 200 z nim.**
Twierdziłem wcześniej, że są nieosiągalne, i na tej podstawie cała stawka
kredytu została opisana jako niepotwierdzona. Nieprawda — sprostowane
w `CENNIK_AI.md` i O21.

**`pydantic-settings` nie wkłada `.env` do `os.environ`.** Klucz Anthropic
istniał w konfiguracji, ale nie dochodził do podprocesu CLI, więc agent
uwierzytelniał się loginem subskrypcyjnym z `~/.claude` — runy działały, a ich
zużycia nie było w konsoli API. Klucz jedzie teraz jawnie przez
`ClaudeAgentOptions(env=...)`. Trzeci przypadek tej samej klasy błędu, po
`--read-only` i `can_use_tool`.

**Guardrail, w który się wierzy bez pomiaru, jest gorszy od braku guardraila.**
`--read-only` w MCP był udokumentowany jako „wymuszony przez serwer, nie do
obejścia z promptu". Pomiar to obalił. To samo powtórzyło się wewnątrz naszego
kodu z `can_use_tool`. Wniosek jest jeden: **warstwa odcięcia bez testu
sprawdzającego jej PODŁĄCZENIE nie jest warstwą.**

**Run, który padnie, musi zostawić status.** Pięć runów w bazie produkcyjnej
wisiało w `w_toku` bez śladu, dlaczego — bo status ustawiał się tylko na
happy pathie. Poprawione: `przerwij_run` w gałęzi błędu, w obu wejściach,
łapie też `KeyboardInterrupt`.

---

## Uruchomienie

```bash
# 1. Collector — kosztuje wywołania na koncie KLIENTA
uv run python -m monday_audit.cli --klient cxlabs --zakres workspace --id 6576039

# 2. Agent — kosztuje pieniądze za model. Tanie próby: --klasy i --limit
uv run python -m monday_audit.cli_agent --klient cxlabs --snapshot 5 \
    --klasy ZOMBIE_ACCOUNT --limit 1 --koszt-licencji-mies 100 \
    --zrodlo-stawki "faktura 07/2026"

# 3. Cennik — osobno, NIGDY w trakcie audytu
uv run python -m monday_audit.cli_cennik --odswiez --pokaz
```

Wynik runu agenta ląduje w `raporty/agent_<run_id>.txt` — katalog jest
w `.gitignore`, bo findingi zawierają nazwy tablic i kolumn klienta. Świadomie
w repo, a nie w katalogu tymczasowym: `/private/tmp` okazało się niewidoczne
w Finderze i nieodtwarzalne.

## Testy

**803 testy, 19 odznaczonych** (integracyjne, uderzają w prawdziwe monday —
`-m integracyjny` je włącza). `make sprawdz` to ruff + mypy + pytest.

Warstwy: jednostkowe na danych syntetycznych, integracyjne na koncie CXLABS,
plus testy pilnujące **granic**, nie funkcji — brak PII w wyjściu narzędzi,
parametryzacja zapytań SQL, podłączenie hooka odcinającego zapis, brak sekretu
w komunikacie błędu konfiguracji.
