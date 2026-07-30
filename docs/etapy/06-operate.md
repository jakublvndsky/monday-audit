# Etap 6 — Operate

**Stan: zablokowany do zamknięcia etapu 5.**

---

## Punkt wyjścia: Console nic nie pokaże

Decyzja D1 (Agent SDK zamiast Managed Agents) ma cenę i tutaj ją płacimy.
Anthropic Console pokazuje trace'y sesji **tylko dla Managed Agents**.
Przy Agent SDK dostajemy w Console zagregowane zużycie i koszty,
bez trace'ów per run.

**Obserwowalność jest w całości na nas.** To nie argument, żeby wracać
do Managed Agents — cross-tenant credentials nadal je wykluczają — ale
to pozycja, którą trzeba świadomie zbudować, a nie odkryć w trakcie
debugowania.

---

## Co logujemy

Tabela `wywolania` (D7). Per wywołanie narzędzia:

`run_id`, `hipoteza_id`, `narzedzie`, `tokens_in`, `tokens_out`,
`latency_ms`, `model`, `at`

Per run, osobno:

`client_id`, `snapshot_id`, czas całkowity, wywołania API monday,
suma complexity, koszt w tokenach, liczba findingów, liczba odrzuconych
na walidacji, liczba hipotez zbadanych i odrzuconych

**Bez Langfuse** (D10). Przy ~20 audytach miesięcznie SQL odpowie
na każde pytanie. Jeśli logujemy w znormalizowanej tabeli, późniejszy
eksport to skrypt.

---

## Pytania, na które musisz umieć odpowiedzieć

To jest właściwy test instrumentacji — nie "czy logujemy", ale
"czy da się to policzyć":

1. Ile kosztuje jeden audyt, w złotówkach?
2. Który krok jest najdroższy? ← **to odblokowuje routing modeli (D2)**
3. Która klasa hipotez zużywa najwięcej wywołań na jedno potwierdzone
   znalezisko? (kandydatka do wyrzucenia)
4. Ile wywołań API monday zużyliśmy u klienta i jaki to procent
   jego dziennego limitu?
5. Czy prompt caching faktycznie działa? (porównaj `tokens_in`
   pierwszego i kolejnych wywołań w runie)

Pytanie 2 jest najważniejsze, bo **dopiero po nim routing Haiku/Sonnet/Opus
przestaje być zgadywaniem.** To jest moment, w którym D2 się otwiera.

---

## Guardrailsy w runtime

| Guardrail | Mechanizm | Reakcja na naruszenie |
|---|---|---|
| Budżet per hipoteza | licznik w narzędziu | narzędzie zwraca komunikat o wyczerpaniu; agent domyka hipotezę z tym, co ma |
| Bezpiecznik globalny 600 | licznik w kliencie | przerwij run, zaloguj, powiadom człowieka |
| Limit dzienny klienta | licznik przed każdym wywołaniem | przerwij przy 50% limitu — to konto klienta, nie nasze |
| Read-only | flaga `--read-only` MCP | wymuszone przez serwer, nie do obejścia z promptu |
| Brak PII | walidacja snapshotu przy zapisie | odrzuć zapis, zaloguj jako błąd krytyczny |
| Kontrakt wyjściowy | walidacja przed rendererem | odrzuć finding, zaloguj |
| Limit czasu runu | timeout sesji | przerwij, zachowaj snapshot |

**Przerwanie przy 50% dziennego limitu klienta** jest tu najważniejsze
i najłatwiejsze do pominięcia. To jego konto i jego integracje.
Audyt, który spowolni klientowi pracę w środku dnia, jest gorszy
niż brak audytu.

---

## Playbook incydentów

| Objaw | Prawdopodobna przyczyna | Reakcja |
|---|---|---|
| Run przerwany bezpiecznikiem 600 | pętla w logice badania hipotezy | nie podnoś limitu — znajdź pętlę |
| Odsetek odrzuceń walidacji skacze | zmiana modelu albo dryf promptu | sprawdź pinowanie (etap 5) |
| Agent nie odrzuca żadnej hipotezy | prompt zbyt zachęcający do potwierdzania | popraw prompt, przetestuj na korpusie |
| Snapshot z PII | błąd w pseudonimizacji (3.4) | **zatrzymaj wszystko**, usuń snapshot, popraw, przejrzyj poprzednie |
| Klient zgłasza spowolnienie monday | audyt zużył limit dzienny | przeproś, przenieś audyty na noc, zawęź sampling |
| Finding, którego klient nie potwierdza | fałszywe trafienie | zapisz jako przypadek do złotego zestawu → etap 7 |

Ostatni wiersz jest najcenniejszy. Każde zakwestionowane znalezisko
to darmowy przypadek testowy.

---

## Definition of Done — etap 6

- [ ] Tabela `wywolania` zapisuje wszystkie pola
- [ ] Pięć pytań z sekcji wyżej da się odpowiedzieć jednym zapytaniem SQL
- [ ] Wszystkie guardrailsy z tabeli zaimplementowane i przetestowane
- [ ] Zmierzony koszt jednego audytu
- [ ] Zidentyfikowany najdroższy krok → decyzja o routingu modeli
- [ ] Potwierdzone, że prompt caching działa
