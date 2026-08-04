-- 003: stawki jako DANE z pochodzeniem, nie liczby wklejone w markdown
--
-- Powód: `docs/CENNIK_AI.md` trzymał stawki jako tekst, a cenniki się zmieniają
-- i nikt nie poprawia ich ręcznie. Docelowo dochodzi front i Marketplace, więc
-- stara stawka w raporcie klienta przestaje być niedogodnością i staje się
-- błędem. Liczby dostają więc jedno miejsce prawdy — tutaj — a markdown zostaje
-- opisem metodologii.
--
-- ROZDZIELENIE, KTÓREGO NIE WOLNO ZLAĆ. Dwie tabele, bo to dwa różne światy:
--
--   `cennik`         stawki PUBLICZNE monday (kredyt AI, ceny per plan).
--                    Odświeżane automatycznie ze stron monday.
--   `stawki_klienta` cena, którą płaci KONKRETNY klient. Na Enterprise jest
--                    NEGOCJOWANA, więc publiczny cennik jej nie zawiera.
--
-- **Scraper NIGDY nie zapisuje do `stawki_klienta`.** Podstawienie ceny
-- z publicznego cennika jako `koszt_licencji_mies` dałoby liczbę pewnie
-- brzmiącą i błędną — dokładnie ten rodzaj wpadki, którą rubryka nazywa
-- podważającą cały raport (O7).

-- ── cennik publiczny ─────────────────────────────────────────────────
CREATE TABLE cennik (
    id              INTEGER PRIMARY KEY,
    pozycja         TEXT    NOT NULL,   -- np. 'kredyt_ai_usd', 'ai_block_kredyty'
    wartosc         REAL    NOT NULL,
    waluta          TEXT,               -- NULL dla pozycji liczonych w kredytach
    jednostka       TEXT    NOT NULL,   -- 'kredyt', 'akcja', 'godzina', 'miesiac'

    -- Pochodzenie. Bez tego stawka jest nieweryfikowalna, a stawka w raporcie
    -- klienta musi dać się sprawdzić.
    zrodlo_url      TEXT,
    sposob          TEXT    NOT NULL,   -- 'scraper' | 'reczna' | 'zasiew'
    wiarygodnosc    TEXT    NOT NULL,   -- 'zrodlo_pierwotne' | 'zewnetrzne'
    pobrano_at      TEXT    NOT NULL,
    wazna_do        TEXT,               -- po tej dacie stawka jest przeterminowana

    -- Cytat z HTML-a, z którego liczba została wyjęta. Gdy scraper poda dziwną
    -- wartość, to jedyny sposób odróżnić „zmieniła się cena" od „zmienił się
    -- układ strony".
    surowy_fragment TEXT,

    CHECK (sposob IN ('scraper', 'reczna', 'zasiew')),
    CHECK (wiarygodnosc IN ('zrodlo_pierwotne', 'zewnetrzne')),
    -- Stawka niedodatnia nie jest stawką. Przedziały rozsądku per pozycja
    -- sprawdza `cennik.py` — schemat łapie tylko przypadek bezsporny.
    CHECK (wartosc > 0)
) STRICT;

-- Historia jest celowa: nie nadpisujemy stawek, dopisujemy nowe odczyty.
-- Dzięki temu snapshot sprzed trzech miesięcy da się zinterpretować stawką,
-- która wtedy obowiązywała (D7, pinowanie z 05-deploy).
CREATE INDEX idx_cennik_pozycja ON cennik (pozycja, pobrano_at DESC);


-- ── stawki konkretnego klienta ───────────────────────────────────────
-- NIGDY nie zapisuje tu scraper. Wyłącznie człowiek: przez `cli_agent
-- --koszt-licencji-mies`, a docelowo formularzem we froncie.
CREATE TABLE stawki_klienta (
    id         INTEGER PRIMARY KEY,
    client_id  TEXT    NOT NULL,
    pozycja    TEXT    NOT NULL,   -- np. 'koszt_licencji_mies'
    wartosc    REAL    NOT NULL,
    waluta     TEXT    NOT NULL,
    podano_at  TEXT    NOT NULL,
    -- Skąd człowiek wziął tę liczbę. Opis słowny, np. „faktura 07/2026".
    -- Raport ma powiedzieć, na czym stoi kwota, którą pokazuje klientowi.
    zrodlo     TEXT    NOT NULL,

    CHECK (wartosc > 0)
) STRICT;

CREATE INDEX idx_stawki_klienta ON stawki_klienta (client_id, pozycja, podano_at DESC);
