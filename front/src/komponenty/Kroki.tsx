// Etapy audytu — gdzie jesteśmy i ile jeszcze zostało.
//
// ZGŁOSZONE (Kuba, 2026-08-25): „fajnie, jakby pokazywało etapy, na których
// jest (…) żeby klient wiedział, ile jeszcze zostało i gdzie jest na tej linii".
//
// Poprzednio był sam pasek z procentem i tekstem etapu. Procent mówi „62%",
// ale nie mówi, czy to znaczy „prawie skończone" czy „dopiero zaczęliśmy
// najdłuższą część" — a przy audycie te dwie rzeczy różnią się o dwadzieścia
// minut.
//
// ## Granice etapów wynikają z `postep` zapisywanego w `web/run.py`
//
// Nie z osobnego pola, bo to byłoby drugie źródło prawdy o tym samym. Mapowanie
// jest tutaj, blisko widoku, i celowo TOLERUJE nieznane wartości: gdy backend
// dopisze etap, pasek pokaże najbliższy pasujący, a nie pustkę.

const ETAPY = [
  { do: 59, nazwa: "Zbieranie danych", opis: "czytamy konto z monday" },
  { do: 61, nazwa: "Wybór zakresu", opis: "czekamy na Twoją zgodę" },
  { do: 94, nazwa: "Analiza", opis: "model bada sygnały" },
  { do: 100, nazwa: "Raport", opis: "składamy znaleziska" },
] as const;

/** Pozostały czas z TEMPA TEGO runu, nie ze średniej.
 *
 * ZGŁOSZONE (Kuba, 2026-08-25): „nie wiesz, kiedy co się stanie, za ile się
 * stanie". Średnia globalna jest do tego za słaba — ZMIERZONE na realnych
 * runach: 23,3 s/hipoteza na jednym koncie, 48,0 s na innym. Prognoza z takiej
 * średniej myliłaby się dwukrotnie.
 *
 * Tempo tego runu znamy dokładnie: ile hipotez zbadano i ile to trwało.
 * Zwraca `null`, dopóki nie ma z czego liczyć — brak prognozy jest lepszy niż
 * prognoza zmyślona.
 */
function pozostalyCzas(
  zbadanych: number,
  wszystkich: number,
  sekundOdStartu: number,
): string | null {
  // Poniżej dwóch hipotez tempo jest przypadkowe: pierwsza niesie koszt
  // rozgrzania cache'u i bywa kilka razy dłuższa od kolejnych.
  if (zbadanych < 2 || zbadanych >= wszystkich || sekundOdStartu < 5) return null;
  const naHipoteze = sekundOdStartu / zbadanych;
  const zostalo = Math.round((wszystkich - zbadanych) * naHipoteze);
  if (zostalo < 60) return "mniej niż minuta";
  const minuty = Math.round(zostalo / 60);
  if (minuty === 1) return "około minuty";
  if (minuty < 5) return `około ${minuty} minut`;
  // Powyżej pięciu minut zaokrąglamy do pełnych pięciu: „około 10 minut"
  // jest uczciwsze niż „około 11 minut", bo takiej precyzji nie mamy.
  return `około ${Math.round(minuty / 5) * 5} minut`;
}

export function Kroki({
  postep,
  etap,
  zbadanych,
  wszystkich,
  sekundOdStartu,
}: {
  postep: number;
  etap?: string | null;
  zbadanych?: number;
  wszystkich?: number;
  sekundOdStartu?: number;
}) {
  const prognoza =
    zbadanych !== undefined && wszystkich !== undefined && sekundOdStartu !== undefined
      ? pozostalyCzas(zbadanych, wszystkich, sekundOdStartu)
      : null;

  // Indeks bieżącego etapu: pierwszy, którego górna granica nie została jeszcze
  // przekroczona. `findIndex` zwraca −1 dla wartości poza skalą — wtedy stoimy
  // na ostatnim, bo 100% znaczy „gotowe", nie „nieznane".
  const biezacy = (() => {
    const i = ETAPY.findIndex((e) => postep <= e.do);
    return i === -1 ? ETAPY.length - 1 : i;
  })();

  return (
    <ol className="kroki" aria-label="Etapy audytu">
      {ETAPY.map((krok, i) => {
        const stan = i < biezacy ? "zrobiony" : i === biezacy ? "biezacy" : "przyszly";
        return (
          <li key={krok.nazwa} className={`kroki__krok kroki__krok--${stan}`}>
            <span className="kroki__znacznik" aria-hidden="true">
              {i < biezacy ? "✓" : i + 1}
            </span>
            <span className="kroki__tresc">
              <strong>{krok.nazwa}</strong>
              {/* Opis tylko przy bieżącym: przy wszystkich naraz robi się
                  ściana tekstu, a to ma być jedna linijka orientacji.
                  Tekst z backendu WYGRYWA nad statycznym, bo niesie liczby
                  („zbadano 7 z 24 sygnałów") — a to na nich widać, że coś się
                  rusza. Statyczny zostaje zapasem, gdy stan jeszcze nie doszedł. */}
              {i === biezacy && (
                <span className="kroki__opis">
                  {etap?.trim() || krok.opis}
                  {prognoza && <> · zostało {prognoza}</>}
                </span>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
