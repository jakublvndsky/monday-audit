// Punkt wejścia. Jedna decyzja: czy jest sesja.
//
// `GET /api/ja` zwraca 401 bez ciasteczka, więc odpowiedź serwera decyduje
// o tym, co widać — nie stan w przeglądarce, który da się podmienić.

import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type { Ja } from "./api";
import { Brama } from "./Brama";
import { Panel } from "./Panel";
import { api } from "./klient";
import "./marka.css";
import "./aplikacja.css";

function Aplikacja() {
  const [ja, ustawJa] = useState<Ja | null>(null);
  const [sprawdzam, ustawSprawdzam] = useState(true);

  const odswiez = useCallback(async () => {
    try {
      ustawJa(await api.ja());
    } catch {
      ustawJa(null);
    } finally {
      ustawSprawdzam(false);
    }
  }, []);

  useEffect(() => {
    void odswiez();
  }, [odswiez]);

  const wyloguj = useCallback(async () => {
    await api.wyloguj().catch(() => undefined);
    ustawJa(null);
  }, []);

  if (sprawdzam) return <div className="wczytywanie">wczytuję…</div>;
  if (!ja) return <Brama poZalogowaniu={odswiez} />;
  return <Panel ja={ja} poWylogowaniu={wyloguj} />;
}

createRoot(document.getElementById("korzen")!).render(
  <StrictMode>
    <Aplikacja />
  </StrictMode>,
);
