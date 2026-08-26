// Kreator nowego audytu: klucz → workspace → tablice → zbieranie.
//
// ## Trzy uwagi z użycia na żywo (Kuba, 2026-08-25) i co z nich wynikło
//
// 1. „kliknąłem i nic nic nic i nagle się coś pokazało" — **brak informacji
//    o ładowaniu**. Każdy krok, który czeka na sieć, ma teraz własny stan
//    „czekam" i wyłączony przycisk. Cztery sekundy bez sygnału czyta się jak
//    zepsuty przycisk.
// 2. „wszystko jest w jednym takim rzucie, tragicznie wyświetlane" — lista 59
//    tablic wyłożona płasko. Teraz **grupy są ZWINIĘTE**: klient widzi dwie
//    linie podsumowania i zatwierdza bez rozwijania czegokolwiek.
// 3. „kliknę odznacz oflagowane, a koszt się nie zmienił" — **błąd**, nie
//    kosmetyka. Udział liczył się tylko przy włączonym zawężaniu, więc
//    odznaczanie bez przełącznika nic nie robiło. Naprawione: zaznaczenie jest
//    jednym źródłem prawdy, a kwota liczy się z niego zawsze.
//
// ## Kroki, nie jeden długi ekran
//
// Klucz monday jest sam na pierwszym kroku, bo bez niego nie da się nic
// pokazać. Klucz Anthropic pojawia się dopiero PO zebraniu danych, przy
// dokładnych widełkach — decyzja o pieniądzach ma być podjęta wtedy, gdy
// kwota jest znana, nie na podstawie zgrubnego szacunku.

import { useMemo, useState } from "react";
import type { PodgladKonta, TablicaDoWyboru } from "../api";
import { odmiana } from "../Hasla";

// Tylko dwie flagi są znane przed zbieraniem — reszta wymaga dziennika (47 s).
const OPISY_FLAG: Record<string, string> = {
  nieuzywana_od_startu: "nieużywana od startu",
  raportowa: "raportowa",
};

const TYTULY_FLAG: Record<string, string> = {
  nieuzywana_od_startu:
    "Założona i nietknięta — między utworzeniem a ostatnią zmianą minęło mniej " +
    "niż dobę. Zwykle szablon, którego nikt nie zaczął używać.",
  raportowa:
    "Ponad połowa kolumn liczy się sama (formuły, lustra, zależności). " +
    "Taka tablica raczej czyta z innych, niż prowadzi własny proces.",
};

function usd(kwota: number): string {
  return kwota.toFixed(2).replace(".", ",");
}

/** Zwarty wiersz tablicy — jedna linia, bez rozpychania. */
function Wiersz({
  tablica,
  zaznaczona,
  przelacz,
}: {
  tablica: TablicaDoWyboru;
  zaznaczona: boolean;
  przelacz: (id: string) => void;
}) {
  return (
    <label className="tab-wiersz">
      <input type="checkbox" checked={zaznaczona} onChange={() => przelacz(tablica.board_id)} />
      <span className="tab-wiersz__nazwa" title={tablica.nazwa}>
        {tablica.nazwa}
      </span>
      <span className="tab-wiersz__liczby">
        {tablica.kolumn} kol · {tablica.items_count} elem
      </span>
      {tablica.flagi.map((f) => (
        <span key={f} className="flaga flaga--obojetna" title={TYTULY_FLAG[f]}>
          {OPISY_FLAG[f] ?? f}
        </span>
      ))}
    </label>
  );
}

