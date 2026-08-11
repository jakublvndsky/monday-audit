// Panel główny — rozpiska wszystkich klientów. Widok STARTOWY dla zespołu.
//
// ## Dlaczego istnieje
//
// „Klienci" w sidebarze był nieklikalnym napisem, a Kuba szukał pod nim ogólnego
// zestawienia — słusznie, bo lista po lewej pokazuje same nazwy z licznikiem
// i nie odpowiada na pytanie „na czym stoimy ze wszystkimi klientami".
//
// Tu żyją też **dostępy klientów** (reset hasła, nadanie dostępu, dodanie
// klienta), przeniesione z „Moje konto". Kuba szukał ich właśnie tutaj i to jest
// właściwe miejsce: własne konto to nie zarządzanie klientami. Ten sam błąd
// popełniłem raz wcześniej, umieszczając reset klienta wśród danych audytu.
//
// ## Granica
//
// Cała ta strona jest zespołowa. Sesja klienta nie dostaje `/api/klienci` (404),
// więc nie ma z czego jej zbudować — a `Panel.tsx` nie pokazuje wejścia. Warunek
// roli w tym pliku byłby trzecim miejscem tej samej reguły, więc go nie ma:
// granica stoi w API (D16).

import type { PozycjaKlienta } from "./api";
import { DodajKlienta, NadajDostep, odmiana, ResetHaslaKlienta } from "./Hasla";

/** Data po polsku albo „—", gdy klient nie ma jeszcze audytu. */
function data(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso.slice(0, 10)
    : d.toLocaleDateString("pl-PL", { day: "numeric", month: "short", year: "numeric" });
}

export function Klienci({
  klienci,
  odswiez,
  poWybraniu,
}: {
  klienci: PozycjaKlienta[];
  odswiez: () => void;
  poWybraniu: (clientId: string) => void;
}) {
  const zAudytem = klienci.filter((k) => k.audytow > 0);
  const bezDostepu = klienci.filter((k) => !k.ma_konto);
  const sumaKwot = klienci.reduce((s, k) => s + k.suma_kwot, 0);

  return (
    <div className="strona">
      <p className="eyebrow">Panel CXLABS</p>
      <h1>Klienci</h1>
      <p className="strona__adres">
        {klienci.length} {odmiana(klienci.length, "klient", "klienci", "klientów")} ·{" "}
        {zAudytem.length} z audytem
        {sumaKwot > 0 &&
          ` · ${sumaKwot.toLocaleString("pl-PL", { maximumFractionDigits: 0 })} PLN możliwych oszczędności`}
      </p>

      {bezDostepu.length > 0 && (
        // Stan, którego do 2026-08-10 nie było widać nigdzie: audyt jest,
        // a klient nie może się zalogować. Na górze, bo to do naprawienia.
        <div className="uwaga">
          <p>
            <strong>
              {bezDostepu.length}{" "}
              {odmiana(bezDostepu.length, "klient", "klienci", "klientów")} bez dostępu
            </strong>{" "}
            — mają audyt, ale nie mogą się zalogować: {bezDostepu.map((k) => k.client_id).join(", ")}.
            Nadaj dostęp w tabeli poniżej.
          </p>
        </div>
      )}

      <details className="sekcja" open>
        <summary>
          Wszyscy klienci <span className="opis">audyty, znaleziska i dostęp do panelu</span>
        </summary>
        <div className="sekcja__ciało">
          <DodajKlienta poDodaniu={odswiez} />

          <div className="przewijane">
            <table className="tabela-lista tabela-klientow">
              <thead>
                <tr>
                  <th>Klient</th>
                  <th>Audyty</th>
                  <th>Znaleziska</th>
                  <th>Oszczędność</th>
                  <th>Ostatni audyt</th>
                  <th>Dostęp</th>
                </tr>
              </thead>
              <tbody>
                {klienci.map((k) => (
                  <tr key={k.client_id}>
                    <td>
                      {/* Klik w nazwę wchodzi w panel tego klienta — bo po
                          zobaczeniu rozpiski właśnie tego się chce. */}
                      <a
                        href="#"
                        onClick={(e) => {
                          e.preventDefault();
                          poWybraniu(k.client_id);
                        }}
                      >
                        <b>{k.client_id}</b>
                      </a>
                    </td>
                    <td>{k.audytow > 0 ? k.audytow : <span className="pusto">—</span>}</td>
                    <td>
                      {k.audytow > 0 ? (
                        k.findingow
                      ) : (
                        <span className="pusto">brak audytu</span>
                      )}
                    </td>
                    <td>
                      {k.suma_kwot > 0 ? (
                        `${k.suma_kwot.toLocaleString("pl-PL", { maximumFractionDigits: 0 })} PLN`
                      ) : (
                        // Kwota zero przy znaleziskach znaczy brak stawki licencji,
                        // nie brak oszczędności — rubryka nie wycenia bez stawki.
                        <span className="pusto">
                          {k.findingow > 0 ? "bez stawki" : "—"}
                        </span>
                      )}
                    </td>
                    <td>{data(k.ostatni_run_at)}</td>
                    <td>
                      {k.ma_konto ? (
                        <ResetHaslaKlienta clientId={k.client_id} />
                      ) : (
                        <NadajDostep clientId={k.client_id} poNadaniu={odswiez} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="meta">
            Haseł nie odczytamy — w bazie jest tylko hash. Zgubione zastępujemy nowym,
            a stare przestaje działać od razu (otwarta sesja klienta żyje do 12 h).
          </p>
        </div>
      </details>
    </div>
  );
}
