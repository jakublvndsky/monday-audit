// Zakładka „Ludzie" — kto na czym pracował, kiedy i co robił.
//
// Odpowiada na pytanie, na które ZNALEZISKA nie odpowiadają. Finding mówi „tu jest
// problem, zrób X"; to jest obraz stanu, nie problem — dlatego osobna zakładka,
// a nie kolejna klasa w rubryce.
//
// Dane z COLLECTORA (`pulpit.ludzie`), więc zakładka istnieje nawet gdy agent nic
// nie znalazł, i idzie za wybraną wersją audytu.
//
// DWA WIDOKI tych samych danych, pod przełącznikiem (decyzja Kuby 2026-08-18):
// lista domyślnie, siatka na klik. Powód wyboru „oba": macierz 8×18 ma tylko 20%
// komórek niepustych (zmierzone na #7), więc siatka jest głównie pustką — ale
// odpowiada na „kto gdzie" jednym spojrzeniem, czego lista nie robi. Który czyta
// się lepiej, pokaże użycie na żywych danych.

import { useMemo, useState } from "react";
import type { Ludzie as DaneLudzi, ProfilOsoby, ProfilTablicy } from "../api";

// Kolejność kubełków od najświeższego. `kubelki_dni` to zwykły obiekt, więc
// kolejność kluczy zależy od tego, co przysłał serwer — a pasek czasu musi mieć
// zawsze ten sam kierunek, inaczej nie da się porównać dwóch osób wzrokiem.
const KUBELKI = ["0-7", "8-30", "31-60", "61-90"] as const;

// Podpisy POD słupkami — muszą być krótkie, bo słupek ma ~22 px szerokości.
// „7 d" znaczy „do 7 dni temu"; pełny opis jest w `OPISY_KUBELKOW` i w `title`.
const PODPISY_OSI: Record<string, string> = {
  "0-7": "7 d",
  "8-30": "30 d",
  "31-60": "2 mies.",
  "61-90": "3 mies.",
};

const OPISY_KUBELKOW: Record<string, string> = {
  "0-7": "ostatni tydzień",
  "8-30": "8–30 dni temu",
  "31-60": "1–2 miesiące temu",
  "61-90": "2–3 miesiące temu",
};

const ETYKIETY_RODZAJU: Record<string, string> = {
  czlowiek: "osoba",
  agent_ai: "agent AI",
  nieznany: "konto nieznane",
};

/** Rozkład aktywności w czasie — pasek Z PODPISAMI i zdaniem po polsku.
 *
 * ZGŁOSZONE (Kuba, 2026-08-18): „mamy coś takiego jak »Kiedy«, ale ja z tego nie
 * potrafię nic wyczytać". Pierwsza wersja rysowała cztery szare prostokąty bez
 * podpisu, bez skali i bez osi — trzeba było najechać myszką na każdy osobno,
 * a na telefonie były w ogóle ukryte.
 *
 * Teraz każdy kubełek ma widoczną liczbę, a nad paskiem stoi zdanie wyliczone
 * z danych („przestał — ostatnio 1–2 miesiące temu"). Zdanie jest pierwsze, bo
 * ono odpowiada na pytanie; pasek jest po nim, bo pokazuje kształt rozkładu,
 * którego zdanie nie oddaje.
 */
function PasekCzasu({
  kubelki,
  zwarty = false,
}: {
  kubelki: Record<string, number>;
  zwarty?: boolean;
}) {
  const suma = KUBELKI.reduce((acc, k) => acc + (kubelki[k] ?? 0), 0);
  if (!suma) return <span className="rozklad rozklad--puste">brak aktywności w oknie</span>;

  return (
    <span className={`rozklad${zwarty ? " rozklad--zwarty" : ""}`}>
      {!zwarty && <span className="rozklad__zdanie">{zdanieOKiedy(kubelki)}</span>}
      <span className="rozklad__slupki" aria-label={opisRozkladu(kubelki)}>
        {KUBELKI.map((k) => {
          const ile = kubelki[k] ?? 0;
          return (
            <span key={k} className="rozklad__kolumna" title={`${OPISY_KUBELKOW[k]}: ${ile}`}>
              {/* Liczba NAD słupkiem, nie w tooltipie — to ona jest treścią.
                  Kropka przy zerze, bo pusty słupek bez znaku wygląda jak brak
                  danych, a znaczy „w tym okresie nic nie robił". */}
              <span className="rozklad__liczba">{ile || "·"}</span>
              <span
                className={`rozklad__slupek${ile ? "" : " rozklad__slupek--pusty"}`}
                style={{ height: `${ile ? Math.max((ile / suma) * 100, 8) : 2}%` }}
              />
              <span className="rozklad__podpis">{PODPISY_OSI[k]}</span>
            </span>
          );
        })}
      </span>
    </span>
  );
}

