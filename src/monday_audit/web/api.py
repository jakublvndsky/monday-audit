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

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from monday_audit.baza import polacz, zastosuj_migracje
from monday_audit.dostep import (
    GODZIN_SESJI,
    DostepError,
    Sesja,
    wczytaj_sesje,
    wyloguj,
    zaloguj,
)
from monday_audit.konfiguracja import Ustawienia, wczytaj
from monday_audit.pulpit import do_json, zbuduj_liste_klientow, zbuduj_pulpit
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
    con = polacz(zadanie.app.state.baza)
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


def zbuduj_aplikacje(*, baza: Path | None = None, ustawienia: Ustawienia | None = None) -> FastAPI:
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
    else:
        konf = ustawienia or wczytaj()
        sciezka_bazy = konf.monday_audit_db.absolute()
    rubryka: Rubryka = wczytaj_rubryke()

    aplikacja = FastAPI(title="monday.com Account Audit", docs_url=None, redoc_url=None)
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
    def pulpit(sesja: ZSesji, con: Polaczenie, klient: str | None = None) -> dict[str, Any]:
        """Panel. **Bez parametru `odbiorca`** — wynika z roli sesji.

        `klient` jest opcjonalny i **działa wyłącznie dla zespołu** (przełączanie
        drop-downem). Sesja klienta go ignoruje: podanie cudzego identyfikatora
        nie zmienia niczego, bo `client_id` i tak bierzemy z sesji.
        """
        if sesja.to_klient:
            cel = sesja.client_id
            odbiorca = ODBIORCA_KLIENT
        else:
            cel = klient or _pierwszy_klient(con)
            odbiorca = ODBIORCA_WEWNETRZNY
        if not cel:
            raise HTTPException(status_code=404, detail="brak audytu")

        try:
            return do_json(zbuduj_pulpit(con, client_id=cel, rubryka=rubryka, odbiorca=odbiorca))
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
            }
            for p in zbuduj_liste_klientow(con)
        ]

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
        dane: DaneAudytu, sesja: ZSesji, con: Polaczenie, klient: str | None = None
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

        # Klucz przechodzi jako argument. Nie logujemy go, nie zapisujemy,
        # nie wkładamy do żadnej struktury, która gdzieś trafia.
        asyncio.get_running_loop().run_in_executor(
            None,
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
