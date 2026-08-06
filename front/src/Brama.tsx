// Brama: hasło klienta albo logowanie zespołu. Jeden adres, dwa wejścia (D16).
//
// Klient wpisuje identyfikator konta i hasło, które od nas dostał. Zespół
// loguje się e-mailem. Ta sama strona, bo tak chciał Kuba — a rozdziela je
// serwer, nie ten plik.

import { useState } from "react";
import { api, BladApi } from "./klient";

type Wejscie = "klient" | "zespol";

export function Brama({ poZalogowaniu }: { poZalogowaniu: () => void }) {
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

        {wejscie === "klient" && (
          <p className="brama__stopka">
            Hasło dostajesz od nas. Po wejściu podasz klucz API monday, żeby
            uruchomić audyt — <strong>nie zapisujemy go</strong>.
          </p>
        )}
      </div>
    </div>
  );
}
