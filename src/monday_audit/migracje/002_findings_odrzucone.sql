-- 002: findingi odrzucone przez walidację kontraktu (D8)
--
-- LUKA: `runy.odrzuconych_walidacja` to sam LICZNIK, a D8 nazywa odsetek
-- odrzuconych findingów główną metryką jakości w etapie 4. Licznik mówi
-- „pięć odpadło" i nic więcej — nie da się z niego dowiedzieć, czy agent
-- myli klasy, zapomina `dowod`, czy wymyśla kwoty. A to są trzy różne
-- poprawki promptu.
--
-- `hipotezy_odrzucone` to NIE to samo i nie zastępuje tej tabeli:
-- tam agent świadomie odrzuca hipotezę z uzasadnieniem, tutaj walidacja
-- odrzuca gotowy finding, którego agent chciał. Pierwsze jest sukcesem,
-- drugie błędem.
--
-- Treść klienta: `finding` trzyma surowy JSON od agenta, więc może zawierać
-- nazwy tablic i kolumn pisane przez klienta. Tabela jest objęta tą samą
-- ochroną co snapshoty — baza jest w `.gitignore`, a kopie zapasowe
-- z etapu 5 jej dotyczą.

CREATE TABLE findings_odrzucone (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT    NOT NULL REFERENCES runy (run_id),
    snapshot_id INTEGER NOT NULL REFERENCES snapshots (id),
    klasa_id    TEXT,               -- NULL, gdy agent podał nieistniejącą klasę
    regula      TEXT    NOT NULL,   -- która reguła D8 to złapała
    powod       TEXT    NOT NULL,   -- komunikat walidacji, bez wartości sekretnych
    finding     TEXT    NOT NULL,   -- surowy JSON od agenta

    -- Strukturalnie: to musi być obiekt JSON, żeby eval z etapu 4 dał się
    -- napisać w SQL-u, a nie przez parsowanie stringów.
    CHECK (json_valid(finding) AND json_type(finding) = 'object')
) STRICT;

CREATE INDEX idx_findings_odrzucone_run ON findings_odrzucone (run_id);
CREATE INDEX idx_findings_odrzucone_regula ON findings_odrzucone (regula);