/** Zdanie po polsku, wyliczone z kubełków. To ono odpowiada na „kiedy".
 *
 * Trzy przypadki, wszystkie zaobserwowane na koncie ACME:
 *   * praca również w ostatnim miesiącu → „pracuje nadal", plus gdzie był szczyt;
 *   * cała praca starsza niż miesiąc → „przestał", plus kiedy ostatnio;
 *   * praca tylko w jednym kubełku → „cała praca w jednym okresie" (sygnatura
 *     kogoś, kto rozstawił coś raz i skończył — np. 153 akcje w kubełku 31-60).
 */
function zdanieOKiedy(kubelki: Record<string, number>): string {
  const suma = KUBELKI.reduce((acc, k) => acc + (kubelki[k] ?? 0), 0);
  const swieze = (kubelki["0-7"] ?? 0) + (kubelki["8-30"] ?? 0);
  const niepuste = KUBELKI.filter((k) => kubelki[k]);
  const ostatni = niepuste[0];
  // `ostatni` bywa `undefined` wg typów (filtr nie gwarantuje elementu), ale
  // funkcja jest wołana tylko przy `suma > 0`, więc pusty przypadek nie zachodzi.
  // Zwracamy z niego zdanie zamiast rzucać — widok nie ma prawa paść na tekście.
  if (!ostatni) return "brak aktywności w oknie";

  const szczyt =
    KUBELKI.reduce<(typeof KUBELKI)[number]>(
      (a, b) => ((kubelki[b] ?? 0) > (kubelki[a] ?? 0) ? b : a),
      KUBELKI[0],
    );

  if (niepuste.length === 1) {
    return `cała praca (${suma}) w jednym okresie: ${OPISY_KUBELKOW[ostatni]}`;
  }

  const udzial = Math.round((swieze / suma) * 100);
  // Próg 10%, nie „cokolwiek świeżego".
  //
  // ZŁAPANE na podglądzie danych ACME: konto z 76 akcjami w kubełku 61-90 i JEDNĄ
  // w 8-30 dostawało zdanie „pracuje nadal — 1% aktywności w ostatnim miesiącu".
  // Formalnie prawda, w praktyce mylące: jedna akcja z 77 to cisza, nie praca.
  // Dziesięć procent to granica, przy której „nadal" znaczy cokolwiek.
  if (udzial >= 10) {
    return `pracuje nadal — ${udzial}% aktywności w ostatnim miesiącu, szczyt ${OPISY_KUBELKOW[szczyt]}`;
  }
  if (swieze > 0) {
    return `prawie przestał — tylko ${udzial}% (${swieze} z ${suma}) w ostatnim miesiącu, szczyt ${OPISY_KUBELKOW[szczyt]}`;
  }
  return `przestał — ostatnia praca ${OPISY_KUBELKOW[ostatni]}, szczyt ${OPISY_KUBELKOW[szczyt]}`;
}

/** Tekst dla czytnika ekranu — słupki bez tego są niedostępne. */
function opisRozkladu(kubelki: Record<string, number>): string {
  const czesci = KUBELKI.filter((k) => kubelki[k]).map(
    (k) => `${OPISY_KUBELKOW[k]}: ${kubelki[k]}`,
  );
  return czesci.length ? `Rozkład aktywności — ${czesci.join(", ")}` : "Brak aktywności";
}

function odmianaAkcji(n: number): string {
  if (n === 1) return "akcja";
  const ostatnie = n % 10;
  const dwie = n % 100;
  if (ostatnie >= 2 && ostatnie <= 4 && (dwie < 12 || dwie > 14)) return "akcje";
  return "akcji";
}

/* Nazwy zdarzeń monday po polsku.
 *
 * ZGŁOSZONE (Kuba, 2026-08-25): „żeby nie było takiego update_column_value,
 * tylko normalnie po polsku, żeby to było czytelne dla klienta bez żadnego
 * takiego z API".
 *
 * Klient widział surowe identyfikatory z API — `update_column_value`,
 * `board_workspace_id_changed`, `batch_delete_pulses`. To nazwy techniczne
 * pisane dla programisty, nie dla właściciela konta.
 *
 * Słownik jest JAWNY, nie generowany z zamiany podkreśleń na spacje: `pulse`
 * znaczy „element" (monday tak nazywa wiersze wewnętrznie), a mechaniczne
 * tłumaczenie dałoby „archiwizacja pulsu". Nieznane zdarzenie pokazujemy
 * surowo — lepsze niż zgadywanie, i widać wtedy, co dopisać. */
