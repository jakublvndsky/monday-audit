// Reset haseł — widoczny WYŁĄCZNIE dla sesji zespołu.
//
// ## Dlaczego klient nie ma tu nic
//
// Hasło klienta resetuje zespół. Klient nie może sam, bo hasło jest jedyną bramą
// do jego danych osobowych, a my nie mamy jak potwierdzić, kto o reset prosi —
// nie ma wysyłki maili (O24).
//
// To NIE jest granica: granica stoi w API (klient dostaje 404 na `/api/haslo/*`,
// a `/api/haslo/moje` bierze konto z sesji). Gdyby ktoś usunął warunki `rola ===
// "zespol"` z tego pliku, klient zobaczyłby przyciski, które nie działają — nie
// dostałby możliwości resetu.
//
// ## Nowe hasło widać RAZ
//
// W bazie jest tylko hash, więc po zamknięciu tego widoku hasła nie da się
// odczytać — ani nam, ani nikomu z bazą w ręku. Dlatego komponent mówi to
// wprost, zamiast pokazać hasło i liczyć, że ktoś je zapisze.

import { useState } from "react";
import type { PozycjaKlienta } from "./api";
import { api, BladApi, type WynikResetu } from "./klient";

/** Polska odmiana po liczbie: 1 audyt, 2 audyty, 5 audytów. */
function odmiana(ile: number, poj: string, mnoga: string, dopelniacz: string): string {
  if (ile === 1) return poj;
  if (ile % 10 >= 2 && ile % 10 <= 4 && (ile % 100 < 12 || ile % 100 > 14)) return mnoga;
  return dopelniacz;
}

/** Pokazane raz nowe hasło. Świadomie nie ma tu „skopiuj i zamknij" — dopóki
 *  widok jest otwarty, hasło jest widoczne i można je przepisać. */
function NoweHaslo({ wynik, kogo }: { wynik: WynikResetu; kogo: string }) {
  return (
    <div className="nowe-haslo">
      <p className="nowe-haslo__etykieta">nowe hasło dla {kogo}</p>
      <p className="nowe-haslo__wartosc">{wynik.haslo}</p>
      <p className="meta">
        <strong>Zapisz je teraz.</strong> W bazie jest tylko hash, więc nie
        odczytamy go ponownie — także my. Stare hasło już nie działa.
      </p>
      {wynik.wazne_sesje > 0 && (
        // Reset nie wylogowuje (decyzja Kuby). Bez tego zdania ktoś kliknąłby
        // „reset" i uznał, że odciął dostęp — a to pomyłka kosztowna.
        <p className="uwaga-sesje">
          {wynik.wazne_sesje === 1
            ? "1 otwarta sesja tego konta działa dalej"
            : `${wynik.wazne_sesje} otwarte sesje tego konta działają dalej`}
          , do {wynik.godzin_sesji} h od zalogowania. Reset wydaje nowe hasło; nie
          odcina dostępu natychmiast.
        </p>
      )}
    </div>
  );
}

/** Przycisk „Zresetuj hasło" przy kliencie, z potwierdzeniem. */
export function ResetHaslaKlienta({ clientId }: { clientId: string }) {
  const [pytamy, ustawPytamy] = useState(false);
  const [wynik, ustawWynik] = useState<WynikResetu | null>(null);
  const [blad, ustawBlad] = useState<string | null>(null);
  const [czeka, ustawCzeka] = useState(false);

  async function resetuj() {
    ustawCzeka(true);
    ustawBlad(null);
    try {
      ustawWynik(await api.zresetujHasloKlienta(clientId));
      ustawPytamy(false);
    } catch (e) {
      ustawBlad(e instanceof BladApi ? e.message : "nie udało się zresetować hasła");
    } finally {
      ustawCzeka(false);
    }
  }

  if (wynik) return <NoweHaslo wynik={wynik} kogo={clientId} />;

  return (
    <div className="reset-hasla">
      {blad && (
        <p className="brama__blad" role="alert">
          {blad}
        </p>
      )}
      {pytamy ? (
        <>
          {/* Potwierdzenie, bo akcja jest nieodwracalna: stare hasło klienta
              przestaje działać w chwili kliknięcia i nie da się go przywrócić. */}
          <p className="meta">
            Stare hasło klienta <b>{clientId}</b> przestanie działać. Nowe zobaczysz
            raz — musisz je przekazać klientowi.
          </p>
          <div className="reset-hasla__akcje">
            <button
              type="button"
              className="cx-btn cx-btn--groźny"
              onClick={resetuj}
              disabled={czeka}
            >
              {czeka ? "resetuję…" : "Tak, wydaj nowe hasło"}
            </button>
            <button
              type="button"
              className="cx-btn cx-btn--cichy"
              onClick={() => ustawPytamy(false)}
              disabled={czeka}
            >
              Anuluj
            </button>
          </div>
        </>
      ) : (
        <button
          type="button"
          className="cx-btn cx-btn--groźny"
          onClick={() => ustawPytamy(true)}
        >
          Zresetuj hasło klienta
        </button>
      )}
    </div>
  );
}

