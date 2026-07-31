"""Wspólna konfiguracja testów.

Rozdzielenie, które jest tu całą treścią: **testy jednostkowe nie widzą `.env`,
testy integracyjne widzą.**

Aplikacja czyta `.env` sama (D12), a pytest leci z roota repo, gdzie ten plik
stoi. Bez izolacji test „brak sekretów daje jasny komunikat" znajdowałby prawdziwe
wartości i przechodził, nie sprawdzając niczego — czyli zieleniłby się tym mocniej,
im lepiej wypełniony jest `.env`.

W drugą stronę: warstwa 2 dostaje sekrety **tą samą drogą co program**, czyli
przez `konfiguracja.wczytaj()`. Wcześniej każdy plik integracyjny miał własne
`skipif` po `os.environ` i przez to pomijał się przy wypełnionym `.env` —
bramka, której nikt nie zaprojektował, tylko została po poprzednim mechanizmie.

Testy warstwy 2 uderzają w prawdziwe API, więc są wyłączone z `make testy`
przez `addopts` w `pyproject.toml`. Uruchomienie ich jest osobną decyzją:
`uv run pytest -m integracyjny`.

Dostęp Claude Code do samego pliku `.env` pozostaje zablokowany
w `.claude/settings.json` — to inna granica niż ta i nie znosi jej.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from monday_audit.konfiguracja import ZMIENNA_PLIKU, KonfiguracjaError, wczytaj

SEKRETY = ("MONDAY_TOKEN", "SOL_PSEUDONIMIZACJI", "MONDAY_AUDIT_DB")


@pytest.fixture(autouse=True)
def zrodlo_sekretow(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dwie gałęzie, bo warstwa 1 i warstwa 2 mają przeciwne wymagania."""
    if request.node.get_closest_marker("integracyjny"):
        _wstaw_sekrety_cxlabs(monkeypatch)
    else:
        _odetnij_srodowisko(monkeypatch)


def _odetnij_srodowisko(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warstwa 1 startuje bez sekretów i bez `.env`.

    Wskazanie nieistniejącego pliku, a nie samo skasowanie zmiennych:
    `wczytaj()` bez `MONDAY_AUDIT_ENV_FILE` schodzi na `./.env`, czyli dokładnie
    na plik, który chcemy tu ukryć.
    """
    monkeypatch.setenv(ZMIENNA_PLIKU, str(Path("nie-istnieje-w-testach.env")))
    for zmienna in SEKRETY:
        monkeypatch.delenv(zmienna, raising=False)


def _wstaw_sekrety_cxlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warstwa 2 bierze sekrety z `wczytaj()` i podaje je dalej przez środowisko.

    Środowisko, a nie fixture z wartością, świadomie: testy integracyjne czytają
    `os.environ["MONDAY_TOKEN"]` w kilkunastu miejscach i przepisywanie ich
    sygnatur nic by nie poprawiło. Istotne jest to, **skąd** sekret przychodzi —
    z jedynego wejścia do konfiguracji, nie z osobnej ścieżki obok niego.

    Brak sekretów pomija test z komunikatem. To nie bramka bezpieczeństwa:
    `make sprawdz` na świeżym klonie repo nie może się wywracać przez to,
    że nikt jeszcze nie wypełnił `.env`.
    """
    try:
        ustawienia = wczytaj()
    except KonfiguracjaError as blad:
        pytest.skip(f"warstwa 2 wymaga sekretów konta CXLABS — {blad}")

    monkeypatch.setenv("MONDAY_TOKEN", ustawienia.monday_token.get_secret_value())
    monkeypatch.setenv("SOL_PSEUDONIMIZACJI", ustawienia.sol_pseudonimizacji.get_secret_value())
