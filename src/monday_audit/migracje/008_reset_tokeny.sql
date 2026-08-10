-- „Nie pamiętam hasła" dla zespołu: jednorazowe tokeny resetu.
--
-- POWÓD. Reset z panelu (007) wymaga sesji, a kto zgubił hasło, sesji nie ma —
-- błędne koło. Brakowało drogi dla osoby, która NIE MOŻE się zalogować.
--
-- Dowodem tożsamości jest **skrzynka pocztowa**: link idzie na adres
-- @cxlabs.digital i tylko właściciel skrzynki go dostanie. To jedyny dowód,
-- jaki mamy bez SSO (O24) — dlatego reset bez maila byłby otwartą bramą, nie
-- resetem.

CREATE TABLE reset_tokeny (
    -- HASH tokenu, nie sam token. Ta sama zasada co w `sesje`: wyciek bazy nie
    -- daje wtedy gotowych linków do podstawienia.
    hash_tokenu TEXT    PRIMARY KEY,

    konto_id    INTEGER NOT NULL REFERENCES konta_dostepu (id),
    utworzono   TEXT    NOT NULL,

    -- Krótki termin, bo link leży w skrzynce i przetrwa tam lata. Trzydzieści
    -- minut wystarcza, żeby kliknąć, i nie wystarcza, żeby link znaleziony
    -- w cudzej skrzynce po miesiącach jeszcze działał.
    wazny_do    TEXT    NOT NULL,

    -- JEDNORAZOWOŚĆ. Bez tego link z maila działa aż do wygaśnięcia i można go
    -- użyć drugi raz — a mail bywa przekazywany, cytowany w odpowiedzi albo
    -- czytany przez kogoś, kto ma dostęp do skrzynki później.
    uzyty_o     TEXT,

    ip          TEXT
) STRICT;

-- Sprzątanie starych tokenów po `konto_id` przy każdym nowym żądaniu: jedna
-- osoba klikająca „nie pamiętam" pięć razy nie ma zostawiać pięciu ważnych
-- linków.
CREATE INDEX idx_reset_konto ON reset_tokeny (konto_id);
