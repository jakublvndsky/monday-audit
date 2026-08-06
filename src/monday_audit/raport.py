"""Renderer raportu z zapisanego runu (etap 3.12).

Agent zapisuje wyniki jako WIERSZE w tabeli `findings`. Ten moduł robi z nich
dokument do czytania: sortuje regułą z rubryki, sumuje kwoty, rozwija hashe
na nazwiska i rozdziela na dwie wersje.

**To jest raport, nie oferta.** Nie ma tu zakresu naszych prac ani ceny za nasze
usługi. `kwota_pln` to oszczędność KLIENTA na jego licencjach monday, policzona
ze stawki, którą sam podał — dlatego dokument zawsze pokazuje jej podstawę:
wartość, źródło i datę.

## Dwie wersje, jedna różnica

| | wewnętrzna | klientowa |
|---|---|---|
| findingi `tylko_wewnetrzne` | są | **nie ma** |
| `trop` przy findingu | jest | **nie ma** |
| odrzucone hipotezy i findingi, pinowanie, koszt | są | nie ma |
| nazwiska, kwoty, „czego nie widać" | są | **są** |

Nazwiska są w OBU wersjach świadomie. Granica PII z D6 dotyczy kontekstu
modelu, nie dokumentu; raport mówiący „konto 05677b1a… jest martwe" jest
niewykonalny, bo klient nie wie, o kogo chodzi.

## Filtrowanie jest w SQL, nie w szablonie

`WHERE widocznosc = 'klient'` stoi w zapytaniu, a `trop` nie wchodzi do
struktury przekazywanej szablonowi. Szablon **nie może być ostatnią linią
obrony** przed wyciekiem treści wewnętrznej: jest edytowany przy każdej zmianie
wyglądu, przez osobę patrzącą na układ strony, a nie na granice zaufania.
Pilnują tego testy w warstwie granic.

## Sekcja „czego nie widać" idzie do OBU wersji

To decyzja, nie przeoczenie. Snapshot #5 ma dwa zastrzeżenia: token bez
uprawnień admina oraz statystyki automatyzacji liczone na poziomie konta, bo
filtr `board_id` w API jest zepsuty (O12). Raport, który to ukrywa, sugeruje
pokrycie, którego nie ma — a pierwszy klient, który to sprawdzi, przestanie
wierzyć całej reszcie.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from monday_audit.cennik import Stawka, stawki_dla
from monday_audit.deanonimizacja import Deanonimizacja
from monday_audit.rubryka import Rubryka

logger = logging.getLogger(__name__)

ODBIORCA_WEWNETRZNY = "wewnetrzny"
ODBIORCA_KLIENT = "klient"
ODBIORCY = (ODBIORCA_WEWNETRZNY, ODBIORCA_KLIENT)

# Wzorzec zasobu przy pakiecie — ten sam, którym `baza.py` znajduje migracje.
KATALOG_SZABLONOW = Path(__file__).parent / "szablony"
KATALOG_ZASOBOW = KATALOG_SZABLONOW / "zasoby"
SZABLON = "raport.html.j2"

# Znak marki CXLABS, osadzany jako `data:` URI. Własny zasób klienta, więc
# bez ograniczeń licencyjnych — inaczej niż fonty, patrz `szablony/fonty/README.md`.
LOGO = "cxlabs-mark-ink.png"


def zasob_data_uri(nazwa: str, *, katalog: Path = KATALOG_ZASOBOW) -> str | None:
    """Plik z `szablony/zasoby/` jako `data:` URI. Brak pliku to nie błąd.

    Raport musi otwierać się z dysku i drukować u kogoś bez dostępu do naszej
    sieci, więc każdy obrazek jedzie w treści dokumentu. Gdy zasobu nie ma,
    szablon po prostu go nie pokazuje — dokument zostaje czytelny.
    """
    sciezka = katalog / nazwa
    if not sciezka.is_file():
        logger.warning("brak zasobu %s — raport wyjdzie bez niego", sciezka)
        return None
    typ = "image/svg+xml" if sciezka.suffix == ".svg" else f"image/{sciezka.suffix.lstrip('.')}"
    return f"data:{typ};base64," + base64.b64encode(sciezka.read_bytes()).decode("ascii")


class RaportError(RuntimeError):
    """Nie da się zbudować raportu — brak runu albo nieznany odbiorca."""


@dataclass(frozen=True, slots=True)
class Finding:
    """Jeden finding gotowy do wyświetlenia. Hashe już rozwinięte."""

    klasa_id: str
    nazwa: str
    waga: str
    wysilek: str
    pewnosc: str
    kwota_pln: float | None
    opis: str
    rekomendacja: str
    dowod: dict[str, Any]
    # Tylko wersja wewnętrzna. W wersji klientowej jest `None` i to jest
    # wymuszone w `zbuduj_raport`, nie w szablonie.
    trop: str | None


@dataclass(frozen=True, slots=True)
class Raport:
    """Wszystko, co szablon dostaje. Nic więcej nie jest mu potrzebne."""

    odbiorca: str
    client_id: str
    run_id: str
    run_at: str
    zakres: str
    plan_tier: str
    findingi: tuple[Finding, ...]
    po_wagach: dict[str, int]
    suma_kwot: float
    stawki: tuple[Stawka, ...]
    zastrzezenia: tuple[str, ...]
    # Poniżej: wyłącznie wersja wewnętrzna. Puste krotki w wersji klientowej.
    hipotezy_odrzucone: tuple[dict[str, Any], ...] = ()
    findingi_odrzucone: tuple[dict[str, Any], ...] = ()
    pinowanie: dict[str, Any] = field(default_factory=dict)
    koszt_usd: float | None = None
    nieznane_hashe: int = 0

    @property
    def dla_klienta(self) -> bool:
        return self.odbiorca == ODBIORCA_KLIENT

    @property
    def findingow(self) -> int:
        return len(self.findingi)

    @property
    def ma_kwoty(self) -> bool:
        return self.suma_kwot > 0

    def nazwa_pliku(self) -> str:
        """`RRRR-MM_audyt_konta_<klient>_<odbiorca>.html` — wzorzec z 03-build.

        Miesiąc bierzemy z daty SNAPSHOTU, nie z dzisiejszej: raport
        z sierpniowego snapshotu wyrenderowany w październiku dotyczy sierpnia.
        """
        miesiac = self.run_at[:7] if len(self.run_at) >= 7 else "nieznana-data"
        return f"{miesiac}_audyt_konta_{self.client_id}_{self.odbiorca}.html"


def _snapshot_id(con: sqlite3.Connection, run: sqlite3.Row, run_id: str) -> int:
    """Który snapshot analizował ten run — z `runy`, a jeśli trzeba, z findingów.

    `cli_agent` zaczął wypełniać `runy.snapshot_id` dopiero 2026-08-04, więc
    starsze runy agenta mają tam NULL. `findings.snapshot_id` jest NOT NULL,
    czyli prawda jest w bazie i nie ma powodu odmawiać renderowania runu,
    który poprzedza tę poprawkę.

    Gdyby run nie miał ani jednego findingu ani odrzucenia, nie ma czego
    renderować i to jest błąd, nie cisza.
    """
    if run["snapshot_id"] is not None:
        return int(run["snapshot_id"])

    wiersz = con.execute(
        "SELECT snapshot_id FROM findings WHERE run_id = ? "
        "UNION SELECT snapshot_id FROM findings_odrzucone WHERE run_id = ? LIMIT 1",
        (run_id, run_id),
    ).fetchone()
    if wiersz is None:
        raise RaportError(
            f"run {run_id} nie ma snapshotu ani w `runy`, ani w findingach — "
            f"nie ma czego renderować"
        )
    logger.info(
        "run %s ma puste `runy.snapshot_id` (run sprzed 2026-08-04) — biorę %s z findingów",
        run_id,
        wiersz["snapshot_id"],
    )
    return int(wiersz["snapshot_id"])


def _snapshot(con: sqlite3.Connection, snapshot_id: int) -> dict[str, Any]:
    wiersz = con.execute("SELECT payload FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if wiersz is None:
        raise RaportError(f"nie ma snapshotu {snapshot_id}")
    payload: dict[str, Any] = json.loads(wiersz["payload"])
    return payload


def _zastrzezenia(payload: dict[str, Any]) -> tuple[str, ...]:
    """Czego w tym audycie NIE WIDAĆ — z dwóch miejsc snapshotu.

    `meta.uwagi_o_zakresie` mówi, czego nie da się zawęzić do workspace, bo API
    nie pozwala. `konto.zastrzezenia` mówi, czego nie widzi token. Oba idą do
    obu wersji raportu.
    """
    meta = payload.get("meta") or {}
    konto = payload.get("konto") or {}
    razem = [*(meta.get("uwagi_o_zakresie") or []), *(konto.get("zastrzezenia") or [])]
    return tuple(str(z) for z in razem)


def _opis_zakresu(payload: dict[str, Any]) -> str:
    zakres = ((payload.get("konto") or {}).get("zakres")) or {}
    typ = str(zakres.get("typ") or "nieznany")
    identyfikatory = [*(zakres.get("workspace_ids") or []), *(zakres.get("board_ids") or [])]
    if not identyfikatory:
        return typ
    return f"{typ} {', '.join(str(i) for i in identyfikatory)}"


def zbuduj_raport(
    con: sqlite3.Connection,
    *,
    run_id: str,
    rubryka: Rubryka,
    odbiorca: str = ODBIORCA_WEWNETRZNY,
) -> Raport:
    """Zbiera dane runu w strukturę gotową do wyrenderowania.

    Czyta wyłącznie z bazy — żadnego wywołania do monday i żadnego do modelu.
    Dzięki temu renderowanie jest darmowe i powtarzalne: etap 4 przepuszcza te
    same runy przez nowy szablon bez ponownego płacenia za analizę.
    """
    if odbiorca not in ODBIORCY:
        raise RaportError(f"nieznany odbiorca {odbiorca!r}; dozwolone: {', '.join(ODBIORCY)}")

    run = con.execute("SELECT * FROM runy WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise RaportError(f"nie ma runu {run_id}")

    client_id = str(run["client_id"])
    snapshot_id = _snapshot_id(con, run, run_id)
    payload = _snapshot(con, snapshot_id)
    meta = payload.get("meta") or {}
    deanon = Deanonimizacja(con, client_id, z_emailem=odbiorca == ODBIORCA_WEWNETRZNY)

    # FILTROWANIE ODBIORCY JEST TUTAJ, w SQL. Szablon nie ma nawet z czego
    # wyciekić treści wewnętrznej, bo ta nie wchodzi do struktury.
    warunek = " AND widocznosc = 'klient'" if odbiorca == ODBIORCA_KLIENT else ""
    wiersze = con.execute(
        f"SELECT * FROM findings WHERE run_id = ?{warunek}",  # noqa: S608 — warunek jest literałem
        (run_id,),
    ).fetchall()

    po_id = rubryka.po_id
    findingi = tuple(
        Finding(
            klasa_id=str(w["klasa_id"]),
            nazwa=po_id[str(w["klasa_id"])].nazwa
            if str(w["klasa_id"]) in po_id
            else str(w["klasa_id"]),
            waga=str(w["waga"]),
            wysilek=str(w["wysilek"]),
            pewnosc=str(w["pewnosc"]),
            kwota_pln=float(w["kwota_pln"]) if w["kwota_pln"] is not None else None,
            opis=deanon.tekst(str(w["opis"])),
            rekomendacja=deanon.tekst(str(w["rekomendacja"])),
            dowod=deanon.wartosc(json.loads(w["dowod"])),
            # Trop z RUBRYKI, nie z kolumny: `findings.trop` jest zapisem tego,
            # co obowiązywało w danym runie, a starym wierszom kolumna została
            # NULL-em (pole nie było wczytywane do 2026-08-05). W wersji
            # klientowej `None` bezwarunkowo.
            trop=None
            if odbiorca == ODBIORCA_KLIENT
            else (
                po_id[str(w["klasa_id"])].trop_sprzedazowy if str(w["klasa_id"]) in po_id else None
            ),
        )
        for w in rubryka.kolejnosc_raportu(wiersze)
    )

    po_wagach: dict[str, int] = {}
    for waga in rubryka.kolejnosc_wag:
        liczba = sum(1 for f in findingi if f.waga == waga)
        if liczba:
            po_wagach[waga] = liczba

    # Stawki Z MOMENTU RUNU, nie dzisiejsze. `cennik_ver` pinuje odczyt (D13).
    potrzebne = {
        z for f in findingi if f.klasa_id in po_id for z in po_id[f.klasa_id].zmienne_od_klienta
    }
    stawki = stawki_dla(con, potrzebne, client_id=client_id, do_momentu=run["cennik_ver"])

    wewnetrzne = odbiorca == ODBIORCA_WEWNETRZNY
    raport = Raport(
        odbiorca=odbiorca,
        client_id=client_id,
        run_id=run_id,
        run_at=str(meta.get("run_at") or run["started_at"]),
        zakres=_opis_zakresu(payload),
        plan_tier=str(((payload.get("konto") or {}).get("plan") or {}).get("tier") or "nieznany"),
        findingi=findingi,
        po_wagach=po_wagach,
        suma_kwot=round(sum(f.kwota_pln or 0.0 for f in findingi), 2),
        stawki=tuple(stawki[p] for p in sorted(stawki)),
        zastrzezenia=_zastrzezenia(payload),
        hipotezy_odrzucone=_hipotezy_odrzucone(con, run_id, deanon) if wewnetrzne else (),
        findingi_odrzucone=_findingi_odrzucone(con, run_id) if wewnetrzne else (),
        pinowanie=_pinowanie(run, meta, snapshot_id) if wewnetrzne else {},
        koszt_usd=float(run["koszt_usd"]) if wewnetrzne and run["koszt_usd"] is not None else None,
        nieznane_hashe=len(deanon.nieznane),
    )
    deanon.podsumuj()
    logger.info(
        "raport %s dla %s: %d findingów, suma %.2f PLN",
        odbiorca,
        client_id,
        raport.findingow,
        raport.suma_kwot,
    )
    return raport


def _hipotezy_odrzucone(
    con: sqlite3.Connection, run_id: str, deanon: Deanonimizacja
) -> tuple[dict[str, Any], ...]:
    """Co agent OBALIŁ i dlaczego. Najmocniejszy element wiarygodności.

    „Sprawdziliśmy 19 rzeczy, 8 nie wytrzymało" mówi o audycie więcej niż
    sama lista znalezisk — agent potwierdzający wszystko jest bezużyteczny.
    """
    return tuple(
        {
            "klasa_id": str(w["klasa_id"]),
            "obiekt_id": deanon.tekst(str(w["obiekt_id"])) if w["obiekt_id"] else None,
            "powod": deanon.tekst(str(w["powod"])),
        }
        for w in con.execute(
            "SELECT klasa_id, obiekt_id, powod FROM hipotezy_odrzucone WHERE run_id = ? "
            "ORDER BY klasa_id, id",
            (run_id,),
        )
    )


def _findingi_odrzucone(con: sqlite3.Connection, run_id: str) -> tuple[dict[str, Any], ...]:
    """Co odrzuciła NASZA walidacja — metryka jakości, nie agenta (D8).

    Treść findingu zostaje w bazie, ale do raportu idzie sama reguła i powód:
    odrzucony finding nie jest ustaleniem i nie ma prawa czytać się jak
    znalezisko.
    """
    return tuple(
        {"klasa_id": w["klasa_id"], "regula": str(w["regula"]), "powod": str(w["powod"])}
        for w in con.execute(
            "SELECT klasa_id, regula, powod FROM findings_odrzucone WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
    )


def _pinowanie(run: sqlite3.Row, meta: dict[str, Any], snapshot_id: int) -> dict[str, Any]:
    """Sześć elementów pinowania (05-deploy). Bez nich run jest nieodtwarzalny."""
    return {
        "model": run["model"],
        "rubryka": run["rubric_ver"],
        "prompt_hash": run["prompt_hash"],
        "collector": meta.get("collector_ver"),
        "wersja_api": meta.get("wersja_api"),
        "cennik": run["cennik_ver"],
        "snapshot_id": snapshot_id,
        "okno_dni": meta.get("okno_dni"),
    }


# Etykiety pól dowodu, które po deanonimizacji przestały pasować do nazwy.
# `user_hash: Maciej Zieliński` w dokumencie dla klienta czyta się jak usterka —
# wartość jest już nazwiskiem, a podpis nadal mówi o haszu.
ETYKIETY_DOWODU = {
    "user_hash": "konto",
    "guest_hash": "konta gości",
    "top_kontrybutor_hash": "najaktywniejsza osoba",
}

# Wartości ze słowników rubryki są bez polskich znaków, bo służą też jako
# klucze w SQL i w YAML-u. W dokumencie pokazujemy je poprawnie.
SLOWNIE = {
    "srednia": "średnia",
    "sredni": "średni",
    "niska": "niska",
    "niski": "niski",
    "wysoka": "wysoka",
    "wysoki": "wysoki",
    "krytyczna": "krytyczna",
}


def etykieta(klucz: str) -> str:
    """Nazwa pola dowodu do pokazania człowiekowi."""
    return ETYKIETY_DOWODU.get(klucz, klucz.replace("_", " "))


def slownie(wartosc: str) -> str:
    """Wartość ze słownika rubryki z polskimi znakami."""
    return SLOWNIE.get(wartosc, wartosc)


def odmiana(liczba: int, jeden: str, kilka: str, wiele: str) -> str:
    """Polska odmiana po liczbie: 1 pole, 2–4 pola, 5+ pól.

    Bez tego dokument mówi „Dowód (7 pola)". Drobiazg, ale raport ma być
    wiarygodny, a pierwszy sygnał niedbałości podważa resztę.
    """
    if liczba == 1:
        return jeden
    if 2 <= liczba % 10 <= 4 and liczba % 100 not in range(12, 15):
        return kilka
    return wiele


def srodowisko(katalog: Path) -> Environment:
    """Jinja z JAWNYM autoescapingiem. Publiczne, bo używa jej też `pulpit`.

    Nazwa bez podkreślnika świadomie: funkcja prywatna wołana z innego modułu
    to sprzeczność, którą Kuba wyłapał już raz przy `_payload` w `narzedzia.py`.
    Skoro panele budują szablony tym samym środowiskiem — a muszą, bo inaczej
    autoescaping i polityka `tojson` rozjechałyby się między dokumentami —
    to jest część API tego modułu.

    `Environment` domyślnie ma `autoescape=False`. W dokumencie niosącym nazwy
    tablic i kolumn klienta to znaczy, że nazwa `Oferty <b>2026</b>` rozwala
    układ strony, a `<script>` staje się skryptem. Pilnuje tego test — bo to
    jedna flaga, którą łatwo zgubić przy refaktorze.
    """
    srodowisko = Environment(
        loader=FileSystemLoader(katalog),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # `tojson` domyślnie woła `json.dumps` z `ensure_ascii=True`, czyli
    # „Anna Górniak" wychodzi jako „Anna Górniak". W zagnieżdżonym dowodzie
    # to znaczy nieczytelne nazwiska i nazwy tablic w dokumencie dla klienta.
    # Bezpieczeństwo zostaje: jinja i tak escapuje `<`, `>`, `&` w tym wyjściu.
    srodowisko.policies["json.dumps_kwargs"] = {
        "ensure_ascii": False,
        "indent": 2,
        "sort_keys": True,
    }
    srodowisko.filters["etykieta"] = etykieta
    srodowisko.filters["slownie"] = slownie
    srodowisko.globals["odmiana"] = odmiana
    return srodowisko


def wyrenderuj(raport: Raport, *, katalog: Path = KATALOG_SZABLONOW, szablon: str = SZABLON) -> str:
    """Wstawia dane w szablon. Zwraca kompletny, samodzielny HTML."""
    return (
        srodowisko(katalog)
        .get_template(szablon)
        .render(r=raport, logo=zasob_data_uri(LOGO, katalog=katalog / "zasoby"))
    )


def zapisz(
    raport: Raport, *, katalog: Path = Path("raporty"), katalog_szablonow: Path = KATALOG_SZABLONOW
) -> Path:
    """Zapisuje raport do pliku. `raporty/` jest w `.gitignore` — zawiera PII."""
    katalog.mkdir(parents=True, exist_ok=True)
    cel = katalog / raport.nazwa_pliku()
    cel.write_text(wyrenderuj(raport, katalog=katalog_szablonow), encoding="utf-8")
    return cel
