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
import { api, BladApi, type WynikResetu } from "./klient";

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
