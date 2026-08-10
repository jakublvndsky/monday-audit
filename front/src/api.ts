// PLIK GENEROWANY — nie edytuj ręcznie.
//
// Źródło: dataclassy w `src/monday_audit/pulpit.py`, przez `pulpit.do_json()`.
// Regeneracja:  uv run python -m monday_audit.generuj_typy
//
// Ręczne typy rozjechałyby się z Pythonem przy pierwszej zmianie pola, i to
// cicho — `tsc` nie widzi Pythona. Test `--sprawdz` zatrzymuje CI, gdy ten plik
// jest nieaktualny.
//
// UWAGA na pola opcjonalne: w wariancie KLIENTOWYM `do_json()` USUWA klucze
// wewnętrzne ze struktury (nie zeruje ich), dlatego są tu oznaczone `?`.
// To nie luźność typu, a odwzorowanie granicy bezpieczeństwa (D16).


export interface Metryka {
  nazwa: string;
  wartosc: number;
  z: number | null;
  uwaga: boolean;
  opis: string | null;
  // z `@property` — udział wyliczony, `null` gdy brak mianownika
  udzial: number | null;
}

export interface Sekcja {
  tytul: string;
  opis: string;
  metryki: Metryka[];
}

export interface Finding {
  klasa_id: string;
  nazwa: string;
  waga: string;
  wysilek: string;
  pewnosc: string;
  kwota_pln: number | null;
  opis: string;
  rekomendacja: string;
  dowod: Record<string, unknown>;
  trop: string | null;
}

export interface PozycjaRunu {
  run_id: string;
  run_at: string;
  findingow: number;
}

export interface Pulpit {
  odbiorca: string;
  client_id: string;
  nazwa_konta: string;
  run_id: string;
  run_at: string;
  zakres: string;
  plan_tier: string;
  findingi: Finding[];
  po_wagach: Record<string, number>;
  suma_kwot: number;
  sekcje: Sekcja[];
  zastrzezenia: string[];
  wersje: PozycjaRunu[];
  poprzedni_run_at: string | null;
  hipotezy_odrzucone?: Record<string, unknown>[];
  findingi_odrzucone?: Record<string, unknown>[];
  pinowanie?: Record<string, unknown>;
  koszt_usd?: number | null;
  nieznane_hashe?: number;
  // dokładane przez `do_json()` z `@property` — nie są polami dataclassy
  findingow: number;
  ma_kwoty: boolean;
  ma_porownanie: boolean;
  dla_klienta: boolean;
}

export interface PozycjaKlienta {
  client_id: string;
  audytow: number;
  ostatni_run_id: string | null;
  ostatni_run_at: string | null;
  findingow: number;
  suma_kwot: number;
  ma_konto: boolean;
}

export interface Ja {
  rola: "klient" | "zespol";
  client_id: string | null;
  email: string | null;
}

export interface StanAudytu {
  id: string;
  stan: string;
  etap: string | null;
  postep: number | null;
  run_id: string | null;
  blad: string | null;
  trwa: boolean;
}

export interface Mozliwosc {
  wolno: boolean;
  powod: string;
  client_id: string;
}
