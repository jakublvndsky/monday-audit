"""Nowa ścieżka nie importuje niczego ze starego panelu.

`stary_panel/` idzie do usunięcia w całości, gdy wejdzie portal (decyzja Kuby
2026-09-25). Gdyby `usluga` albo `agent` sięgały tam choćby po stałą, usunięcie
zabrałoby im kod — w tym granicę zapisu z `agent.sdk`, która do podziału repo
mieszkała w starym `agent.py`. Test czyta importy z AST, bo import wewnątrz
funkcji też się liczy.
"""

from __future__ import annotations

import ast
from pathlib import Path

PAKIET = Path(__file__).resolve().parents[1] / "src" / "monday_audit"
STARY = "monday_audit.stary_panel"


def _importy(plik: Path) -> set[str]:
    drzewo = ast.parse(plik.read_text(encoding="utf-8"))
    wynik: set[str] = set()
    for wezel in ast.walk(drzewo):
        if isinstance(wezel, ast.ImportFrom) and wezel.module:
            wynik.add(wezel.module)
            wynik.update(f"{wezel.module}.{a.name}" for a in wezel.names)
        elif isinstance(wezel, ast.Import):
            wynik.update(a.name for a in wezel.names)
    return wynik


def test_nowa_sciezka_nie_importuje_starego_panelu() -> None:
    naruszenia = {
        str(plik.relative_to(PAKIET)): sorted(i for i in _importy(plik) if i.startswith(STARY))
        for plik in PAKIET.rglob("*.py")
        if "stary_panel" not in plik.relative_to(PAKIET).parts
    }
    naruszenia = {k: v for k, v in naruszenia.items() if v}

    assert not naruszenia, f"nowa ścieżka sięga do stary_panel/: {naruszenia}"


def test_test_widzi_pakiety() -> None:
    """Pusty wynik z pustego skanu nie jest dowodem — upewniamy się, że skanuje."""
    pliki = {p.relative_to(PAKIET).parts[0] for p in PAKIET.rglob("*.py")}

    assert {"agent", "zbieranie", "stary_panel", "usluga.py"} <= pliki
