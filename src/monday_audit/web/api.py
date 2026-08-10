"""Endpointy aplikacji webowej (D16).

## Jedna reguła, z której wynika całe bezpieczeństwo tego pliku

> **Odbiorcę i klienta wyznacza SESJA, nigdy parametr zapytania.**

Parametr przychodzi od przeglądarki, a przeglądarka należy do odbiorcy. Endpoint
czytający `client_id` z zapytania daje dostęp do cudzych danych przez podmianę
jednego słowa w URL-u — i żaden test wyglądu tego nie wyłapie.

Dlatego `GET /api/pulpit` **nie ma** parametrów `client_id` ani `odbiorca`.
Bierze je z `Sesja`, a `pulpit.do_json()` usuwa klucze wewnętrzne ze struktury
dla odbiorcy klientowego. To zaostrzenie zasady z 3.12 („filtrowanie w SQL,
nie w szablonie") — tu szablon jest u odbiorcy, więc granica musi być wcześniej.

## 404, nie 403

Sesja klienta pytająca o cudzego klienta albo o endpoint zespołowy dostaje
**404**. 403 potwierdziłoby, że taki zasób istnieje — a lista klientów CXLABS
to informacja handlowa.

## Klucz API klienta

Przychodzi w ciele `POST /api/audyt`, przez HTTPS, **nigdy w URL-u** (URL-e
lądują w logach serwera i w historii przeglądarki). Idzie prosto do zadania
w tle jako argument i nie jest zapisywany nigdzie — patrz `zadania.py` i D11.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    BackgroundTasks,
    Cookie,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.dostep import (
    GODZIN_SESJI,
    MINUT_TOKENU_RESETU,
    ROLA_KLIENT,
    DostepError,
    Sesja,
    WynikResetu,
    konto_klienta,
    poproszono_o_reset,
    utworz_konto,
    wczytaj_sesje,
    wygeneruj_haslo,
    wyloguj,
    zaloguj,
    zresetuj_haslo,
    zuzyj_token_resetu,
)
from monday_audit.konfiguracja import Ustawienia, UstawieniaPoczty, wczytaj
from monday_audit.poczta import PocztaError, wyslij_link_resetu
from monday_audit.pulpit import do_json, run_nalezy_do, zbuduj_liste_klientow, zbuduj_pulpit
from monday_audit.raport import ODBIORCA_KLIENT, ODBIORCA_WEWNETRZNY, RaportError
from monday_audit.rubryka import Rubryka, wczytaj_rubryke
from monday_audit.web.run import uruchom_audyt_w_tle
from monday_audit.zadania import ZadanieError, utworz_zadanie, wczytaj_stan, wolno_odpalic

logger = logging.getLogger(__name__)

CIASTECZKO = "audyt_sesja"

# Katalog z zbudowanym frontem. Gdy go nie ma (świeże repo bez `npm run build`),
# API działa dalej — nie chcemy, żeby brak frontu blokował testy endpointów.
KATALOG_FRONTU = Path(__file__).parent.parent.parent.parent / "front" / "dist"


class DaneKlienta(BaseModel):
    haslo: str = Field(min_length=1, max_length=200)
    client_id: str = Field(min_length=1, max_length=100)


class DaneZespolu(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    haslo: str = Field(min_length=1, max_length=200)


class DaneZapomnianego(BaseModel):
    """„Nie pamiętam hasła". Sam e-mail — reszta dzieje się przez skrzynkę."""

    email: str = Field(min_length=3, max_length=200)


