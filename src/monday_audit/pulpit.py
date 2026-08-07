"""Dashboardy audytu — warstwa danych (makieta frontu).

Panel wewnętrzny dla CXLABS z drop-downem klientów i panel dla klienta,
widzący tylko siebie. Wzorem jest CXLABS Docs Publisher.

## Python zostaje przy danych, prezentacja jest wymienna

**Ten moduł nie zna HTML-a.** Zwraca strukturę, którą `do_json()` zamienia
w gotowy payload — i to jest cała różnica między „makietą do wyrzucenia"
a fundamentem, na którym stanie front w JS.

Python nie jest technologią frontu: jinja2 renderuje stronę raz, po stronie
serwera, więc filtry, sortowanie po kliknięciu i wykresy wychodzą w nim
topornie. Docelowo prezentacja idzie do React albo do komponentów Docs
Publishera — a agregacja i **granice zostają tutaj**, bo tu żyją dane
i pseudonimizacja.

## Granica wewnętrzne/klientowe jest STRUKTURALNA, nie wizualna

Payload dla klienta **nie zawiera** treści wewnętrznych — nie „nie wyświetla
ich". Przy froncie w JS to jedyny wariant, który cokolwiek znaczy: odbiorca
widzi payload w narzędziach przeglądarki, więc ukrywanie w warstwie widoku
nie jest ukrywaniem.

To ta sama zasada, którą 3.12 zapisało jako „filtrowanie w SQL, nie
w szablonie" (D9), tylko ostrzejsza — bo tam szablon był u nas, a tu będzie
u odbiorcy.

## Poziom konta, bez podziału na workspace'y

Świadomie i na podstawie pomiaru: wszystkie 105 tablic snapshotu #5 siedzi
w jednym workspace (audyt był zawężony), a **tylko 2 z 11 znalezisk niesie
`board_id`** — reszta jest kontowa (martwe konta, goście, plan). Podział
przestrzenny nie miałby czego pokazać. Wraca, gdy audyt obejmie całe konto.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from monday_audit.raport import (
    KATALOG_SZABLONOW,
    ODBIORCA_KLIENT,
    ODBIORCA_WEWNETRZNY,
    ODBIORCY,
    Finding,
    Raport,
    RaportError,
    srodowisko,
    zasob_data_uri,
    zbuduj_raport,
)
from monday_audit.rubryka import Rubryka

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Metryka:
    """Jedna liczba z podpisem. `z` wypełnione tylko tam, gdzie jest całość.

    Rozkład typu „36 z 95" jest w audycie licencji ważniejszy od samej liczby:
    36 agentów nic nie mówi, 36 **z 95 kont** mówi, że ponad jedna trzecia
    „użytkowników" to nie ludzie.
    """

    nazwa: str
    wartosc: float | int
    z: int | None = None
    uwaga: bool = False
    opis: str | None = None

    @property
    def udzial(self) -> float | None:
        if not self.z:
            return None
        return round(100 * float(self.wartosc) / self.z, 1)


@dataclass(frozen=True, slots=True)
class Sekcja:
    """Grupa metryk — „Ludzie", „Tablice", „Automatyzacje", „Aktywność"."""

    tytul: str
    opis: str
    metryki: tuple[Metryka, ...]


@dataclass(frozen=True, slots=True)
class PozycjaKlienta:
    """Wiersz drop-downu. Pochodzi Z BAZY — żadnych wymyślonych klientów."""

    client_id: str
    audytow: int
    ostatni_run_id: str | None
    ostatni_run_at: str | None
    findingow: int
    suma_kwot: float


@dataclass(frozen=True, slots=True)
class PozycjaRunu:
    """Wiersz drop-downu wersji — jeden audyt tego samego klienta.

    Osobno od `PozycjaKlienta`, bo to inne pytanie: tam „którego klienta",
    tu „z kiedy". Front pokazuje datę i liczbę znalezisk, żeby było widać,
    który run jest pełny, a który był próbą techniczną.
    """

    run_id: str
    run_at: str
    findingow: int


@dataclass(frozen=True, slots=True)
class Pulpit:
    """Wszystko, co panel jednego klienta dostaje. Serializowalne przez `do_json`."""

    odbiorca: str
    client_id: str
    nazwa_konta: str
    run_id: str
    run_at: str
    zakres: str
    plan_tier: str
    findingi: tuple[Finding, ...]
    po_wagach: dict[str, int]
    suma_kwot: float
    sekcje: tuple[Sekcja, ...]
    zastrzezenia: tuple[str, ...]
    # Wszystkie audyty tego klienta — drop-down wersji. Zawiera też run WŁAŚNIE
    # pokazywany, bo kontrolka musi mieć zaznaczoną pozycję.
    wersje: tuple[PozycjaRunu, ...] = ()
    # Puste, gdy klient ma jeden audyt. Panel MUSI to napisać, a nie pokazać
    # zera udającego brak zmian.
    poprzedni_run_at: str | None = None
    # Poniżej wyłącznie wersja wewnętrzna.
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

    @property
    def ma_porownanie(self) -> bool:
        return self.poprzedni_run_at is not None


# Klucze, które w wariancie klientowym NIE MOGĄ istnieć w strukturze.
# Nie „nie być wyświetlone" — nie istnieć, bo payload zobaczy front w JS.
KLUCZE_WEWNETRZNE = (
    "hipotezy_odrzucone",
    "findingi_odrzucone",
    "pinowanie",
    "koszt_usd",
    "nieznane_hashe",
)


def _liczby(zrodlo: dict[str, Any], klucz: str) -> int:
    wartosc = zrodlo.get(klucz)
    return int(wartosc) if isinstance(wartosc, (int, float)) else 0


def _sekcje_konta(payload: dict[str, Any]) -> tuple[Sekcja, ...]:
    """Cztery sekcje agregatów Z SNAPSHOTU. Zero nowych zapytań do monday.

    Bierzemy tylko te liczby, które collector już policzył — dashboard nie ma
    prawa dopytywać API, bo wtedy przestałby być darmowy i powtarzalny.
    """
    osoby = (payload.get("uzytkownicy") or {}).get("podsumowanie") or {}
    tablice = (payload.get("tablice") or {}).get("podsumowanie") or {}
    automaty = (payload.get("automatyzacje") or {}).get("podsumowanie") or {}
    aktywnosc = (payload.get("aktywnosc") or {}).get("podsumowanie") or {}

    razem_osob = _liczby(osoby, "razem")
    razem_tablic = _liczby(tablice, "razem")
    zbadanych = _liczby(aktywnosc, "tablic_zbadanych")
    widzianych = _liczby(automaty, "automatyzacji_widzianych")

    return (
        Sekcja(
            tytul="Ludzie i licencje",
            opis="kto zajmuje płatne miejsca, a kto nie jest człowiekiem",
            metryki=(
                Metryka("kont razem", razem_osob),
                Metryka(
                    "agentów AI",
                    _liczby(osoby, "agentow"),
                    z=razem_osob,
                    uwaga=True,
                    opis="konta nieludzkie — liczą się inaczej przy licencjach",
                ),
                Metryka("zajmuje miejsce", _liczby(osoby, "zajmujacych_miejsce"), z=razem_osob),
                Metryka("adminów", _liczby(osoby, "adminow"), z=razem_osob),
                Metryka("gości", _liczby(osoby, "gosci"), z=razem_osob),
                Metryka("tylko podgląd", _liczby(osoby, "tylko_podglad"), z=razem_osob),
                Metryka(
                    "bez śladu aktywności",
                    _liczby(osoby, "bez_last_activity"),
                    z=razem_osob,
                    uwaga=True,
                ),
                Metryka("oczekujących", _liczby(osoby, "oczekujacych"), z=razem_osob),
            ),
        ),
        Sekcja(
            tytul="Tablice i struktura",
            opis="ile struktury zbudowano i ile z niej stoi puste",
            metryki=(
                Metryka("tablic", razem_tablic),
                Metryka("kolumn razem", _liczby(tablice, "kolumn_suma")),
                Metryka(
                    "najwięcej kolumn na tablicy",
                    _liczby(tablice, "kolumn_max"),
                    opis="powyżej ~15 pól zespoły przestają wypełniać",
                ),
                Metryka("itemów", _liczby(tablice, "itemow_suma"), opis="tylko licznik (D5)"),
                Metryka("tablic bez itemów", _liczby(tablice, "tablic_bez_itemow"), z=razem_tablic),
                Metryka(
                    "bez właściciela",
                    _liczby(tablice, "tablic_bez_wlasciciela"),
                    z=razem_tablic,
                    uwaga=True,
                ),
                Metryka("workspace'ów", _liczby(tablice, "workspace_ow")),
            ),
        ),
        Sekcja(
            tytul="Automatyzacje",
            opis="co miało odciążać zespół, a się psuje albo nie chodzi",
            metryki=(
                Metryka("automatyzacji", widzianych),
                Metryka(
                    "z błędami",
                    _liczby(automaty, "automatyzacji_z_bledami"),
                    z=widzianych,
                    uwaga=True,
                ),
                Metryka(
                    "z wyczerpaniem",
                    _liczby(automaty, "automatyzacji_z_wyczerpaniem"),
                    z=widzianych,
                    uwaga=True,
                ),
                Metryka(
                    "tablic bez zdarzeń",
                    _liczby(automaty, "tablic_bez_zdarzen"),
                    z=_liczby(automaty, "tablic_sondowanych"),
                    opis="statystyki są na poziomie konta — filtr po tablicy w API jest zepsuty",
                ),
            ),
        ),
        Sekcja(
            tytul="Aktywność w oknie 90 dni",
            opis="czy z tego konta ktoś naprawdę korzysta",
            metryki=(
                Metryka("wpisów w logu", _liczby(aktywnosc, "wpisow_razem")),
                Metryka("tablic zbadanych", zbadanych),
                Metryka(
                    "zdominowanych jednym autorem",
                    _liczby(aktywnosc, "tablic_zdominowanych_jednym_autorem"),
                    z=zbadanych,
                    uwaga=True,
                    opis="sygnał liczony przez collector, którego żadna klasa jeszcze nie używa",
                ),
                Metryka(
                    "pozornie żywych",
                    _liczby(aktywnosc, "tablic_pozornie_zywych"),
                    z=zbadanych,
                    opis="ostatnie zmiany tylko od automatyzacji, zero od ludzi",
                ),
                Metryka("bez wpisów", _liczby(aktywnosc, "tablic_bez_wpisow"), z=zbadanych),
            ),
        ),
    )


def _poprzedni_run(con: sqlite3.Connection, client_id: str, run_id: str) -> str | None:
    """Data poprzedniego zakończonego audytu tego klienta, jeśli był."""
    wiersz = con.execute(
        "SELECT started_at FROM runy WHERE client_id = ? AND run_id != ? "
        "AND status = 'zakonczony' AND findingow > 0 "
        "AND started_at < (SELECT started_at FROM runy WHERE run_id = ?) "
        "ORDER BY started_at DESC LIMIT 1",
        (client_id, run_id, run_id),
    ).fetchone()
    return str(wiersz["started_at"]) if wiersz else None


def _ostatni_run(con: sqlite3.Connection, client_id: str) -> str | None:
    """Najświeższy zakończony audyt klienta. Po dacie, bez sztuczek.

    ## Dlaczego stąd zniknęło sortowanie po liczbie hipotez

    Do 2026-08-06 ta funkcja sortowała `hipotez_zbadanych DESC, started_at DESC`,
    czyli wybierała audyt **najobszerniejszy**, nie najnowszy. Powód był realny:
    nasze runy diagnostyczne z jedną hipotezą przesłaniały pełny audyt, więc panel
    pokazywał jedno znalezisko i sugerował, że konto jest prawie czyste.

    Obejście przestało być potrzebne, gdy panel dostał **jawny wybór wersji**
    (drop-down z `lista_runow`). Skoro odbiorca widzi wszystkie audyty z datami
    i liczbą znalezisk, ukrywanie najnowszego jest już tylko zaskoczeniem: wchodzi
    się na panel i widzi dane z 1 sierpnia, choć audyt szedł 5 sierpnia.

    **Nie przywracaj sortowania po hipotezach.** Jeśli chude runy znowu zaśmiecą
    listę, właściwą odpowiedzią jest próg w `lista_runow` albo oznaczenie runu jako
    diagnostycznego przy zapisie — nie ciche podmienianie tego, co panel pokazuje.
    """
    wiersz = con.execute(
        "SELECT run_id FROM runy WHERE client_id = ? AND status = 'zakonczony' "
        "AND findingow > 0 ORDER BY started_at DESC LIMIT 1",
        (client_id,),
    ).fetchone()
    return str(wiersz["run_id"]) if wiersz else None


def lista_runow(con: sqlite3.Connection, client_id: str) -> list[PozycjaRunu]:
    """Audyty jednego klienta, najnowszy pierwszy — do drop-downu wersji.

    Ta sama zasada co w `zbuduj_liste_klientow`: **runy z bazy, żadnych wymyślonych
    pozycji.** Gdy klient ma jeden audyt, lista ma jeden element, a front nie
    pokazuje martwej kontrolki.

    Liczymy WIERSZE w `findings`, nie czytamy `runy.findingow`: ten licznik zapisuje
    się przy domknięciu runu i może się rozjechać z tabelą. Drop-down pokazujący
    „11 znalezisk" przy runie, który ma ich 9, kłamie w miejscu, w którym odbiorca
    właśnie wybiera, czemu zaufać.
    """
    pozycje: list[PozycjaRunu] = []
    for wiersz in con.execute(
        "SELECT r.run_id, r.started_at, "
        "(SELECT COUNT(*) FROM findings f WHERE f.run_id = r.run_id) findingow "
        "FROM runy r WHERE r.client_id = ? AND r.status = 'zakonczony' "
        "AND r.findingow > 0 ORDER BY r.started_at DESC",
        (client_id,),
    ):
        pozycje.append(
            PozycjaRunu(
                run_id=str(wiersz["run_id"]),
                run_at=str(wiersz["started_at"]),
                findingow=int(wiersz["findingow"]),
            )
        )
    return pozycje


def run_nalezy_do(con: sqlite3.Connection, run_id: str, client_id: str) -> bool:
    """Czy ten run jest audytem TEGO klienta.

    Granica z D16: `run_id` przychodzi z przeglądarki, więc jest parametrem od
    atakującego, dopóki serwer go nie sprawdzi. Bez tej funkcji sesja klienta
    „acme" mogłaby wpisać identyfikator runu klienta „cxlabs" i przeczytać cudzy
    audyt razem z nazwiskami — bo `zbuduj_pulpit` z podanym `run_id` nie pyta,
    czyj on jest.

    Zwraca `False` także dla runu nieistniejącego, żeby wywołujący mógł oddać
    **404 na oba przypadki**. Rozróżnienie „nie ma" od „nie twój" potwierdzałoby
    istnienie cudzych audytów.
    """
    wiersz = con.execute(
        "SELECT 1 FROM runy WHERE run_id = ? AND client_id = ?", (run_id, client_id)
    ).fetchone()
    return wiersz is not None


def zbuduj_liste_klientow(con: sqlite3.Connection) -> list[PozycjaKlienta]:
    """Klienci Z BAZY, do drop-downu. Żadnych wymyślonych pozycji.

    Panel z fałszywymi klientami wyglądałby lepiej i kłamał. Gdy jest jeden
    klient, lista ma jedną pozycję — a widok mówi o tym wprost.
    """
    pozycje: list[PozycjaKlienta] = []
    for wiersz in con.execute(
        "SELECT client_id, COUNT(*) audytow FROM runy WHERE status = 'zakonczony' "
        "AND findingow > 0 GROUP BY client_id ORDER BY client_id"
    ):
        client_id = str(wiersz["client_id"])
        run_id = _ostatni_run(con, client_id)
        szczegol = con.execute("SELECT started_at FROM runy WHERE run_id = ?", (run_id,)).fetchone()
        # Liczymy WIERSZE, nie czytamy `runy.findingow`. Ten licznik zapisuje się
        # przy domknięciu runu i może się rozjechać z tabelą — a panel ma
        # pokazywać to, co w bazie faktycznie jest.
        suma = con.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(kwota_pln), 0) s FROM findings WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        pozycje.append(
            PozycjaKlienta(
                client_id=client_id,
                audytow=int(wiersz["audytow"]),
                ostatni_run_id=run_id,
                ostatni_run_at=str(szczegol["started_at"]) if szczegol else None,
                findingow=int(suma["n"]),
                suma_kwot=round(float(suma["s"]), 2),
            )
        )
    logger.info("panel wewnętrzny: %d klientów w bazie", len(pozycje))
    return pozycje


def zbuduj_pulpit(
    con: sqlite3.Connection,
    *,
    client_id: str,
    rubryka: Rubryka,
    odbiorca: str = ODBIORCA_WEWNETRZNY,
    run_id: str | None = None,
) -> Pulpit:
    """Panel jednego klienta. Bez `run_id` bierze najświeższy audyt.

    Stoi na `raport.zbuduj_raport`, więc **dziedziczy jego granice**: filtrowanie
    widoczności w SQL i deanonimizację hashy. Dashboard nie może mieć własnej,
    słabszej wersji tych reguł — jedna implementacja, jedno miejsce do zepsucia.
    """
    if odbiorca not in ODBIORCY:
        raise RaportError(f"nieznany odbiorca {odbiorca!r}; dozwolone: {', '.join(ODBIORCY)}")

    wybrany = run_id or _ostatni_run(con, client_id)
    if wybrany is None:
        raise RaportError(f"klient {client_id} nie ma zakończonego audytu ze znaleziskami")

    raport: Raport = zbuduj_raport(con, run_id=wybrany, rubryka=rubryka, odbiorca=odbiorca)
    payload = json.loads(
        con.execute(
            "SELECT payload FROM snapshots WHERE id = "
            "(SELECT snapshot_id FROM findings WHERE run_id = ? LIMIT 1)",
            (wybrany,),
        ).fetchone()["payload"]
    )
    nazwa = str(((payload.get("konto") or {}).get("konto") or {}).get("nazwa") or client_id)

    wewnetrzny = odbiorca == ODBIORCA_WEWNETRZNY
    return Pulpit(
        odbiorca=odbiorca,
        client_id=client_id,
        nazwa_konta=nazwa,
        run_id=wybrany,
        run_at=raport.run_at,
        zakres=raport.zakres,
        plan_tier=raport.plan_tier,
        findingi=raport.findingi,
        po_wagach=raport.po_wagach,
        suma_kwot=raport.suma_kwot,
        sekcje=_sekcje_konta(payload),
        zastrzezenia=raport.zastrzezenia,
        wersje=tuple(lista_runow(con, client_id)),
        poprzedni_run_at=_poprzedni_run(con, client_id, wybrany),
        hipotezy_odrzucone=raport.hipotezy_odrzucone if wewnetrzny else (),
        findingi_odrzucone=raport.findingi_odrzucone if wewnetrzny else (),
        pinowanie=raport.pinowanie if wewnetrzny else {},
        koszt_usd=raport.koszt_usd if wewnetrzny else None,
        nieznane_hashe=raport.nieznane_hashe if wewnetrzny else 0,
    )


def do_json(pulpit: Pulpit) -> dict[str, Any]:
    """Payload dla frontu w JS. **Wariant klientowy nie ma kluczy wewnętrznych.**

    To nie kosmetyka struktury, a granica bezpieczeństwa. Przy froncie w JS
    payload jest widoczny w narzędziach przeglądarki, więc „wyślij wszystko
    i ukryj w widoku" znaczy „wyślij wszystko". Klucze wewnętrzne są tu
    USUWANE, a nie zerowane — brak klucza jest sprawdzalny, zero nie jest.

    Wychodzi czysty JSON: bez dat, bez `Decimal`, bez obiektów. Test pilnuje,
    że przechodzi przez `json.dumps` — bo to jedyny sposób, żeby obietnica
    „przepisujemy szablony, nie logikę" była faktem.
    """
    dane = asdict(pulpit)
    # Właściwości nie wchodzą do `asdict`, a front ich potrzebuje.
    dane["findingow"] = pulpit.findingow
    dane["ma_kwoty"] = pulpit.ma_kwoty
    dane["ma_porownanie"] = pulpit.ma_porownanie
    dane["dla_klienta"] = pulpit.dla_klienta
    for sekcja, zrodlo in zip(dane["sekcje"], pulpit.sekcje, strict=True):
        for metryka, oryginal in zip(sekcja["metryki"], zrodlo.metryki, strict=True):
            metryka["udzial"] = oryginal.udzial

    if pulpit.dla_klienta:
        for klucz in KLUCZE_WEWNETRZNE:
            dane.pop(klucz, None)
    return dane


# ── renderowanie ─────────────────────────────────────────────────────────

# Znak marki NA CIEMNE TŁO. Sidebar jest w ink, więc `cxlabs-mark-ink.png`
# z raportu był tam niewidoczny — wersja ciemna na ciemnym. Wyszło na zrzucie
# ekranu, nie w testach: HTML zawierał `<img>`, tylko nikt go nie widział.
LOGO_NA_CIEMNYM = "cxlabs-white.png"

SZABLON_KLIENTA = "pulpit_klient.html.j2"
SZABLON_INDEKSU = "pulpit_index.html.j2"

# Metryki wyciągnięte na górę, do kafli. Wybrane, bo to one zmieniają rozmowę:
# udział agentów w kontach i udział tablic z jednym autorem.
KAFLE_KLUCZOWE = ("agentów AI", "zdominowanych jednym autorem")


def _kafle_kluczowe(pulpit: Pulpit) -> tuple[Metryka, ...]:
    wszystkie = {m.nazwa: m for s in pulpit.sekcje for m in s.metryki}
    return tuple(wszystkie[n] for n in KAFLE_KLUCZOWE if n in wszystkie)


def wyrenderuj_pulpit(
    pulpit: Pulpit,
    *,
    klienci: list[PozycjaKlienta],
    katalog: Path = KATALOG_SZABLONOW,
) -> str:
    """Panel jednego klienta jako HTML. Reużywa środowiska jinja z `raport`."""
    szablon = srodowisko(katalog).get_template(SZABLON_KLIENTA)
    return szablon.render(
        p=pulpit,
        klienci=klienci,
        klientow=len(klienci),
        kafle_kluczowe=_kafle_kluczowe(pulpit),
        logo=zasob_data_uri(LOGO_NA_CIEMNYM, katalog=katalog / "zasoby"),
    )


def wyrenderuj_indeks(klienci: list[PozycjaKlienta], *, katalog: Path = KATALOG_SZABLONOW) -> str:
    """Panel wewnętrzny z listą klientów."""
    szablon = srodowisko(katalog).get_template(SZABLON_INDEKSU)
    return szablon.render(
        klienci=klienci,
        findingow_razem=sum(k.findingow for k in klienci),
        kwoty_razem=round(sum(k.suma_kwot for k in klienci), 2),
        logo=zasob_data_uri(LOGO_NA_CIEMNYM, katalog=katalog / "zasoby"),
    )
