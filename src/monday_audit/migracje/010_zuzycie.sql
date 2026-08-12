-- Rozbicie kosztu runu: cztery liczby tokenów zamiast dwóch, czas, i zużycie
-- PER HIPOTEZA.
--
-- POWÓD. Pierwszy pełny audyt z panelu kosztował 7,09 USD i trwał 62 minuty
-- (86 hipotez, 27 znalezisk, 2026-08-11). Etap 4 ma to obniżyć, ale **nie dało się
-- powiedzieć, gdzie te pieniądze idą**:
--
--   * `runy.tokens_in` i `tokens_out` były NULL, bo `web/run.py` zapisywał sam
--     `koszt_usd`, a `cli_agent.py` dwie liczby z czterech, które agent liczy;
--   * kolumn na tokeny z CACHE nie było wcale — a przy prompt cachingu (D2) właśnie
--     tam siedzi większość wejścia, więc nie wiadomo było nawet, czy cache działa;
--   * zużycie per hipoteza istniało w kodzie (`wynik.zuzycie`) i było sumowane,
--     a szczegóły wyrzucane — czyli nie wiadomo, które KLASY znalezisk są drogie.
--
-- Bez tego rozbicia optymalizacja jest zgadywaniem, a każdy pomiar kontrolny na
-- prawdziwym koncie to kolejne ~7 USD.
--
-- Zmierzone przy okazji, więc zapisane tutaj, żeby nie zginęło: z 62 minut tylko
-- 40 sekund (1,1%) zajęły wywołania do monday. Czas i koszt siedzą w sesjach
-- agenta, nie w API — optymalizacja collectora nie da nic.

-- ── 1. Uzupełnienie `runy` ──────────────────────────────────────────────
--
-- Tokeny z cache: `cache_read` jest tani, `cache_write` droższy od zwykłego
-- wejścia. Bez rozdzielenia tych dwóch nie da się ocenić, czy caching się opłaca.
ALTER TABLE runy ADD COLUMN tokens_cache_read INTEGER;
ALTER TABLE runy ADD COLUMN tokens_cache_write INTEGER;

-- Czas SAMEJ pętli agenta, w sekundach. `finished_at - started_at` obejmuje też
-- collector i walidację, więc nie odpowiada na pytanie „ile zajął model".
ALTER TABLE runy ADD COLUMN sekund_agenta REAL;

-- ── 2. Zużycie per hipoteza ─────────────────────────────────────────────
--
-- Jedna hipoteza, jeden wiersz. To ta tabela odpowiada na pytanie, od którego
-- zależy każda decyzja o optymalizacji: KTÓRE KLASY SĄ DROGIE.
--
-- Bez niej wiadomo tylko, że run kosztował 7,09 USD. Z nią wiadomo, czy 32
-- hipotezy `BOARD_GHOST` to 60% rachunku (wtedy warto tam eksperymentować)
-- czy 20% (wtedy nie warto).
CREATE TABLE zuzycie_hipotez (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT    NOT NULL REFERENCES runy (run_id),

    -- Klasa z rubryki. Nie FOREIGN KEY, bo rubryka żyje w YAML-u, nie w bazie —
    -- ta sama zasada co w `findings.klasa_id`.
    klasa_id    TEXT    NOT NULL,
    -- Którego obiektu dotyczyła hipoteza (hash konta, id tablicy). Bywa NULL dla
    -- hipotez na poziomie konta.
    obiekt_id   TEXT,

    -- Cztery liczby, które agent i tak liczy. Wszystkie cztery, bo suma wejścia
    -- bez rozbicia na cache nie mówi, czy caching działa.
    tokens_in           INTEGER NOT NULL DEFAULT 0,
    tokens_out          INTEGER NOT NULL DEFAULT 0,
    tokens_cache_read   INTEGER NOT NULL DEFAULT 0,
    tokens_cache_write  INTEGER NOT NULL DEFAULT 0,

    koszt_usd   REAL,
    -- `time.monotonic()`, nie różnica `datetime` — zegar systemowy potrafi skoczyć
    -- w trakcie godzinnego runu i dać czas ujemny.
    sekund      REAL,

    -- Ile razy agent sięgnął do narzędzia. Budżet jest w rubryce per klasa, więc
    -- to mówi, czy agent go wykorzystuje, czy się w nim gubi.
    wywolan_narzedzi INTEGER NOT NULL DEFAULT 0,

    -- Czy ta hipoteza skończyła się znaleziskiem. Hipoteza odrzucona też kosztuje,
    -- i to jest istotne: jeśli 60 z 86 hipotez kończy się odrzuceniem, płacimy
    -- głównie za dowiadywanie się, że czegoś NIE MA.
    byl_finding INTEGER NOT NULL DEFAULT 0,

    zapisano    TEXT    NOT NULL,

    CHECK (byl_finding IN (0, 1))
) STRICT;

CREATE INDEX idx_zuzycie_run ON zuzycie_hipotez (run_id);
CREATE INDEX idx_zuzycie_klasa ON zuzycie_hipotez (klasa_id);

-- Runy sprzed tej migracji mają NULL w nowych kolumnach i zero wierszy tutaj.
-- ŚWIADOMIE nie rozdzielamy sumy 7,09 USD na 86 hipotez „po równo": liczba
-- wyglądająca na pomiar i nie będąca nim jest gorsza od jej braku. Ta sama zasada
-- co przy `runy.rozliczenie` (migracja 009) i przy `runy.prompt_hash`.
