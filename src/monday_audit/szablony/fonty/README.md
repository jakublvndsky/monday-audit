# Fonty marki — dlaczego NIE MA tu plików fontów

Raport używa dwóch krojów CXLABS: **Clash Display** (nagłówki) i **Avenir**
(tekst). W tym katalogu leży wyłącznie licencja Clash Display — **nie same
fonty** — i to jest decyzja, nie przeoczenie.

## Powód: licencja zabrania tego, czego potrzebowałby samowystarczalny HTML

Clash Display jest darmowy (Fontshare / Indian Type Foundry), ale FF EULA
z `FFL.txt` mówi wprost:

> **§02** The Fonts may not […] be distributed, duplicated, loaned, resold or
> licensed in any way […] This includes the distribution of the Fonts by
> e-mail […] **uploading them in a public server**.
>
> **§03** You may embed the Font Software in PDF and other digital documents
> provided that is done in a **secured, read-only mode**. It must be ensured
> beyond doubt that the recipient cannot use the Font Software to edit or to
> create new documents. **The extraction of the Font Software in whole or in
> part is prohibited.**

Osadzenie fontu w HTML-u jako `data:` URI łamie oba punkty naraz:

- plik HTML jest **tekstem**, więc każdy odbiorca wyjmie z niego woff2 jednym
  poleceniem — a „extraction […] is prohibited"
- trzymanie binarki w tym repo znaczy wysłanie jej na GitHub, czyli
  „uploading […] in a public server"

Avenir jest komercyjny (Linotype) i tam jest jeszcze ciaśniej.

## Co robimy zamiast tego

**Raport odwołuje się do fontów ZAINSTALOWANYCH w systemie**, bez osadzania:

```css
--font-display: "Clash Display", "Avenir Next", "Avenir", …;
--font-body: "Avenir", "Avenir Next", …;
```

Konsekwencje, każda świadoma:

| Gdzie otwierany | Jak wygląda |
|---|---|
| Mac z zainstalowanym Clash Display | dokładnie zgodnie z marką |
| dowolny Mac (Avenir jest systemowy) | nagłówki w Avenirze — **drugi font marki**, nie Arial |
| Windows/Linux bez obu | ostatni fallback systemowy |

Degradacja spada na **drugi krój marki**, a nie na losowy systemowy — dlatego
`"Avenir Next", "Avenir"` stoją w stosie nagłówkowym przed czymkolwiek innym.

## Jak dostać pełną zgodność z marką

**Zainstaluj Clash Display lokalnie** (raz, na swoim Macu):

```bash
curl -sL -o /tmp/clash.zip https://api.fontshare.com/v2/fonts/download/clash-display
unzip -j /tmp/clash.zip 'ClashDisplay_Complete/Fonts/OTF/*' -d ~/Library/Fonts
```

Od tej chwili każdy raport wyrenderowany na tej maszynie ma nagłówki w Clash
Display — na ekranie i w PDF.

## Wysyłka do klienta: PDF, nie HTML

Licencja **wprost dopuszcza** to, czego potrzebujemy (§03, §04): font osadzony
w nieedytowalnym dokumencie „solely for printing and display purposes".

Więc: otwórz raport w przeglądarce na maszynie z fontem → wydrukuj do PDF →
wyślij PDF. Dokument wygląda dokładnie jak marka, font jest osadzony legalnie,
a odbiorca nie ma jak go wyjąć.

Wysłanie surowego HTML też jest w porządku — tylko nagłówki spadną na Avenira
u kogoś, kto nie ma Clash Display.

## Czego NIE robić

- **Nie wrzucaj plików fontów do tego repo.** Żaden `.otf`, `.ttf`, `.woff2`.
- **Nie generuj `@font-face` z `data:` URI** dla Clash Display ani Avenira.
- Nie podpinaj Google Fonts ani żadnego CDN-u — raport musi otwierać się
  offline i jest na to test (`test_dokument_nie_ma_zasobow_zewnetrznych`).

Oryginalny `colors_and_type.css` z Design Systemu ma `@import` do Google Fonts
po Nunito Sans jako zamiennik brakujących wag Avenira. **Do raportu tego nie
przenosimy** — to zewnętrzny zasób, a raport ma być samowystarczalny.
