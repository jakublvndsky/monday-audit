// Brama: hasło klienta albo logowanie zespołu. Jeden adres, dwa wejścia (D16).
//
// Klient wpisuje identyfikator konta i hasło, które od nas dostał. Zespół
// loguje się e-mailem. Ta sama strona, bo tak chciał Kuba — a rozdziela je
// serwer, nie ten plik.

import { useEffect, useState } from "react";
import { api, BladApi, type WynikResetu } from "./klient";

type Wejscie = "klient" | "zespol";

/** Ekran „nie pamiętam hasła" dla ZESPOŁU.
 *
 * Reset z panelu wymaga sesji, a kto zgubił hasło, sesji nie ma — bez tego ekranu
 * było błędne koło i to była luka.
 *
 * Komunikat po wysłaniu jest ZAWSZE taki sam, także dla adresu bez konta. Front
 * nie ma z czego wnioskować, bo serwer nie robi różnicy: inaczej brama mówiłaby,
 * które adresy @cxlabs.digital są prawdziwe.
 */
function Zapomniane({ poPowrocie }: { poPowrocie: () => void }) {
  const [email, ustawEmail] = useState("");
  const [wyslane, ustawWyslane] = useState<string | null>(null);
  const [czeka, ustawCzeka] = useState(false);

  async function wyslij(zdarzenie: React.FormEvent) {
    zdarzenie.preventDefault();
    ustawCzeka(true);
    try {
      const odp = await api.zapomnianeHaslo(email.trim());
      ustawWyslane(odp.komunikat);
    } catch {
      // Nawet błąd sieci nie może dać innego komunikatu niż sukces — patrz
      // docstring. Pokazujemy to samo zdanie, bo różnica też jest informacją.
      ustawWyslane("Jeśli ten adres ma konto w panelu, link do zmiany hasła jest w drodze.");
    } finally {
      ustawCzeka(false);
    }
  }

  if (wyslane) {
    return (
      <>
        <h1>Sprawdź skrzynkę</h1>
        <div className="uwaga">
          <p>{wyslane}</p>
          <p className="meta">
            Link otwiera stronę, która wyda nowe hasło. Działa raz i tylko przez
            pół godziny.
          </p>
        </div>
        <button type="button" className="cx-btn cx-btn--cichy" onClick={poPowrocie}>
          Wróć do logowania
        </button>
      </>
    );
  }

  return (
    <>
      <h1>Nie pamiętam hasła</h1>
      <form onSubmit={wyslij}>
        <label htmlFor="email-reset">E-mail służbowy</label>
        <input
          id="email-reset"
          type="email"
          value={email}
          onChange={(e) => ustawEmail(e.target.value)}
          autoComplete="username"
          placeholder="imie@cxlabs.digital"
          required
        />
        <p className="brama__stopka">
          Wyślemy link na tę skrzynkę. <strong>Dotyczy tylko zespołu CXLABS</strong> —
          jeśli jesteś klientem, napisz do osoby prowadzącej twój audyt, a wydamy
          nowe hasło.
        </p>
        <button type="submit" className="cx-btn" disabled={czeka}>
          {czeka ? "wysyłam…" : "Wyślij link"}
        </button>
      </form>
      <button type="button" className="cx-btn cx-btn--cichy" onClick={poPowrocie}>
        Wróć do logowania
      </button>
    </>
  );
}

/** Odbiór linku z maila: `/?reset=TOKEN`. Wymienia token na nowe hasło. */
function ZLinku({ token, poPowrocie }: { token: string; poPowrocie: () => void }) {
  const [wynik, ustawWynik] = useState<WynikResetu | null>(null);
  const [blad, ustawBlad] = useState<string | null>(null);

  useEffect(() => {
    // Token zużywamy od razu przy wejściu — jest jednorazowy, więc nie ma po co
    // trzymać go w interfejsie i czekać na kolejne kliknięcie.
    api
      .hasloZLinku(token)
      .then(ustawWynik)
      .catch((e) => ustawBlad(e instanceof BladApi ? e.message : "link nie zadziałał"));
    // Adres czyścimy z tokenu, żeby nie został w historii przeglądarki
    // ani nie wyciekł w nagłówku `Referer`.
    window.history.replaceState(null, "", "/");
  }, [token]);

  if (blad) {
    return (
      <>
        <h1>Link nie działa</h1>
        <p className="brama__blad" role="alert">
          {blad}
        </p>
        <button type="button" className="cx-btn cx-btn--cichy" onClick={poPowrocie}>
          Wróć do logowania
        </button>
      </>
    );
  }

  if (!wynik) return <p className="wczytywanie">sprawdzam link…</p>;

  return (
    <>
      <h1>Nowe hasło</h1>
      <div className="nowe-haslo">
        <p className="nowe-haslo__etykieta">zapisz je teraz</p>
        <p className="nowe-haslo__wartosc">{wynik.haslo}</p>
        <p className="meta">
          W bazie jest tylko hash, więc nie odczytamy go ponownie. Stare hasło już
          nie działa.
        </p>
      </div>
      <button type="button" className="cx-btn" onClick={poPowrocie}>
        Zaloguj się nowym hasłem
      </button>
    </>
  );
}

