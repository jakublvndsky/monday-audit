# Etap 1 — Requirements

**Stan: zatwierdzony.** Ten dokument jest referencją. Jeśli implementacja
wymaga czegoś, czego tu nie ma — to nie jest brak dokumentu, to zmiana
zakresu. Zapytaj.

---

## Problem

CXLABS wdraża monday.com i utrzymuje konta klientów. Przy pierwszym
kontakcie z prospektem oraz przy rozmowie o kolejnym etapie u obecnego
klienta brakuje **twardej listy rzeczy do zrobienia**. Rozmowa opiera się
na ogólnikach, a nie na konkretach z ich własnego konta.

Jednocześnie CXLABS nie ma case studies z liczbami, bo nikt nie mierzy
stanu przed wdrożeniem.

## Produkt

Wewnętrzne narzędzie, które audytuje konto monday.com klienta i produkuje
raport ze znaleziskami — rankowany, z rekomendacjami i, gdzie to uczciwie
możliwe, z kwotą.

**Model użycia:** klient udostępnia token read-only w ramach relacji
doradczej. CXLABS odpala audyt ręcznie. Raport wychodzi jako link.

## Czego to NIE jest

Zapisane wprost, bo każdy z tych punktów był rozważany i odrzucony:

- ❌ **Nie SaaS.** Klient nie ma dostępu do narzędzia.
- ❌ **Nie self-service.** Prospekt nie wkleja sam tokena.
- ❌ **Nie abonament.** Jednorazowy run, nie monitoring cykliczny.
- ❌ **Nie dashboard.** Wyjściem jest raport, nie interfejs.
- ❌ **Nie Health Score.** Zamiast jednej liczby z arbitralnymi wagami —
  ranking znalezisk po `waga × wysiłek`.

Monitoring cykliczny z notyfikacjami to **produkt drugi** i tam wchodzi
front Artura w React. Nie mieszamy.

## Odbiorcy

Jeden run, dwa wyjścia z tego samego JSON-a:

| Wersja | Odbiorca | Zakres |
|---|---|---|
| **wewnętrzna** (główna) | zespół CXLABS | wszystko + pole `trop` wskazujące na usługę |
| **klientowa** | admin klienta | bez klas `tylko_wewnetrzne`, bez `trop` |

Wersja wewnętrzna jest podstawą. Klientowa jest z niej pochodna.

**Powód rozdziału:** raport musi być na tyle niepokojący, żeby prowadził
do rozmowy, i na tyle taktowny, żeby nie zabrzmiał jak wytyk wobec osoby,
która to konto zbudowała — a to zwykle ta sama osoba, z którą rozmawiacie.
Dwie klasy (`PROCESS_BYPASS`, `ENGAGEMENT_DROP`) są dlatego
tylko wewnętrzne.

## Co jest znaleziskiem

Definicja żyje w `rubryka_znalezisk.yaml`. To specyfikacja, nie
dokumentacja — bez niej nie da się ani zbudować walidacji, ani napisać
testów.

Kluczowe reguły z rubryki:

- każde znalezisko wskazuje na **fakt deterministyczny** ze snapshotu
- każda klasa ma jawne **warunki odrzucenia** (obrona przed fałszywymi
  trafieniami)
- każda klasa ma **budżet wywołań** dla agenta
- wycena istnieje **tylko** tam, gdzie jest mnożeniem znanych liczb
  (licencje). Nigdzie indziej — wymyślone stałe godzinowe zabijają
  wiarygodność raportu

## Podział pracy AI / kod

Wynika z rubryki, per klasa:

- `ZOMBIE_ACCOUNT` — czysty SQL, agent nie uczestniczy
- `PROCESS_BYPASS` — w całości agent, 12 wywołań budżetu
- reszta — sygnał deterministyczny, interpretacja agenta

**Fakty nigdy nie wychodzą z modelu.** LLM nie liczy wiarygodnie,
a jedna zmyślona liczba w raporcie zabija produkt u pierwszego klienta,
który ją sprawdzi.

## Kryteria sukcesu v1

1. Audyt konta CXLABS kończy się bez błędu i produkuje raport
2. Każdy finding w raporcie da się zweryfikować ręcznie w monday
   po polu `dowod`
3. Zero findingów bez dowodu (walidacja to gwarantuje)
4. Run średniego konta poniżej ~300 wywołań API
5. Raport jest wystawiony jako link w CXLABS Design System
6. `hipotezy_odrzucone` zawiera co najmniej jedną pozycję —
   agent, który potwierdza wszystko, jest zepsuty

## Definition of Done — etap 1

- [x] Zakres i wykluczenia zapisane
- [x] Odbiorcy i widoczność per klasa ustalone
- [x] Rubryka znalezisk w wersji 0.1 (11 klas, 1 nieaktywna)
- [x] Kryteria sukcesu mierzalne
- [x] Otwarte założenia wyodrębnione do `OTWARTE.md`
