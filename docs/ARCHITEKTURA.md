# Architektura — decyzje i uzasadnienia

Format każdej pozycji: **decyzja → powód → co ją unieważni**.

Powód jest ważniejszy od decyzji. Bez niego kolejna sesja zaproponuje
coś "lepszego" i cofnie ustalenie, które miało konkretną przyczynę.

---

## D1. Agent SDK, nie Managed Agents, nie gołe Messages API

**Decyzja:** pętla agenta działa w procesie workera, na Agent SDK.

**Powód:**
- **Managed Agents odpadają** przez cross-tenant credentials. Vault jest
  zaprojektowany pod stałe poświadczenia właściciela agenta, a my
  wstrzykujemy token innego klienta przy każdym runie. Dodatkowo tracimy
  kontrolę nad budżetowaniem limitów API klienta.
- **Gołe Messages API odpada**, bo musielibyśmy sami napisać pętlę
  tool-use, zarządzanie historią i obsługę błędów narzędzi.

**Co unieważni:** przejście na model abonamentowy z wieloma klientami
i cyklicznymi runami. Wtedy Managed Agents zyskują (sesje wznawialne,
memory store), a problem credentiali trzeba rozwiązać inaczej.

**Konsekwencja do zaakceptowania:** Anthropic Console **nie pokaże
trace'ów per sesja** — pokazuje je tylko dla Managed Agents. Obserwowalność
jest w całości na nas (patrz etap 6).

---

## D2. Sonnet w całej pętli, bez routowania modeli

**Decyzja:** jeden model wszędzie. Prompt caching na inwentarzu włączony
od pierwszego dnia.

**Powód:** routing Haiku/Sonnet/Opus przed pomiarem to zgadywanie.
Nie wiemy, który krok jest drogi. Caching na inwentarzu jest darmową
oszczędnością, bo inwentarz jest stały przez cały run.

**Co unieważni:** dane z etapu 6. Gdy zmierzymy koszt per krok, routing
staje się decyzją opartą na faktach.

---

## D3. SQLite, nie Postgres

**Decyzja:** SQLite z JSON1.

**Powód:** Mikrus ma 2 GB RAM **dzielone z innymi aplikacjami CXLABS**.
Postgres to ~250–500 MB rezydentnie. Jedyny argument za nim była kolejka
przez `FOR UPDATE SKIP LOCKED`, ale to ma sens przy równoległych workerach —
a my mamy jeden audyt naraz, odpalany ręcznie. SQLite obsłuży snapshoty
i findingi z zapytaniami po JSON-ie bez zająknięcia.

**Co unieważni:** wiele audytów równolegle albo model abonamentowy.
Migracja = wymiana warstwy dostępu, nie przepisywanie.

---

## D4. Bez MCP monday. Collector I agent na własnym kliencie

**ZMIENIONE 2026-08-03.** Pierwotna decyzja brzmiała „collector bez MCP, agent
z MCP `--read-only`" i jej jedyne uzasadnienie okazało się nieprawdziwe.

**Decyzja:**
- Collector (faza 1): czysty GraphQL przez `httpx`
- Agent (faza 2): **te same** `httpx` i `MondayClient`, narzędzia w
  `monday_audit.narzedzia`. Żadnego MCP, żadnego podprocesu Node.

**Powód zmiany — zmierzony, nie estetyczny.** Pierwotne D4 mówiło: „Read-only
wymuszony na poziomie serwera to mechanizm, nie polityka — model nie ma go jak
obejść, nawet przy prompt injection". Sprawdzone na
`@mondaydotcomorg/monday-api-mcp@3.3.0`:

| Sprawdzenie | Wynik |
|---|---|
| lista narzędzi z `--read-only` | **92, te same co bez flagi** — z `create_item`, `delete_item`, `create_board`, `all_api_write`, `execute_code` |
| `create_board` z `--read-only` | **przeszło do API monday** (serwer zbudował `mutation createBoard` i wysłał) |
| `all_api_write` z surową mutacją | **przeszło do API monday** |

Oba nie powiodły się WYŁĄCZNIE dlatego, że token był atrapą — odpowiedź to
`401 Not authenticated`. Z prawdziwym tokenem powstałaby tablica. Flaga nie
filtruje listy narzędzi ani nie blokuje wywołania.

**Co daje własny klient, czego MCP nie dawał:**
- `przygotuj_zapytanie()` **odrzuca `mutation` i `subscription`** niezależnie od
  wielkości liter i wiodących spacji. To jest odebranie możliwości z D6:
  w tej ścieżce kodu nie ma jak wysłać zapisu, a nie „serwer powinien odmówić".
- licznik wywołań i hamulec complexity — MCP nie liczył ani jednego
- zapis KAŻDEGO wywołania do tabeli `wywolania` (D10) — MCP nie zapisywał
- brak 92-narzędziowej powierzchni, w tym `execute_code`

**Koszt:** trzeba było napisać narzędzia samemu. Wyszło ich dwa do API monday,
bo przy mapowaniu `rola_agenta` wszystkich 11 klas okazało się, że resztę
pytań odpowiada snapshot.

**Zysk uboczny:** z drogi runtime znika Node 20 jako zależność MCP (Agent SDK
nadal go potrzebuje). Przy okazji: `isolated-vm`, zależność natywna MCP,
nie kompiluje się na Node 25.

**Co unieważni:** naprawa `--read-only` po stronie monday. Wtedy MCP wraca
do rozważenia — ale już nie jako mechanizm bezpieczeństwa, tylko jako wygoda,
i bez budżetów oraz `wywolania` nadal przegrywa.

---

## D5. Nie schodzimy na poziom itemów

**Decyzja:** `items_count` to granica. Zero pobierania treści itemów.

**Powód:** jednym ruchem rozwiązuje dwa problemy — objętość (setki tysięcy
rekordów) i PII (cała treść biznesowa klienta). Dzięki temu audyt średniego
konta to ~250 wywołań API, czyli 2,5% dziennego limitu na planie Pro.

**Jedyny wyjątek:** klasa `BOARD_OVERCOMPLEX` wymaga próbki itemów, żeby
ocenić wypełnienie kolumn. Sampling jawnie ograniczony, oznaczony
w `OTWARTE.md`. Jeśli okaże się drogi, ta klasa wypada pierwsza.

**Co unieważni:** gdyby więcej niż jedna klasa okazała się niewykonalna
bez napływu itemów. Wtedy decyzja do przemyślenia od nowa — ale świadomie,
z akceptacją kosztu PII.

---

## D6. Granice zaufania

| Granica | Mechanizm |
|---|---|
| **Dane niezaufane** | nazwy tablic, kolumn, itemów i treść updateów pisał klient — mogą zawierać prompt injection |
| **Obrona** | nie filtrowanie, a **odebranie możliwości**: `przygotuj_zapytanie()` odrzuca `mutation` i `subscription`, więc w kodzie narzędzi nie ma ścieżki zapisu. **NIE polegamy na `--read-only` w MCP — sprawdzone, nie blokuje (D4)** |
| **Maksymalna szkoda** | fałszywe znalezisko w raporcie. Nie wyciek, nie modyfikacja |
| **Wyjście** | strukturalny JSON, każdy finding z obowiązkowym `dowod` wskazującym na fakt ze snapshotu. Bez dowodu — odpada na walidacji |
| **PII** | pseudonimizacja przed modelem, tabela mapowania bez żadnego narzędzia dostępowego |
| **Poświadczenia** | token klienta w konfiguracji procesu workera (D12), nigdy w kontekście modelu ani w argv |
| **Koszt** | budżet wywołań per hipoteza (z rubryki) + bezpiecznik globalny 600/run |

