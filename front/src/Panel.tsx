// Panel — powłoka z makiety plus przełączanie klienta bez przeładowania.
//
// Jedna powłoka dla obu ról. Różnicę robi to, CO PRZYSZŁO Z SERWERA: sesja
// klienta nie dostaje kluczy wewnętrznych, więc `pulpit.pinowanie` jest
// `undefined` i sekcja diagnostyczna nie ma z czego się wyrenderować.
//
// Warunki `if (!pulpit.dla_klienta)` w tym pliku **nie są granicą** — są
// wyświetlaniem. Granica stoi w API (D16). Gdyby ktoś usunął te warunki, klient
// zobaczyłby puste sekcje, nie cudze dane.

import { useCallback, useEffect, useState } from "react";
import type { Ja, PozycjaKlienta, Pulpit } from "./api";
import { Audyt } from "./Audyt";
import { MojeKonto, ResetHaslaKlienta } from "./Hasla";
import { api } from "./klient";
import {
  CzegoNieWidac,
  KafelDuzy,
  przewinDoSekcji,
  SekcjaMetryk,
  slugSekcji,
  Znaleziska,
} from "./komponenty/Sekcje";

const KAFLE_KLUCZOWE = ["agentów AI", "zdominowanych jednym autorem"];

/** Data audytu po polsku, do drop-downu wersji. */
function dataWersji(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso.slice(0, 10)
    : d.toLocaleDateString("pl-PL", { day: "numeric", month: "long", year: "numeric" });
}

function odmianaZnalezisk(ile: number): string {
  if (ile === 1) return "znalezisko";
  if (ile % 10 >= 2 && ile % 10 <= 4 && (ile % 100 < 12 || ile % 100 > 14)) return "znaleziska";
  return "znalezisk";
}

/** Sekcje otwartego audytu pod pozycją klienta. Klik przewija do sekcji.
 *
 * Rysuje to, co przysłał serwer w `pulpit.sekcje` — nie ma tu listy tytułów
 * wpisanej na sztywno. Gdyby collector przestał zbierać automatyzacje, sekcja
 * zniknie z panelu I z nawigacji naraz, bez osobnej poprawki.
 */
function PodnawigacjaSekcji({ pulpit }: { pulpit: Pulpit | null }) {
  if (!pulpit) return null;
  const pozycje = [
    { id: slugSekcji("Znaleziska"), etykieta: "Znaleziska", licznik: pulpit.findingow },
    ...pulpit.sekcje.map((s) => ({
      id: slugSekcji(s.tytul),
      etykieta: s.tytul,
      licznik: s.metryki.length,
    })),
  ];
  return (
    <div className="sidebar__podnawigacja">
      {pozycje.map((p) => (
        <a
          key={p.id}
          href={`#${p.id}`}
          onClick={(e) => {
            e.preventDefault();
            przewinDoSekcji(p.id);
          }}
        >
          {p.etykieta}
          <span className="sidebar__licznik">{p.licznik}</span>
        </a>
      ))}
    </div>
  );
}

