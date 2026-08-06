// Komponenty prezentacyjne przeniesione z zaakceptowanej makiety.
// Te same klasy CSS, ten sam wygląd — różnica jest w tym, że tu da się filtrować
// i sortować bez przeładowania strony.

import { useMemo, useState } from "react";
import type { Finding, Metryka, Sekcja } from "../api";

export function KafelDuzy({
  podpis,
  wartosc,
  pod,
  akcent,
}: {
  podpis: string;
  wartosc: string | number;
  pod?: string;
  akcent?: boolean;
}) {
  return (
    <div className={`kafel-duzy${akcent ? " kafel-duzy--akcent" : ""}`}>
      <small>{podpis}</small>
      <b>{wartosc}</b>
      {pod && <div className="pod">{pod}</div>}
    </div>
  );
}

function KartaMetryki({ m }: { m: Metryka }) {
  return (
    <div className={`metryka${m.uwaga ? " metryka--uwaga" : ""}`}>
      <b>{m.wartosc}</b>
      <small>{m.nazwa}</small>
      {m.udzial !== null && (
        <>
          <div className="pasek-udzialu">
            <span style={{ width: `${m.udzial}%` }} />
          </div>
          <small className="udzial">
            {m.udzial}% z {m.z}
          </small>
        </>
      )}
      {m.opis && <small className="metryka__opis">{m.opis}</small>}
    </div>
  );
}

export function SekcjaMetryk({ s }: { s: Sekcja }) {
  return (
    <details className="sekcja" open>
      <summary>
        {s.tytul} <span className="opis">{s.opis}</span>
      </summary>
      <div className="sekcja__ciało">
        <div className="metryki">
          {s.metryki.map((m) => (
            <KartaMetryki key={m.nazwa} m={m} />
          ))}
        </div>
      </div>
    </details>
  );
}

function Dowod({ dowod }: { dowod: Record<string, unknown> }) {
  const pola = Object.entries(dowod);
  const odmiana = pola.length === 1 ? "pole" : pola.length < 5 ? "pola" : "pól";
  return (
    <details>
      <summary>
        Dowód ({pola.length} {odmiana})
      </summary>
      <dl className="dowod">
        {pola.map(([klucz, wartosc]) => (
          <div key={klucz} style={{ display: "contents" }}>
            <dt>{etykieta(klucz)}</dt>
            <dd>{wartoscDowodu(wartosc)}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

// Te same etykiety co w `raport.py` — po deanonimizacji `user_hash` niesie
// nazwisko, więc podpis „user_hash" byłby sprzeczny sam w sobie.
const ETYKIETY: Record<string, string> = {
  user_hash: "konto",
  guest_hash: "konta gości",
  top_kontrybutor_hash: "najaktywniejsza osoba",
};

function etykieta(klucz: string): string {
  return ETYKIETY[klucz] ?? klucz.replaceAll("_", " ");
}

function wartoscDowodu(wartosc: unknown): React.ReactNode {
  if (wartosc === null || wartosc === undefined) return "—";
  if (Array.isArray(wartosc)) {
    if (wartosc.length === 0) return "—";
    if (typeof wartosc[0] === "object") return <pre>{JSON.stringify(wartosc, null, 2)}</pre>;
    return wartosc.join(", ");
  }
  if (typeof wartosc === "object") return <pre>{JSON.stringify(wartosc, null, 2)}</pre>;
  return String(wartosc);
}

const SLOWNIE: Record<string, string> = {
  srednia: "średnia",
  sredni: "średni",
};

function slownie(w: string): string {
  return SLOWNIE[w] ?? w;
}

const PORZADEK_WAG = ["krytyczna", "wysoka", "srednia", "niska"];

/** Znaleziska z filtrem po wadze — interaktywność, której makieta nie mogła mieć. */
export function Znaleziska({ findingi }: { findingi: Finding[] }) {
  const [filtr, ustawFiltr] = useState<string | null>(null);

  const wagi = useMemo(
    () =>
      PORZADEK_WAG.filter((w) => findingi.some((f) => f.waga === w)).map((w) => ({
        waga: w,
        ile: findingi.filter((f) => f.waga === w).length,
      })),
    [findingi],
  );
  const widoczne = filtr ? findingi.filter((f) => f.waga === filtr) : findingi;

  return (
    <details className="sekcja" open>
      <summary>
        Znaleziska <span className="opis">kolejność z rubryki: waga, potem koszt naprawy</span>
      </summary>
      <div className="sekcja__ciało">
        {wagi.length > 1 && (
          <div className="filtry">
            <button
              type="button"
              className={filtr === null ? "aktywny" : ""}
              onClick={() => ustawFiltr(null)}
            >
              wszystkie ({findingi.length})
            </button>
            {wagi.map(({ waga, ile }) => (
              <button
                key={waga}
                type="button"
                className={filtr === waga ? "aktywny" : ""}
                onClick={() => ustawFiltr(waga)}
              >
                {slownie(waga)} ({ile})
              </button>
            ))}
          </div>
        )}

        {widoczne.length === 0 && (
          <p className="pusto">Ten audyt nie dał znalezisk widocznych w tej wersji panelu.</p>
        )}
        {widoczne.map((f, i) => (
          <article key={`${f.klasa_id}-${i}`} className={`finding ${f.waga}`}>
            {f.kwota_pln !== null && (
              <span className="kwota">
                {f.kwota_pln.toLocaleString("pl-PL", { maximumFractionDigits: 0 })} PLN / rok
              </span>
            )}
            <h3>
              {i + 1}. {f.nazwa}
            </h3>
            <p className="etykiety">
              waga <b>{slownie(f.waga)}</b> · naprawa <b>{slownie(f.wysilek)}</b> · pewność{" "}
              <b>{slownie(f.pewnosc)}</b>
            </p>
            <p>{f.opis}</p>
            <div className="rekomendacja">
              <strong>Co zrobić</strong>
              {f.rekomendacja}
            </div>
            {/* `trop` jest `null` dla klienta — serwer go nie przysyła (D16),
                więc ten warunek nie jest granicą, tylko wyświetlaniem. */}
            {f.trop && (
              <p className="trop">
                <b>Trop:</b> {f.trop}
              </p>
            )}
            {Object.keys(f.dowod).length > 0 && <Dowod dowod={f.dowod} />}
          </article>
        ))}
      </div>
    </details>
  );
}

export function CzegoNieWidac({ zastrzezenia }: { zastrzezenia: string[] }) {
  return (
    <details className="sekcja" open>
      <summary>
        Czego ten audyt nie widzi <span className="opis">granice zebranych danych</span>
      </summary>
      <div className="sekcja__ciało">
        {zastrzezenia.length > 0 ? (
          <div className="uwaga">
            <p>Bez tej listy panel sugerowałby pokrycie, którego nie ma:</p>
            <ul>
              {zastrzezenia.map((z) => (
                <li key={z}>{z}</li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="pusto">Snapshot nie zgłosił zastrzeżeń co do zakresu.</p>
        )}
      </div>
    </details>
  );
}