Wszystkie granice składają się w jedną zasadę: **agent tylko czyta
i tylko proponuje.** Publikuje worker, po walidacji.

To jest "AI myśli, automat przenosi dane, człowiek decyduje" zapisane
jako architektura, nie hasło.

---

## D7. Schemat danych

Wszystkie tabele są `STRICT`. Bez tego SQLite typuje doradczo i wpuści tekst
do kolumny liczbowej — a te liczby idą potem do raportu klienta.

Implementacja: `src/monday_audit/migracje/001_schemat.sql`.

```sql
CREATE TABLE snapshots (
  id            INTEGER PRIMARY KEY,
  client_id     TEXT    NOT NULL,
  run_at        TEXT    NOT NULL,
  collector_ver TEXT    NOT NULL,
  payload       TEXT    NOT NULL,   -- JSON, niemutowalny
  CHECK (json_valid(payload))
) STRICT;

-- Niemutowalność jako mechanizm, nie opis. DELETE zostaje dozwolony:
-- usunięcie danych klienta musi być wykonalne.
CREATE TRIGGER snapshots_bez_update
BEFORE UPDATE ON snapshots
BEGIN
  SELECT RAISE(ABORT, 'snapshot jest niemutowalny (D7)');
END;

CREATE TABLE runy (
  run_id      TEXT    PRIMARY KEY,
  client_id   TEXT    NOT NULL,
  snapshot_id INTEGER REFERENCES snapshots(id),
  status      TEXT    NOT NULL,
  started_at  TEXT    NOT NULL,
  finished_at TEXT,
  model       TEXT,                 -- pełny identyfikator, alias zakazany
  rubric_ver  TEXT,
  prompt_hash TEXT,
  wywolania_monday      INTEGER,    -- agregaty per run (etap 6)
  complexity_suma       INTEGER,
  tokens_in             INTEGER,
  tokens_out            INTEGER,
  findingow             INTEGER,
  odrzuconych_walidacja INTEGER,
  hipotez_zbadanych     INTEGER,
  hipotez_odrzuconych   INTEGER,
  CHECK (status IN ('w_toku', 'zakonczony', 'przerwany'))
) STRICT;

CREATE TABLE findings (
  id           INTEGER PRIMARY KEY,
  run_id       TEXT    NOT NULL REFERENCES runy(run_id),
  snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id),
  klasa_id     TEXT    NOT NULL,
  rubric_ver   TEXT    NOT NULL,
  waga         TEXT    NOT NULL,
  wysilek      TEXT    NOT NULL,
  typ_wyceny   TEXT    NOT NULL,
  kwota_pln    REAL,               -- NULL dla typu `ryzyko`
  widocznosc   TEXT    NOT NULL,
  opis         TEXT    NOT NULL,
  rekomendacja TEXT    NOT NULL,
  dowod        TEXT    NOT NULL,   -- JSON, obiekt
  pewnosc      TEXT    NOT NULL,
  trop         TEXT,               -- tylko wersja wewnętrzna
  CHECK (json_valid(dowod) AND json_type(dowod) = 'object')
) STRICT;

CREATE TABLE hipotezy_odrzucone (
  id        INTEGER PRIMARY KEY,
  run_id    TEXT    NOT NULL REFERENCES runy(run_id),
  klasa_id  TEXT    NOT NULL,
  obiekt_id TEXT,
  powod     TEXT    NOT NULL
) STRICT;

CREATE TABLE osoby_mapowanie (
  client_id     TEXT NOT NULL,
  user_hash     TEXT NOT NULL,
  imie_nazwisko TEXT,
  email         TEXT,
  PRIMARY KEY (client_id, user_hash)
) STRICT;
-- Agent NIE MA narzędzia czytającego tę tabelę. Renderer ma.

CREATE TABLE wywolania (
  id          INTEGER PRIMARY KEY,
  run_id      TEXT    NOT NULL REFERENCES runy(run_id),
  hipoteza_id TEXT,
  narzedzie   TEXT    NOT NULL,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  latency_ms  INTEGER,
  complexity  INTEGER,             -- complexity { query after } z 3.2
  model       TEXT,
  at          TEXT    NOT NULL
) STRICT;
```

Indeksy: `snapshots(client_id, run_at)`, `findings(snapshot_id)`,
`findings(run_id)`, `runy(snapshot_id)`, `hipotezy_odrzucone(run_id)`,
`wywolania(run_id)`.

**Klucze obce wymagają `PRAGMA foreign_keys = ON` przy każdym połączeniu.**
SQLite ma to domyślnie wyłączone, więc bez tego `REFERENCES` powyżej są
dekoracją. Ustawia to `monday_audit.baza.polacz()`.

**Słowniki rubryki (`waga`, `wysilek`, `typ_wyceny`, `widocznosc`) świadomie
NIE są zamknięte w `CHECK`.** Rubryka jest wersjonowana niezależnie od bazy —
każdy finding niesie `rubric_ver` — więc zmiana słownika w rubryce nie może
wymagać migracji schematu. Waliduje je kod przeciwko wczytanej rubryce (3.11).
`CHECK` na `runy.status` jest, bo to słownik własny, nie z rubryki.

---

### Pięć uzupełnień wobec pierwotnego zapisu D7

Wykryte przy 3.1, gdy schemat miał trafić do migracji. Każde wynika
z wymagania, które istniało już w innym dokumencie i nie miało gdzie usiąść.

| Uzupełnienie | Powód |
|---|---|
| `findings.run_id` | Sensem niemutowalnego snapshotu jest powtórne przepuszczenie agenta. Etap 4 mierzy powtarzalność przez porównanie dwóch runów na jednym snapshocie — przy samym `snapshot_id` findingi z obu są nierozróżnialne |
| tabela `runy` | Etap 5 wymaga pinowania czterech elementów per run. `collector_ver` siedzi w `snapshots`, ale pełny identyfikator modelu i hash promptu nie miały miejsca. Etap 6 dokłada agregaty per run |
| tabela `hipotezy_odrzucone` | D8 czyni je obowiązkowymi i nazywa głównym wejściem do evali; etap 4 wymaga 100% niepustych. Bez tabeli nie ma gdzie ich zapisać |
| `findings.pewnosc` | Kontrakt D8 to produkuje, schemat nie przyjmował |
| `client_id` w `osoby_mapowanie` | To magazyn PII. Sól jest osobna per klient, a po audycie dostęp jest odbierany (D11) — bez `client_id` nie da się skasować mapowań jednego klienta |

**`runy` obejmuje cały przebieg**, nie samą fazę agentową: collector →
detektory → agent. Powód: etap 6 wymaga per run zarówno metryk collectora
(wywołania monday, complexity), jak i agenta (tokeny, findingi). Gdyby run
zaczynał się przy agencie, `wywolania.run_id` nie miałoby do czego wskazywać
w czasie zbierania danych.

**Konsekwencja do zaakceptowania:** `snapshot_id`, `model`, `rubric_ver`
i `prompt_hash` są NULL-owalne, bo wypełniają się etapami. Komplet tych
czterech to warunek domknięcia runu — pilnuje go kod, nie baza.

**Dlaczego snapshot jest niemutowalny i oddzielony od findingów** — to
najważniejsza decyzja projektowa w całym systemie:

