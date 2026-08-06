-- 006: dostęp do aplikacji webowej — konta, sesje, zadania, limity
--
-- ══════════════════════════════════════════════════════════════════════
-- CZEGO TU NIE MA: KOLUMNY NA KLUCZ API KLIENTA
-- ══════════════════════════════════════════════════════════════════════
--
-- To nie przeoczenie, a decyzja (D11, aneks 2026-08-05). Klient wkleja swój
-- klucz monday w formularz, ale klucz **żyje wyłącznie w pamięci procesu runu**
-- i ginie razem z nim. Nie ma go w tej bazie, w logach ani w argv.
--
-- Dlaczego tak twardo: klucz admina monday NIE JEST read-only. Kto go ma, może
-- usunąć każdą tablicę na koncie klienta. Zapisanie go u nas znaczyłoby, że
-- włamanie do naszego serwera jest włamaniem do wszystkich kont klientów —
-- a D11 mówi wprost: „nie stajemy się depozytariuszem dostępu do kont klientów".
--
-- Jeśli ktoś kiedyś doda tu `token` albo `klucz_api`, robi to przeciw tej
-- decyzji i musi ją najpierw zmienić w `docs/ARCHITEKTURA.md`.

-- ── konta dostępu ────────────────────────────────────────────────────
-- Dwie role w jednej tabeli, bo mechanizm jest ten sam: hash hasła plus sesja.
-- Różni je TO, CO WIDZĄ — i o tym decyduje serwer po `rola`, nigdy przeglądarka.
CREATE TABLE konta_dostepu (
    id          INTEGER PRIMARY KEY,
    rola        TEXT    NOT NULL,   -- 'klient' | 'zespol'

    -- Dla roli `klient`: którego klienta widzi. Dla `zespol`: NULL, bo widzi
    -- wszystkich. Ten warunek jest granicą danych, więc pilnuje go CHECK.
    client_id   TEXT,

    -- Dla `zespol`: e-mail osoby. Hasła per OSOBA, nie jedno wspólne —
    -- wspólne uniemożliwia powiedzenie, kto odpalił audyt za 1,71 USD.
    -- Dla `klient`: NULL, bo klient dostaje samo hasło (tak jak w Docs Publisherze).
    email       TEXT,

    -- scrypt ze stdlib: bez nowej zależności, a wolny z założenia.
    hash_hasla  TEXT    NOT NULL,
    sol_hasla   TEXT    NOT NULL,

    utworzono   TEXT    NOT NULL,
    wazne_do    TEXT,               -- NULL = bez terminu; O23 chce terminu dla klientów
    aktywne     INTEGER NOT NULL DEFAULT 1,

    CHECK (rola IN ('klient', 'zespol')),
    -- Rola `klient` BEZ `client_id` widziałaby wszystko albo nic — oba warianty
    -- są błędem, więc niech schemat na to nie pozwoli.
    CHECK ((rola = 'klient' AND client_id IS NOT NULL)
        OR (rola = 'zespol' AND email IS NOT NULL)),
    CHECK (aktywne IN (0, 1))
) STRICT;

CREATE UNIQUE INDEX idx_konta_email ON konta_dostepu (email) WHERE email IS NOT NULL;
CREATE INDEX idx_konta_klient ON konta_dostepu (client_id) WHERE client_id IS NOT NULL;


-- ── sesje ────────────────────────────────────────────────────────────
-- W bazie, nie w podpisanym ciasteczku bez stanu: dzięki temu odebranie dostępu
-- działa NATYCHMIAST (skasuj wiersz), a nie „gdy token wygaśnie". Przy danych
-- osobowych klienta to różnica, która ma znaczenie (O23).
CREATE TABLE sesje (
    -- Losowy identyfikator z `secrets.token_urlsafe`. Trzymamy HASH, nie samą
    -- wartość: wyciek bazy nie daje wtedy gotowych ciasteczek.
    hash_tokenu TEXT    PRIMARY KEY,
    konto_id    INTEGER NOT NULL REFERENCES konta_dostepu (id),
    utworzono   TEXT    NOT NULL,
    wazna_do    TEXT    NOT NULL,
    ostatnie_uzycie TEXT,
    -- Do logu wejść, którego chce O23. Bez user-agenta: nie potrzebujemy go,
    -- a im mniej zbieramy, tym mniej mamy do stracenia.
    ip          TEXT
) STRICT;

CREATE INDEX idx_sesje_konto ON sesje (konto_id);
CREATE INDEX idx_sesje_wazna ON sesje (wazna_do);


-- ── próby logowania ──────────────────────────────────────────────────
-- Hasło klienta jest JEDYNĄ bramą do jego danych osobowych, więc bez limitu
-- prób da się je odgadnąć. Liczymy per identyfikator ORAZ per IP: pierwsze
-- chroni konto, drugie utrudnia zgadywanie po wielu kontach naraz.
CREATE TABLE proby_logowania (
    id           INTEGER PRIMARY KEY,
    identyfikator TEXT   NOT NULL,  -- e-mail albo client_id, którego próbowano
    ip           TEXT,
    kiedy        TEXT    NOT NULL,
    udana        INTEGER NOT NULL,

    CHECK (udana IN (0, 1))
) STRICT;

CREATE INDEX idx_proby ON proby_logowania (identyfikator, kiedy DESC);
CREATE INDEX idx_proby_ip ON proby_logowania (ip, kiedy DESC);


-- ── zadania (run w tle) ──────────────────────────────────────────────
-- Audyt trwa ~17 minut, więc żądanie HTTP go nie utrzyma. Klik zwraca id,
-- run leci osobno, a front odpytuje o stan.
--
-- POWTÓRZENIE, BO TU JEST NAJWIĘKSZA POKUSA: klucz API klienta NIE WCHODZI
-- do tej tabeli. Zadanie startuje z kluczem w pamięci; tutaj ląduje sam stan.
CREATE TABLE zadania (
    id          TEXT    PRIMARY KEY,   -- token_urlsafe, nie licznik: nie zdradza liczby audytów
    client_id   TEXT    NOT NULL,
    konto_id    INTEGER NOT NULL REFERENCES konta_dostepu (id),
    stan        TEXT    NOT NULL,      -- 'w_kolejce' | 'zbieram' | 'analizuje' | 'gotowe' | 'blad'
    etap        TEXT,                  -- tekst dla człowieka: „zbieram tablice 45/105"
    postep      INTEGER,               -- 0–100, NULL gdy nie da się oszacować
    run_id      TEXT,                  -- wypełniane, gdy run agenta wystartuje
    blad        TEXT,                  -- komunikat BEZ wartości sekretnych
    zaczeto     TEXT    NOT NULL,
    skonczono   TEXT,

    CHECK (stan IN ('w_kolejce', 'zbieram', 'analizuje', 'gotowe', 'blad')),
    CHECK (postep IS NULL OR (postep >= 0 AND postep <= 100))
) STRICT;

-- Hamulec kosztu czyta tę tabelę, więc indeks po kliencie i dacie jest wiążący,
-- nie kosmetyczny: sprawdzenie „czy wolno odpalić" leci przy każdym kliknięciu.
CREATE INDEX idx_zadania_klient ON zadania (client_id, zaczeto DESC);
