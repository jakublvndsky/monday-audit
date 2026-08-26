// Panel — powłoka z makiety plus przełączanie klienta bez przeładowania.
//
// Jedna powłoka dla obu ról. Różnicę robi to, CO PRZYSZŁO Z SERWERA: sesja
// klienta nie dostaje kluczy wewnętrznych, więc `pulpit.pinowanie` jest
// `undefined` i sekcja diagnostyczna nie ma z czego się wyrenderować.
//
// Warunki `if (!pulpit.dla_klienta)` w tym pliku **nie są granicą** — są
// wyświetlaniem. Granica stoi w API (D16). Gdyby ktoś usunął te warunki, klient
// zobaczyłby puste sekcje, nie cudze dane.

import { useCallback, useEffect, useState } from "react";
import type { Ja, PozycjaKlienta, Pulpit } from "./api";
import { Audyt } from "./Audyt";
import { MojeKonto } from "./Hasla";
import { Klienci } from "./Klienci";
import { api } from "./klient";
import {
  CzegoNieWidac,
  KafelDuzy,
  kolejnoscSekcji,
  przewinDoSekcji,
  SekcjaMetryk,
  Znaleziska,
} from "./komponenty/Sekcje";
import { Ludzie } from "./komponenty/Ludzie";

const KAFLE_KLUCZOWE = ["agentów AI", "zdominowanych jednym autorem"];

/** Data audytu po polsku, do drop-downu wersji. */
function dataWersji(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso.slice(0, 10)
    : d.toLocaleDateString("pl-PL", { day: "numeric", month: "long", year: "numeric" });
}

function odmianaZnalezisk(ile: number): string {
  if (ile === 1) return "znalezisko";
  if (ile % 10 >= 2 && ile % 10 <= 4 && (ile % 100 < 12 || ile % 100 > 14)) return "znaleziska";
  return "znalezisk";
}

/** Sekcje otwartego audytu pod pozycją klienta. Klik przewija do sekcji.
 *
 * Rysuje to, co przysłał serwer w `pulpit.sekcje` — nie ma tu listy tytułów
 * wpisanej na sztywno. Gdyby collector przestał zbierać automatyzacje, sekcja
 * zniknie z panelu I z nawigacji naraz, bez osobnej poprawki.
 */
function PodnawigacjaSekcji({ pulpit }: { pulpit: Pulpit | null }) {
  if (!pulpit) return null;
  // Kolejność z JEDNEJ funkcji, tej samej co w treści — inaczej sidebar obiecuje
  // jeden porządek, a strona pokazuje inny (usterka zgłoszona 2026-08-11).
  const pozycje = kolejnoscSekcji(pulpit.sekcje, pulpit.findingow);
  return (
    <div className="sidebar__podnawigacja">
      {pozycje.map((p) => (
        <a
          key={p.id}
          href={`#${p.id}`}
          onClick={(e) => {
            e.preventDefault();
            przewinDoSekcji(p.id);
          }}
        >
          {p.etykieta}
          <span className="sidebar__licznik">{p.licznik}</span>
        </a>
      ))}
    </div>
  );
}