1. Możesz przepuścić agenta ponownie po starym snapshocie **bez dotykania
   konta klienta**. To jest harness ewaluacyjny za darmo.
2. Snapshot #1 vs #4 u tego samego klienta = case study z liczbami.
3. Gdy agent zmyśli, masz `dowod` + snapshot, żeby to wychwycić.

**Co unieważni:** zejście na poziom itemów (gdyby padło D5) — wtedy `payload`
jako jeden JSON przestaje się skalować i snapshot trzeba znormalizować.
Drugi warunek to wiele audytów równolegle: `runy` zakłada jeden przebieg
naraz, a `snapshots` bez wersjonowania schematu payloadu utrudni porównywanie
snapshotów zebranych różnymi wersjami collectora niż sam numer `collector_ver`.

**Zastosowane migracje są niezmienne.** Tabela `_migracje` trzyma sumę
kontrolną SHA-256 każdego pliku; edycja już zastosowanej migracji przerywa
działanie z błędem. Bez tego baza i pliki rozjeżdżają się po cichu,
a etap 5 wymaga odtwarzalności audytu sprzed miesięcy.

---

## D8. Kontrakt wyjściowy agenta

Agent zwraca wyłącznie to. Walidacja przed rendererem, bez wyjątków.

```json
{
  "run_id": "string",
  "snapshot_id": 0,
  "rubric_version": "0.1",
  "findings": [
    {
      "klasa_id": "PROCESS_BYPASS",
      "waga": "krytyczna",
      "wysilek_naprawy": "wysoki",
      "typ_wyceny": "ryzyko",
      "kwota_pln": null,
      "opis": "string — po polsku, dla człowieka",
      "rekomendacja": "string — konkretna akcja, nie ogólnik",
      "dowod": { "board_stary": 123, "boardy_nowe": [456, 789] },
      "pewnosc": "wysoka|srednia|niska"
    }
  ],
  "hipotezy_odrzucone": [
    { "klasa_id": "BOARD_GHOST", "board_id": 111, "powod": "archiwum roczne" }
  ],
  "zuzycie": { "wywolania": 0, "tokens_in": 0, "tokens_out": 0 }
}
```

**Reguły walidacji:**
- `klasa_id` musi istnieć w `rubryka_znalezisk.yaml`
- `dowod` niepuste, a jego klucze muszą pokrywać pola wymagane
  przez `dowod` w definicji klasy
- `kwota_pln` niezerowe **tylko** przy `typ_wyceny: oszczednosc_bezposrednia`
- `waga` i `wysilek_naprawy` ze słowników rubryki
- klasa ze `status: do_weryfikacji` (np. `AI_UNUSED`) → finding odrzucony

**`hipotezy_odrzucone` jest obowiązkowe.** Agent musi raportować, czego
nie potwierdził i dlaczego. Bez tego nie da się ocenić, czy działa —
i to jest główne wejście do evali w etapie 4.

---

## D9. Wyjście: szablon + wstrzyknięcie JSON

**Decyzja:** brak frontu w v1. Szablon HTML z Claude Design w CXLABS
Design System, worker wstrzykuje JSON, publisher wystawia na
`docs.cxlabs.digital/klient/RRRR-MM_audyt_konta.html`.

**Powód:** raport jest statyczny na dany run. Nie ma czego klikać.
Ten sam wzorzec jest już zwalidowany w Proposal Engine.

**Dwa wyjścia z jednego runu:**
- wewnętrzna — wszystkie findingi + pole `trop`
- klientowa — bez klas oznaczonych `tylko_wewnetrzne`, bez `trop`

**ZBUDOWANE 2026-08-05 (3.12), z trzema doprecyzowaniami z praktyki:**

**1. Filtrowanie odbiorcy jest w SQL, nie w szablonie.** `WHERE widocznosc =
'klient'` stoi w zapytaniu, a `trop` w ogóle nie wchodzi do struktury
przekazywanej szablonowi. Powód: szablon jest edytowany przy KAŻDEJ zmianie
wyglądu, przez osobę patrzącą na układ strony, a nie na granice zaufania.
Warstwa, którą rusza się najczęściej, nie może być ostatnią linią obrony.
Trzy granice pilnowane testami na danych syntetycznych: brak findingu
`tylko_wewnetrzne`, brak treści tropu, brak surowego hasha.

**2. `autoescape` jawnie włączony.** Jinja domyślnie ma go **wyłączony**,
a dokument niesie nazwy tablic i kolumn pisane przez klienta. Bez tej flagi
nazwa `Oferty <b>2026</b>` rozwala układ, a `<script>` staje się skryptem.
To jedna flaga, którą łatwo zgubić przy refaktorze — dlatego ma test, nie
komentarz.

**3. Nazwiska idą do OBU wersji.** Pierwotny zapis sugerował deanonimizację
tylko wewnętrzną. Raport mówiący „konto 05677b1ab370bae1 jest martwe" jest
**niewykonalny**: klient nie wie, o kogo chodzi, więc rekomendacja „zwolnij to
miejsce" nie da się wykonać. Granica PII z D6 dotyczy kontekstu MODELU, nie
dokumentu — renderer jest zwykłym kodem i działa po zakończeniu analizy.
Różnica między wersjami to `tylko_wewnetrzne`, `trop`, odrzucenia, pinowanie
i koszt.

Publikacja pod URL-em **przeszła do etapu 5** — skilla `cxlabs-docs-publisher`
w repo nie ma, a Caddy stoi dopiero tam. 3.12 daje pliki, które otwierają się
z dysku i drukują do PDF.

**Co unieważni:** produkt drugi (monitor subskrypcyjny). Tam React
Artura ma sens, bo pojawia się interaktywność i notyfikacje.

---

## D10. Obserwowalność własna, nie Langfuse

**Decyzja:** tabela `wywolania` w SQLite.

**Powód:** Langfuse od v3 wymaga ClickHouse + Redis + storage na blobach.
Sam ClickHouse chce więcej RAM-u, niż zostaje na Mikrusie. Langfuse Cloud
ma darmowy tier, ale trace'y wychodzą z naszej infrastruktury, co komplikuje
narrację o danych klienta. Przy ~20 audytach miesięcznie SQL odpowie
na każde pytanie, które zadalibyśmy Langfuse'owi.

**Co unieważni:** wolumen. Jeśli logujemy w znormalizowanej tabeli,
eksport do Langfuse'a później to skrypt.

---

## D11. Brak OAuth w v1

**Decyzja:** token read-only od admina klienta, przekazany w ramach
relacji doradczej. Bez aplikacji OAuth.

**Powód:** to narzędzie wewnętrzne, odpalane ręcznie przez CXLABS.
Klient świadomie daje dostęp. Aplikacja OAuth była potrzebna przy
self-service dla obcego prospekta — a tego nie robimy.

**Konsekwencja:** brak przechowywania refresh tokenów, więc nie stajemy się
depozytariuszem dostępu do kont klientów. Token wygasa albo jest usuwany
po audycie.

**Co unieważni:** produktyzacja. Wtedy OAuth wraca jako wymóg,
razem z szyfrowaniem at-rest i zapisem w umowie.

---

## D12. Konfiguracja przez pydantic-settings, precedencja env > plik

**Decyzja:** jeden moduł `konfiguracja.py` jako wyłączne wejście do środowiska.
Kolejność źródeł: argument wywołania → zmienna środowiskowa → plik `.env` →
wartość domyślna. Sekrety mieszkają w `SecretStr`.

