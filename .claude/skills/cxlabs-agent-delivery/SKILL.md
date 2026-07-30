---
name: cxlabs-agent-delivery
description: Metodyka CXLABS budowania systemów agentowych — siedmioetapowy cykl, granica agent/automat, granice zaufania, wzorzec collector/analityk. Używaj przy projektowaniu nowego systemu z agentem AI, decydowaniu czy coś ma być agentem czy automatyzacją, ustalaniu granic bezpieczeństwa oraz przy pytaniach o kolejność prac.
---

# Metodyka CXLABS — systemy agentowe

Ten skill jest **niezależny od projektu** i ma przeżyć audyt monday.
Wszystko tu opisane wynika z praktyki, nie z teorii.

---

## Zasada nadrzędna

> **AI myśli, automatyzacja przenosi dane, człowiek decyduje.**

To nie hasło marketingowe — to kryterium podziału odpowiedzialności
w architekturze. Jeśli w projekcie nie da się wskazać, która warstwa
robi którą z tych trzech rzeczy, projekt jest źle podzielony.

---

## Test agentowości

Zadanie jest agentem tylko wtedy, gdy spełnia **wszystkie trzy**:

1. **Ścieżka nie jest znana z góry** — model decyduje, które narzędzie
   wywołać, na podstawie tego, co właśnie znalazł
2. **Liczba kroków jest nieograniczona** — może być 3 wywołania, może 40,
   zależnie od danych
3. **Model sam stwierdza, że skończył** — nie ma "ostatniego node'a"

**Jeśli któryś punkt nie jest spełniony, to workflow.** Zrób to w Make,
n8n albo zwykłym kodzie — będzie tańsze, szybsze i przewidywalne.

Przykłady, które **oblewają** ten test (i były odrzucone):
- „PDF wchodzi → ekstrakcja → walidacja → item w monday" — zawsze te same
  cztery kroki
- „Klasyfikuj zdjęcia" — jedno wywołanie, znany wynik
- „Zbierz metryki i policz score" — to zapytanie, nie rozumowanie

Przykłady, które **przechodzą**:
- „Dlaczego ten projekt się przesunął" — agent nie wie z góry, gdzie szukać
- „Dopasuj wykonawcę do zakresu" — ścieżka zależy od tego, co znajdzie
  w historii
- „Czy ta tablica jest archiwum, czy porzuconym procesem" — hipoteza,
  sprawdzenie, korekta

---

## Wzorzec collector / analityk

Sprawdzony podział dla każdego systemu, który analizuje cudze dane:

| Warstwa | Charakter | Zadanie |
|---|---|---|
| **Collector** | deterministyczny, kod | spisz wyczerpująco, **co istnieje** |
| **Detektory** | deterministyczne, SQL | wzbudź hipotezy z sygnałów |
| **Analityk** | agentowy | rozstrzygnij **dlaczego tak jest** |
| **Walidacja** | deterministyczna | odrzuć to, co nie ma dowodu |
| **Renderer** | deterministyczny | wyprodukuj artefakt |

Kluczowe rozgraniczenie:

> **Inwentaryzacja to robota kodu. Dochodzenie to robota agenta.**

I dalej:

> **Agent nie decyduje, *czy* sprawdzić anomalię — decyduje *jak*.**
> Lista rzeczy do zbadania jest deterministyczna i wyczerpująca.
> Swoboda jest w sposobie dochodzenia, nie w zakresie.

Dwa błędy, których ten wzorzec unika:
- **Agent bez inwentarza** marnuje pierwsze 200 wywołań na ustalanie,
  co w ogóle istnieje. To spis, nie rozumowanie — kod robi to taniej.
- **Agent zamknięty tylko w snapshocie** znajdzie wyłącznie to, co collector
  przewidział. Traci się dokładnie tę zdolność, dla której się go dodało.

---

## Granice zaufania — checklista

Do przejścia przy każdym systemie agentowym:

- [ ] **Co jest niezaufanym wejściem?** Każdy tekst pisany przez
      użytkownika końcowego, w tym nazwy obiektów. Nie tylko treść —
      nazwa tablicy też może zawierać injection.
- [ ] **Obrona przez odebranie możliwości, nie filtrowanie.**
      Nie ma narzędzia zapisu → injection nie ma czego wykorzystać.
      Wymuszaj na poziomie procesu (np. flaga `--read-only` serwera),
      nie w prompcie.
- [ ] **Jaka jest maksymalna szkoda?** Nazwij ją wprost. Jeśli brzmi
      gorzej niż „błędny wpis w raporcie", przeprojektuj.
- [ ] **Wyjście strukturalne z obowiązkowym dowodem.** Element bez
      wskazania na fakt źródłowy odpada na walidacji.
- [ ] **PII:** pseudonimizacja przed modelem, mapowanie bez narzędzia
      dostępowego, deanonimizacja dopiero w rendererze.
- [ ] **Poświadczenia:** w env procesu narzędzia, nigdy w kontekście
      modelu, nigdy w argv.
- [ ] **Koszt:** budżet per jednostka pracy (nie per run) + bezpiecznik
      globalny jako wyłącznik awaryjny.