class DaneTokenu(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class DaneZmianyHasla(BaseModel):
    """Zmiana WŁASNEGO hasła. Nowego się nie podaje — system je generuje.

    Nie ma tu `konto_id`: konto bierzemy z sesji. Gdyby było w ciele, osoba
    z zespołu zmieniałaby hasło komukolwiek.
    """

    obecne_haslo: str = Field(min_length=1, max_length=200)


class DaneResetuKlienta(BaseModel):
    """Reset hasła ISTNIEJĄCEGO klienta — bez wzorca.

    Wzorzec byłby tu szkodliwy: konta założone wcześniej (choćby z CLI) mogą mieć
    identyfikatory, których dzisiejsza reguła nie przepuszcza. Odmowa resetu dla
    konta, które istnieje i działa, zamieniłaby walidację w blokadę.
    """

    client_id: str = Field(min_length=1, max_length=100)


# Identyfikator NOWEGO klienta. `client_id` trafia do adresów (`?klient=`), do nazw
# plików raportu i do `runy.client_id`, więc nie może być dowolnym napisem —
# „Kancelaria Ekologiczna sp. z o.o." wygląda na dobry pomysł, dopóki nie trafi
# do URL-a albo do nazwy pliku.
#
# Reguła: małe litery, cyfry i łączniki; zaczyna się od znaku alfanumerycznego.
# Bez podkreśleń i kropek, bo kropka w nazwie pliku myli się z rozszerzeniem.
WZORZEC_CLIENT_ID = r"^[a-z0-9][a-z0-9-]{1,49}$"
OPIS_CLIENT_ID = (
    "identyfikator: małe litery, cyfry i łączniki, 2–50 znaków, "
    "zaczyna się od litery lub cyfry (np. kancelaria-eko)"
)


class DaneNowegoKlienta(BaseModel):
    """Nowy klient — TU wzorzec obowiązuje, bo identyfikator powstaje teraz."""

    client_id: str = Field(pattern=WZORZEC_CLIENT_ID, description=OPIS_CLIENT_ID)


def _pelne(ustawienia: UstawieniaPoczty) -> Ustawienia:
    """Bez `baza` fabryka potrzebuje `monday_audit_db`, czyli PEŁNYCH ustawień.

    Wywołanie `zbuduj_aplikacje(ustawienia=...)` bez `baza` istnieje tylko
    w ścieżce produkcyjnej (`cli_web`), gdzie zawsze przychodzą `Ustawienia`.
    Ten rzut zamienia niemożliwy przypadek w jasny błąd, zamiast w `AttributeError`
    trzy linijki dalej.
    """
    if not isinstance(ustawienia, Ustawienia):
        raise TypeError("bez `baza` potrzebne są pełne `Ustawienia` (z `monday_audit_db`)")
    return ustawienia


def _baza_adresu(zadanie: Request, ustawienia: UstawieniaPoczty | None = None) -> str:
    """Adres, pod który ma prowadzić link resetu.

    ## ZMIERZONA USTERKA, którą to naprawia

    `ADRES_PUBLICZNY` miało stałą domyślną `http://127.0.0.1:8000`, a
    `--serwuj --port 8010` jej nie dotykało. Kuba dostał link na `:8000`, kliknął
    i przeglądarka nie miała z czym się połączyć — serwer stał na `:8010`.

    Dwa źródła prawdy o jednym adresie: port serwera i port w linku. Klasyczna
    usterka „każdy element działa osobno", której nie widzi żaden test endpointu,
    bo `TestClient` nie ma pojęcia o porcie prawdziwego procesu.

    ## Poprawka: adres z ŻĄDANIA, nie z konfiguracji

    Domyślnie bierzemy host i port z żądania, czyli z tego, w co odbiorca właśnie
    kliknął. Wtedy nie mogą się rozjechać — jest jedno źródło.

    `ADRES_PUBLICZNY` **nadal wygrywa**, gdy jest ustawiony, i jest wtedy
    potrzebny: za odwrotnym proxy (Caddy, etap 5) żądanie widzi `127.0.0.1:8000`,
    a odbiorca `https://audyt.cxlabs.digital`. Ale to musi być decyzja, nie
    wartość domyślna, która cicho psuje link.
    """
    jawny = (ustawienia.adres_publiczny if ustawienia else "").strip()
    if jawny:
        return jawny.rstrip("/")
    # `base_url` niesie schemat, host i port tego żądania — łącznie z portem
    # niestandardowym, o który się potknęliśmy.
    return str(zadanie.base_url).rstrip("/")


def _odpowiedz_resetu(wynik: WynikResetu) -> dict[str, Any]:
    """Hasło wraca RAZ, w ciele odpowiedzi — nie idzie do logu.

    `wazne_sesje` i `godzin_sesji` jadą razem z hasłem, bo reset **nie
    wylogowuje**: kto jest zalogowany, pracuje dalej. Interfejs musi to napisać,
    inaczej ktoś kliknie „reset" i uzna, że odciął dostęp.
    """
    return {
        "haslo": wynik.haslo,
        "wazne_sesje": wynik.wazne_sesje,
        "godzin_sesji": GODZIN_SESJI,
    }


class DaneAudytu(BaseModel):
    """Klucz API w CIELE żądania, nigdy w URL-u.

    `min_length` to nie walidacja formatu, a odsianie pustego pola. Formatu
    nie sprawdzamy: monday zmieni postać tokenu, a my nie chcemy odrzucać
    poprawnego klucza, bo nasz wzorzec się zestarzał.
    """

    klucz_api: str = Field(min_length=20, max_length=4000)
    zakres: str = Field(default="cale_konto", pattern="^(cale_konto|workspace)$")
    workspace_id: str | None = Field(default=None, max_length=50)


# ── zależności na poziomie MODUŁU ────────────────────────────────────────
#
# NIE wewnątrz fabryki, i to nie kwestia stylu. `from __future__ import
# annotations` zamienia wszystkie adnotacje w NAPISY, a FastAPI rozwiązuje je
# w przestrzeni nazw MODUŁU. Aliasy zdefiniowane lokalnie były tam niewidoczne,
# więc `sesja: ZSesji` stawało się zwykłym parametrem zapytania i endpoint
# zwracał 422 „Field required" zamiast 401. Wyszło w testach granic.
#
# Ścieżka bazy jedzie przez `app.state`, bo to jedyny sposób, żeby zależność
# na poziomie modułu wiedziała, którą bazę otworzyć.


def polaczenie(zadanie: Request) -> Iterator[sqlite3.Connection]:
    """Połączenie na jedno żądanie, otwierane W TYM SAMYM wątku, co endpoint.

    ZMIERZONA USTERKA. FastAPI wykonuje synchroniczne endpointy (`def`, nie
    `async def`) w PULI WĄTKÓW, a generator zależności jest uruchamiany w innym
    wątku niż ciało endpointu. sqlite3 tego nie wybacza:

        sqlite3.ProgrammingError: SQLite objects created in a thread can only
        be used in that same thread.

    Objawiało się jako 500 przy DWÓCH RÓWNOLEGŁYCH żądaniach — front pyta
    jednocześnie `/api/pulpit`, `/api/klienci` i `/api/audyt/mozliwosc`, więc
    trafiało prawie za każdym razem. `TestClient` tego NIE POKAZAŁ, bo obsługuje
    żądania po kolei w jednym wątku: 20 testów granic było zielonych, a panel
    w przeglądarce mówił „nie ma jeszcze audytu tego konta".

    Poprawka: `check_same_thread=False` plus jedno połączenie na żądanie.
    Bezpieczne, bo połączenia NIE dzielimy między żądaniami — każde ma własne
    i zamyka je w `finally`. Współbieżny zapis chroni WAL i blokady SQLite.
    """
    con = polacz(zadanie.app.state.baza, wielowatkowe=True)
    try:
        yield con
    finally:
        con.close()


def sesja_z_ciasteczka(
    con: Annotated[sqlite3.Connection, Depends(polaczenie)],
    audyt_sesja: Annotated[str | None, Cookie()] = None,
) -> Sesja:
    """Jedyne wejście do tożsamości. Brak sesji to 401, nie „gość"."""
    znaleziona = wczytaj_sesje(con, audyt_sesja)
    if znaleziona is None:
        raise HTTPException(status_code=401, detail="brak sesji")
    return znaleziona


Polaczenie = Annotated[sqlite3.Connection, Depends(polaczenie)]
ZSesji = Annotated[Sesja, Depends(sesja_z_ciasteczka)]


def zbuduj_aplikacje(
    *, baza: Path | None = None, ustawienia: UstawieniaPoczty | None = None
) -> FastAPI:
    """Fabryka aplikacji. Baza wstrzykiwana, żeby testy nie tykały produkcyjnej.

    Gdy `baza` jest podana, **konfiguracji nie czytamy wcale**. Powód jest
    praktyczny: `wczytaj()` wymaga `MONDAY_TOKEN` i `SOL_PSEUDONIMIZACJI`, a testy
    granic sesji nie potrzebują ani jednego sekretu — pytają tylko, kto co widzi.
    Wymuszanie ich tam znaczyłoby, że test bezpieczeństwa nie da się uruchomić
    bez produkcyjnych poświadczeń, a to najgorszy możliwy powód, żeby go pominąć.

    Sekrety są potrzebne dopiero przy ODPALANIU audytu — i tam `web/run.py`
    czyta je sam, już w trakcie zadania.
    """
    if baza is not None:
        sciezka_bazy = baza.absolute()
        # Testy granic wołają fabrykę BEZ sekretów (patrz docstring), a poczta
        # potrzebuje tylko własnych pól. `UstawieniaPoczty` daje je bez wymagania
        # `MONDAY_TOKEN` — więc „nie pamiętam hasła" da się testować bez
        # produkcyjnych poświadczeń, i to jest cały powód tej gałęzi.
        konf_poczty: Ustawienia | UstawieniaPoczty = ustawienia or UstawieniaPoczty()
    else:
        konf: Ustawienia = wczytaj() if ustawienia is None else _pelne(ustawienia)
        sciezka_bazy = konf.monday_audit_db.absolute()
        konf_poczty = konf
    ustawienia_aplikacji = konf_poczty
    rubryka: Rubryka = wczytaj_rubryke()

    aplikacja = FastAPI(title="monday.com Account Audit", docs_url=None, redoc_url=None)

    @aplikacja.exception_handler(RequestValidationError)
    def _blad_walidacji(_zadanie: Request, blad: RequestValidationError) -> JSONResponse:
        """Zamienia surowy 422 pydantica na zdanie dla człowieka.

        Domyślna odpowiedź to lista obiektów z `loc`/`type`/`ctx`, której front
        nie umie pokazać — `klient.ts` spłaszcza ją do „nieprawidłowe dane
        w formularzu", czyli komunikatu, który nie mówi, co poprawić.

        Tłumaczymy TYLKO to, co wiemy nazwać (dziś wzorzec `client_id`); resztę
        oddajemy bez zmian, żeby nie ukryć błędu, którego nie przewidzieliśmy.
        """
        for szczegol in blad.errors():
            if szczegol.get("type") == "string_pattern_mismatch" and "client_id" in [
                str(cz) for cz in szczegol.get("loc", ())
            ]:
                return JSONResponse(status_code=422, content={"detail": OPIS_CLIENT_ID})
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(blad.errors())})

    aplikacja.state.baza = sciezka_bazy
    aplikacja.state.rubryka = rubryka

    def _ustaw_ciasteczko(odpowiedz: Response, token: str) -> None:
        odpowiedz.set_cookie(
            CIASTECZKO,
            token,
            max_age=GODZIN_SESJI * 3600,
            httponly=True,  # JS go nie odczyta, więc XSS nie kradnie sesji
            secure=True,  # tylko HTTPS
            samesite="lax",  # blokuje CSRF z obcych stron
            path="/",
        )

    # ── sesje ────────────────────────────────────────────────────────

    @aplikacja.post("/api/sesja/klient")
    def sesja_klienta(
        dane: DaneKlienta, zadanie: Request, odpowiedz: Response, con: Polaczenie
    ) -> dict[str, str]:
        try:
            token = zaloguj(
                con,
                haslo=dane.haslo,
                client_id=dane.client_id,
                ip=zadanie.client.host if zadanie.client else None,
            )
        except DostepError as blad:
            # Ten sam komunikat dla „nie ma konta" i „złe hasło": rozróżnienie
            # mówiłoby, które identyfikatory klientów są prawdziwe.
            raise HTTPException(status_code=401, detail=str(blad)) from None
        _ustaw_ciasteczko(odpowiedz, token)
        return {"rola": "klient"}

    @aplikacja.post("/api/sesja/zespol")
    def sesja_zespolu(
        dane: DaneZespolu, zadanie: Request, odpowiedz: Response, con: Polaczenie
    ) -> dict[str, str]:
        try:
            token = zaloguj(
                con,
                haslo=dane.haslo,
                email=dane.email,
                ip=zadanie.client.host if zadanie.client else None,
            )
        except DostepError as blad:
            raise HTTPException(status_code=401, detail=str(blad)) from None
        _ustaw_ciasteczko(odpowiedz, token)
        return {"rola": "zespol"}

    @aplikacja.post("/api/sesja/koniec")
    def koniec_sesji(
        odpowiedz: Response,
        con: Polaczenie,
        audyt_sesja: Annotated[str | None, Cookie()] = None,
    ) -> dict[str, bool]:
        wyloguj(con, audyt_sesja)
        odpowiedz.delete_cookie(CIASTECZKO, path="/")
        return {"wylogowano": True}

    @aplikacja.get("/api/ja")
    def kim_jestem(sesja: ZSesji) -> dict[str, Any]:
        """Front musi wiedzieć, którą powłokę pokazać. Bez danych audytu."""
        return {"rola": sesja.rola, "client_id": sesja.client_id, "email": sesja.email}

    # ── dane ─────────────────────────────────────────────────────────

    @aplikacja.get("/api/pulpit")
    def pulpit(
        sesja: ZSesji, con: Polaczenie, klient: str | None = None, run: str | None = None
    ) -> dict[str, Any]:
        """Panel. **Bez parametru `odbiorca`** — wynika z roli sesji.

        `klient` jest opcjonalny i **działa wyłącznie dla zespołu** (przełączanie
        po lewej stronie). Sesja klienta go ignoruje: podanie cudzego identyfikatora
        nie zmienia niczego, bo `client_id` i tak bierzemy z sesji.

        `run` wybiera **wersję audytu** — i tu granica działa inaczej niż przy
        `klient`. Klienta nie da się zignorować „w drugą stronę": klient MA prawo
        wybrać swój starszy audyt, więc parametru nie wolno wyrzucić. Zamiast tego
        serwer **sprawdza właściciela** przez `run_nalezy_do`. Obcy run daje 404,
        nie 403 — 403 potwierdzałoby, że taki audyt istnieje.

        Bez tego sprawdzenia `zbuduj_pulpit(run_id=...)` zbudowałby panel cudzego
        klienta razem z nazwiskami, bo sama ta funkcja nie pyta, czyj run dostała.

        Lista wersji jedzie w TYM SAMYM payloadzie (`Pulpit.wersje`), nie osobnym
        endpointem: front potrzebuje jej razem z danymi, a drugie żądanie znaczyłoby
        drugi moment, w którym może się nie udać. Siedzi w dataclassie, więc typ dla
        frontu **generuje się sam** — dopisywanie klucza tutaj byłoby polem, którego
        `api.ts` nie zna.
        """
        if sesja.to_klient:
            cel = sesja.client_id
            odbiorca = ODBIORCA_KLIENT
        else:
            cel = klient or _pierwszy_klient(con)
            odbiorca = ODBIORCA_WEWNETRZNY
        if not cel:
            raise HTTPException(status_code=404, detail="brak audytu")

        if run is not None and not run_nalezy_do(con, run, cel):
            raise HTTPException(status_code=404, detail="nie znaleziono audytu")

        try:
            return do_json(
                zbuduj_pulpit(con, client_id=cel, rubryka=rubryka, odbiorca=odbiorca, run_id=run)
            )
        except RaportError as blad:
            raise HTTPException(status_code=404, detail=str(blad)) from None

    @aplikacja.get("/api/klienci")
    def klienci(sesja: ZSesji, con: Polaczenie) -> list[dict[str, Any]]:
        """Lista do drop-downu. **404 dla klienta**, nie 403.

        403 potwierdziłoby, że taki endpoint istnieje i ma treść — a lista
        klientów CXLABS to informacja handlowa.
        """
        if not sesja.to_zespol:
            raise HTTPException(status_code=404, detail="nie znaleziono")
        return [
            {
                "client_id": p.client_id,
                "audytow": p.audytow,
                "ostatni_run_at": p.ostatni_run_at,
                "findingow": p.findingow,
                "suma_kwot": p.suma_kwot,
                # Czy klient MOŻE się zalogować. Panel administracyjny pokazuje
                # brak konta jako stan do naprawy, nie ukrywa wiersza.
                "ma_konto": p.ma_konto,
            }
            for p in zbuduj_liste_klientow(con)
        ]

    # ── hasła ────────────────────────────────────────────────────────
    #
    # Dwie akcje, dwie różne granice, obie wynikające z jednego wymagania:
    # **klient nie może zresetować sobie hasła.** Robi to zespół.
    #
    # Dlatego klient nie ma tu ŻADNEGO endpointu — nie „ma, ale zabroniony".
    # Wołając zespołowy dostaje 404, bo 403 znaczyłoby „istnieje i nie wolno ci",
    # a to podpowiedź, że taka droga jest.

    @aplikacja.post("/api/haslo/zapomniane")
    def zapomniane_haslo(
        dane: DaneZapomnianego, zadanie: Request, con: Polaczenie
    ) -> dict[str, str]:
        """„Nie pamiętam hasła" — JEDYNY endpoint hasła BEZ sesji, i tak musi być.

        Reset z panelu wymaga sesji, a kto zgubił hasło, sesji nie ma. Bez tej
        drogi mieliśmy błędne koło: „zmień hasło, gdy je znasz" zamiast „nie
        pamiętam hasła". To była luka w poprzedniej wersji.

        ## Odpowiedź jest ZAWSZE taka sama

        Dla konta istniejącego, nieistniejącego, obcej domeny i przekroczonego
        limitu — ten sam kod i ten sam komunikat. Inaczej brama staje się
        wyrocznią: „ten adres @cxlabs.digital jest prawdziwy, tamten nie". Ta
        sama zasada, którą stosuje `zaloguj` dla „nie ma konta" i „złe hasło".

        Dlatego też **nie zmieniamy odpowiedzi, gdy wysyłka maila zawiedzie** —
        różnica w kodzie HTTP też jest różnicą.

        ## Tylko zespół

        Hasło klienta wydaje CXLABS z panelu (D16 aneks). Klient nie ma skrzynki
        w naszej domenie, więc nie mamy czym potwierdzić, że to on prosi.
        """
        email = dane.email.strip().lower()
        ip = zadanie.client.host if zadanie.client else None

        token = poproszono_o_reset(con, email=email, ip=ip)
        if token is not None:
            link = f"{_baza_adresu(zadanie, ustawienia_aplikacji)}/?reset={token}"
            try:
                wyslij_link_resetu(
                    ustawienia_aplikacji,
                    email=email,
                    link=link,
                    minut=MINUT_TOKENU_RESETU,
                )
            except PocztaError:
                # Log już to zapisał. Odpowiedź bez zmian — patrz docstring.
                # Bez śladu stosu (TRY400 świadomie): `poczta.py` już zapisał typ
                # błędu, a stos z `smtplib` niesie poświadczenia SMTP.
                logger.error(  # noqa: TRY400
                    "wysyłka linku resetu zawiodła dla %s", email
                )

        return {
            "komunikat": (
                "Jeśli ten adres ma konto w panelu, link do zmiany hasła jest "
                f"w drodze. Ważny {MINUT_TOKENU_RESETU} minut."
            )
        }

    @aplikacja.post("/api/haslo/z-linku")
    def haslo_z_linku(dane: DaneTokenu, con: Polaczenie) -> dict[str, Any]:
        """Wymienia token z maila na nowe hasło. Też BEZ sesji — z tego samego powodu.

        Token jest jednorazowy i wygasa po `MINUT_TOKENU_RESETU`; sprawdzenie
        siedzi w `zuzyj_token_resetu`, żeby jedna implementacja pilnowała obu
        warunków. Komunikat błędu nie rozróżnia „nie ma", „wygasł" i „już użyty" —
        rozróżnienie mówiłoby, czy token kiedykolwiek istniał.
        """
        try:
            wynik = zuzyj_token_resetu(con, dane.token)
        except DostepError as blad:
            raise HTTPException(status_code=400, detail=str(blad)) from None
        return _odpowiedz_resetu(wynik)

    @aplikacja.post("/api/haslo/moje")
    def zmien_moje_haslo(dane: DaneZmianyHasla, sesja: ZSesji, con: Polaczenie) -> dict[str, Any]:
        """Nowe hasło do WŁASNEGO konta. Tylko zespół, tylko swoje.

        **Konto bierzemy z sesji**, nie z ciała żądania. Gdyby szło z ciała, osoba
        z zespołu zmieniałaby hasło komukolwiek — w tym innej osobie z zespołu.
        Nie ma powodu, żeby ta droga istniała.

        **Wymagamy obecnego hasła**, choć sesja już potwierdza tożsamość. Sesja
        bywa porzucona w cudzej przeglądarce; bez tego warunku przejęta sesja
        pozwalałaby przejąć konto na stałe — a to różnica między szkodą na 12
        godzin i szkodą bez końca.

        Klient dostaje 404: reset klienta robi zespół, nie klient.
        """
        if not sesja.to_zespol:
            raise HTTPException(status_code=404, detail="nie znaleziono")
        if sesja.email is None:
            raise HTTPException(status_code=404, detail="nie znaleziono")

        try:
            zaloguj(con, haslo=dane.obecne_haslo, email=sesja.email, ip=None)
        except DostepError:
            raise HTTPException(status_code=403, detail="obecne hasło się nie zgadza") from None

        try:
            wynik = zresetuj_haslo(con, konto_id=sesja.konto_id)
        except DostepError as blad:
            raise HTTPException(status_code=404, detail=str(blad)) from None
        return _odpowiedz_resetu(wynik)

    @aplikacja.post("/api/klient/dostep")
    def nadaj_dostep_klientowi(
        dane: DaneNowegoKlienta, sesja: ZSesji, con: Polaczenie
    ) -> dict[str, Any]:
        """Zakłada konto dostępu klientowi, który go jeszcze nie ma.

        Potrzebne, bo panel pokazuje teraz „BRAK KONTA" jako stan (patrz
        `zbuduj_liste_klientow`) — a pokazywanie braku bez drogi do naprawienia go
        byłoby połową roboty. Klient `cxlabs` miał w bazie 17 audytów i żadnego
        konta: audyt istniał, a odbiorca nie mógł go zobaczyć.

        Tylko zespół; klient dostaje 404, jak przy każdym endpoincie zespołowym.
        Gdy konto już jest, **odmawiamy zamiast po cichu wydać drugie hasło** —
        to ta sama reguła, którą wymusza `utworz_konto` i indeks z migracji 007.
        """
        if not sesja.to_zespol:
            raise HTTPException(status_code=404, detail="nie znaleziono")

        haslo = wygeneruj_haslo()
        try:
            utworz_konto(con, rola=ROLA_KLIENT, haslo=haslo, client_id=dane.client_id)
        except DostepError as blad:
            raise HTTPException(status_code=409, detail=str(blad)) from None

        logger.info("nadano dostęp klientowi %s", dane.client_id)
        return {"haslo": haslo, "wazne_sesje": 0, "godzin_sesji": GODZIN_SESJI}

    @aplikacja.post("/api/haslo/klienta")
    def zresetuj_haslo_klienta(
        dane: DaneResetuKlienta, sesja: ZSesji, con: Polaczenie
    ) -> dict[str, Any]:
        """Nowe hasło dla klienta. **Wyłącznie dla sesji zespołu.**

        To jest cały sens wymagania: klient nie może sam sobie zresetować hasła,
        bo hasło jest jedyną bramą do jego danych osobowych, a my nie mamy jak
        potwierdzić, kto o reset prosi (nie ma maili — patrz O24).

        Sesja klienta dostaje **404**, nie 403.
        """
        if not sesja.to_zespol:
            raise HTTPException(status_code=404, detail="nie znaleziono")

        konto_id = konto_klienta(con, dane.client_id)
        if konto_id is None:
            raise HTTPException(status_code=404, detail="ten klient nie ma konta dostępu")

        try:
            wynik = zresetuj_haslo(con, konto_id=konto_id)
        except DostepError as blad:
            raise HTTPException(status_code=404, detail=str(blad)) from None
        return _odpowiedz_resetu(wynik)

    # ── audyt ────────────────────────────────────────────────────────

    @aplikacja.get("/api/audyt/mozliwosc")
    def mozliwosc(sesja: ZSesji, con: Polaczenie, klient: str | None = None) -> dict[str, Any]:
        """Czy wolno odpalić audyt i kiedy będzie można. Do wyszarzenia przycisku."""
        cel = sesja.client_id if sesja.to_klient else (klient or _pierwszy_klient(con))
        if not cel:
            raise HTTPException(status_code=404, detail="brak klienta")
        wolno, powod = wolno_odpalic(con, cel)
        return {"wolno": wolno, "powod": powod, "client_id": cel}

    @aplikacja.post("/api/audyt")
    def odpal_audyt(
        dane: DaneAudytu,
        sesja: ZSesji,
        con: Polaczenie,
        w_tle: BackgroundTasks,
        klient: str | None = None,
    ) -> dict[str, str]:
        """Startuje audyt. Klucz API idzie do zadania i **nie jest zapisywany**."""
        cel = sesja.client_id if sesja.to_klient else (klient or _pierwszy_klient(con))
        if not cel or not sesja.widzi_klienta(cel):
            raise HTTPException(status_code=404, detail="nie znaleziono")
        try:
            zadanie_id = utworz_zadanie(con, client_id=cel, konto_id=sesja.konto_id)
        except ZadanieError as blad:
            # 429: to nie błąd danych, a hamulec kosztu.
            raise HTTPException(status_code=429, detail=str(blad)) from None

        # `BackgroundTasks`, nie `get_running_loop().run_in_executor` — endpoint
        # jest synchroniczny, więc pętli zdarzeń w nim NIE MA i tamto rzucało
        # `RuntimeError: no running event loop`. Wyszło przy pierwszym `curl`-u
        # na żywo; testy granic tego nie łapały, bo nie odpalały runu.
        #
        # Klucz przechodzi jako argument zadania. Nie logujemy go, nie
        # zapisujemy, nie wkładamy do struktury, która gdzieś trafia.
        w_tle.add_task(
            uruchom_audyt_w_tle,
            sciezka_bazy,
            zadanie_id,
            cel,
            dane.klucz_api,
            dane.zakres,
            dane.workspace_id,
        )
        logger.info("audyt %s wystartował dla %s", zadanie_id, cel)
        return {"zadanie_id": zadanie_id}

    @aplikacja.get("/api/audyt/{zadanie_id}")
    def stan_audytu(zadanie_id: str, sesja: ZSesji, con: Polaczenie) -> dict[str, Any]:
        stan = wczytaj_stan(con, zadanie_id)
        # Nieistniejące i cudze zadanie dają TO SAMO 404 — inaczej po kodzie
        # odpowiedzi dałoby się sprawdzać, czy dany identyfikator istnieje.
        if stan is None or not sesja.widzi_klienta(stan.client_id):
            raise HTTPException(status_code=404, detail="nie znaleziono")
        return {
            "id": stan.id,
            "stan": stan.stan,
            "etap": stan.etap,
            "postep": stan.postep,
            "run_id": stan.run_id,
            "blad": stan.blad,
            "trwa": stan.trwa,
        }

    if KATALOG_FRONTU.is_dir():
        aplikacja.mount("/", StaticFiles(directory=KATALOG_FRONTU, html=True), name="front")
    else:
        logger.warning(
            "brak zbudowanego frontu w %s — API działa, interfejsu nie ma "
            "(uruchom `npm run build` w katalogu front/)",
            KATALOG_FRONTU,
        )

    return aplikacja


def _pierwszy_klient(con: sqlite3.Connection) -> str | None:
    """Domyślny klient dla zespołu, gdy drop-down jeszcze nic nie wybrał."""
    pozycje = zbuduj_liste_klientow(con)
    return pozycje[0].client_id if pozycje else None


def przygotuj_baze(sciezka: Path) -> None:
    """Migracje przed startem serwera — jak w `cli_agent` i `cli_raport`."""
    con = polacz(sciezka)
    try:
        zastosowane = zastosuj_migracje(con)
        if zastosowane:
            logger.info("zastosowane migracje: %s", zastosowane)
    finally:
        con.close()
