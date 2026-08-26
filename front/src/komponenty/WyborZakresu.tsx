// Ekran wyboru zakresu — co audytujemy i ile to będzie kosztować.
//
// Klient płaci własnym kluczem Anthropic, więc musi zobaczyć rachunek PRZED
// jego powstaniem i móc go zawęzić. ZMIERZONE na snapshocie #7: pełny audyt to
// 53 hipotezy i ~3,93 USD, jedna tablica to ~1,01 USD — 74% mniej.
//
// Podłoga jest widoczna cały czas: ~0,87 USD to hipotezy o ludziach, gościach,
// planie i automatyzacjach. Żaden wybór tablic ich nie usuwa, bo nie dotyczą
// tablic. Bez tej informacji klient odznaczyłby wszystko i zdziwił się kwotą.
//
// FLAGI SĄ ETYKIETAMI, NIE REKOMENDACJAMI (decyzja Kuby 2026-08-21). Nie piszemy
// „proponujemy pominąć": to klient wie, która tablica jest dla niego ważna,
// a rekomendacja przenosiłaby na nas odpowiedzialność za to, czego nie zobaczy
// w raporcie.
//
// Dwie flagi wyglądają podobnie i znaczą PRZECIWNE rzeczy — dlatego mają różne
// kolory i różne opisy:
//   * „nieużywana od startu" — szablon, którego nikt nie ruszył. Hałas.
//   * „cisza 90 dni" — ktoś zaczął proces i porzucił. To jest ZNALEZISKO.
// Zlanie ich w jedno „śmieć" kazałoby klientowi odznaczyć jedno razem z drugim.

import { useMemo, useState } from "react";
import type { PozycjaTablicy, WyborZakresu as Dane } from "../api";
// Ta sama funkcja, której używa panel haseł — nie druga obok niej. „1 wpisów"
// widać w każdym wierszu i psuje zaufanie do liczb obok.
import { odmiana } from "../Hasla";

// Opisy flag dla człowieka. Klucze muszą zgadzać się ze stałymi
// `FLAGA_*` w `wybor_zakresu.py` — rozjazd objawiłby się surowym
// identyfikatorem na ekranie, więc `OPIS_NIEZNANEJ` jest widocznym zapasem.
const OPISY_FLAG: Record<string, { etykieta: string; tytul: string; ton: string }> = {
  nieuzywana_od_startu: {
    etykieta: "nieużywana od startu",
    tytul:
      "Założona i nietknięta — odstęp między utworzeniem a ostatnią zmianą " +
      "jest krótszy niż doba. Zwykle szablon, którego nikt nie zaczął używać.",
    ton: "obojetna",
  },
  cisza_90_dni: {
    etykieta: "cisza 90 dni",
    tytul:
      "Tablica żyła, a potem zamilkła: ani jednego wpisu w ostatnich 90 dniach. " +
      "To najczęściej porzucony proces — i zwykle warto ją zbadać.",
    ton: "uwaga",
  },
  raportowa: {
    etykieta: "raportowa",
    tytul:
      "Ponad połowa kolumn liczy się sama (formuły, lustra, zależności). " +
      "Taka tablica raczej czyta z innych, niż prowadzi własny proces.",
    ton: "obojetna",
  },
  nieprobkowana: {
    etykieta: "niepróbkowana",
    tytul:
      "Poza próbką logów — o tej tablicy NIE WIEMY, czy była aktywna. " +
      "To brak danych, nie brak aktywności.",
    ton: "nieznana",
  },
};

const OPIS_NIEZNANEJ = { etykieta: "", tytul: "", ton: "obojetna" };

function zlotowki(usd: number): string {
  return usd.toFixed(2).replace(".", ",");
}

/** Jedna etykieta przy tablicy. */
function Flaga({ nazwa }: { nazwa: string }) {
  const opis = OPISY_FLAG[nazwa] ?? { ...OPIS_NIEZNANEJ, etykieta: nazwa };
  return (
    <span className={`flaga flaga--${opis.ton}`} title={opis.tytul}>
      {opis.etykieta}
    </span>
  );
}

