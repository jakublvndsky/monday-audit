"""Stary panel (etapy 3–5): snapshot trwały, pętla per hipoteza, findingi z wagami.

Zostaje do wejścia portalu, potem idzie do usunięcia w całości. Nowa ścieżka
nie importuje stąd niczego — pilnuje `tests/test_granice_pakietow.py`.

## Usunięcie to więcej niż ten katalog

Test granicy obejmuje tylko `src/`. Poza nim ze starego panelu korzystają
(stan 2026-09-28, code review po podziale repo):

- `tests/stary_panel/` — w całości,
- cztery testy nowej ścieżki, które porównują nowy kod ze starym albo testują
  starą pętlę: `tests/tracing/test_trace.py`, `tests/detekcja/test_szablony_findingow.py`,
  `tests/agent/test_uwagi.py`, `tests/zbieranie/test_przebieg.py` — do przycięcia,
- `evals/petla_jednosesyjna.py` — eksperyment na starej pętli, do usunięcia,
- `deploy/` (jednostka woła `stary_panel.cli_web`), `front/`, krok CI z
  `generuj_typy` — to wdrożenie tego panelu,
- `agent/dowod.py` — reguły `REGULA_KWOTA_*`, `REGULA_SLOWNIK`,
  `REGULA_NIEZGODNA_Z_RUBRYKA` używa tylko `kontrakt.py` stąd; po usunięciu martwe.

Sprawdzenie przed usunięciem: `grep -rn stary_panel src tests evals deploy .github`.
"""