/** Nadanie dostępu klientowi, który konta nie ma.
 *
 * Panel pokazuje „BRAK KONTA" jako stan (patrz `zbuduj_liste_klientow`), więc musi
 * dawać drogę do naprawienia go — pokazywanie braku bez możliwości działania to
 * połowa roboty. W bazie produkcyjnej `cxlabs` miał 17 audytów i żadnego konta:
 * audyt istniał, a odbiorca nie mógł go zobaczyć.
 */
function NadajDostep({ clientId, poNadaniu }: { clientId: string; poNadaniu: () => void }) {
  const [wynik, ustawWynik] = useState<WynikResetu | null>(null);
  const [blad, ustawBlad] = useState<string | null>(null);
  const [czeka, ustawCzeka] = useState(false);

  async function nadaj() {
    ustawCzeka(true);
    ustawBlad(null);
    try {
      ustawWynik(await api.nadajDostep(clientId));
      poNadaniu();
    } catch (e) {
      ustawBlad(e instanceof BladApi ? e.message : "nie udało się nadać dostępu");
    } finally {
      ustawCzeka(false);
    }
  }

  if (wynik) return <NoweHaslo wynik={wynik} kogo={clientId} />;

  return (
    <>
      {blad && (
        <p className="brama__blad" role="alert">
          {blad}
        </p>
      )}
      <button type="button" className="cx-btn cx-btn--cichy" onClick={nadaj} disabled={czeka}>
        {czeka ? "zakładam…" : "Nadaj dostęp"}
      </button>
    </>
  );
}

/** Dodanie NOWEGO klienta: identyfikator plus wygenerowane hasło.
 *
 * Do 2026-08-10 konto zakładało się tylko z konsoli (`--dodaj-klienta`), więc
 * każdy nowy klient wymagał dostępu do serwera. CLI zostaje jako droga ratunkowa.
 *
 * Identyfikator waliduje SERWER (wzorzec `WZORZEC_CLIENT_ID`), nie ten formularz.
 * `pattern` na `<input>` jest tylko podpowiedzią dla przeglądarki — trafia do
 * adresów i nazw plików raportu, więc reguła musi stać tam, gdzie jej nie da się
 * pominąć.
 */
