# Projekt z Claude Design: moduł „Audyt monday.com”

**Źródło:** projekt Claude Design `1de87990-f58f-448a-a4af-7268f9f8db75`
(Kuba, 2026-09-25), plik `AuditModule.dc.html`, zbudowany na CXLABS Design
System (`2b90221c-…`). Pobrany do repo 2026-09-25 jako **wzorzec**, a nie kod
do uruchomienia. Działa tylko w runtime Claude Design (`support.js`,
`_ds_bundle.js`), którego tu nie ma.

Dane w projekcie są **zmyślone**: „Nordwind Logistics”, nazwiska i liczby
pochodzą z projektu, a nie z żadnego konta.

## Co z niego wzięliśmy (faza 7)

Ekrany **C** (raport główny) i **D** (raport pogłębiony kategorii), wariant
„hero” oraz układ mobilny. Implementacja:
`src/monday_audit/raport/szablony/raport_uwag.html.j2` i `src/monday_audit/raport/uwagi.py`.

## Czego nie wzięliśmy i dlaczego

| Element projektu | Dlaczego nie |
|---|---|
| Ekrany A, B, H (przegląd konta, analiza w toku, historia) | to ekrany portalu, nie raportu |
| Wariant C „tiles” | wybrany „hero” (decyzja 2026-09-25) |
| Blok „tylko dla zespołu CXLABS” i linia sygnału przy uwadze | plik idzie do klienta: dane zespołu są usunięte, nie ukryte |
| Baner „Zapisz ten raport teraz” i przycisk „Pobierz” | należą do portalu; w pobranym pliku nie mają sensu |
| Wariant zamaskowany z historii | na później (decyzja 2026-09-25) |
| Fonty Clash Display i Avenir oraz `@import` Google Fonts | D14: licencja Clash Display zabrania osadzania w formie do wyjęcia, Avenir jest komercyjny, a raport ma działać offline |
| Animacje (liczniki, `data-anim`) | dokument do pobrania, nie aplikacja |
| Workspace z uwagami („bez aktywnych tablic”) | nie ma takiej klasy, więc kategoria jest „jeszcze nie mierzona” |