export function Brama({ poZalogowaniu }: { poZalogowaniu: () => void }) {
  // Token z maila czytamy RAZ, przy pierwszym renderze. `useState` z funkcją,
  // nie `useEffect`: inaczej brama mrugnęłaby formularzem logowania, zanim
  // zauważyłaby link.
  const [tokenZMaila, ustawTokenZMaila] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("reset"),
  );
  const [zapomniane, ustawZapomniane] = useState(false);
  const [wejscie, ustawWejscie] = useState<Wejscie>("klient");
  const [clientId, ustawClientId] = useState("");
  const [email, ustawEmail] = useState("");
  const [haslo, ustawHaslo] = useState("");
  const [blad, ustawBlad] = useState<string | null>(null);
  const [czeka, ustawCzeka] = useState(false);

  async function wyslij(zdarzenie: React.FormEvent) {
    zdarzenie.preventDefault();
    ustawBlad(null);
    ustawCzeka(true);
    try {
      if (wejscie === "klient") await api.zalogujKlienta(clientId.trim(), haslo);
      else await api.zalogujZespol(email.trim(), haslo);
      poZalogowaniu();
    } catch (e) {
      // Serwer daje ten sam komunikat dla „nie ma konta" i „złe hasło" —
      // pokazujemy go bez upiększania, żeby nie dodać różnicy, której nie ma.
      ustawBlad(e instanceof BladApi ? e.message : "nie udało się zalogować");
    } finally {
      ustawCzeka(false);
    }
  }

  if (tokenZMaila) {
    return (
      <div className="brama">
        <div className="brama__karta">
          <p className="eyebrow">Audyt konta monday.com</p>
          <ZLinku token={tokenZMaila} poPowrocie={() => ustawTokenZMaila(null)} />
        </div>
      </div>
    );
  }

  if (zapomniane) {
    return (
      <div className="brama">
        <div className="brama__karta">
          <p className="eyebrow">Audyt konta monday.com</p>
          <Zapomniane poPowrocie={() => ustawZapomniane(false)} />
        </div>
      </div>
    );
  }

  return (
    <div className="brama">
      <div className="brama__karta">
        <p className="eyebrow">Audyt konta monday.com</p>
        <h1>{wejscie === "klient" ? "Twój audyt" : "Panel CXLABS"}</h1>

        <div className="brama__zakladki" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={wejscie === "klient"}
            className={wejscie === "klient" ? "aktywna" : ""}
            onClick={() => ustawWejscie("klient")}
          >
            Jestem klientem
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={wejscie === "zespol"}
            className={wejscie === "zespol" ? "aktywna" : ""}
            onClick={() => ustawWejscie("zespol")}
          >
            Zespół CXLABS
          </button>
        </div>

        <form onSubmit={wyslij}>
          {wejscie === "klient" ? (
            <>
              <label htmlFor="klient">Nazwa konta</label>
              <input
                id="klient"
                value={clientId}
                onChange={(e) => ustawClientId(e.target.value)}
                autoComplete="username"
                required
              />
            </>
          ) : (
            <>
              <label htmlFor="email">E-mail służbowy</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => ustawEmail(e.target.value)}
                autoComplete="username"
                required
              />
            </>
          )}

          <label htmlFor="haslo">Hasło</label>
          <input
            id="haslo"
            type="password"
            value={haslo}
            onChange={(e) => ustawHaslo(e.target.value)}
            autoComplete="current-password"
            required
          />

          {blad && (
            <p className="brama__blad" role="alert">
              {blad}
            </p>
          )}

          <button type="submit" className="cx-btn" disabled={czeka}>
            {czeka ? "sprawdzam…" : "Wejdź"}
          </button>
        </form>

        {/* „Nie pamiętam hasła" znaczy co innego dla każdej roli — i to nie jest
            niespójność, a odbicie tego, kto wydaje hasło. Zespół ma skrzynkę
            w naszej domenie, więc może odzyskać hasło sam. Klient nie ma czym
            potwierdzić tożsamości, więc jego hasło wydajemy my (D16 aneks). */}
        {wejscie === "zespol" ? (
          <>
            <button
              type="button"
              className="brama__link"
              onClick={() => ustawZapomniane(true)}
            >
              Nie pamiętam hasła
            </button>
            <p className="brama__stopka">
              Wyślemy link na twoją skrzynkę <code>@cxlabs.digital</code>.
            </p>
          </>
        ) : (
          <p className="brama__stopka">
            Hasło dostajesz od nas. Po wejściu podasz klucz API monday, żeby
            uruchomić audyt — <strong>nie zapisujemy go</strong>.
            <br />
            <br />
            <strong>Nie pamiętasz hasła?</strong> Napisz do osoby, która prowadzi
            twój audyt — wydamy nowe. Nie da się go odzyskać samodzielnie, bo hasło
            jest jedyną bramą do twoich danych.
          </p>
        )}
      </div>
    </div>
  );
}