function DodajKlienta({ poDodaniu }: { poDodaniu: () => void }) {
  const [clientId, ustawClientId] = useState("");
  const [wynik, ustawWynik] = useState<{ haslo: string; kto: string } | null>(null);
  const [blad, ustawBlad] = useState<string | null>(null);
  const [czeka, ustawCzeka] = useState(false);

  async function wyslij(zdarzenie: React.FormEvent) {
    zdarzenie.preventDefault();
    ustawCzeka(true);
    ustawBlad(null);
    const kto = clientId.trim().toLowerCase();
    try {
      const odp = await api.nadajDostep(kto);
      ustawWynik({ haslo: odp.haslo, kto });
      ustawClientId("");
      poDodaniu();
    } catch (e) {
      ustawBlad(e instanceof BladApi ? e.message : "nie udało się dodać klienta");
    } finally {
      ustawCzeka(false);
    }
  }

  return (
    <div className="dodaj-klienta">
      {wynik ? (
        <>
          <NoweHaslo wynik={{ haslo: wynik.haslo, wazne_sesje: 0, godzin_sesji: 0 }} kogo={wynik.kto} />
          <button
            type="button"
            className="cx-btn cx-btn--cichy"
            onClick={() => ustawWynik(null)}
          >
            Dodaj kolejnego klienta
          </button>
        </>
      ) : (
        <form onSubmit={wyslij}>
          <label htmlFor="nowy-klient">
            Identyfikator nowego klienta
            <span className="meta">
              małe litery, cyfry i łączniki — trafia do adresu panelu, np.
              kancelaria-eko
            </span>
          </label>
          <div className="dodaj-klienta__wiersz">
            <input
              id="nowy-klient"
              value={clientId}
              onChange={(e) => ustawClientId(e.target.value)}
              placeholder="kancelaria-eko"
              autoComplete="off"
              spellCheck={false}
              required
            />
            <button type="submit" className="cx-btn" disabled={czeka}>
              {czeka ? "dodaję…" : "Dodaj klienta"}
            </button>
          </div>
          {blad && (
            <p className="brama__blad" role="alert">
              {blad}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

/** Strona administracyjna: własne konto plus dostępy wszystkich klientów.
 *
 * Osobna strona, nie sekcja wśród danych audytu. Poprzednia wersja miała „Moje
 * hasło" pomiędzy kaflami klienta, co Kuba słusznie zakwestionował: własne konto
 * nie należy do widoku audytu cudzego konta.
 */
export function MojeKonto({
  email,
  klienci,
  odswiez,
}: {
  email: string;
  klienci: PozycjaKlienta[];
  odswiez: () => void;
}) {
  return (
    <div className="strona">
      <p className="eyebrow">Panel administracyjny</p>
      <h1>Moje konto</h1>
      <p className="strona__adres">{email}</p>

      <MojeHaslo email={email} />

      <details className="sekcja" open>
        <summary>
          Dostępy klientów <span className="opis">kto może wejść na swój panel</span>
        </summary>
        <div className="sekcja__ciało">
          <p className="meta">
            Hasła nie odczytamy — w bazie jest tylko hash. Zgubione zastępujemy nowym.
          </p>
          <DodajKlienta poDodaniu={odswiez} />
          <div className="przewijane">
            <table className="tabela-lista">
              <thead>
                <tr>
                  <th>Klient</th>
                  <th>Audyty</th>
                  <th>Dostęp</th>
                  <th>Hasło</th>
                </tr>
              </thead>
              <tbody>
                {klienci.map((k) => (
                  <tr key={k.client_id}>
                    <td>
                      <b>{k.client_id}</b>
                    </td>
                    <td>
                      {k.audytow > 0 ? (
                        // „5 audytów · 1 znalezisko" — liczba znalezisk dotyczy
                        // NAJNOWSZEGO runu, nie sumy, więc odmiana musi się zgadzać
                        // z jedynką. Wcześniej wychodziło „1 znalezisk".
                        `${k.audytow} ${odmiana(k.audytow, "audyt", "audyty", "audytów")} · ` +
                        `${k.findingow} ${odmiana(k.findingow, "znalezisko", "znaleziska", "znalezisk")}`
                      ) : (
                        <span className="pusto">brak audytu</span>
                      )}
                    </td>
                    <td>
                      {k.ma_konto ? (
                        "ma hasło"
                      ) : (
                        /* Ten stan był dotąd NIEWIDOCZNY: klient z audytem, ale bez
                           konta, nie mógł się zalogować i nikt tego nie wiedział. */
                        <span className="stan-brak">nie może się zalogować</span>
                      )}
                    </td>
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
        </div>
      </details>
    </div>
  );
}

/** Zmiana WŁASNEGO hasła. Konto bierze serwer z sesji, nie z tego formularza. */
export function MojeHaslo({ email }: { email: string }) {
  const [obecne, ustawObecne] = useState("");
  const [wynik, ustawWynik] = useState<WynikResetu | null>(null);
  const [blad, ustawBlad] = useState<string | null>(null);
  const [czeka, ustawCzeka] = useState(false);

  async function wyslij(zdarzenie: React.FormEvent) {
    zdarzenie.preventDefault();
    ustawCzeka(true);
    ustawBlad(null);
    try {
      const odp = await api.zmienMojeHaslo(obecne);
      // Czyścimy pole natychmiast — obecne hasło nie ma po co siedzieć w DOM-ie
      // dłużej, niż trwało żądanie. Tak samo robimy z kluczem API.
      ustawObecne("");
      ustawWynik(odp);
    } catch (e) {
      ustawBlad(e instanceof BladApi ? e.message : "nie udało się zmienić hasła");
    } finally {
      ustawCzeka(false);
    }
  }

  return (
    <details className="sekcja">
      <summary>
        Moje hasło <span className="opis">{email}</span>
      </summary>
      <div className="sekcja__ciało">
        {wynik ? (
          <NoweHaslo wynik={wynik} kogo={email} />
        ) : (
          <form onSubmit={wyslij} className="formularz-hasla">
            {/* Wymagamy obecnego hasła, choć sesja już potwierdza tożsamość:
                sesja bywa porzucona w cudzej przeglądarce, a bez tego warunku
                przejęta sesja pozwala przejąć konto na stałe. */}
            <label htmlFor="obecne">Obecne hasło</label>
            <input
              id="obecne"
              type="password"
              value={obecne}
              onChange={(e) => ustawObecne(e.target.value)}
              autoComplete="current-password"
              required
            />
            <p className="meta">
              Nowego hasła nie wpisujesz — wygenerujemy je w tym samym formacie co
              hasła klientów i pokażemy raz.
            </p>
            {blad && (
              <p className="brama__blad" role="alert">
                {blad}
              </p>
            )}
            <button type="submit" className="cx-btn cx-btn--groźny" disabled={czeka}>
              {czeka ? "zmieniam…" : "Wygeneruj nowe hasło"}
            </button>
          </form>
        )}
      </div>
    </details>
  );
}
