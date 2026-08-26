-- Stan `czeka_na_zgode`: pauza między zebraniem danych a odpaleniem agenta.
--
-- POWÓD. Klient płaci za audyt własnym kluczem Anthropic (etap 4). Skoro
-- rachunek jest jego, musi zobaczyć widełki kosztu i móc zawęzić zakres,
-- ZANIM ruszy agent. Dziś takiej pauzy nie ma: `web/run.py:_audyt` jedzie
-- collector → detektory → agent w jednym `asyncio.run`, bez żadnego punktu,
-- w którym da się zapytać człowieka.
--
-- ZMIERZONE, po co ta pauza: pełny run na snapshocie #7 to 53 hipotezy
-- i ~3,93 USD. Wybór jednej tablicy zostawia 15 hipotez i ~1,01 USD, czyli
-- **−74%**. Podłogą jest 0,87 USD — hipotezy o ludziach, gościach, planie
-- i automatyzacjach, których żaden wybór tablic nie usuwa.
--
-- DLACZEGO PRZEBUDOWA TABELI, A NIE `ALTER`. Kolumna `stan` ma
-- `CHECK (stan IN (...))` z migracji 006, a SQLite nie umie zmienić warunku
-- CHECK w miejscu. Dodanie stanu wymaga nowej tabeli, przepisania wierszy
-- i podmiany nazwy. Bez tego `UPDATE ... SET stan = 'czeka_na_zgode'`
-- wywalałby się na constraincie — i to dopiero w produkcji, bo testy
-- z pustą tabelą przechodzą.
--
-- TRZY NOWE KOLUMNY:
--   * `snapshot_id` — faza druga musi wiedzieć, który snapshot zatwierdzono.
--     Bez tego zgoda wskazywałaby „ostatni snapshot klienta", a ten mógł
--     w międzyczasie powstać z innego zbierania.
--   * `wybor` — zatwierdzony zakres jako JSON `{"workspace_ids": [], "board_ids": []}`.
--     Zapisany, bo raport ma napisać, ile tablic objął audyt, a po fakcie nie
--     da się tego odtworzyć z samego snapshotu (snapshot jest pełny).
--   * `zgoda_do` — do kiedy zgoda jest ważna. Dane starzeją się, a zgoda na
--     kwotę policzoną z tygodniowego snapshotu nie jest zgodą na dzisiejszy
--     rachunek. Po tym terminie zbieramy ponownie.
--
-- CZEGO TA MIGRACJA NIE ROBI: nie wpisuje żadnych sekretów. Klucze monday
-- i Anthropic nie mają tu kolumny i mieć nie będą — faza druga dostaje je
-- ponownie od frontu (D12: token klienta nigdy w bazie ani w argv).

CREATE TABLE zadania_nowe (
    id          TEXT    PRIMARY KEY,
    client_id   TEXT    NOT NULL,
    konto_id    INTEGER NOT NULL REFERENCES konta_dostepu (id),
    stan        TEXT    NOT NULL,
    etap        TEXT,
    postep      INTEGER,
    run_id      TEXT,
    blad        TEXT,
    zaczeto     TEXT    NOT NULL,
    skonczono   TEXT,

    -- Nowe. Wszystkie NULL-owalne: zadania z poprzednich wersji ich nie mają,
    -- a zadanie idące ścieżką bez zawężenia nie musi ich mieć nigdy.
    snapshot_id INTEGER REFERENCES snapshots (id),
    wybor       TEXT,
    zgoda_do    TEXT,

    CHECK (stan IN ('w_kolejce', 'zbieram', 'czeka_na_zgode', 'analizuje', 'gotowe', 'blad')),
    CHECK (postep IS NULL OR (postep >= 0 AND postep <= 100)),
    CHECK (wybor IS NULL OR json_valid(wybor))
) STRICT;

INSERT INTO zadania_nowe (
    id, client_id, konto_id, stan, etap, postep, run_id, blad, zaczeto, skonczono
)
SELECT id, client_id, konto_id, stan, etap, postep, run_id, blad, zaczeto, skonczono
FROM zadania;

DROP TABLE zadania;

ALTER TABLE zadania_nowe RENAME TO zadania;

-- Indeks ginie razem ze starą tabelą, więc odtwarzamy go jawnie. Hamulec
-- kosztu czyta tę tabelę przy każdym kliknięciu „wygeneruj audyt", więc jego
-- brak byłby cichą regresją wydajności.
CREATE INDEX idx_zadania_klient ON zadania (client_id, zaczeto DESC);
