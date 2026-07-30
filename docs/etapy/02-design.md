# Etap 2 — Design

**Stan: zatwierdzony.**

Pełne decyzje z uzasadnieniami: **`docs/ARCHITEKTURA.md`** (D1–D11).
Ten dokument jest podsumowaniem i mapą przepływu — nie powtarza uzasadnień.

---

## Trzy pytania etapu Design

Twój cykl definiuje Design jako: **platforma, model, granice zaufania.**

| Pytanie | Odpowiedź | Gdzie uzasadnienie |
|---|---|---|
| Platforma | Agent SDK w procesie workera | D1 |
| Model | Sonnet wszędzie + prompt caching, routing odłożony | D2 |
| Granice zaufania | 7 granic, obrona przez odebranie możliwości | D6 |

---

## Przepływ

```
                    ┌─────────────────────────────┐
                    │  Token klienta (read-only)  │
                    └──────────────┬──────────────┘
                                   ▼
  FAZA 1 ─ COLLECTOR (deterministyczny, httpx, bez MCP)
  ├─ konto i plan                                    ~1 wywołanie
  ├─ użytkownicy + PSEUDONIMIZACJA                   ~2
  ├─ tablice, kolumny, właściciele, znaczniki        ~8
  ├─ automatyzacje per tablica                       ~200
  └─ activity logs — SAMPLING (top 30 + 20 losowych) ~50
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │ snapshots (SQLite)   │  niemutowalny
                        │ osoby_mapowanie      │  bez narzędzia
                        └──────────┬───────────┘
                                   ▼
  DETEKTORY (czysty SQL, zero AI)
  └─ sygnały z rubryki → lista wzbudzonych hipotez
                                   │
                                   ▼
  FAZA 2 ─ AGENT (Agent SDK, Sonnet)
  ├─ narzędzia własne: pobierz_inwentarz, zapytaj_snapshot
  ├─ MCP monday --read-only (podproces, token w env)
  ├─ budżet per hipoteza (z rubryki), bezpiecznik 600/run
  └─ wyjście: JSON wg kontraktu D8
                                   │
                                   ▼
  WALIDACJA (kod) ─ finding bez `dowod` odpada
                                   │
                                   ▼
  RENDERER ─ wstrzyknięcie JSON w szablon Claude Design
  ├─ wersja wewnętrzna (pełna + trop)
  └─ wersja klientowa (bez tylko_wewnetrzne)
                                   ▼
              publisher → docs.cxlabs.digital/klient/...
```

**Zwróć uwagę na kierunek:** agent jest w środku, a nie na końcu.
Po nim jest jeszcze walidacja i renderer — oba deterministyczne.
Agent nic nie publikuje.

---

## Dlaczego collector jest wyczerpujący, a agent wybiórczy

To była zmiana w trakcie projektowania i warto znać jej powód.

Pierwotnie agent miał tylko czytać snapshot. **Błąd:** wtedy znajduje
wyłącznie to, co collector przewidział — a cała jego wartość miała być
w zauważaniu rzeczy nieprzewidzianych.

Poprawka: agent **ma dostęp do monday** (read-only), ale nie jako pierwszy
ruch. Bez inwentarza pierwsze 200 wywołań to ustalanie, co w ogóle
istnieje — a to nie rozumowanie, to spis, i kod robi to taniej.

**Podział ostateczny:**
- **inwentaryzacja = robota kodu** (co istnieje — lista skończona i znana)
- **dochodzenie = robota agenta** (dlaczego tak jest — ścieżka nieznana z góry)

**Agent nie decyduje, *czy* sprawdzić anomalię — decyduje *jak*.**
Lista rzeczy do zbadania jest deterministyczna i wyczerpująca.
Swoboda jest w sposobie dochodzenia, nie w zakresie.

---

## Budżet wywołań — jednostka

Nie "N na audyt". **Budżet na hipotezę.**

Każda wzbudzona hipoteza dostaje limit z rubryki (`budzet_wywolan`).
Konto z trzema problemami zużyje ~20 wywołań, konto z czterdziestoma ~300 —
**i tak ma być**, bo drugie faktycznie wymaga więcej pracy.

Bezpiecznik globalny 600/run to wyłącznik awaryjny: przekroczenie znaczy
błąd w logice, nie duże konto. Loguj i przerwij.

---

## Infrastruktura

```
Caddy (TLS, reverse proxy, ~20 MB)
  ├─ FastAPI (localhost:8000) — trigger runu, odczyt wyników
  └─ worker.py — proces jednorazowy, nie demon
SQLite — w procesie, bez osobnego serwera
```

Caddy sam wyciąga i odnawia certyfikaty Let's Encrypt. Cała konfiguracja:

```
audyt.cxlabs.digital {
    reverse_proxy localhost:8000
}
```

Budżet RAM (Mikrus 2 GB, dzielony — patrz O6):

| | spoczynek | szczyt runu |
|---|---|---|
| Caddy | 20 MB | 20 MB |
| FastAPI | 100 MB | 100 MB |
| Worker | 40 MB | ~350 MB |
| SQLite | 0 | ~50 MB |
| System | 200 MB | 200 MB |
| **Razem** | **~360 MB** | **~720 MB** |

---

## Definition of Done — etap 2

- [x] Platforma wybrana z uzasadnieniem i warunkiem unieważnienia (D1)
- [x] Strategia modelu i cachingu (D2)
- [x] Siedem granic zaufania z mechanizmami (D6)
- [x] Schemat danych (D7)
- [x] Kontrakt wyjściowy agenta (D8)
- [x] Przepływ end-to-end narysowany
- [x] Budżet RAM policzony
