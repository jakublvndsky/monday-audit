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

```sql
CREATE TABLE snapshots (
  id            INTEGER PRIMARY KEY,
  client_id     TEXT NOT NULL,
  run_at        TEXT NOT NULL,
  collector_ver TEXT NOT NULL,
  payload       TEXT NOT NULL   -- JSON, niemutowalny
);

CREATE TABLE findings (
  id           INTEGER PRIMARY KEY,
  snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id),
  klasa_id     TEXT NOT NULL,
  rubric_ver   TEXT NOT NULL,
  waga         TEXT NOT NULL,
  wysilek      TEXT NOT NULL,
  typ_wyceny   TEXT NOT NULL,
  kwota_pln    REAL,            -- NULL dla typu `ryzyko`
  widocznosc   TEXT NOT NULL,
  opis         TEXT NOT NULL,
  rekomendacja TEXT NOT NULL,
  dowod        TEXT NOT NULL,   -- JSON
  trop         TEXT             -- tylko wersja wewnętrzna
);

CREATE TABLE osoby_mapowanie (
  user_hash  TEXT PRIMARY KEY,
  imie_nazwisko TEXT,
  email      TEXT
);
-- Agent NIE MA narzędzia czytającego tę tabelę. Renderer ma.

CREATE TABLE wywolania (
  id          INTEGER PRIMARY KEY,
  run_id      TEXT NOT NULL,
  hipoteza_id TEXT,
  narzedzie   TEXT NOT NULL,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  latency_ms  INTEGER,
  model       TEXT,
  at          TEXT NOT NULL
);
```

**Dlaczego snapshot jest niemutowalny i oddzielony od findingów** — to
najważniejsza decyzja projektowa w całym systemie:

1. Możesz przepuścić agenta ponownie po starym snapshocie **bez dotykania
   konta klienta**. To jest harness ewaluacyjny za darmo.
2. Snapshot #1 vs #4 u tego samego klienta = case study z liczbami.
3. Gdy agent zmyśli, masz `dowod` + snapshot, żeby to wychwycić.

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
