"""Wspólna konfiguracja testów.

**Świadomie NIE czyta `.env`.** Pierwotna wersja tego pliku parsowała `.env`,
żeby testy integracyjne same znalazły `MONDAY_TOKEN`. To był błąd: sekrety
klienta nie mają być czytane przez kod, który uruchamia narzędzie —
udostępnia je człowiek, świadomie i na czas jednego uruchomienia.

Testy warstwy 2 wymagają więc jawnego wyeksportowania zmiennych w powłoce:

    export MONDAY_TOKEN=...
    export SOL_PSEUDONIMIZACJI=...
    uv run pytest -m integracyjny

Bez tego pomijają się z komunikatem, a nie sięgają po plik z sekretami.
Blokada dostępu do `.env` jest też wymuszona w `.claude/settings.json`.
"""

from __future__ import annotations