const NAZWY_ZDARZEN: Record<string, string> = {
  create_pulse: "dodanie elementu",
  update_column_value: "wypełnienie kolumny",
  update_name: "zmiana nazwy elementu",
  create_group: "dodanie grupy",
  update_board_name: "zmiana nazwy tablicy",
  update_board_nickname: "zmiana nazwy tablicy",
  board_workspace_id_changed: "przeniesienie tablicy",
  change_column_settings: "zmiana ustawień kolumny",
  update_column_name: "zmiana nazwy kolumny",
  delete_column: "usunięcie kolumny",
  create_column: "dodanie kolumny",
  archive_pulse: "archiwizacja elementu",
  delete_pulse: "usunięcie elementu",
  batch_delete_pulses: "usunięcie wielu elementów",
  archive_group: "archiwizacja grupy",
  delete_group: "usunięcie grupy",
  update_group_name: "zmiana nazwy grupy",
  archive_group_pulse: "archiwizacja elementów grupy",
  delete_group_pulse: "usunięcie elementów grupy",
  move_pulse_from_group: "przeniesienie elementu",
  move_pulse_into_group: "przeniesienie elementu",
  subscribe: "dołączenie do tablicy",
  unsubscribe: "opuszczenie tablicy",
  board_view_added: "dodanie widoku",
  board_view_enabled: "włączenie widoku",
  board_view_deleted: "usunięcie widoku",
  create_board: "utworzenie tablicy",
  duplicate_board: "duplikowanie tablicy",
  add_file: "dodanie pliku",
  delete_file: "usunięcie pliku",
  create_update: "komentarz",
  delete_update: "usunięcie komentarza",
  board_view_changed: "zmiana widoku",
  set_entity_board_role: "zmiana uprawnień",
  // NASZE kategorie z `logi.py`, nie zdarzenia monday — `po_klasie` grupuje nimi
  // aktywność tablicy. Trafiają w to samo miejsce na ekranie, więc bez nich
  // klient widziałby „operacyjne 113" obok „dodanie elementu 88" i nie wiedział,
  // że to dwa różne poziomy opisu.
  operacyjne: "praca na danych",
  strukturalne: "zmiany w strukturze",
  uprawnienia: "zmiany uprawnień",
  inne: "pozostałe",
};

function nazwaZdarzenia(surowa: string): string {
  return NAZWY_ZDARZEN[surowa] ?? surowa;
}

/** Najczęstsze zdarzenia — odpowiedź na „CO robił".
 *
 * Trzy pierwsze, bo `po_event` ma czasem szesnaście kluczy i pełna lista zasłania
 * to, co istotne. Reszta idzie do `title`, więc nic nie ginie.
 */
function Zdarzenia({ po_event }: { po_event: Record<string, number> }) {
  const wszystkie = Object.entries(po_event).sort((a, b) => b[1] - a[1]);
  if (!wszystkie.length) return null;
  const widoczne = wszystkie.slice(0, 3);
  const reszta = wszystkie.slice(3);

  return (
    <span
      className="zdarzenia"
      title={wszystkie.map(([k, v]) => `${nazwaZdarzenia(k)}: ${v}`).join("\n")}
    >
      {widoczne.map(([nazwa, ile]) => (
        // `<span>`, nie `<code>`: czcionka o stałej szerokości sugerowała, że to
        // identyfikator techniczny — a teraz to zwykły polski opis.
        <span key={nazwa} className="zdarzenie">
          {nazwaZdarzenia(nazwa)} <b>{ile}</b>
        </span>
      ))}
      {reszta.length > 0 && <small>+{reszta.length} innych</small>}
    </span>
  );
}

