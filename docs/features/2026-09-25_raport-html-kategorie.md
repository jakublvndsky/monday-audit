# Raport HTML w czterech kategoriach

| | |
|---|---|
| **Data** | 2026-09-25 |
| **Projekt / klient** | monday.com Account Audit (CXLABS), odbiór na koncie CXLABS |
| **Faza planu** | 7. Raport: przebudowa treści, HTML i PDF (bez PDF) |
| **Zakres zmian** | 23 pliki (`a1b175d..af873af`); `raport_uwag.py`, `szablony/raport_uwag.html.j2`, `rubryka_znalezisk.yaml` 0.7→0.8, `logi.py`, `detektory.py`, `analiza.py`, `deanonimizacja.py`, `szablony_findingow.py` |

## Co się zmieniło

Raport uwag był listą sekcji po klasach — „strasznie nieprzejrzysty" (Kuba,
2026-09-23). Teraz to jeden plik HTML: strona główna z czterema kategoriami
(Tablice, Użytkownicy, Workspace, Agenci), z liczbą uwag i jednym zdaniem przy
każdej, oraz osobny widok pogłębiony każdej mierzonej kategorii z dowodami
w postaci czytelnych chipów i nazwiskami. Raport mówi też liczbami, czego nie
sprawdził (sufit na klasę, próbka logów, pola „nie zmierzone"). Po drodze
domknęły się dwie klasy, które na pełnym koncie dawały niestabilne wyniki:
BOARD_NO_OWNER i AUTOMATION_DEAD.

## Dlaczego

Raport jest produktem, który klient dostaje do ręki — nieczytelny raport
z trafnymi uwagami jest dla niego bezużyteczny. Układ pochodzi z projektu
Kuby w Claude Design (`docs/design/`, ekrany C i D, wariant „hero").

## Decyzje

- **Markup przeniesiony z projektu 1:1** — pierwsza wersja interpretowała
  projekt i „praktycznie w ogóle się z nim nie pokrywała" (Kuba). Odrzucone:
  własna interpretacja układu — rozjechała się już przy pierwszej próbie.
  Zgodność sprawdzona na projekcie wyrenderowanym lokalnie (1240 px i 375 px).
- **Jeden plik, widoki przez kotwice i `:target`, zero skryptów** — raport to
  nasz dokument, który portal osadza albo daje do pobrania; ma działać jako
  sam plik. Odrzucone: rozwinięcie treści na jednej stronie — kategoria ma
  prowadzić do osobnego widoku (ustalenie z 2026-09-23).
- **PDF odłożony** (Kuba) — PDF to nowa zależność: headless Chrome to powrót
  Node'a na produkcję, WeasyPrint to biblioteki systemowe. Odrzucone:
  decyzja w trakcie fazy.
- **Plik = wersja dla klienta** (Kuba) — bez kosztu, odrzuconych hipotez,
  sygnałów i banera „zapisz teraz". Wersja zamaskowana z historii — później.
- **Workspace i Agenci jako „jeszcze nie mierzone"** (Kuba) — nie mają klas
  z detektorem. Odrzucone: „0 uwag", które czyta się jak „w porządku".
- **Zdanie przy kategorii: „Najczęstszy problem: x (n z m)."** — pierwsze
  zdanie pierwszej uwagi (ustalone przed fazą) dało na pełnym koncie 180
  znaków o jednej tablicy. Kategoria mówi o kategorii, nie o uwadze.
- **Kolejność kategorii: od najliczniejszej, niemierzone na końcu** (Kuba) —
  niemierzony „Workspace" stał na pierwszym miejscu raportu.
- **Fonty bez zmian (D14)** — nie osadzamy. Odrzucone: osadzenie Clash
  Display — EULA z `szablony/fonty/FFL.txt` zabrania wyjmowalnego osadzenia.
- **Słownik pól dowodu w rubryce** (`pola_dowodu`: etykieta i format) — chipy
  formatuje kod ze słownika, nie model. Pusty fakt (`None`, pusta mapa) nie
  daje chipa; pusta lista zostaje jako „brak", bo `owners: []` to sedno
  BOARD_NO_OWNER.
- **BOARD_NO_OWNER: log bez okna dla wszystkich tablic bez właściciela,
  limit 100 osobny od próbki** (Kuba) — przy limicie 20 collector odpytywał
  inne tablice niż te, które wybierał sufit; 11 z 20 uwag odpadało bez
  kandydata. Po zmianie 20 z 20.
- **AUTOMATION_DEAD: „brak pliku" to złe dane wejściowe** (Kuba) — fakt
  `tylko_bledy_danych_wejsciowych` i warunek odrzucenia w rubryce 0.8.
  Detektor sam nie odrzuca, rozstrzyga model z uzasadnieniem. Odrzucone:
  zostawienie oceny modelowi bez faktu — dawał 11, 1 i 7 uwag w trzech
  runach na tym samym koncie. Po zmianie 2.
- **Pokrycie rozstrzygnięć parami (klasa, obiekt), nie sumą** — run odbiorczy
  dał 95 rozstrzygnięć na 94 hipotezy, a ostrzeżenie mówiło „reszta
  przepadła". Uwaga niesie `obiekt_id`; brak to ostrzeżenie, nie odrzucenie,
  bo pole nie zmienia treści opłaconej uwagi.
- **Hash bez mapowania: „konto spoza listy użytkowników (prefiks…)"** (Kuba)
  — zamiast „[nieznane konto]". Mówi, co wiemy, bez zgadywania, że konto
  usunięto. Prefiks zostaje, żeby CXLABS mógł odnaleźć wpis.

## Jak to działa

```
analiza_konta (usluga.py)
  └─ uwagi przyjęte walidacją
       └─ zbuduj_raport_uwag (raport_uwag.py)       ← baza RAM runu: osoby_mapowanie, snapshot
            ├─ Deanonimizacja: hash / [OSOBA:hash] → nazwisko
            ├─ chipy_dowodu: rubryka.pola_dowodu → etykieta + format
            ├─ _kategorie: rubryka.kategorie + kategoria klasy → KategoriaRaportu
            └─ _pokrycie: sufit (PozaSufitem), próbka logów, „nie zmierzone"
       └─ wyrenderuj_uwagi → raport_uwag.html.j2 → zapisz_html (prawa 600)
```

Raport z nazwiskami powstaje **raz**, przed zamknięciem bazy RAM runu —
potem mapowania osób już nie ma. Na dysku zostaje tylko plik wskazany przez
`--raport`; w bazie trwałej są uwagi zamaskowane.

## Konfiguracja ręczna

Brak — całość w kodzie i konfiguracji w repo.

## Jak zweryfikować

1. `uv run pytest -q` → 1133 testy zielone.
2. `uv run python -m monday_audit.cli.analiza --zakres workspace --id 3554099 --tylko-szacunek`
   → szacunek kosztu bez wywołania modelu (~1,6 USD dla Demo - 44).
3. `uv run python -m monday_audit.cli.analiza --zakres cale_konto --raport <plik>.html`
   → plik z prawami 600; w przeglądarce strona główna z kategoriami,
   kliknięcie kategorii otwiera jej widok, „Wróć do raportu głównego" wraca.
   Poniżej ~720 px układ mobilny. Plik z nazwiskami po przejrzeniu usunąć.

## Znane ograniczenia

- **PDF** — odłożony, decyzja o zależności przed wznowieniem.
- **Wersja zamaskowana z historii** — nie ma; raport z nazwiskami da się
  złożyć tylko w trakcie runu.
- **BOARD_OVERCOMPLEX mało wybiórczy** — 398 hipotez na pełnym koncie,
  do modelu idzie 20 przez sufit.
- **`obiekt_id` grupy duplikatów to złączone ID tablic** — przy grupie
  91 tablic model przepisuje ~1000 znaków; literówka daje fałszywe
  ostrzeżenie o pokryciu (uwaga i tak przechodzi).
- **Inne błędy automatyzacji z danych wejściowych** (np.
  `invalid_person_assignment`) — bez decyzji, rozstrzyga model.
- **Workspace i Agenci** — bez klas z detektorem, w raporcie „jeszcze nie
  mierzone".
- **Szacunek kosztu** ma podłogę z pomiaru startowego — przy małym zakresie
  zawyża (Demo - 44: ~1,58 USD szacowane, 1,07 USD rzeczywiste).

## Dla klienta

Raport z audytu czytasz teraz od najważniejszego: na pierwszej stronie
widzisz cztery obszary konta i to, gdzie jest najwięcej problemów, a jednym
kliknięciem przechodzisz do szczegółów z nazwami tablic i osób. Raport mówi
też wprost, czego nie sprawdził, więc brak uwagi w danym miejscu nie udaje
potwierdzenia, że wszystko jest w porządku.