**Powód:** pierwotnie sekrety szły wyłącznie przez `os.environ`, a program
świadomie nie znał ścieżki do `.env`. To była pomyłka w rozumieniu granicy:
zakaz czytania `.env` dotyczy **narzędzi Claude Code**, nie aplikacji. Skutkiem
był wymóg `export` przed każdym uruchomieniem — nieodtwarzalny na serwerze,
gdzie worker z etapu 5 leci jako proces jednorazowy z katalogu innego niż root
repo, więc nie ma powłoki, w której ten `export` miałby się wykonać.

Biblioteka zamiast własnego czytnika, bo daje dokładnie tę precedencję jako
domyślną. Napisane ręcznie znaczy gałąź „a jeśli w env już coś stoi" w każdym
miejscu odczytu osobno. Koszt jest zerowy: `pydantic-settings` był już
w drzewie zależności tranzytywnie, przez `mcp` z Agent SDK — deklaracja
w `pyproject.toml` tylko nazywa to, co i tak było instalowane.

**Zmierzone, nie założone:** pydantic wkłada do `ValidationError.errors()[i]["input"]`
**surowe** wejście pola, także wtedy gdy walidator jest `mode="after"` i sam
dostaje już zamaskowany `SecretStr`. Czyli `str(ValidationError)` zawiera
odrzucony sekret jawnym tekstem. Dlatego `wczytaj()` przechwytuje ten wyjątek,
buduje komunikat wyłącznie z nazw pól i powodów, i urywa łańcuch przyczyn przez
`from None`. Pilnuje tego test — bo `from blad` wygląda porządniej i ktoś to
kiedyś „poprawi".

**Konsekwencja, którą trzeba nazwać wprost:** skoro kod czyta `.env` sam, to
testy integracyjne uderzające w prawdziwe monday nie wymagają już udziału
człowieka — wystarczy, że plik istnieje. Kuba przyjął to świadomie
(rozmowa 2026-07-31), odrzucając wariant z dodatkową bramką. Blokada
`Read`/`Edit`/`Write` na `.env` w `.claude/settings.json` zostaje i jest inną
granicą: dotyczy dostępu do pliku, nie uruchamiania programu.

**PUŁAPKA, KTÓRA NAS ZŁAPAŁA (2026-08-05).** `pydantic-settings` wczytuje `.env`
**do obiektu `Ustawienia`, a NIE do `os.environ`**. Zmierzone:
`"ANTHROPIC_API_KEY" in os.environ` jest `False` także po `wczytaj()`.

Konsekwencja była poważna i cicha. `klucz_anthropic()` tylko sprawdzał, że klucz
istnieje, a jego docstring twierdził: „Agent SDK czyta zmienną ze środowiska
podprocesu sam". Nie czytał — bo w środowisku jej nie było. Podproces CLI spadał
więc na **własne poświadczenia** (login subskrypcyjny w `~/.claude`): runy
działały, findingi wychodziły, `total_cost_usd` się liczył, ale **zużycia nie
było w konsoli API**, bo szło na subskrypcję. Wyszło to z pytania Kuby
„dlaczego nie widzę, żeby agent zużywał tokeny".

**Poprawka:** klucz jedzie jawnie przez `ClaudeAgentOptions(env=...)`.
`options.env` **dokłada się** do odziedziczonego środowiska (SDK składa
`{**inherited_env, ..., **options.env}`), więc PATH zostaje, a klucz trafia do
env podprocesu — nie do argv, bo argv widać w `ps`.

**Wniosek szerszy, trzeci raz ta sama klasa błędu.** Wcześniej: flaga
`--read-only` w MCP (O19) i callback `can_use_tool` (3.11). Za każdym razem
mechanizm był udokumentowany, wyglądał na działający i **nie chodził**, a testy
tego nie łapały, bo sprawdzały elementy w izolacji zamiast ich PODŁĄCZENIA.
Dlatego konstrukcja opcji sesji jest teraz osobną, testowalną funkcją
(`agent.zbuduj_opcje`), a nie kodem w środku pętli.

**Co unieważni:** wiele kont obsługiwanych w jednym procesie. Sól jest osobna
per klient (05-deploy.md), a jedna zmienna `SOL_PSEUDONIMIZACJI` obsługuje jeden
audyt naraz. Przy runach współbieżnych sól musi wejść jako parametr runu,
nie jako element środowiska procesu.

---

## D13. Stawki jako dane z pochodzeniem, odświeżane scraperem

**Decyzja:** stawki mieszkają w tabelach `cennik` i `stawki_klienta`, a nie
w pliku w repo. Publiczne odświeża `cli_cennik --odswiez`, pobierając strony
monday. Cena licencji u klienta wchodzi wyłącznie ręcznie.

**Rozważone i odrzucone:**