export function Panel({ ja, poWylogowaniu }: { ja: Ja; poWylogowaniu: () => void }) {
  const [pulpit, ustawPulpit] = useState<Pulpit | null>(null);
  const [klienci, ustawKlienci] = useState<PozycjaKlienta[]>([]);
  const [wybrany, ustawWybranego] = useState<string | undefined>(undefined);
  // Wybrana WERSJA audytu. `undefined` znaczy „domyślna", czyli najnowsza —
  // serwer to rozstrzyga (`_ostatni_run`), front nie zgaduje.
  const [wersja, ustawWersje] = useState<string | undefined>(undefined);
  // Trzy widoki: panel główny „Klienci", audyt jednego klienta, „Moje konto".
  // Zespół startuje na PANELU GŁÓWNYM — wcześniej wchodził od razu w pierwszego
  // klienta, co przy kilku klientach jest zgadywaniem.
  // „nowy" to OSOBNY widok, nie sekcja w widoku audytu.
  //
  // ZGŁOSZONE (Kuba, 2026-08-25): „nie możemy plątać starych audytów
  // z wyszukaniem nowego, trzeba na to osobny panel". Kreator renderowany nad
  // kaflami poprzedniego audytu mieszał dwie różne rzeczy: przegląd wyniku
  // i zamawianie nowego.
  const [widok, ustawWidok] = useState<"klienci" | "audyt" | "nowy" | "konto">(
    ja.rola === "zespol" ? "klienci" : "audyt",
  );
  const [blad, ustawBlad] = useState<string | null>(null);
  // Zakładka WEWNĄTRZ widoku audytu, nie czwarty widok globalny: „Ludzie" to
  // inne spojrzenie na TEN SAM audyt, więc drop-down wersji i pasek klienta
  // muszą zostać na miejscu. Reset przy zmianie klienta — patrząc na nowego
  // klienta zaczynasz od znalezisk.
  const [zakladka, ustawZakladke] = useState<"audyt" | "ludzie">("audyt");

  const odswiezKlientow = useCallback(() => {
    if (ja.rola !== "zespol") return;
    api.klienci().then(ustawKlienci).catch(() => undefined);
  }, [ja.rola]);

  const wczytaj = useCallback(async () => {
    try {
      ustawPulpit(await api.pulpit(wybrany, wersja));
      ustawBlad(null);
    } catch {
      ustawPulpit(null);
      ustawBlad("nie ma jeszcze audytu tego konta");
    }
  }, [wybrany, wersja]);

  useEffect(() => {
    // Lista klientów tylko dla zespołu. Sesja klienta dostałaby 404, więc nawet
    // nie pytamy — nie chcemy hałasu w logach z żądań, które muszą się nie udać.
    odswiezKlientow();
  }, [odswiezKlientow]);

  // Po zakończonym audycie odświeżamy OBA źródła. ZMIERZONA USTERKA: `poZakonczeniu`
  // odświeżało tylko pulpit, więc licznik znalezisk przy kliencie zostawał stary
  // i panel wyglądał, jakby run nic nie zrobił. Kuba zobaczył „acme 0" po audycie
  // z 27 znaleziskami — dopiero przeładowanie strony pokazywało prawdę.
  const poAudycie = useCallback(async () => {
    await wczytaj();
    odswiezKlientow();
  }, [wczytaj, odswiezKlientow]);

  useEffect(() => {
    void wczytaj();
  }, [wczytaj]);

  // Powrót na „audyt" przy zmianie klienta ALBO wersji. Zostawienie zakładki
  // „Ludzie" po przełączeniu klienta pokazywałoby cudzych ludzi w miejscu, gdzie
  // właśnie zmieniłeś kontekst — a to najgorszy moment na taką pomyłkę.
  useEffect(() => {
    ustawZakladke("audyt");
  }, [wybrany]);

  const kafle = (pulpit?.sekcje ?? [])
    .flatMap((s) => s.metryki)
    .filter((m) => KAFLE_KLUCZOWE.includes(m.nazwa));

  return (
    <>
      <aside className="sidebar">
        <div className="sidebar__marka">
          <img src="/cxlabs-white.png" alt="CXLABS" />
          <small>Audyt monday.com</small>
        </div>
        {/* Nawigacja MOBILNA — w SIDEBARZE, nie w pasku.
            ZMIERZONE: pierwsza wersja siedziała w `.pasek`, który renderuje się
            tylko w widoku audytu. Na telefonie startujesz w panelu głównym, więc
            menu nie istniało w DOM-ie — dokładnie ten sam brak, który miał naprawić.

            Sidebar jest widoczny w KAŻDYM widoku (pod 900 px jako pasek u góry),
            więc to jedyne miejsce, gdzie menu jest zawsze. CSS ukrywa je na
            desktopie, żeby nie dublowało listy klientów. */}
        {ja.rola === "zespol" && (
          <label className="nawigacja-mobilna">
            <span>widok</span>
            <select
              aria-label="Nawigacja"
              value={widok === "audyt" ? (wybrany ?? pulpit?.client_id ?? "") : widok}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "klienci" || v === "konto") {
                  ustawWidok(v);
                  return;
                }
                ustawWersje(undefined);
                ustawWybranego(v);
                ustawWidok("audyt");
              }}
            >
              <option value="klienci">Wszyscy klienci</option>
              {klienci.map((k) => (
                <option key={k.client_id} value={k.client_id}>
                  {k.client_id} — {k.findingow}
                </option>
              ))}
              <option value="konto">Moje konto</option>
            </select>
          </label>
        )}
        <nav>
          {ja.rola === "zespol" ? (
            <>
              {/* „Klienci" jest teraz WEJŚCIEM do panelu głównego, nie napisem.
                  Kuba próbował w to kliknąć i miał rację, że powinno działać. */}
              <a
                href="#"
                className={`poz ${widok === "klienci" ? "aktywny" : ""}`}
                onClick={(e) => {
                  e.preventDefault();
                  ustawWidok("klienci");
                }}
              >
                Klienci<span className="sidebar__licznik">{klienci.length}</span>
              </a>
              {/* Stałe miejsce, z którego zaczyna się nowy audyt. Wcześniej
                  kreator siedział NAD wynikiem poprzedniego audytu, więc nie
                  było jasne, czy patrzysz na stary, czy zamawiasz nowy. */}
              <a
                href="#"
                className={`poz ${widok === "nowy" ? "aktywny" : ""}`}
                onClick={(e) => {
                  e.preventDefault();
                  ustawWidok("nowy");
                }}
              >
                + Nowy audyt
              </a>
              {klienci.map((k) => {
                const aktywny =
                  widok === "audyt" && k.client_id === (wybrany ?? pulpit?.client_id);
                return (
                  <div key={k.client_id}>
                    <a
                      href="#"
                      className={aktywny ? "aktywny" : ""}
                      onClick={(e) => {
                        e.preventDefault();
                        // Zmiana klienta zeruje wersję: run poprzedniego klienta
                        // nie należy do nowego, więc serwer oddałby 404. Panel
                        // pokazałby wtedy „nie ma audytu" przy kliencie, który
                        // audyt ma.
                        ustawWersje(undefined);
                        ustawWybranego(k.client_id);
                        ustawWidok("audyt");
                      }}
                    >
                      {k.client_id}
                      <span className="sidebar__licznik">{k.findingow}</span>
                    </a>
                    {aktywny && <PodnawigacjaSekcji pulpit={pulpit} />}
                  </div>
                );
              })}
            </>
          ) : (
            <>
              <a
                href="#"
                className={`poz ${widok === "audyt" ? "aktywny" : ""}`}
                onClick={(e) => {
                  e.preventDefault();
                  ustawWidok("audyt");
                }}
              >
                Twój audyt
              </a>
              {widok === "audyt" && <PodnawigacjaSekcji pulpit={pulpit} />}
              <a
                href="#"
                className={`poz ${widok === "nowy" ? "aktywny" : ""}`}
                onClick={(e) => {
                  e.preventDefault();
                  ustawWidok("nowy");
                }}
              >
                + Nowy audyt
              </a>
            </>
          )}
        </nav>
        {ja.rola === "zespol" && (
          <div className="sidebar__admin">
            <a
              href="#"
              className={widok === "konto" ? "aktywny" : ""}
              onClick={(e) => {
                e.preventDefault();
                ustawWidok("konto");
              }}
            >
              Moje konto
            </a>
          </div>
        )}
        <div className="sidebar__stopka">
          {pulpit && (
            <>
              <p>
                run <code>{pulpit.run_id}</code>
              </p>
              <p>dane zebrane {pulpit.run_at.slice(0, 10)}</p>
            </>
          )}
          <button type="button" className="sidebar__wyloguj" onClick={poWylogowaniu}>
            Wyloguj
          </button>
        </div>
      </aside>

      <main className="tresc">
        {widok === "klienci" ? (
          <Klienci
            klienci={klienci}
            odswiez={odswiezKlientow}
            poWybraniu={(clientId) => {
              ustawWersje(undefined);
              ustawWybranego(clientId);
              ustawWidok("audyt");
            }}
          />
        ) : widok === "konto" ? (
          <MojeKonto email={ja.email ?? ""} />
        ) : widok === "nowy" ? (
          /* Czysty ekran kreatora. Bez kafli, bez znalezisk, bez drop-downu
             wersji — te należą do PRZEGLĄDANIA audytu, nie do zamawiania. */
          <div className="strona strona--kreator">
            <p className="eyebrow">Audyt konta monday.com</p>
            <Audyt
              klient={ja.rola === "zespol" ? wybrany : undefined}
              poZakonczeniu={() => {
                poAudycie();
                ustawWidok("audyt");
              }}
            />
          </div>
        ) : (
        <>
        <div className="pasek">
          <span className="okruszki">
            {ja.rola === "zespol" && "Klienci / "}
            {/* `wybrany` PRZED `ja.client_id`: dla zespołu to drugie jest `null`,
                a dla klienta `wybrany` jest `undefined` — jeden łańcuch obsługuje
                obie role. Bez `wybrany` panel przy kliencie bez audytu pokazywał
                „—" i „Audyty", czyli gubił, którego klienta oglądasz. */}
            <b>{pulpit?.client_id ?? wybrany ?? ja.client_id ?? "—"}</b>
          </span>
          <span className="pasek__prawo">
            {/* Drop-down odpowiada na inne pytanie niż sidebar: tam „którego
                klienta", tu „z kiedy". Pokazujemy go dopiero od dwóch wersji —
                przy jednym audycie kontrolka byłaby martwa. Dostają go OBIE role,
                bo klient też ma prawo obejrzeć swój starszy audyt; granicę trzyma
                serwer, sprawdzając właściciela runu. */}
            {(pulpit?.wersje.length ?? 0) > 1 && (
              <label className="wybor-wersji">
                {/* „analiza z", nie „wersja audytu" — bo `PozycjaRunu.run_at` to
                    `runy.started_at`, czyli kiedy agent BADAŁ dane, a nagłówek
                    strony pokazuje `Pulpit.run_at`, czyli kiedy dane ZEBRANO. */}
                <span>analiza z</span>
                <select
                  aria-label="Wersja audytu — data analizy"
                  value={wersja ?? pulpit?.run_id ?? ""}
                  onChange={(e) => ustawWersje(e.target.value)}
                >
                  {pulpit?.wersje.map((w) => (
                    <option key={w.run_id} value={w.run_id}>
                      {dataWersji(w.run_at)} — {w.findingow} {odmianaZnalezisk(w.findingow)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <span
              className={`plomba-odbiorcy plomba-odbiorcy--${
                ja.rola === "zespol" ? "wewn" : "klient"
              }`}
            >
              {ja.rola === "zespol" ? "wewnętrzny" : "Twoje konto"}
            </span>
          </span>
        </div>

        <div className="strona">
          <p className="eyebrow">Audyt konta monday.com</p>
          {/* DWA różne identyfikatory, oba prawdziwe — i to jest sedno pomyłki,
              którą zgłosił Kuba („jestem na acme, a widzę CXLABS").

              `client_id` to NASZ identyfikator klienta w bazie, a `nazwa_konta`
              to nazwa konta W MONDAY ze snapshotu. Audyt klienta `acme` poszedł
              kluczem na koncie monday „CXLABS" (inny workspace), więc panel mówił
              prawdę — tylko pokazywał jedną nazwę bez podpisu.

              Zespół widzi identyfikator jako tytuł (spójnie z sidebarem), a nazwę
              monday w linii pod nim. Klient widzi swoją nazwę: on jej nie zna jako
              „acme", zna ją jako firmę. */}
          <h1>
            {ja.rola === "zespol"
              ? (pulpit?.client_id ?? wybrany ?? "Audyty")
              : (pulpit?.nazwa_konta ?? ja.client_id ?? "Twój audyt")}
          </h1>
          {pulpit && (
            <p className="strona__adres">
              {ja.rola === "zespol" && (
                <>
                  konto monday: <b>{pulpit.nazwa_konta}</b> ·{" "}
                </>
              )}
              {pulpit.zakres} · plan {pulpit.plan_tier} · dane zebrane{" "}
              {pulpit.run_at.slice(0, 10)}
            </p>
          )}

          {/* ZAKŁADKI. „Ludzie" odpowiada na pytanie, na które znaleziska nie
              odpowiadają: kto z czego korzysta, jak i kiedy. Dane z collectora,
              więc zakładka istnieje nawet gdy agent nic nie znalazł — ale bez
              pulpitu nie ma czego pokazać, stąd warunek. */}
          {pulpit && (
            <nav className="zakladki" aria-label="Widok audytu">
              <button
                type="button"
                className={zakladka === "audyt" ? "aktywny" : ""}
                onClick={() => ustawZakladke("audyt")}
              >
                Znaleziska i metryki
                <small>{pulpit.findingow}</small>
              </button>
              <button
                type="button"
                className={zakladka === "ludzie" ? "aktywny" : ""}
                onClick={() => ustawZakladke("ludzie")}
              >
                Ludzie
                {pulpit.ludzie && <small>{pulpit.ludzie.osoby.length}</small>}
              </button>
            </nav>
          )}

          {/* Sekcji „Dostęp klienta" TU NIE MA i to jest celowe.
              Reset hasła klienta żyje w „Moje konto" → „Dostępy klientów", razem
              z dodawaniem klienta i widokiem, kto może się zalogować. Trzymanie
              tego samego przycisku w dwóch miejscach było dublowaniem — dokładnie
              tym, co Kuba zakwestionował przy „Moje hasło". Widok audytu pokazuje
              audyt. */}
          {blad && (
            <div className="brak-danych">
              <p>
                <strong>Ten klient nie ma jeszcze audytu.</strong> Ma dostęp do
                panelu, ale nikt jeszcze nie uruchomił zbierania danych.
              </p>
              {/* „zrób to wyżej" wskazywało na formularz, który przeniósł się
                  do osobnego widoku — martwa instrukcja jest gorsza niż żadna. */}
              <p className="meta">
                Poproś go, żeby wszedł na swój panel i wkleił klucz API monday,
                albo zamów audyt jego kluczem.
              </p>
              <button
                type="button"
                className="cx-btn"
                onClick={() => ustawWidok("nowy")}
              >
                + Nowy audyt
              </button>
            </div>
          )}

          {pulpit && zakladka === "audyt" && (
            <>
              <div className="kafle-gorne">
                <KafelDuzy
                  podpis="Znaleziska"
                  wartosc={pulpit.findingow}
                  pod={Object.entries(pulpit.po_wagach)
                    .map(([w, i]) => `${i} ${w}`)
                    .join(" · ")}
                />
                {pulpit.ma_kwoty ? (
                  <KafelDuzy
                    podpis="Możliwa oszczędność roczna"
                    wartosc={pulpit.suma_kwot.toLocaleString("pl-PL", {
                      maximumFractionDigits: 0,
                    })}
                    pod="PLN na licencjach monday"
                    akcent
                  />
                ) : (
                  <KafelDuzy podpis="Oszczędność" wartosc="—" pod="nie podano stawki licencji" />
                )}
                {kafle.map((m) => (
                  <KafelDuzy
                    key={m.nazwa}
                    podpis={m.nazwa}
                    wartosc={m.wartosc}
                    pod={m.udzial !== null ? `${m.udzial}% z ${m.z}` : (m.opis ?? undefined)}
                  />
                ))}
                {/* `koszt_usd` NIE ISTNIEJE w payloadzie klienta (D16), więc
                    ten kafel nie ma jak się pojawić — niezależnie od warunku. */}
                {pulpit.koszt_usd != null && (
                  <KafelDuzy
                    podpis={
                      pulpit.rozliczenie === "subskrypcja"
                        ? "Koszt (szacunek)"
                        : pulpit.rozliczenie === "klucz_klienta"
                          ? "Koszt (rachunek klienta)"
                          : "Koszt audytu"
                    }
                    wartosc={pulpit.koszt_usd.toFixed(2)}
                    /* Ta sama liczba znaczy TRZY różne rzeczy zależnie od trybu
                       rozliczenia: nasz wydatek, wycenę teoretyczną, albo wydatek
                       KLIENTA. Bez tego podpisu wyceniałoby się usługę po kwocie,
                       za którą nikt nie zapłacił — albo zapłacił ktoś inny. */
                    pod={
                      pulpit.rozliczenie === "subskrypcja"
                        ? "USD — run szedł z subskrypcji, to nie faktura"
                        : pulpit.rozliczenie === "klucz_klienta"
                          ? "USD — run szedł z klucza KLIENTA, nie obciąża CXLABS"
                          : pulpit.rozliczenie === "klucz"
                            ? "USD za analizę, z klucza API"
                            : "USD za analizę"
                    }
                  />
                )}
              </div>

              {!pulpit.ma_porownanie && (
                <p className="brak-danych">
                  To pierwszy audyt tego konta, więc nie ma z czym porównywać. Przy
                  następnym panel pokaże różnicę.
                </p>
              )}

              {/* TA SAMA funkcja co w sidebarze — jedno źródło kolejności.
                  Wcześniej sidebar stawiał Znaleziska pierwsze, a tu renderowały
                  się ostatnie: dwa porządki, żaden nie pilnował drugiego. */}
              {kolejnoscSekcji(pulpit.sekcje, pulpit.findingow).map((poz) =>
                poz.sekcja ? (
                  <SekcjaMetryk key={poz.id} s={poz.sekcja} />
                ) : (
                  <Znaleziska key={poz.id} findingi={pulpit.findingi} />
                ),
              )}
              <CzegoNieWidac zastrzezenia={pulpit.zastrzezenia} />

              {pulpit.pinowanie && Object.keys(pulpit.pinowanie).length > 0 && (
                <details className="sekcja">
                  <summary>
                    Diagnostyka runu <span className="opis">nie dla klienta</span>
                  </summary>
                  <div className="sekcja__ciało">
                    <div className="przewijane">
                      <table className="tabela-lista">
                        <tbody>
                          {Object.entries(pulpit.pinowanie).map(([k, w]) => (
                            <tr key={k}>
                              <th>{k}</th>
                              <td>{w === null ? "—" : String(w)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {(pulpit.hipotezy_odrzucone?.length ?? 0) > 0 && (
                      <>
                        <h3>Hipotezy obalone przez agenta</h3>
                        <div className="przewijane">
                          <table className="tabela-lista">
                            <tbody>
                              {pulpit.hipotezy_odrzucone?.map((h, i) => (
                                <tr key={i}>
                                  <td>{String(h.klasa_id)}</td>
                                  <td>{String(h.powod)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </>
                    )}
                  </div>
                </details>
              )}
            </>
          )}

          {/* Zakładka „Ludzie". `pulpit.ludzie` może być `null` dla runów sprzed
              tej zmiany — wtedy mówimy to wprost, zamiast pokazywać pustą tabelę
              wyglądającą jak „nikt nie pracuje". */}
          {pulpit && zakladka === "ludzie" && (
            pulpit.ludzie ? (
              <Ludzie dane={pulpit.ludzie} />
            ) : (
              <div className="brak-danych">
                <p>
                  <strong>Ten audyt nie ma danych o aktywności osób.</strong>{" "}
                  Sekcja `per_uzytkownik` weszła do snapshotu w etapie 4 — starsze
                  audyty jej nie mają.
                </p>
                <p className="meta">
                  Wybierz nowszą wersję audytu w drop-downie „analiza z", albo
                  uruchom zbieranie danych ponownie.
                </p>
              </div>
            )
          )}

          <footer>
            Panel zbudowany z zamrożonego snapshotu. Każde znalezisko ma dowód
            wskazujący na konkretny fakt. CXLABS.
          </footer>
        </div>
        </>
        )}
      </main>
    </>
  );
}