export function Panel({ ja, poWylogowaniu }: { ja: Ja; poWylogowaniu: () => void }) {
  const [pulpit, ustawPulpit] = useState<Pulpit | null>(null);
  const [klienci, ustawKlienci] = useState<PozycjaKlienta[]>([]);
  const [wybrany, ustawWybranego] = useState<string | undefined>(undefined);
  // Wybrana WERSJA audytu. `undefined` znaczy „domyślna", czyli najnowsza —
  // serwer to rozstrzyga (`_ostatni_run`), front nie zgaduje.
  const [wersja, ustawWersje] = useState<string | undefined>(undefined);
  // Który widok: audyt klienta czy własne konto. „Moje konto" było wcześniej
  // sekcją WŚRÓD danych audytu klienta, co Kuba słusznie zakwestionował —
  // własne konto nie należy do widoku cudzego audytu.
  const [naKoncie, ustawNaKoncie] = useState(false);
  const [blad, ustawBlad] = useState<string | null>(null);

  const wczytaj = useCallback(async () => {
    try {
      ustawPulpit(await api.pulpit(wybrany, wersja));
      ustawBlad(null);
    } catch {
      ustawPulpit(null);
      ustawBlad("nie ma jeszcze audytu tego konta");
    }
  }, [wybrany, wersja]);

  useEffect(() => {
    // Lista klientów tylko dla zespołu. Sesja klienta dostałaby 404, więc nawet
    // nie pytamy — nie chcemy hałasu w logach z żądań, które muszą się nie udać.
    if (ja.rola === "zespol") api.klienci().then(ustawKlienci).catch(() => ustawKlienci([]));
  }, [ja.rola]);

  useEffect(() => {
    void wczytaj();
  }, [wczytaj]);

  const kafle = (pulpit?.sekcje ?? [])
    .flatMap((s) => s.metryki)
    .filter((m) => KAFLE_KLUCZOWE.includes(m.nazwa));

  return (
    <>
      <aside className="sidebar">
        <div className="sidebar__marka">
          <img src="/cxlabs-white.png" alt="CXLABS" />
          <small>Audyt monday.com</small>
        </div>
        <nav>
          {ja.rola === "zespol" ? (
            <>
              <span className="poz">
                Klienci<span className="sidebar__licznik">{klienci.length}</span>
              </span>
              {klienci.map((k) => {
                const aktywny = k.client_id === (wybrany ?? pulpit?.client_id);
                return (
                  <div key={k.client_id}>
                    <a
                      href="#"
                      className={aktywny ? "aktywny" : ""}
                      onClick={(e) => {
                        e.preventDefault();
                        // Zmiana klienta zeruje wersję: run poprzedniego klienta
                        // nie należy do nowego, więc serwer oddałby 404. Panel
                        // pokazałby wtedy „nie ma audytu" przy kliencie, który
                        // audyt ma.
                        ustawWersje(undefined);
                        ustawWybranego(k.client_id);
                        ustawNaKoncie(false);
                      }}
                    >
                      {k.client_id}
                      <span className="sidebar__licznik">{k.findingow}</span>
                    </a>
                    {aktywny && <PodnawigacjaSekcji pulpit={pulpit} />}
                  </div>
                );
              })}
            </>
          ) : (
            <>
              <span className="poz aktywny-poz">Twój audyt</span>
              <PodnawigacjaSekcji pulpit={pulpit} />
            </>
          )}
        </nav>
        {ja.rola === "zespol" && (
          <div className="sidebar__admin">
            <a
              href="#"
              className={naKoncie ? "aktywny" : ""}
              onClick={(e) => {
                e.preventDefault();
                ustawNaKoncie(true);
              }}
            >
              Moje konto
            </a>
          </div>
        )}
        <div className="sidebar__stopka">
          {pulpit && (
            <>
              <p>
                run <code>{pulpit.run_id}</code>
              </p>
              <p>dane zebrane {pulpit.run_at.slice(0, 10)}</p>
            </>
          )}
          <button type="button" className="sidebar__wyloguj" onClick={poWylogowaniu}>
            Wyloguj
          </button>
        </div>
      </aside>

      <main className="tresc">
        {naKoncie ? (
          <MojeKonto
            email={ja.email ?? ""}
            klienci={klienci}
            odswiez={() => {
              // Po nadaniu dostępu lista musi się przerysować, inaczej wiersz
              // dalej mówi „nie może się zalogować".
              api.klienci().then(ustawKlienci).catch(() => undefined);
            }}
          />
        ) : (
        <>
        <div className="pasek">
          <span className="okruszki">
            {ja.rola === "zespol" && "Klienci / "}
            <b>{pulpit?.client_id ?? ja.client_id ?? "—"}</b>
          </span>
          <span className="pasek__prawo">
            {/* Drop-down odpowiada na inne pytanie niż sidebar: tam „którego
                klienta", tu „z kiedy". Pokazujemy go dopiero od dwóch wersji —
                przy jednym audycie kontrolka byłaby martwa. Dostają go OBIE role,
                bo klient też ma prawo obejrzeć swój starszy audyt; granicę trzyma
                serwer, sprawdzając właściciela runu. */}
            {(pulpit?.wersje.length ?? 0) > 1 && (
              <label className="wybor-wersji">
                {/* „analiza z", nie „wersja audytu" — bo `PozycjaRunu.run_at` to
                    `runy.started_at`, czyli kiedy agent BADAŁ dane, a nagłówek
                    strony pokazuje `Pulpit.run_at`, czyli kiedy dane ZEBRANO.
                    Oba są prawdziwe i potrafią się różnić: dwie analizy tego
                    samego snapshotu mają jedną datę zbiórki i dwie daty badania.
                    Zobaczyłem to na zrzucie — drop-down mówił „5 sierpnia", a pod
                    tytułem stało „dane z 2026-08-01" i wyglądało na sprzeczność.
                    Nazwanie obu rozwiązuje to bez zmiany danych. */}
                <span>analiza z</span>
                <select
                  aria-label="Wersja audytu — data analizy"
                  value={wersja ?? pulpit?.run_id ?? ""}
                  onChange={(e) => ustawWersje(e.target.value)}
                >
                  {pulpit?.wersje.map((w) => (
                    <option key={w.run_id} value={w.run_id}>
                      {dataWersji(w.run_at)} — {w.findingow} {odmianaZnalezisk(w.findingow)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <span
              className={`plomba-odbiorcy plomba-odbiorcy--${
                ja.rola === "zespol" ? "wewn" : "klient"
              }`}
            >
              {ja.rola === "zespol" ? "wewnętrzny" : "Twoje konto"}
            </span>
          </span>
        </div>

        <div className="strona">
          <p className="eyebrow">Audyt konta monday.com</p>
          <h1>{pulpit?.nazwa_konta ?? ja.client_id ?? "Audyty"}</h1>
          {pulpit && (
            <p className="strona__adres">
              {pulpit.zakres} · plan {pulpit.plan_tier} · dane zebrane{" "}
              {pulpit.run_at.slice(0, 10)}
            </p>
          )}

          <Audyt klient={ja.rola === "zespol" ? wybrany : undefined} poZakonczeniu={wczytaj} />

          {/* Dostęp klienta — tylko dla zespołu. Klient nie ma tu nic, bo reset
              jego hasła robimy my; granica stoi w API, nie w tym warunku. */}
          {ja.rola === "zespol" && (pulpit?.client_id ?? wybrany) && (
            <details className="sekcja">
              <summary>
                Dostęp klienta{" "}
                <span className="opis">hasło do panelu, nie klucz API monday</span>
              </summary>
              <div className="sekcja__ciało">
                <p className="meta">
                  Klient wchodzi na ten sam adres i podaje nazwę konta oraz hasło,
                  które od nas dostał. Hasła nie odczytamy — jeśli je zgubił, wydaj
                  nowe.
                </p>
                <ResetHaslaKlienta clientId={(pulpit?.client_id ?? wybrany) as string} />
              </div>
            </details>
          )}

          {blad && <p className="brak-danych">{blad}</p>}

          {pulpit && (
            <>
              <div className="kafle-gorne">
                <KafelDuzy
                  podpis="Znaleziska"
                  wartosc={pulpit.findingow}
                  pod={Object.entries(pulpit.po_wagach)
                    .map(([w, i]) => `${i} ${w}`)
                    .join(" · ")}
                />
                {pulpit.ma_kwoty ? (
                  <KafelDuzy
                    podpis="Możliwa oszczędność roczna"
                    wartosc={pulpit.suma_kwot.toLocaleString("pl-PL", {
                      maximumFractionDigits: 0,
                    })}
                    pod="PLN na licencjach monday"
                    akcent
                  />
                ) : (
                  <KafelDuzy podpis="Oszczędność" wartosc="—" pod="nie podano stawki licencji" />
                )}
                {kafle.map((m) => (
                  <KafelDuzy
                    key={m.nazwa}
                    podpis={m.nazwa}
                    wartosc={m.wartosc}
                    pod={m.udzial !== null ? `${m.udzial}% z ${m.z}` : (m.opis ?? undefined)}
                  />
                ))}
                {/* `koszt_usd` NIE ISTNIEJE w payloadzie klienta (D16), więc
                    ten kafel nie ma jak się pojawić — niezależnie od warunku. */}
                {pulpit.koszt_usd != null && (
                  <KafelDuzy
                    podpis="Koszt audytu"
                    wartosc={pulpit.koszt_usd.toFixed(2)}
                    pod="USD za analizę"
                  />
                )}
              </div>

              {!pulpit.ma_porownanie && (
                <p className="brak-danych">
                  To pierwszy audyt tego konta, więc nie ma z czym porównywać. Przy
                  następnym panel pokaże różnicę.
                </p>
              )}

              {pulpit.sekcje.map((s) => (
                <SekcjaMetryk key={s.tytul} s={s} />
              ))}
              <Znaleziska findingi={pulpit.findingi} />
              <CzegoNieWidac zastrzezenia={pulpit.zastrzezenia} />

              {pulpit.pinowanie && Object.keys(pulpit.pinowanie).length > 0 && (
                <details className="sekcja">
                  <summary>
                    Diagnostyka runu <span className="opis">nie dla klienta</span>
                  </summary>
                  <div className="sekcja__ciało">
                    <div className="przewijane">
                      <table className="tabela-lista">
                        <tbody>
                          {Object.entries(pulpit.pinowanie).map(([k, w]) => (
                            <tr key={k}>
                              <th>{k}</th>
                              <td>{w === null ? "—" : String(w)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {(pulpit.hipotezy_odrzucone?.length ?? 0) > 0 && (
                      <>
                        <h3>Hipotezy obalone przez agenta</h3>
                        <div className="przewijane">
                          <table className="tabela-lista">
                            <tbody>
                              {pulpit.hipotezy_odrzucone?.map((h, i) => (
                                <tr key={i}>
                                  <td>{String(h.klasa_id)}</td>
                                  <td>{String(h.powod)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </>
                    )}
                  </div>
                </details>
              )}
            </>
          )}

          <footer>
            Panel zbudowany z zamrożonego snapshotu. Każde znalezisko ma dowód
            wskazujący na konkretny fakt. CXLABS.
          </footer>
        </div>
        </>
        )}
      </main>
    </>
  );
}
