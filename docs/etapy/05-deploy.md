# Etap 5 — Deploy

**Stan: zablokowany do zamknięcia etapu 4.**

> Ten dokument opisuje **kryteria i mechanizmy**, nie implementację.
> Szczegóły wdrożenia domykamy po pierwszym działającym runie —
> pisanie ich teraz byłoby opisywaniem czegoś, czego nie znamy.

---

## Pinowanie wersji

Wszystkie cztery elementy poniżej muszą być zapisane przy każdym runie.
Bez tego audyt sprzed trzech miesięcy jest nieodtwarzalny.

| Element | Gdzie zapinane | Dlaczego |
|---|---|---|
| **Model** | pełny identyfikator, nie alias | alias przesuwa się przy nowym wydaniu i wynik zmienia się bez zmiany kodu |
| **Rubryka** | `rubric_version` przy każdym findingu | umożliwia porównanie starego snapshotu z nową rubryką |
| **Prompt agenta** | hash pliku `PROMPT_AGENTA.md` | zmiana promptu zmienia wynik i musi być śledzona |
| **Collector** | `collector_ver` w snapshocie | zmiana zakresu zbierania zmienia znaczenie snapshotu |

Alias modelu (typu `latest`) w produkcji jest zakazany. Podnoszenie
wersji modelu przechodzi przez bramę promocji jak każda inna zmiana.

---

## Brama promocji

Zmiana idzie na produkcję **tylko** po przejściu warstwy 3 z etapu 4.

Blokery bezwzględne:
- fałszywe trafienia > 0.1
- odsetek odrzuconych na walidacji > 0.15
- test antyprzeciekowy PII nie przechodzi
- test injection nie przechodzi
- którykolwiek `hipotezy_odrzucone` pusty

Regresja w trafności o więcej niż 0.05 wobec poprzedniego baseline'u
wymaga świadomej decyzji człowieka, nie automatycznego przejścia.

**Uruchamiaj evale na tym samym zamrożonym korpusie co poprzednio.**
Zmiana korpusu i zmiana promptu w jednym kroku = brak informacji,
co spowodowało różnicę.

---

## Sekrety

| Sekret | Gdzie | Uwagi |
|---|---|---|
| Klucz API Anthropic | env procesu workera | nigdy w repo |
| Token klienta monday | env podprocesu MCP, **nie argv** | argv widoczne w `ps` |
| Sól do hashowania osób | env, osobno per klient | wyciek soli = możliwość deanonimizacji |
| Klucz publishera docs | env | |

Po zakończeniu audytu **token klienta jest usuwany.** Nie przechowujemy
poświadczeń między runami (D11). Jeśli klient chce powtórki, daje token
ponownie.

---

## Wdrożenie na Mikrusa

Kolejność, każdy krok weryfikowalny osobno:

1. Node 20 (pod podproces MCP), Python 3.12, `uv`
2. Caddy + `Caddyfile` → sprawdź, czy certyfikat się wystawił
3. SQLite + migracje → sprawdź, czy aplikują się od zera
4. FastAPI jako usługa systemd → sprawdź `/health`
5. Worker jako proces jednorazowy wywoływany przez FastAPI —
   **nie demon** (O6, budżet RAM)
6. Pierwszy run produkcyjny na koncie CXLABS

**Sprawdź realną rezerwę RAM przed krokiem 5** (O6). Jeśli poniżej 800 MB,
zawęź sampling activity logs.

---

## Kopie zapasowe

Snapshoty są niemutowalne i są jedynym źródłem case studies z liczbami.
Ich utrata jest nieodwracalna — nie da się odtworzyć stanu konta klienta
z przeszłości.

- Codzienny `.backup` SQLite poza Mikrusa
- Test odtworzenia raz, ręcznie, przed pierwszym audytem klienta

---

## Definition of Done — etap 5

- [ ] Cztery elementy zapinane i zapisywane przy runie
- [ ] Brama promocji zaimplementowana jako skrypt, nie procedura w głowie
- [ ] Sekrety w env, token klienta nie w argv
- [ ] Run produkcyjny na koncie CXLABS przechodzi
- [ ] Kopia zapasowa działa i odtworzenie zostało przetestowane
