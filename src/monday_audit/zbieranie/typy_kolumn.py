"""Typy kolumn monday: które wypełnia człowiek, a które liczy monday.

Wspólne dla podglądu zakresu (flaga `raportowa`) i detekcji (BOARD_OVERCOMPLEX
liczy tylko kolumny ręczne). Osobny moduł bez sieci — wydzielony po code review
2026-09-28, żeby detekcja nie ładowała klienta GraphQL po jedną stałą.
"""

from __future__ import annotations

# Kolumny wyliczane przez monday, nie wypełniane przez człowieka. Wysoki
# udział znaczy „tablica raportowa" — czyta z innych, nie prowadzi procesu.
# Zastępnik nieosiągalnej flagi o pustych kolumnach (patrz docstring modułu).
TYPY_AUTOMATYCZNE = frozenset(
    {
        "formula",
        "mirror",
        "lookup",
        "dependency",
        "progress",
        "auto_number",
        "creation_log",
        "last_updated",
        "item_id",
    }
)

# Od tego udziału kolumn automatycznych tablica dostaje flagę `raportowa`.
PROG_RAPORTOWEJ = 0.5