/** Grupa tablic, DOMYŚLNIE ZWINIĘTA. Nagłówek wystarcza do decyzji. */
function Grupa({
  tytul,
  opis,
  tablice,
  wybrane,
  przelacz,
  ustawGrupe,
}: {
  tytul: string;
  opis: string;
  tablice: TablicaDoWyboru[];
  wybrane: Set<string>;
  przelacz: (id: string) => void;
  ustawGrupe: (ids: string[], wlacz: boolean) => void;
}) {
  const [otwarta, ustawOtwarta] = useState(false);
  if (tablice.length === 0) return null;

  const idy = tablice.map((t) => t.board_id);
  const zaznaczonych = idy.filter((id) => wybrane.has(id)).length;
  const wszystkie = zaznaczonych === idy.length;

  return (
    <div className={`grupa${otwarta ? " grupa--otwarta" : ""}`}>
      <div className="grupa__naglowek">
        <input
          type="checkbox"
          checked={wszystkie}
          // Stan „część zaznaczona" musi być widoczny, inaczej pusty checkbox
          // przy 12 z 47 zaznaczonych kłamie.
          ref={(el) => {
            if (el) el.indeterminate = zaznaczonych > 0 && !wszystkie;
          }}
          onChange={() => ustawGrupe(idy, !wszystkie)}
          aria-label={`zaznacz grupę ${tytul}`}
        />
        <button
          type="button"
          className="grupa__przycisk"
          onClick={() => ustawOtwarta((o) => !o)}
          aria-expanded={otwarta}
        >
          <span className="grupa__strzalka">{otwarta ? "▾" : "▸"}</span>
          <strong>
            {zaznaczonych}/{idy.length}
          </strong>{" "}
          {tytul}
          <span className="grupa__opis">{opis}</span>
        </button>
      </div>
      {otwarta && (
        <div className="grupa__lista">
          {tablice.map((t) => (
            <Wiersz
              key={t.board_id}
              tablica={t}
              zaznaczona={wybrane.has(t.board_id)}
              przelacz={przelacz}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function PodgladZakresu({
  podglad,
  workspaceId,
  wybierzWorkspace,
  wczytujeTablice,
  zbierz,
  trwa,
  blad,
}: {
  podglad: PodgladKonta;
  workspaceId: string | null;
  wybierzWorkspace: (id: string) => void;
  wczytujeTablice: boolean;
  zbierz: (workspaceId: string, boardIds: string[]) => void;
  trwa: boolean;
  blad: string;
}) {
  const [szukaj, ustawSzukaj] = useState("");
  // Zaznaczenie to JEDNO źródło prawdy o wyborze — nie ma osobnego
  // przełącznika „zawężaj". To on był przyczyną błędu z kwotą: przy wyłączonym
  // przełączniku odznaczanie tablic nie wpływało na nic.
  const [wybrane, ustawWybrane] = useState<Set<string>>(new Set());
  const [ostatniWs, ustawOstatniWs] = useState<string | null>(null);

  // Nowy workspace → zaznaczamy wszystkie jego tablice. Bez tego przejście
  // między workspace'ami zostawiałoby zaznaczenia z poprzedniego.
  if (workspaceId !== ostatniWs && !wczytujeTablice && podglad.tablice.length > 0) {
    ustawOstatniWs(workspaceId);
    ustawWybrane(new Set(podglad.tablice.map((t) => t.board_id)));
  }

  const widoczne = useMemo(() => {
    const fraza = szukaj.trim().toLowerCase();
    if (!fraza) return podglad.workspace_y;
    return podglad.workspace_y.filter((w) => w.nazwa.toLowerCase().includes(fraza));
  }, [podglad.workspace_y, szukaj]);

  const { oflagowane, zwykle } = useMemo(
    () => ({
      oflagowane: podglad.tablice.filter((t) => t.oflagowana),
      zwykle: podglad.tablice.filter((t) => !t.oflagowana),
    }),
    [podglad.tablice],
  );

  const przelacz = (id: string) =>
    ustawWybrane((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const ustawGrupe = (idy: string[], wlacz: boolean) =>
    ustawWybrane((p) => {
      const n = new Set(p);
      idy.forEach((id) => (wlacz ? n.add(id) : n.delete(id)));
      return n;
    });

  // Kwota liczy się z ZAZNACZENIA, zawsze. To poprawka zgłoszonego błędu:
  // „kliknę odznacz oflagowane, a koszt się nie zmienił".
  const udzial = podglad.tablice.length ? wybrane.size / podglad.tablice.length : 0;
  // Podłoga zostaje nawet przy zerze tablic: sygnały o koncie (ludzie, goście,
  // plan) idą niezależnie od wyboru.
  const skala = 0.2 + 0.8 * udzial;
  const nazwaWs = podglad.workspace_y.find((w) => w.workspace_id === workspaceId)?.nazwa;

  return (
    <section className="kreator">
      <header className="kreator__naglowek">
        <h2>Nowy audyt</h2>
        <ol className="kreator__kroki">
          <li className={workspaceId ? "zrobiony" : "biezacy"}>workspace</li>
          <li className={workspaceId ? "biezacy" : ""}>tablice</li>
          <li>zbieranie</li>
          <li>zatwierdzenie</li>
        </ol>
      </header>

      {/* KROK: workspace. Konto ma 100+ workspace'ów (ZMIERZONE), więc lista
          ma wyszukiwanie i własne przewijanie — bez tego przycisk na dole
          znikał poniżej ekranu. */}
      <div className="kreator__krok">
        <label htmlFor="szukaj-ws" className="kreator__etykieta">
          1. Wybierz workspace
          <span className="meta">{podglad.workspace_y.length} dostępnych</span>
        </label>
        {workspaceId && nazwaWs ? (
          <div className="kreator__wybrany">
            <strong>{nazwaWs}</strong>
            <button type="button" className="kreator__zmien" onClick={() => ustawSzukaj("")}>
              zmień
            </button>
          </div>
        ) : null}
        {(!workspaceId || szukaj !== "") && (
          <>
            <input
              id="szukaj-ws"
              value={szukaj}
              onChange={(e) => ustawSzukaj(e.target.value)}
              placeholder="szukaj po nazwie…"
              autoComplete="off"
            />
            <div className="kreator__lista" role="listbox" aria-label="Workspace'y">
              {widoczne.length === 0 && <p className="meta">Nic nie pasuje do „{szukaj}".</p>}
              {widoczne.slice(0, 30).map((w) => (
                <button
                  key={w.workspace_id}
                  type="button"
                  role="option"
                  aria-selected={w.workspace_id === workspaceId}
                  className={`kreator__pozycja${
                    w.workspace_id === workspaceId ? " kreator__pozycja--wybrany" : ""
                  }`}
                  onClick={() => {
                    ustawSzukaj("");
                    wybierzWorkspace(w.workspace_id);
                  }}
                >
                  {w.nazwa}
                </button>
              ))}
              {widoczne.length > 30 && (
                <p className="meta">…i {widoczne.length - 30} więcej — zawęź wyszukiwanie.</p>
              )}
            </div>
          </>
        )}
      </div>

      {/* Stan ładowania. ZGŁOSZONE: „kliknąłem i nic nic nic i nagle się coś
          pokazało" — cztery sekundy bez sygnału czyta się jak zepsuty przycisk. */}
      {wczytujeTablice && (
        <div className="kreator__krok">
          <p className="etap etap--czeka">czytam tablice tego workspace'u…</p>
          <div className="postep postep--nieokreslony">
            <span />
          </div>
        </div>
      )}

      {/* KROK: tablice. Grupy ZWINIĘTE — nagłówki wystarczają do decyzji,
          a rozwija ten, kto chce sprawdzić konkrety. */}
      {workspaceId && !wczytujeTablice && podglad.tablice.length > 0 && (
        <div className="kreator__krok">
          <p className="kreator__etykieta">
            2. Tablice do audytu
            <span className="meta">
              {wybrane.size} z {podglad.tablice.length} zaznaczonych
            </span>
          </p>

          <Grupa
            tytul="zwykłych tablic"
            opis="bez etykiet — wyglądają na używane"
            tablice={zwykle}
            wybrane={wybrane}
            przelacz={przelacz}
            ustawGrupe={ustawGrupe}
          />
          <Grupa
            tytul="oflagowanych"
            opis="szablony i tablice raportowe"
            tablice={oflagowane}
            wybrane={wybrane}
            przelacz={przelacz}
            ustawGrupe={ustawGrupe}
          />

          <div className="kreator__akcje">
            <button
              type="button"
              className="wybor__akcja"
              onClick={() => ustawWybrane(new Set(podglad.tablice.map((t) => t.board_id)))}
              disabled={wybrane.size === podglad.tablice.length}
            >
              wszystkie
            </button>
            {oflagowane.length > 0 && (
              <button
                type="button"
                className="wybor__akcja"
                onClick={() => ustawWybrane(new Set(zwykle.map((t) => t.board_id)))}
              >
                odznacz oflagowane ({oflagowane.length})
              </button>
            )}
          </div>

          <p className="kreator__kwota">
            zgrubnie{" "}
            <strong>
              {usd(podglad.zgrubnie_od_usd * skala)}–{usd(podglad.zgrubnie_do_usd * skala)} USD
            </strong>
            <span className="meta">
              {" "}
              szacunek z liczby tablic — dokładną kwotę pokażemy po zebraniu danych
            </span>
          </p>

          <details className="kreator__szczegoly">
            <summary>czego jeszcze nie wiemy</summary>
            <p>
              Na tej liście nie widać, które tablice zamilkły — to wymaga dziennika
              aktywności, który czytamy przy zbieraniu. Etykiety wyżej wynikają
              z samych dat i typów kolumn.
            </p>
            {podglad.pominietych_pomocniczych > 0 && (
              <p>
                Pominęliśmy {podglad.pominietych_pomocniczych}{" "}
                {odmiana(podglad.pominietych_pomocniczych, "obiekt", "obiekty", "obiektów")}{" "}
                pomocniczych (podelementy, dokumenty, obiekty własne).
              </p>
            )}
            {podglad.urwano_na_stronach && (
              <p>Ten workspace ma więcej tablic, niż pokazujemy — lista jest ucięta.</p>
            )}
          </details>
        </div>
      )}

      {blad && (
        <p className="brama__blad" role="alert">
          {blad}
        </p>
      )}

      <div className="kreator__stopka">
        <button
          type="button"
          className="cx-btn"
          onClick={() => {
            if (!workspaceId) return;
            // Pełne zaznaczenie wysyłamy jako PUSTĄ listę, czyli „cały
            // workspace" — nie jako 97 identyfikatorów.
            //
            // ZGŁOSZONE (Kuba, 2026-08-25): „raport zawiera więcej tablic niż
            // te, co chciałem". Przy pełnym zaznaczeniu front wysyłał
            // `zakres: tablice` z listą wszystkich, więc snapshot zapisywał
            // zakres „97 wskazanych tablic" zamiast „ten workspace" — a raport
            // czyta zakres ze snapshotu i mówił coś innego, niż klient wybrał.
            const wszystkieZaznaczone = wybrane.size === podglad.tablice.length;
            zbierz(workspaceId, wszystkieZaznaczone ? [] : [...wybrane]);
          }}
          disabled={!workspaceId || wybrane.size === 0 || trwa || wczytujeTablice}
        >
          {trwa ? "zaczynam…" : "Zbierz dane"}
        </button>
        <span className="meta">
          {!workspaceId
            ? "najpierw wskaż workspace"
            : wybrane.size === 0
              ? "zaznacz choć jedną tablicę"
              : "klucz do analizy podasz po zebraniu danych"}
        </span>
      </div>
    </section>
  );
}