| Wariant | Dlaczego nie |
|---|---|
| liczby w `docs/CENNIK_AI.md` | tak było do 2026-08-04. Cenniki się zmieniają, a markdownu nikt nie odświeża. Przy froncie i publikacji na Marketplace stara stawka w raporcie klienta staje się błędem, nie niedogodnością |
| plik `cennik.yaml` w repo, jak rubryka | rubryka to nasza decyzja i wersjonuje się razem z kodem. Stawka to cudzy fakt zmieniający się bez naszego udziału — commit nie jest właściwym mechanizmem aktualizacji. Kuba odrzucił ten wariant wprost („plik w repo mi trochę nie pasuje") |
| pobieranie w trakcie audytu | run przestałby być odtwarzalny (D7) i zależałby od tego, czy cudza strona odpowiada w danej minucie. Osobna komenda, osobny moment |
| API monday jako źródło ceny | ceny tam nie ma. `Plan` oddaje `max_users`, `period`, `tier`, `version`; jedyne pola cenowe dotyczą ceny aplikacji NA Marketplace, czyli tego, co klient płaciłby nam |
| podstawienie ceny z publicznego cennika jako `koszt_licencji_mies` | cena Enterprise jest negocjowana. Dałoby liczbę pewnie brzmiącą i błędną — dokładnie ten rodzaj wpadki, którą rubryka nazywa podważającą cały raport (O7) |

**Co z tego wynika dla kodu:**

`cennik.py` jest **jedynym** wejściem do stawek. Front z etapu 5 podłącza
formularz i przycisk „odśwież" do tych samych funkcji, które woła CLI — zero
zmian w kodzie liczącym kwoty.

Kwota nigdy nie jest samym `float`. `Stawka` niesie wartość razem z wiekiem,
źródłem i wiarygodnością, bo kwota w raporcie klienta musi dać się sprawdzić.

**Trzy zabezpieczenia, każde z konkretnego ryzyka:**

1. **Przedziały rozsądku przy ZAPISIE.** Scraper czytający cudzy HTML pomyli
   się kiedyś na pewno — pytanie tylko, czy zauważymy. Strona monday zawiera
   zarówno `$0.01` (stawka kredytu), jak i `$9` (cena planu per user), więc
   bez sufitu można policzyć klientowi kwotę 900 razy za dużą.
2. **Niepowodzenie NIE nadpisuje niczego.** Zostaje ostatnia dobra wartość
   ze swoją datą, a komenda kończy się kodem błędu. Cichy zapis śmiecia jest
   groźniejszy od braku odświeżenia.
3. **Przeterminowanie jest sygnałem, nie ciszą.** Po `wazna_do` stawka nadal
   się zwraca, ale liczenie na niej jest zabronione mechanicznie
   (`REGULA_KWOTA_BEZ_PODSTAWY`). Cicho zgniła stawka w raporcie klienta jest
   groźniejsza od braku kwoty.

Punkt 3 to D6 w praktyce: **odebranie możliwości, nie prośba w prompcie.**
Prompt agenta mówi, żeby brał stawki wyłącznie z sekcji PARAMETRY WYCENY,
ale to warstwa dodatkowa — kontrakt sprawdza mechanicznie, czy każda zmienna
wzoru miała stawkę i czy wolno było na niej liczyć.

**Nowa zależność, którą trzeba nazwać wprost:** to pierwsze wyjście sieciowe
poza API monday. Zamknięte w osobnej komendzie, więc nie dotyka runu audytu,
ale profil ryzyka zmienia się przy publikacji na Marketplace — aplikacja
z Marketplace regularnie scrapująca strony własnego dostawcy to inna sytuacja
niż wewnętrzny skrypt. Zapisane jako **O22**, do rozstrzygnięcia przed
publikacją. Awaryjna droga już istnieje: `sposob = 'reczna'` jest w schemacie
i obsłużony.

**Co unieważni:** udostępnienie przez monday maszynowego źródła cennika
(endpoint albo plik). Wtedy scraper znika, a `cennik.py` zostaje bez zmian —
i o to chodziło w rozdzieleniu.

---

## D14. Marka z Claude Design, ale bez osadzania fontów

**Decyzja:** raport używa tokenów z CXLABS Design System (projekt Claude Design
`2b90221c-6624-4eec-9151-ab20b3af3b2d`, plik `colors_and_type.css`), **przeniesionych
do szablonu**, a nie zaimportowanych. Fontów marki **nie osadzamy** — dokument
odwołuje się do zainstalowanych w systemie.

**Co weszło:** paleta ink + lime, skala odstępów 8pt, promienie (karty 16, karty
statystyk 24, przyciski 12), cienie użytkowe, skala typograficzna, podwójny
szewron jako eyebrow. Znak marki osadzony jako `data:` URI — własny zasób CXLABS,
bez ograniczeń.

**Czego NIE wzięliśmy, choć było w projekcie:**

| | Dlaczego nie |
|---|---|
| `templates/oferta-cennik/**` | to szablon **oferty handlowej** z kalkulatorem wyceny. Budujemy raport, nie ofertę — decyzja z 2026-08-05. Wciągnięcie tego zatarłoby granicę, którą Kuba postawił wprost |
| `ui_kits/website/**` (React) | repo jest Pythonem bez frontu w v1 (D9). Komponenty React nie mają tu czego renderować |
| `@import` Google Fonts z `colors_and_type.css` | zewnętrzny zasób; raport musi otwierać się offline i jest na to test |
| `screenshots/`, `uploads/`, `assets/images/` | zdjęcia ludzi i miniatury case studies — raport z audytu ich nie używa |

**Ograniczenie licencyjne, które przesądziło sprawę fontów.** Clash Display jest
darmowy, ale jego EULA (`szablony/fonty/FFL.txt`) mówi:

> **§02** The Fonts may not […] be distributed […] This includes the distribution
> of the Fonts by e-mail […] **uploading them in a public server**.
>
> **§03** You may embed the Font Software in PDF and other digital documents
> provided that is done in a **secured, read-only mode** […] **The extraction of
> the Font Software in whole or in part is prohibited.**

Plik HTML jest **tekstem**, więc `data:` URI z woff2 każdy odbiorca wyjmie jednym
poleceniem. To łamie §03, a trzymanie binarki w repo łamie §02, bo repo idzie na
GitHub. Avenir jest komercyjny (Linotype) i tam jest jeszcze ciaśniej.

**Rozwiązanie:** stos `"Clash Display", "Avenir Next", "Avenir", …`. Degradacja
spada na **drugi krój marki**, nie na losowy systemowy — README marki zabrania
systemowych jako podstawowych, więc to najbliższe zgodności, co da się osiągnąć
bez łamania licencji. Pilnują tego **dwa testy**: brak `@font-face` w dokumencie
i brak binarek fontów w repo. Mechanizm, nie komentarz — bo „poprawienie" wyglądu
przez osadzenie fontu jest dokładnie tym, co ktoś kiedyś zrobi.

**Droga do pełnej zgodności:** licencja wprost dopuszcza (§03, §04) font osadzony
w nieedytowalnym dokumencie „solely for printing and display purposes". Czyli:
zainstaluj Clash Display lokalnie, otwórz raport, **wydrukuj do PDF**, wyślij PDF.
Instrukcja w `src/monday_audit/szablony/fonty/README.md`.

**Co unieważni:** wykupienie licencji webfontowej Avenira i Clash Display albo
zgoda ITF na piśmie. Wtedy fonty wchodzą do `szablony/fonty/`, a dwa testy
strażnicze trzeba świadomie usunąć — nie obejść.

---

## D15. Front wraca do zakresu. Python zostaje przy danych, prezentacja jest wymienna

**Decyzja:** powstaje front — panel wewnętrzny CXLABS z drop-downem klientów
i panel dla klienta za hasłem. **Agregacja i granice zostają w Pythonie,
prezentacja jest warstwą wymienną.**

**To unieważnia D9 w części „brak frontu w v1".** Warunek unieważnienia był tam
zapisany jako „produkt drugi (monitor subskrypcyjny). Tam React Artura ma sens,
bo pojawia się interaktywność i notyfikacje". Panel dla klienta z logowaniem to
dokładnie ten zwrot — zapisujemy go jako świadomą zmianę, nie przemilczamy.
Raport z 3.12 **nie znika**: Kuba zdecydował „panel zastępuje raport" jako to,
co dostaje klient, a dokument zostaje eksportem datowanej wersji.

**Podział, który jest tu całą treścią decyzji:**

| Warstwa | Gdzie | Dlaczego tam |
|---|---|---|
| agregacja, deanonimizacja, podział odbiorcy | **Python (`pulpit.py`)** | tu żyją dane i granice bezpieczeństwa; to najtrudniejsza i najlepiej testowalna część |
| widok | **wymienny** — dziś jinja2, docelowo React albo komponenty Docs Publishera | Python nie jest technologią frontu: filtry, sortowanie po kliknięciu i wykresy wychodzą w nim topornie |

Mechanizmem, który zamienia to z obietnicy w fakt, jest **`pulpit.do_json()`**:
ta sama struktura idzie do szablonu i do payloadu. Test sprawdza, że przechodzi
przez `json.dumps`. Bez tego „przepisujemy szablony, nie logikę" byłoby
życzeniem.

**Granica odbiorcy jest STRUKTURALNA, nie wizualna.** Payload dla klienta
**nie zawiera** kluczy wewnętrznych — nie „nie wyświetla ich". Przy froncie
w JS to jedyny wariant, który cokolwiek znaczy: odbiorca widzi payload
w narzędziach przeglądarki, a szablon jest u niego, nie u nas. To zaostrzenie
zasady, którą 3.12 zapisało jako „filtrowanie w SQL, nie w szablonie".

**Poziom konta, bez podziału na workspace'y.** Decyzja Kuby, potwierdzona
pomiarem: wszystkie 105 tablic snapshotu #5 siedzi w jednym workspace, a tylko
2 z 11 znalezisk niesie `board_id` — reszta jest kontowa. Podział przestrzenny
nie miałby czego pokazać. Wraca, gdy audyt obejmie całe konto i dojdą klasy
przypisane do tablic.

**Czego ta decyzja NIE obejmuje:** serwera. Bez FastAPI, uwierzytelniania
i hostingu — makieta to statyczne pliki. Gdzie front zamieszka (moduł
w Docs Publisherze czy osobna aplikacja) rozstrzyga się po obejrzeniu układu.

**Co unieważni:** decyzja, że front idzie do Docs Publishera. Wtedy szablony
jinja2 znikają, a `do_json` staje się jedynym wyjściem tego modułu — i o to
w tym podziale chodziło.

---

## D16. Jedna aplikacja, dwa wejścia. Granicę wyznacza sesja, nie parametr

**Decyzja:** jeden adres, jeden proces, jeden bundle. Klient wchodzi hasłem,
CXLABS e-mailem z domeny `@cxlabs.digital`. O tym, co odbiorca dostaje,
decyduje **rola zapisana w sesji po stronie serwera** — nigdy nic, co przyszło
z przeglądarki.

**Powód:** pierwsza rekomendacja brzmiała „dwie aplikacje" i była nadmiernie
ostrożna. Dwa procesy nie dają bezpieczeństwa same z siebie: gdyby o pokazaniu
tropu sprzedażowego decydował `if` w JavaScripcie, dwie aplikacje niczego by
nie uratowały, bo dane i tak wyszłyby z API. A jeśli granica stoi w API, to
drugi proces nie dodaje nic poza drugim miejscem do pomyłki przy wdrożeniu.

Kluczowe zdanie tej decyzji:

> **Odbiorcę wyznacza sesja po stronie serwera, nigdy parametr od przeglądarki.**

To, że bundle JS zawiera komponenty widoków wewnętrznych, **nie jest wyciekiem** —
wyciekiem byłyby dane. `Panel.tsx` ma warunki `ja.rola === "zespol"`, ale one
tylko *wyświetlają*: sesja klienta nie dostaje `pinowanie` ani `trop`, bo
`pulpit.do_json()` usuwa te klucze ze struktury po stronie serwera. Gdyby ktoś
skasował wszystkie warunki z frontu, klient zobaczyłby puste sekcje, nie cudze dane.

**Konsekwencja — trzy reguły, każda z testem w `tests/test_web_granice.py`:**

| Reguła | Dlaczego tak, a nie inaczej |
|---|---|
| `GET /api/pulpit` **nie przyjmuje** `client_id` ani `odbiorca` | parametr od przeglądarki to parametr od atakującego |
| sesja klienta pytająca o cudzego klienta → **404**, nie 403 | 403 potwierdza, że taki klient u nas jest |
| sesja klienta na `/api/klienci` → **404** | ta lista to nasz portfel klientów |

**Klucz API klienta nie ma kolumny w schemacie.** Migracja 006 nosi na ten temat
komentarz, bo brak kolumny wygląda jak przeoczenie, a jest decyzją. Klucz żyje
jako argument funkcji w procesie runu i ginie razem z nim.

**Czego to NIE załatwia:** aplikacja jest uruchamiana lokalnie. TLS, wdrożenie
i backupy to etap 5. Publiczne wystawienie panelu z danymi osobowymi klienta pod
samym hasłem jest osobnym ryzykiem — O23.

**Co unieważni:** wystawienie panelu klientom bez pośrednictwa CXLABS. Wtedy
wraca OAuth (aneks do D11) i SSO na domenę (O24).

---

## D11 — aneks (2026-08-06). Klient sam wkleja klucz, czyli self-service

D11 kończyło się zdaniem: *„aplikacja OAuth była potrzebna przy self-service dla
obcego prospekta — a tego nie robimy"*. Od tego etapu **robimy**: klient dostaje
hasło, wchodzi na panel, wkleja swój klucz API monday i sam odpala audyt.

Zapisuję to wprost, zamiast przemilczeć — przy D9 przemilczenie zmiany kosztowało
później więcej niż jej opisanie.

**Co się zmieniło, a co zostało:**

- zostało: **nie stajemy się depozytariuszem dostępu.** Nie ma refresh tokenów,
  nie ma kolumny na klucz, nie ma szyfrowania at-rest, bo nie ma czego szyfrować.
- zmieniło się: klucz podaje **klient**, nie my. Nie widzimy go nawet w logach.
- doszło: **formularz mówi prawdę o promieniu rażenia.** Klucz admina monday
  **nie jest read-only** — kto go ma, może usunąć każdą tablicę na koncie.
  Podpowiadamy admina, bo `pokrycie_pelne` jest prawdą tylko dla admina, ale
  piszemy też, co ten klucz umie, i sugerujemy unieważnienie go po audycie.
  „Zalecane" bez tego zdania byłoby wprowadzaniem w błąd.

**Warunek przed wystawieniem publicznym pozostaje OAuth.** Klucz w pamięci jest
wariantem dla relacji doradczej, gdzie klienta znamy. Dla obcego prospekta
zakres tokenu musi być ograniczony przez dostawcę, nie przez naszą obietnicę.

---

## D16 — aneks (2026-08-07). Wybór wersji audytu i koniec ukrytego obejścia

Panel dostał drop-down z **wersjami audytu**: lewa strona wybiera, *którego
klienta*, drop-down — *z kiedy*. Przy audycie powtarzanym cyklicznie to jest
właśnie to, po co odbiorca wraca do panelu.

**Granica dla `run_id` jest inna niż dla `client_id`, i to celowo.** `client_id`
od klienta po prostu **ignorujemy** — sesja wie, kim jest. `run_id` zignorować
nie można, bo klient ma prawo obejrzeć swój starszy audyt. Więc regułą nie jest
„pomiń parametr", a **„sprawdź właściciela"**: `pulpit.run_nalezy_do` porównuje
`runy.client_id` z celem, a obcy albo nieistniejący run daje **404 na oba
przypadki** — rozróżnienie byłoby wyrocznią istnienia cudzych audytów.

Sprawdzenie stoi w endpointcie, nie w `zbuduj_pulpit`, i to jest właściwe
miejsce: funkcja budująca panel nie wie, kto pyta, a endpoint wie. Test padł przy
wyłączonym sprawdzeniu, więc pilnuje mechanizmu, nie siebie.

### Zniknęło obejście, o którym nikt nie wiedział

`_ostatni_run` sortował `hipotez_zbadanych DESC, started_at DESC` — wybierał audyt
**najobszerniejszy**, nie najnowszy. Powód był realny (nasze runy diagnostyczne
z jedną hipotezą przesłaniały pełny audyt i panel sugerował, że konto jest prawie
czyste), ale skutek uboczny brzmiał: panel otwierał dane z 1 sierpnia, choć audyt
szedł 5 sierpnia, i **nie było tego po czym poznać**.

Przy jawnym wyborze wersji obejście przestało chronić i zaczęło zaskakiwać.
Domyślnie jest teraz **najnowszy zakończony**. Cena: chude runy widać na liście —
zaakceptowana, bo drop-down pokazuje przy każdym liczbę znalezisk, a na koncie
klienta każdy run jest pełny.

Cenę trzeba było zapłacić w drugą stronę: **starszy run musi zostać osiągalny.**
Gdyby `run_id` przestało działać, zmiana domyślnego wyboru byłaby regresją, nie
poprawką — dlatego jeden z testów pilnuje właśnie tego, a nie samej odmowy.

### Dwie daty, obie prawdziwe, obie nazwane

`Pulpit.run_at` to **kiedy dane zebrano** (ze snapshotu), `PozycjaRunu.run_at` to
**kiedy agent je badał** (`runy.started_at`). Dwie analizy tego samego snapshotu
mają jedną datę zbiórki i dwie daty badania — więc drop-down mówiący „5 sierpnia"
obok nagłówka „dane z 2026-08-01" wyglądał na sprzeczność w danych, a był brakiem
dwóch słów. Panel mówi teraz „analiza z" i „dane zebrane". Wyszło ze zrzutu, nie
z testu; test dopisany, żeby nikt tego nie „uprościł" z powrotem.

---

## D16 — aneks (2026-08-10). Reset haseł i czwarty guardrail bez pomiaru

### Usterka, od której to się zaczęło

`--dodaj-klienta cxlabs` wywołane drugi raz **nie zmieniało hasła** — zakładało
DRUGIE konto, a stare hasło nadal wpuszczało. Zmierzone na kopii bazy demo: klient
`cxlabs` miał konta id 3 i 7, oba działające, bo `zaloguj` bierze konto klienta
przez `fetchone()` bez `ORDER BY`.

To **czwarty przypadek guardraila, w który się wierzyło bez pomiaru** — po
`--read-only` w MCP, `can_use_tool` i kluczu API. Wzorzec był ten sam: „wydałem
nowe hasło" wyglądało na odebranie starego dostępu i nie odbierało go, a nic tego
nie sprawdzało. Konta zespołu luki nie miały, bo `idx_konta_email` jest UNIQUE od
006; brakowało odpowiednika dla `client_id`.

### Naprawa w dwóch warstwach, nie jednej

`zresetuj_haslo` nadpisuje `hash_hasla` i `sol_hasla` na ISTNIEJĄCYM wierszu.
Samo to naprawia objaw — ale duplikaty wróciłyby inną drogą (skrypt, ręczny SQL),
więc migracja **007** dokłada unikalny indeks CZĘŚCIOWY:

```sql
CREATE UNIQUE INDEX idx_konta_klient_aktywny ON konta_dostepu (client_id)
  WHERE rola = 'klient' AND aktywne = 1;
```

Częściowy, bo historia dezaktywowanych kont ma prawo mieć wiele wierszy na
klienta — blokujemy tylko wiele kont **jednocześnie ważnych**. `utworz_konto`
odmawia z komunikatem mówiącym, co zrobić; `IntegrityError` z indeksu by nie
powiedział.

### Kto może resetować: wymaganie i jego realizacja

> **Klient nie resetuje sobie hasła. Robi to zespół.**

Powód: hasło jest jedyną bramą do danych osobowych klienta, a nie mamy jak
potwierdzić, kto o reset prosi — nie ma wysyłki maili (O24).

Realizacja: klient **nie ma endpointu**, nie „ma zablokowany".
`/api/haslo/klienta` i `/api/haslo/moje` są zespołowe, a sesja klienta dostaje
**404** — 403 znaczyłoby „istnieje i nie wolno ci", czyli podpowiedź, że taka
droga jest. Sprawdzone przez wyłączenie warunku: testy padły, więc pilnują
mechanizmu.

`/api/haslo/moje` bierze konto **z sesji** i wymaga **obecnego hasła**. Sesja już
potwierdza tożsamość, ale bywa porzucona w cudzej przeglądarce — bez tego warunku
przejęta sesja pozwala przejąć konto na stałe, czyli szkoda bez końca zamiast
szkody na 12 godzin.

### Czego reset nie robi

**Nie wylogowuje** (decyzja Kuby) — patrz **O26**. Dlatego `WynikResetu` niesie
`wazne_sesje`, a panel i CLI mówią wprost, ile sesji zostaje ważnych: bez tego
ktoś kliknąłby „reset" i uznał, że odciął dostęp.

Nowe hasło jest **zwracane, nie logowane**. Sprawdzone po realnym resecie: 0
trafień w logu serwera i 0 w pliku bazy — w bazie leży tylko hash.

---

## D16 — aneks (2026-08-10, druga poprawka). „Nie pamiętam hasła"

### Błędne koło, które sam zbudowałem

Poprzedni aneks dodał reset haseł **za sesją**: `/api/haslo/moje` i
`/api/haslo/klienta` wymagają `ZSesji`. Kuba zgłosił to jednym zdaniem: „dalej
nie mogę zresetować hasła z panelu logowania". I miał rację — kto zgubił hasło,
sesji nie ma, więc dostał „zmień hasło, gdy je znasz" zamiast „nie pamiętam
hasła".

Usterka nie polegała na braku funkcji, a na **braku drogi dla osoby, która nie
może się zalogować**. Żaden test tego nie złapał, bo wszystkie logowały się
najpierw.

### Skrzynka jako dowód tożsamości

Bez SSO (O24) nie mamy czym potwierdzić, kto prosi o reset. Wybór Kuby: **SMTP**,
czyli link na adres `@cxlabs.digital` — właściciel skrzynki dowodzi tożsamości.

Alternatywa „podaj e-mail, dostaniesz hasło na ekranie" byłaby otwartą bramą:
każdy znający adres przejąłby konto zespołu z dostępem do danych wszystkich
klientów. Dlatego takiej drogi nie ma i nie może być.

**Bez nowej zależności** — `smtplib`, `email.message` i `ssl` są w stdlib.
Sprawdzone przed napisaniem kodu, bo `CLAUDE.md` zabrania dodawania zależności
bez pytania.

### Trzy warunki na token, każdy z konkretnego ryzyka

Migracja **008** (`reset_tokeny`): hash tokenu, nie token (wyciek bazy nie daje
linków); `uzyty_o` (mail bywa przekazywany i cytowany, więc link użyty raz musi
umrzeć); `wazny_do` 30 minut (link leży w skrzynce latami).

Nowe żądanie usuwa poprzednie tokeny konta: pięć kliknięć „nie pamiętam" nie ma
zostawiać pięciu ważnych linków.

### Odpowiedź jest ZAWSZE identyczna

`/api/haslo/zapomniane` odpowiada tak samo dla konta istniejącego,
nieistniejącego, obcej domeny, przekroczonego limitu **i nieudanej wysyłki maila**.
Inaczej brama staje się wyrocznią: „ten adres @cxlabs.digital jest prawdziwy,
tamten nie". Zweryfikowane na żywo — odpowiedzi znak w znak takie same.

Limit żądań idzie przez `proby_logowania`, ten sam mechanizm co przy logowaniu:
endpoint bez sesji jest otwarty na świat, więc bez limitu da się zasypać czyjąś
skrzynkę i sprawdzać adresy hurtowo.

### Klient nadal nie resetuje sam

Ta droga jest **wyłącznie dla zespołu**. Klient nie ma skrzynki w naszej domenie,
więc nie mamy czym potwierdzić, że to on prosi — jego hasło wydaje CXLABS z panelu.
Brama mówi mu to wprost, zamiast pokazywać formularz, który nic nie zrobi.

### Tryb awaryjny bez SMTP

Bez `SMTP_HOST` link trafia do **logu serwera** z ostrzeżeniem. To nie stan
docelowy (log widzi każdy, kto ma dostęp do logów) i log mówi o tym wprost — ale
brak konfiguracji poczty nie może znaczyć „nikt nigdy nie odzyska hasła".

Konfiguracja siedzi w `UstawieniaPoczty`, osobno od sekretów collectora, żeby
testy odzyskiwania hasła dały się uruchomić bez `MONDAY_TOKEN`.

### Poprawka (2026-08-10): link resetu prowadził w nicość

`ADRES_PUBLICZNY` miało stałą domyślną `http://127.0.0.1:8000`, a `--serwuj
--port 8010` jej nie dotykało. Kuba kliknął link z resetu i przeglądarka nie miała
z czym się połączyć.

**Dwa źródła prawdy o jednym adresie** — port procesu i port w linku — bez niczego,
co pilnowałoby ich zgodności. Testy endpointów tego nie widzą, bo `TestClient` nie
ma pojęcia o porcie prawdziwego serwera: to ta sama rodzina usterek co wcześniejsze
„każdy element działa osobno".

Poprawka: link bierze host i port **z żądania** (`Request.base_url`), czyli z tego,
w co odbiorca właśnie kliknął — jedno źródło, więc nie ma jak się rozjechać.
`ADRES_PUBLICZNY` nadal wygrywa, gdy jest ustawione, bo za odwrotnym proxy (Caddy,
etap 5) adres z żądania jest wewnętrzny. Domyślnie jest jednak **puste**: wartość
domyślna, która cicho psuje link, jest gorsza od jej braku.

Dwa testy: link nie może wskazywać starej stałej i musi nieść adres z żądania;
`ADRES_PUBLICZNY` musi wygrywać, gdy jest podany.

### Poprawka (2026-08-10): panel ukrywał dwa stany naraz

Kuba zapytał, czy nie powinien widzieć w panelu klienta `acme`, któremu sam
zakładał konto. Powinien — a sprawdzenie odsłoniło **dwa** niewidoczne stany,
bo `zbuduj_liste_klientow` budowała listę wyłącznie z `runy`:

| klient | stan w bazie | co widział panel |
|---|---|---|
| `acme` | konto dostępu, 0 audytów | **nic** — mimo wydanego hasła |
| `cxlabs` | 17 audytów, 0 kont | wiersz jak każdy inny |

Drugi przypadek był groźniejszy: audyt istniał, klient nie mógł się zalogować,
i **nie było tego widać nigdzie**. Lista jest teraz sumą obu źródeł (`UNION`),
a `PozycjaKlienta.ma_konto` mówi, czy klient może wejść. Braki są stanem do
pokazania, nie powodem do ukrycia wiersza — ukryty wiersz to brak, o którym nikt
się nie dowie.

Doszedł `POST /api/klient/dostep`, bo pokazywanie braku bez drogi do naprawienia
go byłoby połową roboty. Przy istniejącym koncie odmawia z **409** i komunikatem
kierującym na reset: to ta sama reguła, którą wymusza `utworz_konto` i indeks
z migracji 007.

### „Moje konto" wyszło z widoku audytu

„Moje hasło" wisiało wśród kafli klienta — własne konto nie należy do widoku
cudzego audytu, co Kuba zakwestionował. Jest teraz osobną stroną (`MojeKonto`),
z wejściem w sidebarze pod listą klientów: własne hasło plus tabela dostępów
wszystkich klientów z resetem i nadaniem dostępu w jednym miejscu.

---

## D17 (2026-08-10). Przełącznik rozliczeń agenta

`AGENT_ROZLICZENIE=klucz|subskrypcja`, **domyślnie `klucz`**.

Kuba zapytał, ile kosztuje powrót na rozliczanie subskrypcją. Odpowiedź: jedno
pole konfiguracji i jedna gałąź — SDK spada na login w `~/.claude`, gdy nie dostanie
`env` z kluczem. Ale sama zmiana byłaby cicha, więc dołożone są trzy rzeczy:

**Pusty `env`, nie pusta wartość.** `{"ANTHROPIC_API_KEY": ""}` byłoby GORSZE niż
brak: SDK zobaczyłby zmienną i nie spadł na login, więc run wywróciłby się na
uwierzytelnianiu. Test sprawdza `opcje.env == {}`, nie samą obecność klucza.

**`klucz_anthropic` zależy od trybu.** Wymóg klucza zostaje w trybie `klucz`
(przerywa PRZED wywołaniami monday), a w `subskrypcja` zwraca pusty napis —
wymaganie klucza w trybie, który go nie używa, blokowałoby ten tryb.

**`runy.rozliczenie` (migracja 009).** Bez tej kolumny `koszt_usd` znaczy dwie
różne rzeczy w tej samej kolumnie: wydatek albo wycenę teoretyczną. Sumowanie
mieszałoby jedno z drugim, i to cicho, bo obie są liczbami. Panel oznacza kwotę
podpisem „Koszt (szacunek) — run szedł z subskrypcji, to nie faktura".

Walidator odrzuca literówkę: `subskrybcja` bez niego byłaby traktowana jak „nie
klucz", czyli zmieniałaby sposób płacenia niezauważalnie.

Runy sprzed migracji mają `NULL`, nie zgadywaną wartość: 11 z 17 poszło na
subskrypcję (klucz nie dochodził do 2026-08-05), część później na klucz.
Zgadywanie po dacie dałoby liczby wyglądające na pewne i takie nie będące.

### Przy okazji: dwie daty, które nie były datami

W `web/run.py` — ścieżce, którą klient odpala audyt z panelu — `started_at`
**i** `finished_at` dostawały `raport_runu.run_id`, czyli identyfikator runu
zamiast znacznika czasu. Kolumny są `TEXT`, więc SQLite przyjmował to bez
protestu.

Obie przeżyły, bo **żaden run z panelu nie doszedł jeszcze do zapisu** (`runy`
z sufiksem `-agent`: 0 wierszy — zmierzone). Drop-down wersji sortuje właśnie po
`started_at`, więc pierwszy prawdziwy run z panelu wylądowałby w losowym miejscu
listy. Ścieżka CLI miała własny, poprawny `UPDATE` — dlatego testy tego nie
pokazały.

### Dodawanie klienta z panelu

`POST /api/klient/dostep` przyjmuje teraz też **nowy** identyfikator, walidowany
wzorcem `^[a-z0-9][a-z0-9-]{1,49}$`: `client_id` trafia do adresów (`?klient=`)
i do nazw plików raportu, więc „Kancelaria Sp. z o.o." nie może tam wejść.
Surowy 422 pydantica zamieniamy na zdanie mówiące, co jest dozwolone — front
i tak spłaszczał listę błędów do „nieprawidłowe dane w formularzu".

Reset istniejącego klienta **nie** ma tego wzorca (`DaneResetuKlienta`): konta
założone wcześniej mogą mieć identyfikatory, których dzisiejsza reguła nie
przepuszcza, a odmowa resetu dla działającego konta zamieniłaby walidację
w blokadę.

### Panel nie gubi już, którego klienta oglądasz

Przy kliencie bez audytu `pulpit` jest `null`, a `ja.client_id` dla sesji zespołu
też — więc nagłówek spadał na „Audyty", a okruszek na „—". Łańcuch to teraz
`pulpit?.client_id ?? wybrany ?? ja.client_id`; `wybrany` przed `ja.client_id`,
bo jeden łańcuch obsługuje obie role. Komunikat mówi, **co dalej**, a nie tylko
że czegoś nie ma.

Sekcja „Dostęp klienta" wypadła z widoku audytu: reset żyje w „Moje konto", a ten
sam przycisk w dwóch miejscach był dublowaniem — tym samym, które Kuba
zakwestionował przy „Moje hasło".
