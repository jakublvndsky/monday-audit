// Formularz klucza API i pasek postępu.
//
// ## Formularz mówi PRAWDĘ o kluczach, nie tylko „zalecane"
//
// Klucz admina daje pełniejsze dane — `pokrycie_pelne` jest prawdą tylko dla
// admina na całym koncie. Ale klucz admina monday **nie jest read-only**: kto
// go ma, może usunąć każdą tablicę. Odbiorca ma wybrać świadomie, więc piszemy
// jedno i drugie, a nie samo „zalecane".
//
// Klucz idzie w ciele POST i **nie jest zapisywany** — ani u nas, ani w tym
// komponencie dłużej niż trwa żądanie. Nie ma go w `localStorage`, bo tam
// przeżyłby zamknięcie karty.

import { useEffect, useState } from "react";
import { api, BladApi } from "./klient";
import type { Mozliwosc, StanAudytu } from "./api";

const ODPYTUJ_MS = 2000;

export function Audyt({ klient, poZakonczeniu }: { klient?: string; poZakonczeniu: () => void }) {
  const [kluczApi, ustawKlucz] = useState("");
  const [zakres, ustawZakres] = useState("cale_konto");
  const [workspaceId, ustawWorkspace] = useState("");
  const [zadanieId, ustawZadanie] = useState<string | null>(null);
  const [stan, ustawStan] = useState<StanAudytu | null>(null);
  const [mozliwosc, ustawMozliwosc] = useState<Mozliwosc | null>(null);
  const [blad, ustawBlad] = useState<string | null>(null);

  useEffect(() => {
    api.mozliwosc(klient).then(ustawMozliwosc).catch(() => ustawMozliwosc(null));
  }, [klient]);

  // Odpytywanie o stan. Zatrzymuje się samo, gdy run się skończy — bez tego
  // przeglądarka pytałaby serwer co dwie sekundy do zamknięcia karty.
  useEffect(() => {
    if (!zadanieId) return;
    let zyje = true;
    const zegar = setInterval(async () => {
      try {
        const nowy = await api.stanAudytu(zadanieId);
        if (!zyje) return;
        ustawStan(nowy);
        if (!nowy.trwa) {
          clearInterval(zegar);
          if (nowy.stan === "gotowe") poZakonczeniu();
        }
      } catch {
        clearInterval(zegar);
      }
    }, ODPYTUJ_MS);
    return () => {
      zyje = false;
      clearInterval(zegar);
    };
  }, [zadanieId, poZakonczeniu]);

  async function odpal(zdarzenie: React.FormEvent) {
    zdarzenie.preventDefault();
    ustawBlad(null);
    try {
      const { zadanie_id } = await api.odpalAudyt(
        kluczApi,
        zakres,
        zakres === "workspace" ? workspaceId.trim() || null : null,
        klient,
      );
      // Czyścimy pole natychmiast po wysłaniu. Klucz nie ma po co siedzieć
      // w stanie komponentu ani w DOM-ie dłużej, niż trwało żądanie.
      ustawKlucz("");
      ustawZadanie(zadanie_id);
    } catch (e) {
      ustawBlad(e instanceof BladApi ? e.message : "nie udało się uruchomić audytu");
    }
  }

  if (stan && stan.trwa) {
    return (
      <details className="sekcja" open>
        <summary>
          Audyt w toku <span className="opis">to potrwa około kwadransa</span>
        </summary>
        <div className="sekcja__ciało">
          <p className="etap">{stan.etap ?? "startuję…"}</p>
          <div className="postep">
            <span style={{ width: `${stan.postep ?? 0}%` }} />
          </div>
          <p className="meta">
            {stan.postep ?? 0}% · możesz zamknąć tę stronę, audyt leci dalej
          </p>
        </div>
      </details>
    );
  }

  if (stan?.stan === "blad") {
    return (
      <div className="uwaga">
        <p>
          <strong>Audyt się nie udał.</strong> {stan.blad}
        </p>
        <p className="meta">
          Najczęstsza przyczyna to klucz bez uprawnień albo wyczerpany dzienny
          limit wywołań monday.
        </p>
      </div>
    );
  }

  return (
    <details className="sekcja" open>
      <summary>
        Wygeneruj audyt <span className="opis">potrzebny klucz API monday</span>
      </summary>
      <div className="sekcja__ciało">
        {mozliwosc && !mozliwosc.wolno ? (
          <div className="brak-danych">
            <strong>Teraz nie można uruchomić audytu.</strong> {mozliwosc.powod}
          </div>
        ) : (
          <form onSubmit={odpal} className="formularz-audytu">
            {/* Dwie kolumny: pola po lewej, ostrzeżenie o kluczu po prawej.
                Wcześniej ostrzeżenie stało POD polami, a formularz miał
                `max-width: 34rem` w karcie na pełną szerokość — dwie trzecie
                ekranu stało puste. Obok pól nie tylko wypełnia przestrzeń:
                ostrzeżenie jest przy polu, którego dotyczy. */}
            <div className="formularz-audytu__pola">
              <label htmlFor="klucz">
                Klucz API monday <span className="meta">Profil → Developers → My Access Tokens</span>
              </label>
              <input
                id="klucz"
                type="password"
                value={kluczApi}
                onChange={(e) => ustawKlucz(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                required
                minLength={20}
                // Krótki, bo w kolumnie 22rem dłuższy się obcina — a obcięta
                // instrukcja jest gorsza niż żadna. Pełna droga stoi pod polem.
                placeholder="wklej klucz API"
              />

              <label htmlFor="zakres">Zakres</label>
              <select id="zakres" value={zakres} onChange={(e) => ustawZakres(e.target.value)}>
                <option value="cale_konto">całe konto</option>
                <option value="workspace">jeden workspace</option>
              </select>

              {zakres === "workspace" && (
                <>
                  <label htmlFor="ws">Identyfikator workspace</label>
                  <input
                    id="ws"
                    value={workspaceId}
                    onChange={(e) => ustawWorkspace(e.target.value)}
                    placeholder="np. 6576039"
                    required
                  />
                </>
              )}

              {blad && (
                <p className="brama__blad" role="alert">
                  {blad}
                </p>
              )}

              <button type="submit" className="cx-btn">
                Wygeneruj audyt
              </button>
              <p className="meta">
                Audyt trwa około kwadransa i zużyje ~230 wywołań z twojego dziennego
                limitu monday.
              </p>
            </div>

            <div className="uwaga uwaga--klucz">
              <p>
                <strong>Klucz admina</strong> obejmuje wszystkie workspace'y, więc
                audyt jest dokładniejszy. <strong>Klucz pracownika</strong> pokaże
                tylko to, co ten pracownik widzi — a różnicę zapiszemy w raporcie
                jako zastrzeżenie.
              </p>
              <p>
                Zanim wkleisz admina, wiedz, że{" "}
                <strong>klucz API monday nie jest tylko do czytania</strong> — daje
                pełne uprawnienia twojego konta. Nasz audyt nic nie zmienia
                (odrzucamy zapisy w kodzie), a klucza <strong>nie zapisujemy</strong>:
                żyje w pamięci przez czas audytu i ginie razem z nim.
              </p>
              <p className="meta">
                Możesz go unieważnić w monday zaraz po audycie — to dobra praktyka.
              </p>
            </div>
          </form>
        )}
      </div>
    </details>
  );
}
