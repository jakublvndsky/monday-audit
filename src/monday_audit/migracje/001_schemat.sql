-- 001_schemat.sql — schemat bazowy (etap 3.1)
--
-- Źródło: docs/ARCHITEKTURA.md D7, plus pięć uzupełnień uzgodnionych
-- przy 3.1 — oznaczone niżej jako LUKA 1..5.
--
-- TRANSAKCJA: ten plik NIE zawiera BEGIN/COMMIT. Otwiera ją runner
-- (monday_audit.baza), żeby zastosowanie migracji i jej odnotowanie
-- w `_migracje` były jedną niepodzielną operacją. Nie dopisuj tu
-- sterowania transakcją.
--
-- STRICT na wszystkich tabelach: bez tego SQLite typuje doradczo
-- i wpuści tekst do kolumny liczbowej.
--
-- ŚWIADOMIE BEZ CHECK NA SŁOWNIKACH RUBRYKI (waga, wysilek_naprawy,
-- typ_wyceny, widocznosc). Rubryka jest wersjonowana niezależnie od bazy
-- — każdy finding niesie `rubric_ver` — więc zmiana słownika w rubryce
-- nie może wymagać migracji schematu. Te wartości waliduje kod przeciwko
-- WCZYTANEJ rubryce (3.11). CHECK-i poniżej pilnują wyłącznie własności
-- strukturalnych, niezależnych od rubryki, oraz słowników WŁASNYCH
-- (status runu), które do rubryki nie należą.


-- ── snapshots ────────────────────────────────────────────────────────
-- Niemutowalny zapis stanu konta. To najważniejsza własność w systemie:
-- ten sam snapshot można przepuścić przez agenta ponownie, bez dotykania
-- konta klienta (D7).

CREATE TABLE snapshots (
    id            INTEGER PRIMARY KEY,
    client_id     TEXT    NOT NULL,
    run_at        TEXT    NOT NULL,  -- ISO-8601 ze strefą, zawsze UTC
    collector_ver TEXT    NOT NULL,
    payload       TEXT    NOT NULL,  -- JSON
    CHECK (json_valid(payload))
) STRICT;

CREATE INDEX idx_snapshots_client_run ON snapshots (client_id, run_at);

-- Niemutowalność jako mechanizm, nie zasada — ta sama logika co D6.
-- DELETE zostaje dozwolony: usunięcie danych klienta musi być wykonalne.
CREATE TRIGGER snapshots_bez_update
BEFORE UPDATE ON snapshots
BEGIN
    SELECT RAISE(ABORT, 'snapshot jest niemutowalny (D7) — zapisz nowy zamiast zmieniać');
END;


-- ── runy ──────────────────────────────────────────────── LUKA 2 ─────
-- Jeden wiersz na przebieg end-to-end: collector → detektory → agent.
--
-- Dlaczego jeden, a nie osobno per faza: etap 6 wymaga per run zarówno
-- metryk collectora (wywołania monday, complexity), jak i agenta (tokeny,
-- findingi, odrzucenia). Rozdzielenie zmuszałoby do sklejania ich przy
-- każdym pytaniu z listy pięciu pytań z 06-operate.md.
--
-- Konsekwencja: pola wypełniają się etapami. `snapshot_id` jest NULL,
-- dopóki collector nie zapisze snapshotu. `model`, `rubric_ver`
-- i `prompt_hash` są NULL, dopóki nie ruszy faza agentowa. Komplet tych
-- czterech (z `collector_ver` w snapshots) to cztery elementy pinowania
-- wymagane przez etap 5 i warunek domknięcia runu.

CREATE TABLE runy (
    run_id      TEXT    PRIMARY KEY,
    client_id   TEXT    NOT NULL,
    snapshot_id INTEGER REFERENCES snapshots (id),
    status      TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,

    -- Pinowanie (etap 5). Pełny identyfikator modelu — alias zakazany.
    model       TEXT,
    rubric_ver  TEXT,
    prompt_hash TEXT,

    -- Agregaty per run (06-operate.md). NULL = jeszcze niepoliczone.
    wywolania_monday      INTEGER,
    complexity_suma       INTEGER,
    tokens_in             INTEGER,
    tokens_out            INTEGER,
    findingow             INTEGER,
    odrzuconych_walidacja INTEGER,
    hipotez_zbadanych     INTEGER,
    hipotez_odrzuconych   INTEGER,

    -- Słownik własny, nie z rubryki — więc CHECK jest tu na miejscu.
    CHECK (status IN ('w_toku', 'zakonczony', 'przerwany'))
) STRICT;

CREATE INDEX idx_runy_snapshot ON runy (snapshot_id);


