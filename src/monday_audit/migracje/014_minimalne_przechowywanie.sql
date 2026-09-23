-- Minimalne przechowywanie (plan, faza 5c).
--
-- Decyzja Kuby z 2026-09-23: po runie na dysku nie zostaje nic, co dotyczy
-- konkretnej osoby. Snapshot i tabela `osoby_mapowanie` żyją w bazie W PAMIĘCI
-- przez czas runu. Tu trafia wyłącznie to, co przeszło przez `przechowanie.py`.
--
-- Dwie tabele zamiast rozszerzenia `findings`, bo `findings.snapshot_id` jest
-- NOT NULL z kluczem obcym do `snapshots` — a trwałego snapshotu w tej ścieżce
-- nie ma i mieć nie będzie.

-- Uwagi krytyczne PO MASKOWANIU: pseudonim → [OSOBA], lista pseudonimów →
-- liczba, data → liczba dni przed runem, dane kontaktowe jak w trace'ach.
-- Liczby i fakty audytu zostają; kto konkretnie — nie.
CREATE TABLE uwagi_zapisane (
    id            INTEGER PRIMARY KEY,
    run_id        TEXT    NOT NULL REFERENCES runy (run_id),
    klasa_id      TEXT    NOT NULL,
    zrodlo        TEXT    NOT NULL,
    opis          TEXT    NOT NULL,
    rekomendacja  TEXT    NOT NULL,
    dowod         TEXT    NOT NULL,
    CHECK (zrodlo IN ('model', 'szablon')),
    CHECK (json_valid(dowod) AND json_type(dowod) = 'object')
) STRICT;

CREATE INDEX idx_uwagi_zapisane_run ON uwagi_zapisane (run_id);

-- Liczbowy odcisk obrazu konta: same liczby i kilka kluczy, bez których liczby
-- tracą podpis (`produkt`, `tier`). Nazw tablic, workspace'ów i etykiet tu nie ma.
CREATE TABLE statystyki_runow (
    run_id  TEXT PRIMARY KEY REFERENCES runy (run_id),
    dane    TEXT NOT NULL,
    CHECK (json_valid(dane))
) STRICT;
