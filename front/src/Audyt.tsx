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
//
// ## Audyt ma DWIE fazy i klucze muszą przeżyć przerwę między nimi
//
// Faza pierwsza zbiera dane, potem klient wybiera zakres, faza druga analizuje.
// Serwer kluczy nie przechowuje (token nie ma kolumny w bazie, D11/D12), więc
// faza druga dostaje je ponownie z tego komponentu — i przez tę jedną przerwę
// muszą zostać w pamięci karty.
//
// To świadomy kompromis, wybrany zamiast trzymania kluczy w pamięci procesu
// serwera: tam stan byłby niewidoczny, przeżywałby wielu klientów i ginął przy
// restarcie. Tu żyje w jednej karcie, ginie z jej zamknięciem i nie dotyka
// dysku. Czyścimy natychmiast po zatwierdzeniu zakresu.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, BladApi } from "./klient";
import type {
  Mozliwosc,
  PodgladKonta,
  StanAudytu,
  WyborZakresu as DaneWyboru,
} from "./api";
import { Kroki } from "./komponenty/Kroki";
import { PodgladZakresu } from "./komponenty/PodgladZakresu";
import { WyborZakresu } from "./komponenty/WyborZakresu";

const ODPYTUJ_MS = 2000;

export function Audyt({ klient, poZakonczeniu }: { klient?: string; poZakonczeniu: () => void }) {
  const [kluczApi, ustawKlucz] = useState("");
  // Klucz Anthropic KLIENTA. Puste = koszt idzie na rachunek CXLABS.
  const [kluczModelu, ustawKluczModelu] = useState("");
  const [zadanieId, ustawZadanie] = useState<string | null>(null);
  const [stan, ustawStan] = useState<StanAudytu | null>(null);
  // PODGLĄD: workspace'y i tablice widziane PRZED zbieraniem (~6 s, 0 USD).
  // Oddzielny stan od `wybor`, bo to inne dane: podgląd nie zna ciszy ani
  // liczby znalezisk, a wybór po zebraniu zna jedno i drugie.
  const [podglad, ustawPodglad] = useState<PodgladKonta | null>(null);
  const [workspaceId, ustawWorkspace] = useState<string | null>(null);
  const [wczytujeTablice, ustawWczytujeTablice] = useState(false);
  // Osobny stan na pierwsze zapytanie. ZGŁOSZONE: „kliknąłem i nic nic nic
  // i nagle się coś pokazało" — bez tego przycisk wygląda na zepsuty.
  const [czytamKonto, ustawCzytamKonto] = useState(false);
  // Kiedy zaczęła się BIEŻĄCA faza — do prognozy „zostało około X minut".
  // Nie `zaczeto` zadania: zbieranie i analiza mają różne tempo, a licznik
  // z całego audytu zawyżałby prognozę analizy o czas zbierania.
  const [odKiedyFaza, ustawOdKiedyFaza] = useState<number | null>(null);
  // Poprzedni stan bez udziału cyklu renderowania — potrzebny tylko do
  // wykrycia zmiany fazy, więc `ref`, nie `state`.
  const stanRef = useRef<StanAudytu | null>(null);
  const [wybor, ustawWybor] = useState<DaneWyboru | null>(null);
  const [zatwierdzam, ustawZatwierdzam] = useState(false);
  const [mozliwosc, ustawMozliwosc] = useState<Mozliwosc | null>(null);
  const [blad, ustawBlad] = useState<string | null>(null);

  // Czy jest co obserwować: zadanie trwa albo dopiero wystartowało (`stan`
  // jeszcze nie przyszedł). Zadanie czekające na zgodę NIE jest obserwowane —
  // nic się w nim nie dzieje, dopóki człowiek nie kliknie.
  const czyObserwowac = stan === null || stan.trwa;

  // Licznik „zbadano N z M" wyłuskany z tekstu etapu.
  //
  // Z tekstu, nie z osobnych pól w API: `zadania.etap` to jedno miejsce,
  // w którym backend mówi, co robi, a dokładanie dwóch kolumn do tabeli dla
  // liczb, które już tam są, dałoby dwa źródła prawdy o tym samym.
  const licznik = (() => {
    const m = /zbadano (\d+) z (\d+)/.exec(stan?.etap ?? "");
    return m ? { zbadanych: Number(m[1]), wszystkich: Number(m[2]) } : null;
  })();

  // Pytanie o możliwość odpalenia PLUS powrót do porzuconego wyboru zakresu.
  //
  // `zadanie_id` żyje tylko w stanie tego komponentu, więc po odświeżeniu karty
  // nie mielibyśmy jak wrócić do zebranych danych: limit monday zużyty, zgoda
  // ważna dwanaście godzin, a klient widzi znowu formularz na klucz. Serwer wie,
  // że takie zadanie stoi — pytamy o to przy montowaniu, bez dodatkowego żądania.
  useEffect(() => {
    let zyje = true;
    api
      .mozliwosc(klient)
      .then(async (m) => {
        if (!zyje) return;
        ustawMozliwosc(m);
        if (!m.zadanie_czekajace) return;
        const dane = await api.pobierzWybor(m.zadanie_czekajace);
        if (!zyje) return;
        ustawZadanie(m.zadanie_czekajace);
        ustawWybor(dane);
      })
      .catch(() => {
        if (zyje) ustawMozliwosc(null);
      });
    return () => {
      zyje = false;
    };
  }, [klient]);

  // Odpytywanie o stan. Zatrzymuje się samo, gdy run się skończy — bez tego
  // przeglądarka pytałaby serwer co dwie sekundy do zamknięcia karty.
  //
  // `trwa: false` ma DWA znaczenia i tu się rozdzielają: skończone (albo
  // zepsute) kontra czekające na decyzję o zakresie. W drugim przypadku
  // pobieramy ekran wyboru, zamiast kończyć.
  //
  // ## ZMIERZONA USTERKA (Kuba, 2026-08-25): ekran zamarzał po „Zatwierdź"
  //
  // „kliknąłem zatwierdź i wróciło mnie do panelu (…) zostało mi na wyborze
  // zakresu, nie przeskoczyło na analizę (…) dopiero jak odświeżyłem, to mam".
  //
  // Przyczyna: przy wejściu w `czeka_na_zgode` pętla robiła `clearInterval`
  // i NIGDY nie startowała ponownie — `useEffect` zależał tylko od `zadanieId`,
  // a ten się nie zmieniał. Analiza leciała w tle 9 minut, a ekran stał
  // zamrożony na kroku „Wybór zakresu".
  //
  // Poprawka: pętla zależy też od `czyObserwowac`, czyli od tego, czy JEST co
  // obserwować. Zatwierdzenie ustawia `stan.trwa = true`, co przelicza tę
  // wartość i wznawia odpytywanie.
  useEffect(() => {
    if (!zadanieId || !czyObserwowac) return;
    let zyje = true;
    const zegar = setInterval(async () => {
      try {
        const nowy = await api.stanAudytu(zadanieId);
        if (!zyje) return;
        if (nowy.trwa) {
          // Zmiana fazy (zbieranie → analiza) zeruje licznik czasu: tempo
          // analizy nie ma nic wspólnego z tempem zbierania.
          //
          // Porównanie na `stanRef`, nie w callbacku `ustawStan`: wołanie
          // settera wewnątrz innego settera to aktualizacja w trakcie
          // renderowania i React zgłasza ostrzeżenie. `ref` trzyma poprzednią
          // wartość bez udziału cyklu renderowania.
          const bylaAnaliza = (stanRef.current?.postep ?? 0) >= 62;
          const jestAnaliza = (nowy.postep ?? 0) >= 62;
          if (stanRef.current === null || bylaAnaliza !== jestAnaliza) {
            ustawOdKiedyFaza(Date.now());
          }
          stanRef.current = nowy;
          ustawStan(nowy);
          return;
        }
        clearInterval(zegar);
        // Ekran wyboru pobieramy PRZED `ustawStan`, i to jest istotne:
        // `ustawStan` przełącza `czyObserwowac` na `false`, React rozmontowuje
        // ten efekt i ustawia `zyje = false`. Gdyby `await` był po tym,
        // `if (zyje)` zablokowałby ustawienie wyboru i ekran zostałby pusty.
        if (nowy.czeka_na_zgode) {
          const dane = await api.pobierzWybor(zadanieId);
          ustawWybor(dane);
        }
        ustawStan(nowy);
        if (nowy.stan === "gotowe") poZakonczeniu();
      } catch (e) {
        clearInterval(zegar);
        if (zyje) {
          ustawBlad(
            e instanceof BladApi ? e.message : "nie udało się odczytać stanu audytu",
          );
        }
      }
    }, ODPYTUJ_MS);
    return () => {
      zyje = false;
      clearInterval(zegar);
    };
  }, [zadanieId, czyObserwowac, poZakonczeniu]);

  // Formularz kluczy NIE odpala już zbierania — pokazuje podgląd konta.
  //
  // Zmiana z 2026-08-25: kliknięcie „Pokaż mój zakres" kosztuje 0,5 s i jedno
  // zapytanie, a klient dostaje listę workspace'ów DO WYBORU. Wcześniej ten
  // przycisk startował trzyminutowe zbieranie, po którym dopiero można było
  // cokolwiek wskazać.
  async function pokazPodglad(zdarzenie: React.FormEvent) {
    zdarzenie.preventDefault();
    ustawBlad(null);
    ustawCzytamKonto(true);
    try {
      const dane = await api.podgladZakresu(kluczApi, undefined, klient);
      ustawPodglad(dane);
    } catch (e) {
      ustawBlad(e instanceof BladApi ? e.message : "nie udało się odczytać konta");
    } finally {
      ustawCzytamKonto(false);
    }
  }

  // Wybór workspace'u dociąga jego tablice (~4,5 s). Osobne wywołanie, bo
  // tablice wszystkich workspace'ów naraz to ZMIERZONE 17 s — a klient patrzy
  // na ekran i czeka.
  const wybierzWorkspace = useCallback(
    async (id: string) => {
      ustawWorkspace(id);
      ustawBlad(null);
      ustawWczytujeTablice(true);
      try {
        const dane = await api.podgladZakresu(kluczApi, id, klient);
        // Lista workspace'ów zostaje z pierwszego kroku: drugie zapytanie jej
        // nie pobiera, żeby nie tracić sekundy i wywołania.
        ustawPodglad((poprzedni) =>
          poprzedni ? { ...dane, workspace_y: poprzedni.workspace_y } : dane,
        );
      } catch (e) {
        ustawBlad(e instanceof BladApi ? e.message : "nie udało się odczytać tablic");
        ustawWorkspace(null);
      } finally {
        ustawWczytujeTablice(false);
      }
    },
    [kluczApi, klient],
  );

  // „Zbierz dane" — tu startuje collector, już w ZAWĘŻONYM zakresie.
  const zbierz = useCallback(
    async (ws: string, boardIds: string[]) => {
      ustawBlad(null);
      ustawZatwierdzam(true);
      try {
        const { zadanie_id } = await api.odpalAudyt(
          kluczApi,
          boardIds.length ? "tablice" : "workspace",
          boardIds.length ? null : ws,
          boardIds,
          klient,
          kluczModelu,
        );
        ustawZadanie(zadanie_id);
        ustawPodglad(null);
      } catch (e) {
        ustawBlad(e instanceof BladApi ? e.message : "nie udało się zacząć zbierania");
      } finally {
        ustawZatwierdzam(false);
      }
    },
    [kluczApi, kluczModelu, klient],
  );

  // „Zbierz nowe dane" — powrót na początek kreatora.
  //
  // Czyścimy CAŁY stan wyboru, nie tylko `wybor`: `zadanieId` musi zniknąć,
  // inaczej polling wskrzesiłby ekran zgody przy najbliższym cyklu, a `podglad`
  // i `workspaceId` niosłyby wybór z porzuconego audytu.
  //
  // Klucz monday ZOSTAJE w stanie — klient dopiero go wpisał, a każąc mu wklejać
  // go po raz drugi karalibyśmy go za zmianę zdania.
  const zacznijOdNowa = useCallback(async () => {
    const porzucane = zadanieId;
    ustawBlad(null);
    ustawWybor(null);
    ustawZadanie(null);
    ustawStan(null);
    ustawWorkspace(null);
    ustawPodglad(null);
    if (!porzucane) return;
    try {
      await api.porzucAudyt(porzucane);
    } catch {
      // Świadomie bez komunikatu: ekran już wrócił na początek, a nieudane
      // porzucenie znaczy tylko, że zadanie zostanie w bazie do wygaśnięcia
      // zgody. Błąd tutaj nie zmienia tego, co klient ma zrobić dalej.
    }
    // Odświeżamy `mozliwosc`, żeby licznik limitu i „zadanie_czekajace" były
    // spójne z tym, co właśnie porzuciliśmy.
    api.mozliwosc(klient).then(ustawMozliwosc).catch(() => undefined);
  }, [zadanieId, klient]);

  const zatwierdz = useCallback(
    async (boardIds: string[]) => {
      if (!zadanieId || !wybor) return;
      ustawBlad(null);
      ustawZatwierdzam(true);
      try {
        // Puste `workspace_ids`: workspace'y są tu tylko nagłówkami grup,
        // a zaznaczenie i tak sprowadza się do listy tablic. Serwer rozwija
        // workspace'y do tablic wyłącznie wtedy, gdy tablic nie podano.
        //
        // Pełne zaznaczenie wysyłamy jako PUSTĄ listę, czyli „całe konto".
        // Wysłanie wszystkich identyfikatorów dałoby ten sam audyt, ale
        // zapisałoby w zadaniu zawężenie, którego klient nie wybrał — i raport
        // twierdziłby, że audyt był zawężony.
        const wszystkie = boardIds.length === wybor.tablice.length;
        await api.zatwierdzZakres(
          zadanieId,
          kluczApi,
          kluczModelu,
          [],
          wszystkie ? [] : boardIds,
        );
        // Klucze przestały być potrzebne — faza druga już je dostała.
        ustawKlucz("");
        ustawKluczModelu("");
        ustawWybor(null);
        ustawStan((poprzedni) =>
          poprzedni ? { ...poprzedni, stan: "analizuje", trwa: true } : poprzedni,
        );
      } catch (e) {
        ustawBlad(e instanceof BladApi ? e.message : "nie udało się zatwierdzić zakresu");
      } finally {
        ustawZatwierdzam(false);
      }
    },
    [zadanieId, wybor, kluczApi, kluczModelu],
  );

  // Kolejność gałęzi = kolejność ekranów w przepływie:
  //   1. podgląd (przed zbieraniem, 6 s)
  //   2. postęp zbierania
  //   3. wybór zakresu z dokładnymi widełkami (po zbieraniu)
  if (podglad && !zadanieId) {
    return (
      <PodgladZakresu
        podglad={podglad}
        workspaceId={workspaceId}
        wybierzWorkspace={wybierzWorkspace}
        wczytujeTablice={wczytujeTablice}
        zbierz={zbierz}
        trwa={zatwierdzam}
        blad={blad ?? ""}
      />
    );
  }

  if (wybor) {
    return (
      <WyborZakresu
        dane={wybor}
        trwa={zatwierdzam}
        blad={blad ?? ""}
        zatwierdz={zatwierdz}
        zacznijOdNowa={zacznijOdNowa}
        // Klucz Anthropic pytamy TUTAJ, nie na starcie: dopiero teraz znamy
        // dokładną kwotę, więc dopiero teraz decyzja o pieniądzach jest
        // świadoma. Klucz monday też może być pusty — po odświeżeniu strony
        // przepada razem ze stanem komponentu, a serwer go nie przechowuje.
        brakujeKluczy={!kluczApi || !kluczModelu}
        kluczApi={kluczApi}
        kluczModelu={kluczModelu}
        ustawKluczApi={ustawKlucz}
        ustawKluczModelu={ustawKluczModelu}
      />
    );
  }

  // Zadanie ISTNIEJE, ale pollingu jeszcze nie było (albo run trwa).
  //
  // Warunek jest na `zadanieId`, nie na `stan.trwa`, i to jest poprawka
  // zgłoszonej usterki: `stan` jest `null` przez pierwsze dwie sekundy po
  // kliknięciu „Zbierz dane", więc klient wracał wtedy na chwilę do formularza
  // z kluczem. Wyglądało to jak utrata wszystkiego, co właśnie wybrał.
  if (zadanieId && (!stan || stan.trwa)) {
    // Dwie fazy mają WŁASNE nagłówki: „zbieram dane" trwa minutę i nic nie
    // kosztuje, „analizuję" trwa kwadrans i płaci za nią klient. Jeden tytuł
    // na oba zaciera tę różnicę.
    const analizuje = (stan?.postep ?? 0) >= 62;
    return (
      <section className="postep-ekran">
        <h2>{analizuje ? "Analizuję konto" : "Zbieram dane z monday"}</h2>
        <Kroki
          postep={stan?.postep ?? 0}
          etap={stan?.etap}
          zbadanych={licznik?.zbadanych}
          wszystkich={licznik?.wszystkich}
          sekundOdStartu={
            odKiedyFaza ? Math.round((Date.now() - odKiedyFaza) / 1000) : undefined
          }
        />
        {/* Bez procentów i bez nazw zapytań. ZGŁOSZONE: „nie chcę, żeby było
            widać, że graphQL coś tam (…) klienta to totalnie nie interesuje".
            Kroki wyżej mówią, gdzie jesteśmy; pasek mówi, że coś się dzieje. */}
        <div className="postep">
          <span style={{ width: `${Math.max(stan?.postep ?? 0, 4)}%` }} />
        </div>
        <p className="meta">
          {analizuje
            ? "Model bada znalezione sygnały. To najdłuższa część — możesz zamknąć tę stronę, audyt leci dalej."
            : "Zaraz pokażemy dokładny koszt i poprosimy o zgodę — nic się jeszcze nie analizuje."}
        </p>
      </section>
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
        {/* Ten ekran BYŁ ślepy: jedynym wyjściem było odświeżenie strony.
            Nieudane zadanie nie liczy się do limitu, więc próba od nowa nic
            nie kosztuje — tylko nie było jak jej podjąć. */}
        <button type="button" className="cx-btn" onClick={zacznijOdNowa}>
          Spróbuj od nowa
        </button>
      </div>
    );
  }

  // PIERWSZY EKRAN: tylko klucz monday, nic więcej.
  //
  // ZGŁOSZONE (Kuba, 2026-08-25): „tam nie może być tyle tych informacji
  // nawalone (…) to okno jest takie rozciągnięte". Było tu pięć akapitów
  // ostrzeżeń w dwóch kolumnach i DWA pola na klucze — a drugi klucz jest
  // potrzebny dopiero na końcu, po zebraniu danych.
  //
  // Teraz: jedno pole, jeden przycisk, ostrzeżenia zwinięte w `<details>`.
  // Kto chce je przeczytać, rozwija; kto zna, klika dalej.
  return (
    <section className="start">
      <h2>Nowy audyt</h2>
      {mozliwosc && !mozliwosc.wolno ? (
        <div className="brak-danych">
          <strong>Teraz nie można uruchomić audytu.</strong> {mozliwosc.powod}
        </div>
      ) : (
        <form onSubmit={pokazPodglad} className="start__formularz">
          <label htmlFor="klucz">Klucz API monday</label>
          <input
            id="klucz"
            type="password"
            value={kluczApi}
            onChange={(e) => ustawKlucz(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            required
            minLength={20}
            placeholder="wklej klucz API"
          />
          <p className="meta">
            monday → Profil → Developers → My Access Tokens
          </p>

          {blad && (
            <p className="brama__blad" role="alert">
              {blad}
            </p>
          )}

          <button type="submit" className="cx-btn" disabled={czytamKonto}>
            {czytamKonto ? "czytam konto…" : "Dalej"}
          </button>
          {/* Pasek nieokreślony, bo nie znamy postępu zapytania — ale klient
              musi widzieć, że coś się dzieje. Cztery sekundy ciszy czyta się
              jak zepsuty przycisk. */}
          {czytamKonto && (
            <div className="postep postep--nieokreslony">
              <span />
            </div>
          )}

          <details className="start__info">
            <summary>o kluczach i kosztach</summary>
            <p>
              <strong>Klucz admina</strong> obejmuje wszystkie workspace\'y, więc
              audyt jest dokładniejszy. Klucz pracownika pokaże tylko to, co on
              widzi — różnicę zapiszemy w raporcie.
            </p>
            <p>
              Klucz API monday <strong>nie jest tylko do czytania</strong> — daje
              pełne uprawnienia konta. Nasz audyt nic nie zmienia (odrzucamy zapisy
              w kodzie), a klucza <strong>nie zapisujemy</strong>: żyje w pamięci
              i ginie razem z audytem. Możesz go unieważnić zaraz po audycie.
            </p>
            <p>
              Analizę wykonuje model Claude i to jedyny płatny element — klucz
              Anthropic podasz na końcu, gdy będziesz już znał dokładną kwotę.
            </p>
          </details>
        </form>
      )}
    </section>
  );
}
