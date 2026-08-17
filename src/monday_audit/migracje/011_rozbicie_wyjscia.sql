-- Rozbicie WYJŚCIA na trzy kubełki: myślenie, wyrzucone bloki, finalny JSON.
--
-- POWÓD. Migracja 010 pokazała, gdzie idą pieniądze: w runie `ewal-tablice-s7`
-- (37 hipotez, 3,76 USD) wyjście to 123 806 tokenów = 1,86 USD, czyli **49%
-- rachunku przy 4% wolumenu tokenów**. Wejście jest już wyciśnięte — cache 88,7%
-- oszczędza na tym runie ~6,9 USD, a zawężanie inwentarza odrzucono trzy razy
-- pomiarem (cache rośnie z rozmiarem runu: 79% → 83% → 88,7%).
--
-- Została jedna droga: skrócić wyjście. Ale `tokens_out` skleja trzy różne rzeczy
-- o TRZECH RÓŻNYCH dźwigniach, a rachunek nie mówi, w której proporcji:
--
--   1. tokeny MYŚLENIA — model ma myślenie adaptacyjne z `display: omitted`,
--      więc nie widać ich w żadnym bloku tekstu. Dźwignia: `effort` / `thinking`.
--   2. wcześniejsze bloki tekstu — pętla w `agent.zbadaj_hipoteze` bierze OSTATNI
--      niepusty blok, więc poprzednie są rozliczone w tokenach i WYRZUCONE.
--      Dźwignia: instrukcja w prompcie.
--   3. finalny JSON — to, co trafia do raportu. Dźwignia: limit w kontrakcie.
--
-- Szacunek przed tą migracją (znaki findingu / 3,3) dał 79-86% wyjścia „nie na
-- finding", ale NIE rozdzielił punktu 1 od 2. A to od tego zależy, którą dźwignię
-- ciągnąć — i każda zła próba to ~0,6 USD na próbce, która w ogóle coś mierzy.
--
-- Trzy kolumny zamiast zgadywania. Liczymy ZNAKI, nie tokeny: tokenizatora w tej
-- ścieżce nie mamy, a stosunek znaków wystarcza do rozstrzygnięcia proporcji.
-- Tokeny myślenia wychodzą z odejmowania: `tokens_out - (znaki / ~3,3)`.

-- `blokow_tekstu` = 1 znaczy „model odpowiedział raz i to był JSON" — czyli
-- punkt 2 jest zerowy i cała nadwyżka to myślenie. Wartość > 1 znaczy, że
-- rozpisywał się przed odpowiedzią, i wtedy prompt jest właściwą dźwignią.
ALTER TABLE zuzycie_hipotez ADD COLUMN blokow_tekstu INTEGER;

-- Znaki w blokach 1..n-1, czyli te, za które zapłacono i których nikt nie czyta.
ALTER TABLE zuzycie_hipotez ADD COLUMN znakow_wyrzuconych INTEGER;

-- Znaki w bloku ostatnim — to z niego `_wyluskaj_json` bierze rozstrzygnięcie.
ALTER TABLE zuzycie_hipotez ADD COLUMN znakow_finalnych INTEGER;

-- Kolumny są NULLABLE świadomie: runy sprzed tej migracji ich nie mają i NIE
-- WOLNO ich uzupełniać zerem. Zero znaczyłoby „model nic nie napisał", a prawda
-- jest „nie mierzyliśmy". Ta sama zasada co przy `ma_rozbicie` z migracji 010 —
-- brak danych mówi się wprost, nie podstawia się liczby, która wygląda na pomiar.
