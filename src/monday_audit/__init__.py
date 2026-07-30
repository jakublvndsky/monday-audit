"""Audyt konta monday.com — narzędzie wewnętrzne CXLABS.

Warstwy, w kolejności przepływu (docs/etapy/02-design.md):

    collector — deterministyczny spis konta, czysty GraphQL przez httpx
    detektory — sygnały wzbudzające hipotezy, czysty SQL, zero AI
    agent     — badanie hipotez, wyłącznie narzędzia czytające
    walidacja — kontrakt wyjściowy D8, finding bez `dowod` odpada
    renderer  — wersja wewnętrzna i klientowa

Granice, których nie wolno przekroczyć, są w CLAUDE.md („Zakazy twarde")
i w docs/ARCHITEKTURA.md D5–D6.
"""