/** Wiersz tablicy: nazwa, liczby, etykiety, checkbox. */
function WierszTablicy({
  tablica,
  zaznaczona,
  przelacz,
}: {
  tablica: PozycjaTablicy;
  zaznaczona: boolean;
  przelacz: (boardId: string) => void;
}) {
  return (
    <label className={`wybor__wiersz${zaznaczona ? " wybor__wiersz--wybrana" : ""}`}>
      <input
        type="checkbox"
        checked={zaznaczona}
        onChange={() => przelacz(tablica.board_id)}
      />
      <span className="wybor__nazwa">{tablica.nazwa}</span>
      <span className="wybor__liczby">
        {tablica.kolumn} kol.
        {/* Udział kolumn automatycznych pokazujemy TYLKO gdy jest — to jedyna
            rzecz, jaką wiemy o wypełnieniu kolumn. Ilu jest pustych, wie
            dopiero agent po próbkowaniu itemów (D5 zabrania nam zejść niżej). */}
        {tablica.kolumn_automatycznych > 0 && (
          <span
            className="wybor__auto"
            title={`${tablica.kolumn_automatycznych} z ${tablica.kolumn} kolumn liczy się samo`}
          >
            {" "}
            ({tablica.kolumn_automatycznych} aut.)
          </span>
        )}
        {" · "}
        {tablica.items_count} {odmiana(tablica.items_count, "element", "elementy", "elementów")}
        {/* `null` znaczy „poza próbką logów", więc NIE piszemy „0 wpisów" —
            to byłoby stwierdzenie, którego nie mamy prawa postawić. */}
        {tablica.wpisow !== null &&
          ` · ${tablica.wpisow} ${odmiana(tablica.wpisow, "wpis", "wpisy", "wpisów")}`}
      </span>
      <span className="wybor__flagi">
        {tablica.flagi.map((f) => (
          <Flaga key={f} nazwa={f} />
        ))}
        {tablica.hipotez > 0 && (
          <span
            className="flaga flaga--sygnal"
            title="Tyle sygnałów do zbadania dotyczy tej tablicy"
          >
            {tablica.hipotez} {odmiana(tablica.hipotez, "sygnał", "sygnały", "sygnałów")}
          </span>
        )}
      </span>
    </label>
  );
}

