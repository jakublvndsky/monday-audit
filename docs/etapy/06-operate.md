# Etap 6 — Operate

**Stan: bieżący od 2026-09-02**, po zatwierdzeniu etapu 5.

---

## Kolejka zerowa: domknięcie długu z etapu 5

**Zanim w tym etapie powstanie cokolwiek nowego, zamykamy pięć rzeczy
przeniesionych z wdrożenia.** Decyzja z 2026-09-02. Powód jest prosty:
etap 5 został zatwierdzony jako *wdrożony*, nie jako *gotowy do obsługi
klienta* — a te pięć pozycji to dokładnie różnica między jednym a drugim.

Pełny opis każdej: `05-WYKONANE.md`, sekcja „Co zostaje otwarte po tym etapie".

### Trzy blokery przed pierwszym klientem

| # | co | kto rozstrzyga | dlaczego to nie jest usterka do naprawienia komendą |
|---|---|---|---|
| **Z1** | **O23** — cztery pytania o dane osobowe pod URL-em: wygasanie dostępu, kasowanie konta po zakończeniu relacji, logi wejść, nazwiska w adresie | **człowiek** | Dopóki otwarte, panel jest tylko dla zespołu, a `--dodaj-klienta` zostaje nieużywane. Komenda działa i nikt jej nie zablokował — to decyzja, nie mechanizm |
| **Z2** | **Baseline dla bramy promocji** — złoty zestaw jest dla snapshotu `acme`, nie dla runów CXLABS | **człowiek**, potem ja | Brama istnieje jako skrypt i przeszła na prawdziwym runie, ale dziś **nie ma czego przepuścić**. Specyfikacja mówi „uruchom przed czymkolwiek dla klienta" — bez baselinu ten warunek jest niewykonalny, a nie spełniony |
| **Z3** | **Kopia zapasowa poza serwerem** | **człowiek** (maszyna albo magazyn), potem ja | Snapshoty są niemutowalne i są jedynym źródłem case studies z liczbami; ich utrata jest nieodwracalna. Dziś kopia leży na tym samym dysku co oryginał. Do obcego magazynu potrzebne szyfrowanie, którego `backup.sh` nie ma — to zmiana w kodzie, nie w konfiguracji |

### Dwie pozycje operacyjne, które i tak należą do tego etapu

| # | co | stan |
|---|---|---|
| **Z4** | **Nikt nie pyta `/health`** | Endpoint jest dobry — własne połączenie z bazą, bez sesji, nic o klientach. Brakuje **obserwatora**. Usługa ma `Restart=on-failure`, ale po pięciu nieudanych próbach systemd przestaje próbować i nikt się o tym nie dowie |
| **Z5** | **`nftables` z `policy accept` i zero reguł** | Panel słucha na pętli zwrotnej, więc nas to nie dotyka — ale dotyczy maszyny dzielonej z sześcioma cudzymi aplikacjami. Do zgłoszenia właścicielowi, niekoniecznie do naprawienia przez nas |

### Czego ta kolejka NIE obejmuje

Reszta tego dokumentu — obserwowalność, guardrailsy runtime, playbook incydentów
— zostaje bez zmian i jest właściwą treścią etapu 6. Kolejka zerowa jest długiem,
nie zakresem.

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
| Read-only | `MondayClient.przygotuj_zapytanie()` odrzuca `mutation` i `subscription` + `allowed_tools`/`disallowed_tools` + hook `PreToolUse` | trzy warstwy, każda zatrzymuje run; w ścieżce kodu narzędzi nie ma jak wysłać zapisu |
| Brak PII | walidacja snapshotu przy zapisie | odrzuć zapis, zaloguj jako błąd krytyczny |
| Kontrakt wyjściowy | walidacja przed rendererem | odrzuć finding, zaloguj |
| Limit czasu runu | timeout sesji | przerwij, zachowaj snapshot |

**Sprostowanie 2026-08-03.** Wiersz „read-only" brzmiał wcześniej: „flaga
`--read-only` MCP — wymuszone przez serwer, nie do obejścia z promptu". **To
było nieprawdą i najgroźniejszym zdaniem w tym dokumencie**: pomiar na
`@mondaydotcomorg/monday-api-mcp@3.3.0` pokazał, że przy włączonej fladze
`create_board` i surowa mutacja przez `all_api_write` **przeszły do API**.
Guardrail, w który się wierzy bez pomiaru, jest gorszy od braku guardraila —
bo zdejmuje czujność. Szczegóły: D4 i O19.

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

**Kolejka zerowa (dług z etapu 5) — przed resztą:**

- [ ] **Z1** — O23 rozstrzygnięte albo świadomie odroczone z zapisanym warunkiem
- [ ] **Z2** — baseline bramy promocji wskazany; brama przechodzi na runie CXLABS
- [ ] **Z3** — kopia zapasowa poza serwerem, z testem odtworzenia z tamtego celu
- [ ] **Z4** — coś pyta `/health` i krzyczy, gdy przestanie odpowiadać
- [ ] **Z5** — brak reguł w `nftables` zgłoszony właścicielowi maszyny

**Właściwy zakres etapu:**

- [ ] Tabela `wywolania` zapisuje wszystkie pola
- [ ] Pięć pytań z sekcji wyżej da się odpowiedzieć jednym zapytaniem SQL
- [ ] Wszystkie guardrailsy z tabeli zaimplementowane i przetestowane
- [ ] Zmierzony koszt jednego audytu
- [ ] Zidentyfikowany najdroższy krok → decyzja o routingu modeli
- [ ] Potwierdzone, że prompt caching działa
