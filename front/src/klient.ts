// Wywołania do API. Jedno miejsce, w którym front rozmawia z serwerem.
//
// `credentials: "same-origin"` przy każdym żądaniu, bo sesja jedzie
// w ciasteczku `HttpOnly` — JS go nie widzi i nie może go dołożyć ręcznie.
// To celowe: skoro nie da się go przeczytać, to XSS nie ma go jak wykraść.
//
// **Nie ma tu funkcji, która przyjmowałaby `client_id` od widoku dla panelu
// klienta.** Serwer bierze go z sesji i ignoruje parametr — patrz D16. Gdyby
// front go tu przekazywał, ktoś kiedyś uznałby, że to on decyduje.

import type { Ja, Mozliwosc, PozycjaKlienta, Pulpit, StanAudytu } from "./api";

export class BladApi extends Error {
  readonly status: number;

  constructor(status: number, komunikat: string) {
    super(komunikat);
    this.status = status;
    this.name = "BladApi";
  }
}

async function pobierz<T>(sciezka: string, opcje: RequestInit = {}): Promise<T> {
  const odpowiedz = await fetch(sciezka, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...opcje,
  });
  if (!odpowiedz.ok) {
    // Komunikat z serwera pokazujemy tylko dla 4xx: 5xx może nieść szczegóły
    // techniczne, których odbiorca nie powinien widzieć.
    let komunikat = "coś nie zadziałało — spróbuj ponownie";
    if (odpowiedz.status < 500) {
      const tresc = await odpowiedz.json().catch(() => null);
      komunikat = tresc?.detail ?? komunikat;
      if (Array.isArray(tresc?.detail)) komunikat = "nieprawidłowe dane w formularzu";
    }
    throw new BladApi(odpowiedz.status, komunikat);
  }
  return (await odpowiedz.json()) as T;
}

export const api = {
  ja: () => pobierz<Ja>("/api/ja"),

  zalogujKlienta: (client_id: string, haslo: string) =>
    pobierz<{ rola: string }>("/api/sesja/klient", {
      method: "POST",
      body: JSON.stringify({ client_id, haslo }),
    }),

  zalogujZespol: (email: string, haslo: string) =>
    pobierz<{ rola: string }>("/api/sesja/zespol", {
      method: "POST",
      body: JSON.stringify({ email, haslo }),
    }),

  wyloguj: () => pobierz<{ wylogowano: boolean }>("/api/sesja/koniec", { method: "POST" }),

  // `klient` działa TYLKO dla sesji zespołu. Dla klienta serwer go ignoruje,
  // więc przekazanie go tu nic nie łamie — ale i nic nie daje.
  //
  // `run` wybiera WERSJĘ audytu i działa dla obu ról — klient ma prawo obejrzeć
  // swój starszy audyt. Serwer sprawdza właściciela runu i oddaje 404 na cudzy
  // (`pulpit.run_nalezy_do`), więc granica nie stoi na tym pliku.
  pulpit: (klient?: string, run?: string) => {
    const parametry = new URLSearchParams();
    if (klient) parametry.set("klient", klient);
    if (run) parametry.set("run", run);
    const zapytanie = parametry.toString();
    return pobierz<Pulpit>(zapytanie ? `/api/pulpit?${zapytanie}` : "/api/pulpit");
  },

  klienci: () => pobierz<PozycjaKlienta[]>("/api/klienci"),

  mozliwosc: (klient?: string) =>
    pobierz<Mozliwosc>(
      klient ? `/api/audyt/mozliwosc?klient=${encodeURIComponent(klient)}` : "/api/audyt/mozliwosc",
    ),

  // Klucz API w CIELE żądania, nigdy w URL-u: adresy trafiają do logów serwera
  // i do historii przeglądarki.
  odpalAudyt: (kluczApi: string, zakres: string, workspaceId: string | null, klient?: string) =>
    pobierz<{ zadanie_id: string }>(
      klient ? `/api/audyt?klient=${encodeURIComponent(klient)}` : "/api/audyt",
      {
        method: "POST",
        body: JSON.stringify({
          klucz_api: kluczApi,
          zakres,
          workspace_id: workspaceId,
        }),
      },
    ),

  stanAudytu: (id: string) => pobierz<StanAudytu>(`/api/audyt/${encodeURIComponent(id)}`),

  // Reset haseł. Nowe hasło wraca w ODPOWIEDZI i widać je raz — nigdzie go nie
  // zapisujemy, tak samo jak klucza API. Klient nie ma tu żadnego wywołania,
  // bo nie ma dla niego endpointu: reset klienta robi zespół (D16 aneks).
  zresetujHasloKlienta: (clientId: string) =>
    pobierz<WynikResetu>("/api/haslo/klienta", {
      method: "POST",
      body: JSON.stringify({ client_id: clientId }),
    }),

  // „Nie pamiętam hasła" — jedyne dwa wywołania hasła BEZ sesji, i tak musi być:
  // kto zgubił hasło, sesji nie ma. Serwer odpowiada IDENTYCZNIE niezależnie od
  // tego, czy konto istnieje, więc front nie ma z czego wnioskować i nie próbuje.
  zapomnianeHaslo: (email: string) =>
    pobierz<{ komunikat: string }>("/api/haslo/zapomniane", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  hasloZLinku: (token: string) =>
    pobierz<WynikResetu>("/api/haslo/z-linku", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  // Nadanie dostępu klientowi, który go nie ma. Panel pokazuje „BRAK KONTA"
  // jako stan, więc musi też dawać drogę do naprawienia go.
  nadajDostep: (clientId: string) =>
    pobierz<WynikResetu>("/api/klient/dostep", {
      method: "POST",
      body: JSON.stringify({ client_id: clientId }),
    }),

  zmienMojeHaslo: (obecneHaslo: string) =>
    pobierz<WynikResetu>("/api/haslo/moje", {
      method: "POST",
      body: JSON.stringify({ obecne_haslo: obecneHaslo }),
    }),
};

/** Odpowiedź resetu. `wazne_sesje` jest tu, bo reset NIE wylogowuje. */
export interface WynikResetu {
  haslo: string;
  wazne_sesje: number;
  godzin_sesji: number;
}