- [ ] **Limity osoby trzeciej:** jeśli używasz API klienta, przerwij
      przy 50% jego limitu. To jego konto.

---

## Wybór platformy

| Opcja | Kiedy |
|---|---|
| **Messages API** | brak pętli, jedno wywołanie, znany wynik |
| **Agent SDK** | pętla potrzebna, ale chcesz kontroli nad poświadczeniami, limitami i procesem |
| **Managed Agents** | stałe poświadczenia, długie wznawialne sesje, potrzebny sandbox lub memory store |

**Cross-tenant credentials wykluczają Managed Agents.** Vault jest
zaprojektowany pod stałe poświadczenia właściciela agenta, nie pod
wstrzykiwanie tokena innego klienta per run.

**Cena Agent SDK:** Anthropic Console pokazuje trace'y sesji tylko dla
Managed Agents. Przy SDK obserwowalność jest w całości twoja — zaplanuj ją,
nie odkrywaj.

**Prototyp na Agent SDK → produkcja na Managed Agents** to ścieżka
rekomendowana przez Anthropic. Wybór SDK niczego nie zamyka.

---

## Wybór modelu

**Nie routuj przed pomiarem.** Zacznij jednym modelem (Sonnet) w całej
pętli. Włącz prompt caching od razu na tym, co stałe w obrębie runu —
to darmowa oszczędność.

Routing Haiku/Sonnet/Opus rób **po** instrumentacji, gdy wiesz, który krok
jest drogi. Wcześniej to zgadywanie.

---

## Siedmioetapowy cykl

| Etap | Zawartość | Definition of Done |
|---|---|---|
| **1. Requirements** | zakres, **wykluczenia**, odbiorcy, **rubryka** | mierzalne kryteria sukcesu |
| **2. Design** | platforma, model, granice zaufania, schemat, kontrakt wyjściowy | decyzje z uzasadnieniami i warunkami unieważnienia |
| **3. Build** | funkcja po funkcji, z bramami | pełny run end-to-end + zamrożony korpus |
| **4. Test/Eval** | jednostkowe / integracyjne / evale + sędzia LLM | fałszywe trafienia pod progiem |
| **5. Deploy** | pinowanie wersji, brama promocji, sekrety | brama jako skrypt, nie procedura w głowie |
| **6. Operate** | instrumentacja kosztu i latencji, guardrailsy | da się odpowiedzieć, który krok jest najdroższy |
| **7. Iterate** | ustalenia z produkcji → wymagania | rubryka podniesiona na podstawie realnych danych |

### Trzy rzeczy, które łatwo pomylić w kolejności

**Rubryka należy do etapu 1, nie 3 ani 4.** Nie da się zewaluować
„dobrego wyniku" — to opinia, nie kryterium. Bez zapisanego wcześniej
kryterium poprawności etap 4 zamienia się w dopisywanie testów pod to,
co agent akurat wyprodukował. To nie ewaluacja, to racjonalizacja.

**Korpus testowy powstaje w etapie 3, przed napisaniem agenta.**
Niemutowalne snapshoty prawdziwych danych to jednocześnie harness
ewaluacyjny i mechanizm porównywania wersji. Zamrożone wejście →
mierzalny skutek zmiany promptu.

**Etapy 5–7 nie da się rozpisać przed kodem.** Można zapisać kryteria
i mechanizmy. Szczegóły domykaj po pierwszym działającym runie —
inaczej dokument staje się wymaganiem wobec czegoś, czego nie znasz.

---

## Praktyki pracy z Claude Code

- **Każda decyzja zapisana razem z powodem.** Bez powodu zostanie cofnięta
  w trzeciej sesji — nie ze złej woli, a z pomocności. „Używaj SQLite"
  przegra; „Używaj SQLite, bo maszyna ma 2 GB dzielone i Postgres zabiera
  250 MB rezydentnie przy jednym runie naraz" wygra.
- **Zakazy konkretne, nie zasady ogólne.** „Agent nie dostaje narzędzia
  zapisującego, MCP z `--read-only`" zamiast „dbaj o bezpieczeństwo".
- **Kolejność jako brama, nie sugestia.** Zapisz wprost, czego nie wolno
  zacząć, dopóki coś innego nie jest zweryfikowane przez człowieka.
- **Stan projektu w pliku, którego agent nie edytuje** (`STATUS.md`).
  Instrukcja „zapytaj przed przejściem dalej" jest miękka; brak prawa
  do zmiany stanu jest twardy.
- **Niepotwierdzone założenia w osobnym pliku** i wymienione w `CLAUDE.md`.
  Fakt wpisany jako pewny stanie się fundamentem trzech funkcji.
- **`CLAUDE.md` krótki.** Jest w kontekście zawsze, więc każde zdanie
  kosztuje. Szczegóły do skilli i dokumentów wczytywanych na żądanie.
- **Skille kształtu kompetencji, nie fazy projektu.** „Jak odpytywać
  monday API" ma sygnał wyzwalający. „Etap czwarty" nie ma — nie odpali
  się nigdy albo odpali razem z sześcioma innymi.
