-- Reset haseł: koniec z dwoma aktywnymi kontami jednego klienta.
--
-- ZMIERZONA USTERKA. `--dodaj-klienta cxlabs` wywołane drugi raz nie zmieniało
-- hasła, tylko zakładało DRUGIE konto — i stare hasło nadal wpuszczało. Na
-- kopii bazy demo klient `cxlabs` miał konta id 3 i 7, oba działające.
--
-- `zaloguj` wybiera konto klienta przez `fetchone()` bez `ORDER BY`, więc przy
-- duplikacie wpuszcza dowolne z nich. To gorsze niż brak funkcji resetu:
-- „wydałem nowe hasło" wyglądało na odebranie starego dostępu, a nie odbierało.
--
-- Konta zespołu tej luki nie miały — `idx_konta_email` jest UNIQUE od migracji
-- 006. Brakowało odpowiednika dla `client_id`, i to jest sedno tej migracji:
-- reset w kodzie naprawia objaw, a indeks zamyka drogę, którą duplikaty
-- powstawały. Bez niego wrócą inną drogą za miesiąc.

-- ── 1. Sprzątanie tego, co już powstało ────────────────────────────────
--
-- Zostaje NAJNOWSZE konto per klient (największe `id`), starsze tracą `aktywne`.
-- Świadomie NIE `DELETE`: wiersze są śladem, kto miał dostęp i od kiedy, a to
-- informacja audytowa. Dezaktywowane konto nie wpuszcza, bo `zaloguj` filtruje
-- po `aktywne = 1`.
--
-- Kolejność wobec indeksu jest istotna: na duplikatach `CREATE UNIQUE INDEX`
-- zawiódłby, migracja padłaby w połowie transakcji i baza zostałaby bez indeksu,
-- ale z duplikatami.
UPDATE konta_dostepu SET aktywne = 0
WHERE rola = 'klient'
  AND aktywne = 1
  AND id NOT IN (
    SELECT MAX(id) FROM konta_dostepu
    WHERE rola = 'klient' AND aktywne = 1
    GROUP BY client_id
  );

-- ── 2. Zamknięcie drogi ────────────────────────────────────────────────
--
-- Indeks CZĘŚCIOWY, nie zwykły unikalny. Historia dezaktywowanych kont ma prawo
-- mieć wiele wierszy na klienta — po każdym resecie zostaje jeden. Blokujemy
-- tylko wiele kont JEDNOCZEŚNIE WAŻNYCH, bo to jest stan, który psuł logowanie.
CREATE UNIQUE INDEX idx_konta_klient_aktywny
  ON konta_dostepu (client_id)
  WHERE rola = 'klient' AND aktywne = 1;