function WierszOsoby({ osoba }: { osoba: ProfilOsoby }) {
  const [otwarty, ustawOtwarty] = useState(false);

  return (
    <div className={`osoba osoba--${osoba.rodzaj}`}>
      <button
        type="button"
        className="osoba__naglowek"
        aria-expanded={otwarty}
        onClick={() => ustawOtwarty((s) => !s)}
      >
        <span className="osoba__kto">
          <b>{osoba.etykieta}</b>
          {/* Rodzaj konta PRZY nazwie, nie w osobnej kolumnie: „Steven" bez
              podpisu „agent AI" czyta się jak pracownik. ZMIERZONE na koncie
              ACME: 3 z 8 autorów w logach to konta agentów. */}
          <span className={`znacznik znacznik--${osoba.rodzaj}`}>
            {ETYKIETY_RODZAJU[osoba.rodzaj] ?? osoba.rodzaj}
          </span>
          {osoba.title && <small className="osoba__tytul">{osoba.title}</small>}
        </span>
        <span className="osoba__liczby">
          <b>{osoba.akcji}</b> {odmianaAkcji(osoba.akcji)} · {osoba.tablic}{" "}
          {osoba.tablic === 1 ? "tablica" : "tablic"}
        </span>
        <PasekCzasu kubelki={osoba.kubelki_dni} zwarty />
        {osoba.aktywny_ostatnie_7d ? (
          <span className="znacznik znacznik--zywy">aktywny w tym tygodniu</span>
        ) : (
          <span className="znacznik znacznik--cichy">cisza od tygodnia</span>
        )}
        <span className="osoba__strzalka" aria-hidden="true">
          {otwarty ? "▾" : "▸"}
        </span>
      </button>

      {otwarty && (
        <div className="osoba__szczegoly">
          {/* Zdanie o „kiedy" JAKO PIERWSZE w rozwinięciu — w nagłówku wiersza
              jest tylko zwarty pasek, bo pełne zdanie nie zmieściłoby się obok
              nazwy, liczb i znaczników. */}
          <p className="osoba__kiedy">{zdanieOKiedy(osoba.kubelki_dni)}</p>
          <PasekCzasu kubelki={osoba.kubelki_dni} />
          <Zdarzenia po_event={osoba.po_event} />
          <table className="tabela-udzialow">
            <thead>
              <tr>
                <th>tablica</th>
                <th>akcji</th>
                <th>kiedy</th>
                <th>co robił</th>
              </tr>
            </thead>
            <tbody>
              {osoba.tablice.map((u) => (
                <tr key={u.board_id}>
                  <td>{u.nazwa}</td>
                  <td className="liczba">{u.akcji}</td>
                  <td>
                    <PasekCzasu kubelki={u.kubelki_dni} zwarty />
                  </td>
                  <td>
                    <Zdarzenia po_event={u.po_event} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function WierszTablicy({ tablica }: { tablica: ProfilTablicy }) {
  const [otwarty, ustawOtwarty] = useState(false);

  return (
    <div className="osoba">
      <button
        type="button"
        className="osoba__naglowek"
        aria-expanded={otwarty}
        onClick={() => ustawOtwarty((s) => !s)}
      >
        <span className="osoba__kto">
          <b>{tablica.nazwa}</b>
          <small className="osoba__tytul">
            {tablica.autorzy.length} {tablica.autorzy.length === 1 ? "autor" : "autorów"}
          </small>
        </span>
        <span className="osoba__liczby">
          <b>{tablica.wpisow}</b> {odmianaAkcji(tablica.wpisow)}
        </span>
        {tablica.najnowszy_at && (
          <span className="znacznik znacznik--cichy">
            ostatnio {tablica.najnowszy_at.slice(0, 10)}
          </span>
        )}
        <span className="osoba__strzalka" aria-hidden="true">
          {otwarty ? "▾" : "▸"}
        </span>
      </button>

      {otwarty && (
        <div className="osoba__szczegoly">
          <table className="tabela-udzialow">
            <thead>
              <tr>
                <th>kto</th>
                <th>akcji</th>
                <th>kiedy</th>
              </tr>
            </thead>
            <tbody>
              {tablica.autorzy.map((a) => (
                <tr key={a.user_hash}>
                  <td>
                    {a.etykieta}{" "}
                    <span className={`znacznik znacznik--${a.rodzaj}`}>
                      {ETYKIETY_RODZAJU[a.rodzaj] ?? a.rodzaj}
                    </span>
                  </td>
                  <td className="liczba">{a.akcji}</td>
                  <td>
                    <PasekCzasu kubelki={a.kubelki_dni} zwarty />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Siatka osoby × tablice. Puste komórki są PUSTE, nie zerowe.
 *
 * Zero znaczyłoby „sprawdziliśmy, nic nie robił", a prawda jest „nie ma go na tej
 * tablicy". Kropka mówi to drugie.
 *
 * Przewijanie w poziomie jest konieczne: 18 kolumn nie zmieści się na telefonie,
 * a zwężanie ich do nieczytelności byłoby gorsze niż przewijanie.
 */
function Siatka({ dane }: { dane: DaneLudzi }) {
  // Kolumny tylko dla tablic, na których KTOŚ pracował — reszta byłaby pustą
  // kolumną bez informacji.
  const tablice = dane.tablice;
  const akcje = useMemo(() => {
    const mapa = new Map<string, number>();
    for (const osoba of dane.osoby) {
      for (const u of osoba.tablice) mapa.set(`${osoba.user_hash}|${u.board_id}`, u.akcji);
    }
    return mapa;
  }, [dane.osoby]);

  return (
    <div className="przewijane">
      <table className="siatka-ludzi">
        <thead>
          <tr>
            <th className="siatka-ludzi__naroznik">osoba \ tablica</th>
            {tablice.map((t) => (
              <th key={t.board_id} title={t.nazwa}>
                <span className="siatka-ludzi__kolumna">{t.nazwa}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dane.osoby.map((o) => (
            <tr key={o.user_hash}>
              <th scope="row">
                {o.etykieta}
                <span className={`znacznik znacznik--${o.rodzaj}`}>
                  {ETYKIETY_RODZAJU[o.rodzaj] ?? o.rodzaj}
                </span>
              </th>
              {tablice.map((t) => {
                const ile = akcje.get(`${o.user_hash}|${t.board_id}`);
                return (
                  <td key={t.board_id} className={ile ? "liczba" : "liczba puste"}>
                    {ile ?? "·"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Ludzie({ dane }: { dane: DaneLudzi }) {
  const [widok, ustawWidok] = useState<"lista" | "siatka">("lista");
  const [tylkoLudzie, ustawTylkoLudzi] = useState(false);
  const [odwrotnie, ustawOdwrotnie] = useState(false);

  const osoby = tylkoLudzie ? dane.osoby.filter((o) => o.to_czlowiek) : dane.osoby;

  if (!dane.osoby.length) {
    return (
      <div className="brak-danych">
        <p>
          <strong>Ten snapshot nie ma danych o aktywności osób.</strong> Logi
          aktywności są pobierane dla próbki tablic — jeśli żadna nie miała wpisów
          w oknie 90 dni, nie ma czego pokazać.
        </p>
      </div>
    );
  }

  return (
    <section className="ludzie">
      {/* POKRYCIE PIERWSZE, przed listą. Osiem wierszy przy 94 kontach czyta się
          jako „tylko tyle osób pracuje" — a prawda jest, że tylu widzimy. */}
      <p className="ludzie__pokrycie">
        Widać <b>{dane.osoby.length}</b> z <b>{dane.kont_razem}</b> kont, bo logi
        aktywności mamy z <b>{dane.tablic_z_logami}</b> z{" "}
        <b>{dane.tablic_w_zakresie}</b> tablic w zakresie. O pozostałych kontach
        i tablicach ta zakładka <b>nie orzeka</b>.
      </p>
      <p className="ludzie__rozklad">
        {dane.ludzi} {dane.ludzi === 1 ? "osoba" : "osób"} · {dane.agentow_ai}{" "}
        {dane.agentow_ai === 1 ? "agent AI" : "agentów AI"} · {dane.nieznanych}{" "}
        {dane.nieznanych === 1 ? "konto nieznane" : "kont nieznanych"}
        {dane.nieznanych > 0 && (
          <small>
            {" "}
            — „konto nieznane" to autor obecny w logach, którego nie ma na liście
            kont: usunięty albo spoza zakresu audytu.
          </small>
        )}
      </p>

      <div className="ludzie__sterowanie">
        <div className="przelacznik" role="group" aria-label="Widok danych">
          <button
            type="button"
            className={widok === "lista" ? "aktywny" : ""}
            onClick={() => ustawWidok("lista")}
          >
            lista
          </button>
          <button
            type="button"
            className={widok === "siatka" ? "aktywny" : ""}
            onClick={() => ustawWidok("siatka")}
          >
            siatka
          </button>
        </div>
        {widok === "lista" && (
          <label className="ludzie__odwrotnie">
            <input
              type="checkbox"
              checked={odwrotnie}
              onChange={(e) => ustawOdwrotnie(e.target.checked)}
            />
            grupuj po tablicach
          </label>
        )}
        <label className="ludzie__filtr">
          <input
            type="checkbox"
            checked={tylkoLudzie}
            onChange={(e) => ustawTylkoLudzi(e.target.checked)}
          />
          tylko osoby (bez agentów AI i kont nieznanych)
        </label>
      </div>

      {widok === "siatka" ? (
        <Siatka dane={tylkoLudzie ? { ...dane, osoby } : dane} />
      ) : odwrotnie ? (
        <div className="lista-osob">
          {dane.tablice.map((t) => (
            <WierszTablicy key={t.board_id} tablica={t} />
          ))}
        </div>
      ) : (
        <div className="lista-osob">
          {osoby.map((o) => (
            <WierszOsoby key={o.user_hash} osoba={o} />
          ))}
        </div>
      )}
    </section>
  );
}
