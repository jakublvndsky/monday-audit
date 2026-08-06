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
import { api } from "./klient";
import { CzegoNieWidac, KafelDuzy, SekcjaMetryk, Znaleziska } from "./komponenty/Sekcje";

const KAFLE_KLUCZOWE = ["agentów AI", "zdominowanych jednym autorem"];

export function Panel({ ja, poWylogowaniu }: { ja: Ja; poWylogowaniu: () => void }) {
  const [pulpit, ustawPulpit] = useState<Pulpit | null>(null);
  const [klienci, ustawKlienci] = useState<PozycjaKlienta[]>([]);
  const [wybrany, ustawWybranego] = useState<string | undefined>(undefined);
  const [blad, ustawBlad] = useState<string | null>(null);

  const wczytaj = useCallback(async () => {
    try {
      ustawPulpit(await api.pulpit(wybrany));
      ustawBlad(null);
    } catch {
      ustawPulpit(null);
      ustawBlad("nie ma jeszcze audytu tego konta");
    }
  }, [wybrany]);

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
              {klienci.map((k) => (
                <a
                  key={k.client_id}
                  href="#"
                  className={k.client_id === (wybrany ?? pulpit?.client_id) ? "aktywny" : ""}
                  onClick={(e) => {
                    e.preventDefault();
                    ustawWybranego(k.client_id);
                  }}
                >
                  {k.client_id}
                  <span className="sidebar__licznik">{k.findingow}</span>
                </a>
              ))}
            </>
          ) : (
            <span className="poz">Twój audyt</span>
          )}
        </nav>
        <div className="sidebar__stopka">
          {pulpit && (
            <>
              <p>
                run <code>{pulpit.run_id}</code>
              </p>
              <p>audyt z {pulpit.run_at.slice(0, 10)}</p>
            </>
          )}
          <button type="button" className="sidebar__wyloguj" onClick={poWylogowaniu}>
            Wyloguj
          </button>
        </div>
      </aside>

      <main className="tresc">
        <div className="pasek">
          <span className="okruszki">
            {ja.rola === "zespol" && "Klienci / "}
            <b>{pulpit?.client_id ?? ja.client_id ?? "—"}</b>
          </span>
          <span className="pasek__prawo">
            {ja.rola === "zespol" && klienci.length > 0 && (
              <select
                className="wybor-klienta"
                aria-label="Wybór klienta"
                value={wybrany ?? pulpit?.client_id ?? ""}
                onChange={(e) => ustawWybranego(e.target.value)}
              >
                {klienci.map((k) => (
                  <option key={k.client_id} value={k.client_id}>
                    {k.client_id} — {k.findingow} znalezisk
                  </option>
                ))}
              </select>
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
              {pulpit.zakres} · plan {pulpit.plan_tier} · dane z {pulpit.run_at.slice(0, 10)}
            </p>
          )}

          <Audyt klient={ja.rola === "zespol" ? wybrany : undefined} poZakonczeniu={wczytaj} />

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
      </main>
    </>
  );
}
