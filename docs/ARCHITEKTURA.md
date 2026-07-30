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

## D4. Collector bez MCP, agent z MCP

**Decyzja:**
- Collector (faza 1): czysty GraphQL przez `httpx`
- Agent (faza 2): lokalny serwer `@mondaydotcomorg/monday-api-mcp`
  z flagą `--read-only`, podproces na jeden run

**Powód:**
- Collector potrzebuje paginacji, budżetowania complexity, retry z backoffem
  i logowania każdego zapytania. MCP to abstrahuje — a to jest właśnie
  warstwa, którą chcemy kontrolować.
- Lokalny MCP nad hostowanym **wyłącznie z powodu flagi `--read-only`**.
  Hostowany `mcp.monday.com` przyjmie bearer token, ale nie ma tej flagi.
  Read-only wymuszony na poziomie serwera to mechanizm, nie polityka —
  model nie ma go jak obejść, nawet przy prompt injection.

**Uwaga:** dynamiczne narzędzia API (pełny schemat) nie są kompatybilne
z `--read-only`. Nie szkodzi, nie chcemy ich.

**Koszt:** Node 20 na Mikrusie, ~50 MB.

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
| **Obrona** | nie filtrowanie, a **odebranie możliwości**: agent nie ma narzędzi zapisujących, MCP na `--read-only` |
| **Maksymalna szkoda** | fałszywe znalezisko w raporcie. Nie wyciek, nie modyfikacja |
| **Wyjście** | strukturalny JSON, każdy finding z obowiązkowym `dowod` wskazującym na fakt ze snapshotu. Bez dowodu — odpada na walidacji |
| **PII** | pseudonimizacja przed modelem, tabela mapowania bez żadnego narzędzia dostępowego |
| **Poświadczenia** | token klienta w env procesu MCP, nigdy w kontekście modelu |
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
