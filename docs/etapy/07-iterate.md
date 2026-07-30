# Etap 7 — Iterate

**Stan: zablokowany do zamknięcia etapu 6.**

Cel etapu: **ustalenia z produkcji wracają do wymagań.** Bez tego kroku
cykl jest linią, nie pętlą, a rubryka zastyga w wersji 0.1 na zawsze.

---

## Główna pętla: rubryka to dokument żywy

```
audyt u klienta
      │
      ▼
klient kwestionuje finding ──────────► przypadek do złotego zestawu
      │                                        │
      │                                        ▼
      │                              popraw `warunki_odrzucenia`
      │                                   w rubryce
      │                                        │
      ▼                                        ▼
klient potwierdza,                    rubryka 0.N+1
że coś przemilczeliśmy                        │
      │                                        ▼
      └──────► nowa klasa albo         przepuść korpus przez
               luźniejszy sygnał        nową wersję i porównaj
```

Krok, który to spina i który jest darmowy dzięki D7:
**przepuść zamrożone snapshoty przez nową rubrykę i porównaj findingi
z poprzednią wersją.** Zero zapytań do monday, zero ryzyka, pełna
informacja o tym, co zmiana faktycznie zrobiła.

---

## Wersjonowanie rubryki

- Rubryka żyje w gicie, podlega review jak kod
- Baza zapisuje `rubric_version` przy każdym findingu
- Zmiana `warunku_odrzucenia` = podniesienie wersji
- Nowa klasa = podniesienie wersji
- **Nigdy nie edytuj rubryki bez podniesienia wersji** — inaczej stare
  findingi przestają być wytłumaczalne

Przy każdej nowej wersji: pełne evale na korpusie, porównanie z baseline'em,
zapis wyniku. To jest ten sam mechanizm co brama promocji z etapu 5.

---

## Sygnały, że klasa powinna wypaść

Zbieraj przez pierwsze ~10 audytów. Klasa jest kandydatką do usunięcia, gdy:

- **nigdy się nie wzbudza** — sygnał za wąski albo problem nierealny
- **wzbudza się zawsze** — sygnał za luźny, nie nosi informacji
- **wysoki koszt wywołań na jedno potwierdzone znalezisko**
  (dane z etapu 6, pytanie 3)
- **klient regularnie ją kwestionuje** — nasza teoria jest błędna,
  nie jego konto
- **nigdy nie prowadzi do rozmowy handlowej** — nie ma `tropu`,
  czyli po co ją raportujemy

Pierwsze kandydatki, jeśli okażą się drogie: `BOARD_OVERCOMPLEX`
(przecina zasadę D5, wymaga samplingu itemów — patrz O5).

---

## Decyzje odłożone — kiedy je otworzyć

Każda z nich ma jawny warunek. Nie otwieraj przed nim, nie zwlekaj po nim.

| Decyzja | Warunek otwarcia |
|---|---|
| **Routing modeli** (D2) | dane o koszcie per krok z etapu 6 |
| **Postgres zamiast SQLite** (D3) | równoległe runy albo model abonamentowy |
| **Langfuse** (D10) | wolumen, przy którym SQL przestaje wystarczać |
| **Aplikacja OAuth** (D11) | self-service albo run cykliczny |
| **Managed Agents** (D1) | model abonamentowy + rozwiązany problem credentiali |
| **Zejście do itemów** (D5) | więcej niż jedna klasa niewykonalna bez napływu (O4) |
| **Front w React** | produkt drugi (monitor), nie ten |

---

## Wejście do produktu drugiego

Po ~10 audytach będziecie mieć rzecz, której dziś nie ma nikt inny:
**dane o tym, jak wyglądają realne konta monday w polskim MŚP.**

To jest wejście do:
- **monitora subskrypcyjnego** — tu wchodzi React Artura, bo pojawia się
  interaktywność i notyfikacje
- **benchmarku** — "wasz engagement to 43%, mediana wśród firm waszej
  wielkości to 68%". Tego nie da się skopiować bez waszej bazy snapshotów
- **case studies z liczbami** — różnica między snapshotem #1 i #4
  u tego samego klienta

Trzecia pozycja była jednym z powodów, dla których ten projekt powstał
(etap 1). Warto to sprawdzić po pierwszym roku: czy faktycznie mamy
case study, którego brakowało.

---

## Co przeżyje ten projekt

Skill `cxlabs-agent-delivery` jest jedynym artefaktem, który nie jest
związany z audytem. Po zamknięciu tego projektu:

- przenieś do niego wszystko, co okazało się prawdą **niezależnie od
  domeny** (wzorzec collector/agent, granica deterministyczne/agentowe,
  budżet per hipoteza, dowód obowiązkowy)
- usuń to, co okazało się specyficzne dla audytu
- następny projekt startuje z tego skilla, nie od zera

To jest ta warstwa metodyki, o której była mowa na początku:
**metodyka jako kod, nie dokument w Drive.**

---

## Definition of Done — etap 7

- [ ] Pętla „kwestionowany finding → złoty zestaw → rubryka" przetestowana
      choć raz w praktyce
- [ ] Rubryka podniesiona do 0.2 na podstawie realnych audytów
- [ ] Klasy bez wartości usunięte, decyzja udokumentowana
- [ ] Decyzje odłożone przejrzane wobec warunków otwarcia
- [ ] `cxlabs-agent-delivery` uogólniony na następne projekty
