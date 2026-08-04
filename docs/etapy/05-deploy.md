# Etap 5 — Deploy

**Stan: zablokowany do zamknięcia etapu 4.**

> Ten dokument opisuje **kryteria i mechanizmy**, nie implementację.
> Szczegóły wdrożenia domykamy po pierwszym działającym runie —
> pisanie ich teraz byłoby opisywaniem czegoś, czego nie znamy.

---

## Pinowanie wersji

Wszystkie **sześć** elementów poniżej musi być zapisane przy każdym runie.
Bez tego audyt sprzed trzech miesięcy jest nieodtwarzalny.

Rosło z czterech: dwa doszły z pomiarów, nie z projektu. **Wersja API** —
bo `2026-10` usuwa wszystkie flagi użytkownika, więc ta sama kwerenda na
innej wersji zwraca inne dane (O15). **Wersja cennika** — bo od chwili, gdy
stawki odświeżają się same, ta sama kwota policzona w lipcu i we wrześniu
będzie inna (D13).

| Element | Gdzie zapinane | Dlaczego |
|---|---|---|
| **Model** | `runy.model`, pełny identyfikator, nie alias | alias przesuwa się przy nowym wydaniu i wynik zmienia się bez zmiany kodu |
| **Rubryka** | `rubric_version` przy każdym findingu | umożliwia porównanie starego snapshotu z nową rubryką |
| **Prompt agenta** | hash pliku `PROMPT_AGENTA.md` | zmiana promptu zmienia wynik i musi być śledzona |
| **Collector** | `collector_ver` w snapshocie | zmiana zakresu zbierania zmienia znaczenie snapshotu |
| **Wersja API monday** | `meta.wersja_api` w snapshocie | nieprzypięta wersja to cicha zmiana schematu po stronie dostawcy (O15) |
| **Wersja cennika** | `runy.cennik_ver` | znacznik `pobrano_at` stawek UŻYTYCH w runie; run bez kwot zostaje z NULL, żeby nie pinować cudzej daty |

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
| Token klienta monday | env procesu workera, **nie argv** | argv widoczne w `ps`. Podprocesu MCP nie ma (D4) — token wczytuje `konfiguracja.wczytaj()` i nie wychodzi poza `MondayClient` |
| Sól do hashowania osób | env, osobno per klient | wyciek soli = możliwość deanonimizacji |
| Klucz publishera docs | env | |

Po zakończeniu audytu **token klienta jest usuwany.** Nie przechowujemy
poświadczeń między runami (D11). Jeśli klient chce powtórki, daje token
ponownie.

---

## Wdrożenie na Mikrusa

Kolejność, każdy krok weryfikowalny osobno:

1. Node 20 (pod CLI Agent SDK — **nie** pod MCP, tego nie ma), Python 3.12, `uv`
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

- [ ] Sześć elementów pinowania zapisywanych przy runie
- [ ] Brama promocji zaimplementowana jako skrypt, nie procedura w głowie
- [ ] Sekrety w env, token klienta nie w argv
- [ ] Run produkcyjny na koncie CXLABS przechodzi
- [ ] Kopia zapasowa działa i odtworzenie zostało przetestowane