-- ── findings ─────────────────────────────────────── LUKA 1, LUKA 4 ──
-- LUKA 1: `run_id`. Bez niego findingi z dwóch przebiegów po tym samym
-- snapshocie są nierozróżnialne, a etap 4 mierzy powtarzalność właśnie
-- przez porównanie dwóch runów na jednym snapshocie.
--
-- LUKA 4: `pewnosc` — kontrakt D8 to produkuje, D7 nie miało gdzie przyjąć.
--
-- `snapshot_id` jest redundantne wobec runy.snapshot_id. Zostaje, bo 3.1
-- wymaga indeksu findings(snapshot_id) i bo raporty pytają po snapshocie.
-- Spójność obu kolumn pilnuje ścieżka zapisu — jedna funkcja wypełnia je
-- z tego samego runu.

CREATE TABLE findings (
    id           INTEGER PRIMARY KEY,
    run_id       TEXT    NOT NULL REFERENCES runy (run_id),
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots (id),
    klasa_id     TEXT    NOT NULL,
    rubric_ver   TEXT    NOT NULL,
    waga         TEXT    NOT NULL,
    wysilek      TEXT    NOT NULL,
    typ_wyceny   TEXT    NOT NULL,
    kwota_pln    REAL,              -- NULL dla typ_wyceny = ryzyko (D7, D8)
    widocznosc   TEXT    NOT NULL,
    opis         TEXT    NOT NULL,
    rekomendacja TEXT    NOT NULL,
    dowod        TEXT    NOT NULL,  -- JSON, obiekt
    pewnosc      TEXT    NOT NULL,
    trop         TEXT,              -- wyłącznie wersja wewnętrzna

    -- Strukturalnie, niezależnie od rubryki: dowod musi być obiektem JSON.
    -- Że jego klucze pokrywają pola wymagane przez klasę — sprawdza
    -- walidacja z D8, bo tylko ona zna rubrykę.
    CHECK (json_valid(dowod) AND json_type(dowod) = 'object')
) STRICT;

CREATE INDEX idx_findings_snapshot ON findings (snapshot_id);
CREATE INDEX idx_findings_run ON findings (run_id);


-- ── hipotezy_odrzucone ────────────────────────────────── LUKA 3 ─────
-- D8 czyni to pole obowiązkowym i nazywa głównym wejściem do evali
-- w etapie 4, gdzie metryka wymaga 100% niepustych. Bez tabeli nie ma
-- gdzie tego zapisać, a agent potwierdzający wszystko jest zepsuty.

CREATE TABLE hipotezy_odrzucone (
    id        INTEGER PRIMARY KEY,
    run_id    TEXT    NOT NULL REFERENCES runy (run_id),
    klasa_id  TEXT    NOT NULL,
    obiekt_id TEXT,                 -- board_id, user_hash, workspace_id
    powod     TEXT    NOT NULL
) STRICT;

CREATE INDEX idx_hipotezy_run ON hipotezy_odrzucone (run_id);


-- ── osoby_mapowanie ───────────────────────────────────── LUKA 5 ─────
-- MAGAZYN PII. Agent NIE MA narzędzia czytającego tę tabelę. Renderer ma,
-- i dopiero on deanonimizuje (3.12).
--
-- LUKA 5: `client_id` w kluczu głównym. Bez niego nie da się skasować
-- mapowań jednego klienta — a sól jest osobna per klient i po audycie
-- dostęp jest odbierany (D11).

CREATE TABLE osoby_mapowanie (
    client_id     TEXT NOT NULL,
    user_hash     TEXT NOT NULL,
    imie_nazwisko TEXT,
    email         TEXT,
    PRIMARY KEY (client_id, user_hash)
) STRICT;


-- ── wywolania ────────────────────────────────────────────────────────
-- Obserwowalność (D10). Zamiast Langfuse — tabela, na której SQL odpowie
-- na pięć pytań z 06-operate.md.
--
-- Obejmuje oba rodzaje wywołań: GraphQL collectora (3.2) i narzędzia
-- agenta (3.10). Stąd `runy` musi obejmować cały przebieg, nie samą
-- fazę agentową — inaczej ten klucz obcy nie miałby do czego wskazywać
-- w czasie zbierania danych.

CREATE TABLE wywolania (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT    NOT NULL REFERENCES runy (run_id),
    hipoteza_id TEXT,
    narzedzie   TEXT    NOT NULL,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    latency_ms  INTEGER,
    complexity  INTEGER,            -- complexity { query after } z 3.2
    model       TEXT,
    at          TEXT    NOT NULL
) STRICT;

CREATE INDEX idx_wywolania_run ON wywolania (run_id);