export function WyborZakresu({
  dane,
  trwa,
  blad,
  zatwierdz,
  brakujeKluczy,
  kluczApi,
  kluczModelu,
  ustawKluczApi,
  ustawKluczModelu,
  zacznijOdNowa,
}: {
  dane: Dane;
  trwa: boolean;
  blad: string;
  zatwierdz: (boardIds: string[]) => void;
  zacznijOdNowa: () => void;
  brakujeKluczy: boolean;
  kluczApi: string;
  kluczModelu: string;
  ustawKluczApi: (v: string) => void;
  ustawKluczModelu: (v: string) => void;
}) {
  // Domyślnie WSZYSTKO zaznaczone. Odwrotnie byłoby ryzykowne: klient, który
  // kliknie „dalej" bez czytania, dostałby audyt bez tablic i raport, który
  // niczego nie znalazł, bo niczego nie szukał.
  const [wybrane, ustawWybrane] = useState<Set<string>>(
    () => new Set(dane.tablice.map((t) => t.board_id)),
  );

  const przelacz = (boardId: string) =>
    ustawWybrane((poprzednie) => {
      const nowe = new Set(poprzednie);
      if (nowe.has(boardId)) nowe.delete(boardId);
      else nowe.add(boardId);
      return nowe;
    });

  const oflagowanych = useMemo(
    () => dane.tablice.filter((t) => t.oflagowana).length,
    [dane.tablice],
  );

  // Widełki przeliczane NA ŻYWO, proporcjonalnie do udziału zaznaczonych
  // hipotez. To przybliżenie i tak jest nazwane: dokładne widełki dla każdego
  // podzbioru wymagałyby pytania serwera przy każdym kliknięciu, a rozdzielczość
  // „ile to mniej więcej będzie" wystarcza do decyzji.
  const szacunek = useMemo(() => {
    const hipotezOTablicach = dane.tablice.reduce((suma, t) => suma + t.hipotez, 0);
    const wybranychHipotez = dane.tablice
      .filter((t) => wybrane.has(t.board_id))
      .reduce((suma, t) => suma + t.hipotez, 0);
    const udzial = hipotezOTablicach ? wybranychHipotez / hipotezOTablicach : 0;
    const zmienna = Math.max(0, dane.widelki.srodek_usd - dane.widelki.podloga_usd);
    const srodek = dane.widelki.podloga_usd + zmienna * udzial;
    // Rozpiętość widełek zachowujemy proporcjonalnie do środka, żeby przy
    // jednej tablicy nie pokazywać rozrzutu z pełnego runu.
    const rozpietoscDolu = dane.widelki.srodek_usd - dane.widelki.dolna_usd;
    const rozpietoscGory = dane.widelki.gorna_usd - dane.widelki.srodek_usd;
    const skala = dane.widelki.srodek_usd ? srodek / dane.widelki.srodek_usd : 0;
    return {
      dolna: Math.max(0, srodek - rozpietoscDolu * skala),
      srodek,
      gorna: srodek + rozpietoscGory * skala,
    };
  }, [dane.tablice, dane.widelki, wybrane]);

  const wszystkie = wybrane.size === dane.tablice.length;

  // Podział na tablice, które NIOSĄ KOSZT, i pozostałe.
  //
  // ZGŁOSZONE (Kuba, 2026-08-25): „przeklikam trzy i fajnie się odejmuje, ale
  // już po tych trzech czy czterech nic się nie dzieje (…) czy jest jakiś limit?".
  // Limitu nie ma — ZMIERZONE na snapshocie #8: z 97 tablic tylko DWIE miały
  // sygnał do zbadania. Odznaczanie pozostałych 95 nie zmieniało kwoty, bo nie
  // było w nich nic do analizy.
  //
  // Ekran pokazywał 97 równorzędnych pozycji i milczał o tym, gdzie siedzi
  // koszt — więc kazał zgadywać przez klikanie. Teraz mówi to wprost.
  const { zSygnalami, bezSygnalow, sygnalowOTablicach } = useMemo(
    () => {
      const zSyg = dane.tablice.filter((t) => t.hipotez > 0);
      return {
        zSygnalami: zSyg,
        bezSygnalow: dane.tablice.filter((t) => t.hipotez === 0),
        // Liczba SYGNAŁÓW, nie tablic. Jedna tablica może nieść trzy sygnały,
        // więc `zSygnalami.length` odpowiadało na inne pytanie niż zadane
        // w zdaniu obok („N sygnałów dotyczy tablic").
        sygnalowOTablicach: zSyg.reduce((suma, t) => suma + t.hipotez, 0),
      };
    },
    [dane.tablice],
  );

  return (
    <section className="wybor">
      <header className="wybor__naglowek">
        <h2>Potwierdź zakres analizy</h2>
        <p className="wybor__wstep">
          Dane zebrane. Do zbadania jest{" "}
          <strong>
            {dane.widelki.hipotez}{" "}
            {odmiana(dane.widelki.hipotez, "sygnał", "sygnały", "sygnałów")}
          </strong>
          :{" "}
          {sygnalowOTablicach > 0 && (
            <>
              {sygnalowOTablicach} z wybranych tablic oraz{" "}
            </>
          )}
          {dane.widelki.hipotez - sygnalowOTablicach} dotyczących całego konta —
          osób, gości, planu i automatyzacji.
        </p>
        {/* ZGŁOSZONE (Kuba, 2026-08-25): „przy wyborze tablic miałem 2 sygnały,
            a na etapie analizy widzę 24, skąd ten rozjazd?".
            Nie było rozjazdu w liczeniu — ekran wyboru pokazywał tylko sygnały
            Z TABLIC, bo tylko na nie wpływa wybór. Pozostałe dotyczą konta
            i wchodzą do audytu ZAWSZE. Zdanie wyżej mówi to teraz wprost,
            zamiast kazać się domyślać po kliknięciu „Zatwierdź". */}
        <p className="wybor__wstep">
          Sygnały o koncie zbadamy niezależnie od tego, które tablice wybierzesz —
          nie są związane z żadną tablicą.
        </p>
      </header>

      <div className="wybor__paski">
        <button
          type="button"
          className="wybor__akcja"
          onClick={() => ustawWybrane(new Set(dane.tablice.map((t) => t.board_id)))}
          disabled={wszystkie}
        >
          wszystkie ({dane.tablice.length})
        </button>
        <button
          type="button"
          className="wybor__akcja"
          onClick={() => ustawWybrane(new Set())}
          disabled={wybrane.size === 0}
        >
          żadna
        </button>
        {oflagowanych > 0 && (
          <button
            type="button"
            className="wybor__akcja"
            onClick={() =>
              ustawWybrane(
                new Set(
                  dane.tablice.filter((t) => !t.oflagowana).map((t) => t.board_id),
                ),
              )
            }
            title="Odznacza tablice z etykietami. Przejrzyj je najpierw: „cisza 90 dni” bywa najciekawszym znaleziskiem."
          >
            odznacz oflagowane ({oflagowanych})
          </button>
        )}
      </div>

      {/* DWIE grupy zamiast listy per workspace, i to jest sedno poprawki.
          Grupowanie po workspace'ach nie mówiło nic o koszcie — a klient wybiera
          właśnie ze względu na koszt. Teraz na wierzchu są tablice, które go
          niosą, a reszta jest zwinięta z jawnym „nie wpływają na kwotę". */}
      {zSygnalami.length > 0 && (
        <div className="wybor__grupa">
          <div className="wybor__grupa-naglowek">
            <h3>Tablice z sygnałami</h3>
            <span className="wybor__grupa-liczby">
              te wpływają na koszt
            </span>
          </div>
          {zSygnalami.map((t) => (
            <WierszTablicy
              key={t.board_id}
              tablica={t}
              zaznaczona={wybrane.has(t.board_id)}
              przelacz={przelacz}
            />
          ))}
        </div>
      )}

      {bezSygnalow.length > 0 && (
        <details className="wybor__grupa wybor__grupa--zwinieta">
          <summary>
            <strong>{bezSygnalow.filter((t) => wybrane.has(t.board_id)).length}</strong>
            {" z "}
            {bezSygnalow.length}{" "}
            {odmiana(bezSygnalow.length, "tablicy", "tablic", "tablic")} bez sygnałów
            <span className="wybor__grupa-liczby">
              nie wpływają na koszt — nic w nich nie wzbudziło podejrzeń
            </span>
          </summary>
          {bezSygnalow.map((t) => (
            <WierszTablicy
              key={t.board_id}
              tablica={t}
              zaznaczona={wybrane.has(t.board_id)}
              przelacz={przelacz}
            />
          ))}
        </details>
      )}

      <div className="wybor__podsumowanie">
        <p className="wybor__kwota">
          zaznaczono {wybrane.size} z {dane.tablice.length} → <strong>
            {zlotowki(szacunek.dolna)}–{zlotowki(szacunek.gorna)} USD
          </strong>{" "}
          (środek {zlotowki(szacunek.srodek)})
        </p>
        {/* Podłoga MUSI być widoczna zawsze, nie tylko przy zerze zaznaczonych:
            klient odznaczający tablice ma widzieć, gdzie kwota przestaje spadać. */}
        <p className="wybor__podloga">
          W tym zawsze: {dane.widelki.hipotez_o_koncie}{" "}
          {odmiana(dane.widelki.hipotez_o_koncie, "sygnał", "sygnały", "sygnałów")} o całym koncie
          (ludzie, goście, plan, automatyzacje) ≈ {zlotowki(dane.widelki.podloga_usd)} USD.
          Wybór tablic ich nie dotyczy.
        </p>
        {dane.widelki.oszacowane_z_zapasu && (
          <p className="wybor__zastrzezenie">
            Część kwoty jest oszacowana z średniej — dla klas{" "}
            {dane.widelki.klasy_bez_historii.join(", ")} nie mamy jeszcze historii
            kosztu z wcześniejszych audytów.
          </p>
        )}
        {dane.pominietych_pomocniczych > 0 && (
          <p className="wybor__zastrzezenie">
            Pominęliśmy {dane.pominietych_pomocniczych}{" "}
            {odmiana(dane.pominietych_pomocniczych, "obiekt", "obiekty", "obiektów")} pomocniczych
            (podelementy, dokumenty, obiekty własne) — nie są tablicami i nie
            wybiera się ich osobno.
          </p>
        )}
        {dane.tablic_bez_logow > 0 && (
          <p className="wybor__zastrzezenie">
            Dla {dane.tablic_bez_logow}{" "}
            {odmiana(dane.tablic_bez_logow, "tablicy", "tablic", "tablic")} nie mamy próbki dziennika, więc
            nie wiemy, czy były aktywne. Są oznaczone jako „niepróbkowane" —
            to brak danych, nie brak aktywności.
          </p>
        )}
        {/* Zastrzeżenia z collectora, nie pisane tu po raz drugi: to on wie,
            czego zawężenie nie obejmuje (lista użytkowników i statystyki
            automatyzacji są z natury na poziomie konta). */}
        {dane.uwagi_o_zakresie.map((uwaga) => (
          <p key={uwaga} className="wybor__zastrzezenie">
            {uwaga}
          </p>
        ))}
        {wybrane.size < dane.tablice.length && (
          <p className="wybor__zastrzezenie">
            Znaleziska o duplikatach powstają z porównania tablic PARAMI. Przy
            zawężonym wyborze część par wypadnie z audytu, więc brak takiego
            znaleziska nie będzie znaczył, że duplikatów nie ma.
          </p>
        )}
      </div>

      {/* Klucz Anthropic pytamy DOPIERO TERAZ i to jest sedno kolejności:
          klient widzi wyżej dokładną kwotę, więc podaje klucz wiedząc, na co
          się zgadza. Klucz monday pokazujemy tylko wtedy, gdy przepadł —
          po odświeżeniu strony, bo serwer go nie przechowuje (D11/D12). */}
      {brakujeKluczy && (
        <div className="wybor__klucze">
          {!kluczApi && (
            <>
              <p className="wybor__zastrzezenie">
                Odświeżenie strony wyczyściło klucz monday — zebrane dane są nadal
                ważne, ale narzędzia analizy go potrzebują.
              </p>
              <label htmlFor="klucz-ponownie">Klucz API monday</label>
              <input
                id="klucz-ponownie"
                type="password"
                value={kluczApi}
                onChange={(e) => ustawKluczApi(e.target.value)}
                placeholder="wklej klucz monday"
                autoComplete="off"
              />
            </>
          )}
          <label htmlFor="klucz-modelu-ponownie">Klucz API Anthropic</label>
          <input
            id="klucz-modelu-ponownie"
            type="password"
            value={kluczModelu}
            onChange={(e) => ustawKluczModelu(e.target.value)}
            placeholder="wklej klucz Anthropic"
            autoComplete="off"
          />
          <p className="meta">
            console.anthropic.com → API keys. Analizę wykonuje model Claude i to
            jedyny płatny element — koszt trafia na Twój rachunek. Klucza nie
            zapisujemy.
          </p>
        </div>
      )}

      {blad && (
        <p className="brama__blad" role="alert">
          {blad}
        </p>
      )}

      <div className="wybor__decyzja">
        <button
          type="button"
          className="cx-btn"
          onClick={() => zatwierdz([...wybrane])}
          disabled={trwa || !kluczApi || !kluczModelu}
        >
          {trwa ? "uruchamiam analizę…" : "Zatwierdź i analizuj"}
        </button>
        {/* Wyjście z tej bramki bez zatwierdzania. Zebrane dane zostają
            w snapshocie (D7, niemutowalny), a zadanie idzie w `blad`, więc NIE
            liczy się do limitu audytów — inaczej zmiana zdania kosztowałaby
            klienta tydzień blokady. */}
        <button
          type="button"
          className="wybor__akcja"
          onClick={zacznijOdNowa}
          disabled={trwa}
          title="Porzuca te dane i wraca do wyboru workspace'u. Nie zużywa limitu audytów."
        >
          Zbierz nowe dane
        </button>
      </div>
      {dane.zgoda_do && (
        <p className="wybor__termin">
          Zebrane dane są ważne do{" "}
          {new Date(dane.zgoda_do).toLocaleString("pl-PL", {
            dateStyle: "short",
            timeStyle: "short",
          })}
          . Po tym czasie trzeba je zebrać ponownie — kwota policzona ze starych
          danych przestaje być obietnicą.
        </p>
      )}
    </section>
  );
}
