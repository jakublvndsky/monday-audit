"""Collector — użytkownicy i pseudonimizacja (etap 3.4).

**TO JEST GRANICA PII.** Zaimplementowana raz, tutaj, i nigdzie więcej.
Reszta systemu widzi wyłącznie `user_hash`.

Trzy mechanizmy, nie trzy zasady:

1. **Sól jest obowiązkowa.** Identyfikatory monday to małe liczby, więc hash
   bez soli jest odwracalny tablicą tęczową w kilka sekund. Brak soli
   przerywa run, a nie schodzi po cichu na hashowanie bez niej. Sam odczyt
   soli ze środowiska siedzi w `konfiguracja` (D12) — tutaj zostaje minimalna
   długość i typ wyjątku, bo to reguła granicy PII, nie reguła configu.
2. **Snapshot budowany z listy dozwolonych pól**, nie przez usuwanie
   zabronionych. Nowe pole w API nie wycieknie samo z siebie.
3. **Walidacja antyprzeciekowa w czasie działania**, nie tylko w testach.
   Payload jest skanowany wzorcem e-maila i nazwiskami z mapowania przed
   zwróceniem. Komunikat błędu nie zawiera znalezionej wartości — inaczej
   sam byłby wyciekiem, tym razem do logów.

Mapowanie `user_hash` → imię i e-mail trafia do tabeli `osoby_mapowanie`,
do której **agent nie ma żadnego narzędzia** (D6). Deanonimizuje dopiero
renderer w 3.12.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from monday_audit.zbieranie.klient import MondayClient

logger = logging.getLogger(__name__)

ZAPYTANIE_UZYTKOWNICY = """
query ($p: Int!, $limit: Int!) {
  users (limit: $limit, page: $p) {
    id name email kind status is_deleted is_email_confirmed
    created_at became_active_at last_activity title
    teams { id name }
  }
}
"""

# Rodzaje konta widziane na CXLABS 2026-08-01. **Zbiór NIE jest zamknięty** —
# enum `UserKind` w schemacie (`all`, `guests`, `non_guests`, `non_pending`) to
# typ argumentu filtrującego, nie typ zwracanego pola, więc API nie deklaruje
# listy wartości. Nieznany rodzaj idzie do `discovery.nieznane_rodzaje` i jest
# policzony, a nie wciśnięty do „member".
RODZAJ_ADMIN = "admin"
RODZAJ_GOSC = "guest"
RODZAJ_CZLONEK = "member"
RODZAJ_PODGLAD = "view_only"
RODZAJ_AGENT = "personal_agent_member"

# Dwa kolejne rodzaje agentowe, zmierzone na CXLABS 2026-09-22 (4 konta: trzy
# `external_agent_member`, jedno `external_agent_detached_member`). Potwierdzenie
# ostrzeżenia wyżej: zbiór faktycznie nie był zamknięty.
RODZAJ_AGENT_ZEWNETRZNY = "external_agent_member"
RODZAJ_AGENT_ODLACZONY = "external_agent_detached_member"

# JEDNO miejsce, w którym stoi „co jest agentem". Do 2026-09-22 ta wiedza była
# w dwóch kopiach — tutaj i jako literał w `pulpit.py` — więc dopisanie rodzaju
# w jednym miejscu zostawiało drugie nieaktualne. Skutek był widoczny: cztery
# konta agentów zewnętrznych pokazywały się w zakładce „Ludzie" JAKO LUDZIE.
RODZAJE_AGENTOW = frozenset({RODZAJ_AGENT, RODZAJ_AGENT_ZEWNETRZNY, RODZAJ_AGENT_ODLACZONY})

ZNANE_RODZAJE = frozenset(
    {RODZAJ_ADMIN, RODZAJ_GOSC, RODZAJ_CZLONEK, RODZAJ_PODGLAD} | RODZAJE_AGENTOW
)

# `UserStatus` JEST zamkniętym enumem: ACTIVE, INACTIVE, PENDING.
STATUS_AKTYWNY = "ACTIVE"
STATUS_NIEAKTYWNY = "INACTIVE"
STATUS_OCZEKUJE = "PENDING"

# Sól krótsza od tego nie daje sensownej ochrony, a jej wyciek pozwala
# zdeanonimizować całą tabelę mapowania (D11). Traktuj jak klucz prywatny.
MIN_DLUGOSC_SOLI = 16

# 64 bity pseudonimu. Dla kont rzędu tysięcy użytkowników prawdopodobieństwo
# kolizji jest pomijalne, a krótszy hash jest czytelny w snapshocie i tańszy
# w kontekście modelu niż pełne 64 znaki.
DLUGOSC_HASHA = 16

WZORZEC_EMAILA = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Tokeny krótsze od tego dają fałszywe trafienia (imię „Ola" w słowie
# „Solaris"), więc skan nazwisk ogranicza się do dłuższych.
MIN_TOKEN_SKANU = 4


class PseudonimizacjaError(RuntimeError):
    """Granica PII została naruszona albo nie da się jej utrzymać."""


@dataclass(frozen=True, slots=True)
class WpisPII:
    """Jedna linia do `osoby_mapowanie`. Nazwa krzyczy celowo."""

    user_hash: str
    imie_nazwisko: str | None
    email: str | None


class Mapowanie(Protocol):
    """Odbiorca PII. Implementacja: `monday_audit.baza.MapowanieOsob`."""

    def zapisz_wiele(self, wpisy: Iterable[WpisPII]) -> int: ...


class MaPII(Protocol):
    """Cokolwiek, co niesie imię i e-mail — wejście dla walidacji.

    Protokół, a nie `WpisPII`: 3.8 waliduje złożony snapshot przeciwko
    wpisom ODCZYTANYM z bazy (`baza.WpisOdczytany`), a nie tym świeżo
    zebranym. Walidator potrzebuje tylko dwóch pól.
    """

    @property
    def imie_nazwisko(self) -> str | None: ...

    @property
    def email(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class Osoba:
    """Użytkownik BEZ PII — dokładnie to, co wolno w snapshocie (3.4).

    Model oparty na `kind` + `status`, nie na flagach `is_*` (O17). Powód nie
    jest tylko taki, że flagi giną w API 2026-10: `kind` niesie **więcej**.
    Zmierzone na CXLABS: z 95 rekordów 36 to `personal_agent_member`, a 28 to
    `view_only`. Flagi `is_admin`/`is_guest` pokazywały jedno i drugie jako
    zwykłego członka, więc `ZOMBIE_ACCOUNT` liczyłby „nieaktywnych
    użytkowników" po 95 rekordach i zawyżył wynik czterokrotnie — wystawiając
    klientowi rachunek za konta, które nie zajmują płatnych miejsc ani nie są
    ludźmi.
    """

    user_hash: str
    title: str | None
    zespoly: tuple[str, ...]
    kind: str | None
    status: str | None
    is_deleted: bool
    is_email_confirmed: bool
    created_at: str | None
    became_active_at: str | None
    last_activity: str | None

    # ŚWIADOMIE BEZ `is_verified`. Pole istnieje w API 2026-07 i ginie
    # w 2026-10, a `is_email_confirmed` NIE jest jego zamiennikiem — zmierzone
    # 2026-08-01: 58 z 95 osób ma `is_verified=True` przy
    # `is_email_confirmed=False`. Rezygnacja, nie przemianowanie, i była
    # decyzją: sygnał był prawdziwy u 94 z 95 rekordów, czyli nie nosił
    # informacji, a actionable przypadek („zaproszony, nie wszedł") łapie
    # `status == PENDING`. Utrzymywanie go blokowałoby cały collector na
    # następnej wersji API za jedno pole bez wartości. Patrz O17.

    @property
    def jest_adminem(self) -> bool:
        return self.kind == RODZAJ_ADMIN

    @property
    def jest_gosciem(self) -> bool:
        return self.kind == RODZAJ_GOSC

    @property
    def jest_agentem(self) -> bool:
        """Konto agenta AI. Nie człowiek, więc nie kandydat na `ZOMBIE_ACCOUNT`."""
        return self.kind == RODZAJ_AGENT

    @property
    def zajmuje_miejsce(self) -> bool:
        """Czy rekord w ogóle jest kandydatem do rozliczenia licencji.

        Zmierzone: `active_members_count = 19` na koncie CXLABS to dokładnie
        `admin` (10) + `member` (9). Goście, konta podglądowe i agenci nie
        wchodzą do tej liczby, więc nie wolno ich wliczać do wyceny (O7).
        """
        return self.kind in {RODZAJ_ADMIN, RODZAJ_CZLONEK}

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "user_hash": self.user_hash,
            "title": self.title,
            "zespoly": list(self.zespoly),
            "kind": self.kind,
            "status": self.status,
            "is_deleted": self.is_deleted,
            "is_email_confirmed": self.is_email_confirmed,
            "created_at": self.created_at,
            "became_active_at": self.became_active_at,
            "last_activity": self.last_activity,
        }


@dataclass(frozen=True, slots=True)
class WynikOsob:
    osoby: tuple[Osoba, ...]
    zapisanych_mapowan: int
    discovery: dict[str, Any]

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "uzytkownicy": [o.do_snapshotu() for o in self.osoby],
            "podsumowanie": self.podsumowanie(),
            "discovery": dict(self.discovery),
        }

    @property
    def hashe(self) -> frozenset[str]:
        """Pseudonimy użytkowników konta — wejście dla heurystyki z 3.7.

        Zwracamy hashe, nie surowe identyfikatory: 3.7 musi tylko wiedzieć,
        czy autor wpisu w logu jest użytkownikiem konta, a do tego hash
        wystarcza. Identyfikator osoby nie ma powodu krążyć między etapami.
        """
        return frozenset(o.user_hash for o in self.osoby)

    def podsumowanie(self) -> dict[str, int]:
        """Liczniki dla detektorów.

        `razem` NIE jest liczbą ludzi i nie wolno go tak używać —
        `zajmujacych_miejsce` jest. Na CXLABS to różnica 95 wobec 19.
        """
        return {
            "razem": len(self.osoby),
            "zajmujacych_miejsce": sum(1 for o in self.osoby if o.zajmuje_miejsce),
            "adminow": sum(1 for o in self.osoby if o.jest_adminem),
            "gosci": sum(1 for o in self.osoby if o.jest_gosciem),
            "agentow": sum(1 for o in self.osoby if o.jest_agentem),
            "tylko_podglad": sum(1 for o in self.osoby if o.kind == RODZAJ_PODGLAD),
            "aktywnych": sum(1 for o in self.osoby if o.status == STATUS_AKTYWNY),
            "nieaktywnych": sum(1 for o in self.osoby if o.status == STATUS_NIEAKTYWNY),
            "oczekujacych": sum(1 for o in self.osoby if o.status == STATUS_OCZEKUJE),
            "usunietych": sum(1 for o in self.osoby if o.is_deleted),
            "z_potwierdzonym_mailem": sum(1 for o in self.osoby if o.is_email_confirmed),
            "bez_last_activity": sum(1 for o in self.osoby if not o.last_activity),
            "bez_became_active_at": sum(1 for o in self.osoby if not o.became_active_at),
            "bez_title": sum(1 for o in self.osoby if not o.title),
            "bez_zespolu": sum(1 for o in self.osoby if not o.zespoly),
        }

    def po_rodzaju(self) -> dict[str, int]:
        """Rozkład `kind`. Osobno od podsumowania, bo zbiór nie jest zamknięty."""
        licznik: dict[str, int] = {}
        for osoba in self.osoby:
            klucz = osoba.kind or "brak"
            licznik[klucz] = licznik.get(klucz, 0) + 1
        return dict(sorted(licznik.items()))


def policz_hash(client_id: str, user_id: str | int, sol: bytes) -> str:
    """Stabilny pseudonim użytkownika: HMAC-SHA256 po `client_id:user_id`.

    Publiczna i czysta, bo 3.5 musi policzyć te same hashe dla `owners`
    i `subscribers` tablic. Ta sama sól i ten sam `client_id` dają ten sam
    wynik między runami — inaczej nie da się porównać snapshotu #1 z #4.

    `client_id` w wiadomości, mimo że sól jest już per klient: dwa konta
    obsługiwane tą samą solą przez pomyłkę nie dadzą wtedy wspólnych hashy.
    """
    wiadomosc = f"{client_id}:{user_id}".encode()
    return hmac.new(sol, wiadomosc, hashlib.sha256).hexdigest()[:DLUGOSC_HASHA]


def _pary_do_redakcji(wpisy: Sequence[MaPII]) -> tuple[tuple[str, str], ...]:
    """(szukane, pseudonim) — najdłuższe najpierw, żeby nie ciąć w środku.

    Bierzemy tylko nazwy wieloczłonowe: „CXLABS" to konto serwisowe i jego
    podmiana zniszczyłaby nazwy zespołów bez powodu (O11).
    """
    pary: list[tuple[str, str]] = []
    for wpis in wpisy:
        haszyk = getattr(wpis, "user_hash", "") or "?"
        imie = (wpis.imie_nazwisko or "").strip()
        if len(imie.split()) >= 2:
            pary.append((imie, f"[OSOBA:{haszyk}]"))
        email = (wpis.email or "").strip()
        if email:
            pary.append((email, f"[EMAIL:{haszyk}]"))
    return tuple(sorted(pary, key=lambda para: -len(para[0])))


# Człon imienia albo nazwiska krótszy od tego nie jest redagowany sam: „Al",
# „Ed" to częściej skrót niż osoba. Trzy litery łapią „Jan" i „Ola", a granice
# słów i wielka litera chronią „Solaris" i „jana" (słowo, nie imię).
MIN_CZLONU_IMIENIA = 3


# Człony, które kolidowałyby z naszymi własnymi znacznikami po redakcji.
_ZNACZNIKI = frozenset({"Osoba", "Email", "OSOBA", "EMAIL"})


def _czlony_do_redakcji(
    wpisy: Sequence[MaPII], nie_ludzie: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    """Pojedyncze człony imion i nazwisk LUDZI → (wariant, zamiennik).

    ## ZMIERZONE 2026-09-24 na pełnym koncie CXLABS

    Klient nazywa tablice „Zadania Jacek" i workspace'y „Radek Leady", czyli
    samym imieniem. Redakcja po pełnym „Imię Nazwisko" tego nie widziała, więc
    trzy imiona użytkowników konta doszły do modelu i do trace'u w Langfuse.

    Dlaczego to nie powtarza porażki skanu tokenowego z 3.8 (54 fałszywe
    trafienia): tamte tokeny pochodziły z kont SERWISOWYCH i agentów — „CXLABS",
    „AI Agent" — a te słowa naturalnie żyją w nazwach produktów. Tu biorą udział
    wyłącznie osoby o nazwie co najmniej dwuczłonowej, spoza `nie_ludzie`
    (agenci AI), a człon, który występuje też w nazwie agenta, odpada. Do tego
    wielka litera i granice słów: „Jacek" tak, „jacek" w zdaniu i „Jackson" nie.

    Zamiennik: pseudonim osoby, gdy człon jest jej JEDYNY na koncie (raport
    z nazwiskami umie go rozwinąć), `[OSOBA]`, gdy dzieli go kilka osób — wtedy
    wybranie jednej byłoby zgadywaniem, a zgadnięta tożsamość jest gorsza niż
    żadna.

    Świadomy kompromis: nazwisko będące zwykłym słowem („Maj", „Lis",
    „Kwiecień") zredaguje też „Raport Maj 2026". Nadmiar redakcji psuje nazwę
    obiektu; jej brak wysyła osobę do modelu — wybieramy pierwsze.
    """
    wlasciciele: dict[str, set[str]] = {}
    zakazane: set[str] = set()
    for wpis in wpisy:
        haszyk = getattr(wpis, "user_hash", "") or "?"
        czlony = [c.strip(".,;:()'\"") for c in (wpis.imie_nazwisko or "").split()]
        if haszyk in nie_ludzie:
            zakazane.update(c[:1].upper() + c[1:] for c in czlony)
            continue
        if len(czlony) < 2:
            continue
        # Człon z łącznikiem („Kowalska-Nowak", „Anna-Maria") to dwa człony —
        # klient pisze je też osobno. Całość idzie jako wariant dodatkowy.
        for czlon in [*czlony, *(c for cz in czlony if "-" in cz for c in cz.split("-"))]:
            czysty = czlon.replace("-", "")
            if len(czlon) >= MIN_CZLONU_IMIENIA and czysty.isalpha():
                # Postać kanoniczna z wielką literą — `name` bywa wpisane małymi.
                kanon = czlon[0].upper() + czlon[1:]
                wlasciciele.setdefault(kanon, set()).add(haszyk)
    wynik: list[tuple[str, str]] = []
    for czlon, hasze in wlasciciele.items():
        if czlon in zakazane or czlon in _ZNACZNIKI:
            continue
        zamiennik = f"[OSOBA:{next(iter(hasze))}]" if len(hasze) == 1 else "[OSOBA]"
        for wariant in {czlon, czlon.upper()}:
            wynik.append((wariant, zamiennik))
    return tuple(sorted(wynik, key=lambda p: (-len(p[0]), p[0])))


def _jeden_wzorzec(szukane: Sequence[str], flagi: int = 0) -> re.Pattern[str] | None:
    """Jedna alternatywa zamiast wzorca na osobę — ZMIERZONE w review 2026-09-24.

    Redakcja wołała `re.sub` osobno dla każdej osoby i każdego napisu: 60 osób
    na 32 tys. napisów to 4 s, 200 osób — 12,8 s, a 1000 osób nie skończyło się
    w 10 minut (powyżej 512 wzorców cache `re` przestaje pomagać). Tu jest jeden
    przebieg po napisie. Najdłuższe najpierw: alternatywa bierze pierwszą
    pasującą gałąź, więc „Jan Kowalski" wygrywa z „Jan".

    GRANICE SŁÓW są kluczowe. Bez nich konto serwisowe „AI Agent" wpasowuje się
    w nazwę workspace „monday AI Agents" i redakcja psuje 105 rekordów,
    zamieniając je na „monday [OSOBA:...]s" (zmierzone na CXLABS przy 3.8).
    """
    if not szukane:
        return None
    return re.compile(rf"\b(?:{'|'.join(re.escape(s) for s in szukane)})\b", flagi)


def unikalny_klucz(klucz: Any, zajete: dict[Any, Any]) -> Any:
    """Klucz po redakcji, który nie nadpisze sąsiada w tym samym słowniku.

    Redakcja kluczy potrafi skleić dwa różne w jeden: dwa maile dają dwa razy
    `[E-MAIL]`. Zwykłe przypisanie po cichu zgubiłoby jedną z wartości — a to
    jest liczba z rozkładu, na którą model potem wskaże. Stąd dopisek `(2)`.
    """
    if klucz not in zajete or not isinstance(klucz, str):
        return klucz
    numer = 2
    while f"{klucz} ({numer})" in zajete:
        numer += 1
    return f"{klucz} ({numer})"


def zredaguj_pii(
    dane: Any,
    wpisy: Sequence[MaPII],
    *,
    sciezka: str = "",
    nie_ludzie: frozenset[str] = frozenset(),
) -> tuple[Any, list[str]]:
    """Podmienia znane imiona i adresy w treści klienta na pseudonimy.

    Klient potrafi nazwać tablicę, kolumnę albo zespół imieniem osoby — i wtedy
    PII wchodzi do snapshotu nie przez nasze pole `name`, a przez treść, którą
    on sam napisał. Usunięcie takiej nazwy zabrałoby sygnał (fakt, że tablica
    jest nazwana po kimś, jest informacją audytową), więc **podmieniamy ją na
    pseudonim tej samej osoby**. Renderer w 3.12 umie to rozwinąć z powrotem.

    **Klucze słowników też.** Treść klienta bywa kluczem, nie wartością: rozkład
    po grupach to `{"Anna Nowak": 20}`, a `powody_bledow` to komunikat monday
    w roli klucza. Pierwsza wersja przechodziła tylko po wartościach i takie
    nazwisko przepuszczała bez śladu (review 2026-09-23).

    Zwraca strukturę po redakcji i listę ŚCIEŻEK, w których coś podmieniono —
    ścieżki, nie wartości, bo raport z runu nie może być wyciekiem. Dlatego
    ścieżka składa się z klucza JUŻ zredagowanego.

    Po pełnych nazwach idą POJEDYNCZE człony imion i nazwisk ludzi
    (`_czlony_do_redakcji`) — `nie_ludzie` to pseudonimy agentów AI, których
    nazwy są słowami produktu, a nie osobą.
    """
    pary = _pary_do_redakcji(wpisy)
    czlony = _czlony_do_redakcji(wpisy, nie_ludzie)
    # Pełne nazwy i adresy bez względu na wielkość liter, człony — z nią.
    # Pierwsza pasująca para wygrywa, jak w wersji z pętlą.
    zamiennik_pary: dict[str, str] = {}
    for szukane, pseudonim in pary:
        zamiennik_pary.setdefault(szukane.lower(), pseudonim)
    zamiennik_czlonu = dict(czlony)
    wzorzec_par = _jeden_wzorzec([s for s, _ in pary], re.IGNORECASE)
    wzorzec_czlonow = _jeden_wzorzec([s for s, _ in czlony])

    def _zamiennik_pary(trafienie: str) -> str:
        # `lower()` i `IGNORECASE` składają wielkość liter prawie zawsze tak samo;
        # na rzadki wyjątek Unicode — szukanie wprost, żeby NIE zostawić trafienia.
        znany = zamiennik_pary.get(trafienie.lower())
        if znany is not None:
            return znany
        return next(
            p for s, p in pary if re.fullmatch(re.escape(s), trafienie, flags=re.IGNORECASE)
        )

    def redaguj_tekst(tekst: str) -> str:
        if wzorzec_par is not None:
            tekst = wzorzec_par.sub(lambda m: _zamiennik_pary(m.group(0)), tekst)
        # Człony PO pełnych nazwach: „Jan Kowalski" ma zostać jednym
        # pseudonimem, a nie dwoma sklejonymi.
        if wzorzec_czlonow is not None:
            tekst = wzorzec_czlonow.sub(lambda m: zamiennik_czlonu[m.group(0)], tekst)
        return tekst

    def redaguj(wartosc: Any, gdzie: str) -> tuple[Any, list[str]]:
        if isinstance(wartosc, str):
            wynik = redaguj_tekst(wartosc)
            return wynik, ([gdzie] if wynik != wartosc else [])
        if isinstance(wartosc, dict):
            nowy: dict[Any, Any] = {}
            trafienia: list[str] = []
            for klucz, pod in wartosc.items():
                czysty = redaguj_tekst(klucz) if isinstance(klucz, str) else klucz
                czysty = unikalny_klucz(czysty, nowy)
                tutaj = f"{gdzie}.{czysty}" if gdzie else str(czysty)
                if czysty != klucz:
                    trafienia.append(f"{tutaj} (klucz)")
                nowy[czysty], znalezione = redaguj(pod, tutaj)
                trafienia += znalezione
            return nowy, trafienia
        if isinstance(wartosc, list):
            nowa_lista: list[Any] = []
            trafienia = []
            for numer, pod in enumerate(wartosc):
                element, znalezione = redaguj(pod, f"{gdzie}[{numer}]")
                nowa_lista.append(element)
                trafienia += znalezione
            return nowa_lista, trafienia
        return wartosc, []

    if not pary and not czlony:
        return dane, []
    return redaguj(dane, sciezka)


def waliduj_brak_pii(payload: str, wpisy: Sequence[MaPII]) -> None:
    """Twarda granica: przerywa run przy JEDNOZNACZNYM wycieku PII.

    Sprawdza dwie rzeczy, obie bez fałszywych trafień:

    1. **Cokolwiek w formacie adresu e-mail.** Nie ma legalnego powodu, żeby
       adres pojawił się w snapshocie.
    2. **Pełne imię i nazwisko jako ciągły napis.** Tablica nazwana „Jan
       Kowalski" zostanie złapana; „CXLABS Demo" nie, bo to nie jest
       kształt imienia i nazwiska.

    Wersja pierwotna skanowała POJEDYNCZE tokeny z pól `name` i przerywała
    run przy pierwszym trafieniu. Na koncie CXLABS dała 54 trafienia z 3
    tokenów, wszystkie fałszywe: konta serwisowe, których `name` to nazwa
    firmy albo produktu, a nie osoby — a te słowa naturalnie występują
    w nazwach zespołów i stanowiskach pisanych przez klienta. Skan tokenowy
    został więc przeniesiony do `policz_podejrzenia_pii`, gdzie **liczy
    i raportuje**, zamiast przerywać audyt na treści pisanej przez klienta.

    Nazwy jednowyrazowe (konta serwisowe, boty) nie wchodzą do twardego
    sprawdzenia — dla nich zostaje licznik podejrzeń.

    Komunikat NIE zawiera znalezionej wartości: błąd o wycieku PII, który
    sam wpisuje PII do logów, nie jest zabezpieczeniem.
    """
    if WZORZEC_EMAILA.search(payload):
        raise PseudonimizacjaError(
            "payload snapshotu zawiera coś w formacie adresu e-mail — "
            "przeciek PII, run przerwany (wartości nie loguję)"
        )

    maly = payload.lower()
    pelne = 0
    for wpis in wpisy:
        imie = (wpis.imie_nazwisko or "").strip()
        if len(imie.split()) < 2:
            continue
        # Granice słów, z tego samego powodu co w `zredaguj_pii`: „AI Agent"
        # w „AI Agents" to nazwa produktu, nie wyciek nazwiska.
        if re.search(rf"\b{re.escape(imie.lower())}\b", maly):
            pelne += 1

    if pelne:
        raise PseudonimizacjaError(
            f"payload snapshotu zawiera {pelne} pełnych imion i nazwisk z tabeli "
            f"mapowania — przeciek PII, run przerwany (wartości nie loguję)"
        )


def policz_podejrzenia_pii(payload: str, wpisy: Sequence[MaPII]) -> int:
    """Miękki skan: ile tokenów z pól `name` pojawia się w payloadzie.

    Nie przerywa runu, bo trafienia bywają fałszywe (nazwa firmy w nazwie
    zespołu, słowo ze stanowiska). Ale nie milczy: liczba idzie do snapshotu
    i do logu, żeby człowiek zobaczył ją przy BRAMIE po 3.8. Zero znaczy
    „czysto", wartość niezerowa znaczy „przejrzyj ręcznie", a nie „wyciek".
    """
    maly = payload.lower()
    trafione: set[str] = set()

    for wpis in wpisy:
        for czesc in re.split(r"[\s,.]+", wpis.imie_nazwisko or ""):
            czysty = czesc.strip().lower()
            if len(czysty) < MIN_TOKEN_SKANU or czysty in trafione:
                continue
            if re.search(rf"\b{re.escape(czysty)}\b", maly):
                trafione.add(czysty)

    return len(trafione)


def _osoba(surowy: dict[str, Any], user_hash: str) -> Osoba:
    """Buduje rekord z LISTY DOZWOLONYCH pól.

    Kolejność ma znaczenie: nie usuwamy `name` i `email` z kopii słownika,
    tylko przepisujemy wyłącznie to, co wolno. Nowe pole w API nie wycieknie,
    bo nikt go tutaj nie wpisał.
    """
    zespoly = surowy.get("teams") or []
    return Osoba(
        user_hash=user_hash,
        title=surowy.get("title") or None,
        zespoly=tuple(
            str(z.get("name", "")) for z in zespoly if isinstance(z, dict) and z.get("name")
        ),
        kind=surowy.get("kind") or None,
        status=surowy.get("status") or None,
        is_deleted=bool(surowy.get("is_deleted")),
        is_email_confirmed=bool(surowy.get("is_email_confirmed")),
        created_at=surowy.get("created_at") or None,
        became_active_at=surowy.get("became_active_at") or None,
        last_activity=surowy.get("last_activity") or None,
    )


async def zbierz_osoby(
    klient: MondayClient,
    *,
    client_id: str,
    sol: bytes,
    mapowanie: Mapowanie,
    limit: int = 500,
) -> WynikOsob:
    """Zbiera użytkowników, rozdziela PII od snapshotu i waliduje granicę.

    PII żyje w pamięci tylko na czas zbierania, trafia do `osoby_mapowanie`
    i nie wychodzi z tej funkcji — `WynikOsob` nie ma pola, w które dałoby
    się je wpisać.
    """
    osoby: list[Osoba] = []
    do_mapowania: list[WpisPII] = []

    async for surowy in klient.paginate(
        ZAPYTANIE_UZYTKOWNICY,
        "users",
        {"limit": limit},
        etykieta="users",
    ):
        user_id = surowy.get("id")
        if user_id is None:
            raise PseudonimizacjaError("użytkownik bez `id` — nie da się policzyć pseudonimu")

        user_hash = policz_hash(client_id, str(user_id), sol)
        osoby.append(_osoba(surowy, user_hash))
        do_mapowania.append(
            WpisPII(
                user_hash=user_hash,
                imie_nazwisko=surowy.get("name") or None,
                email=surowy.get("email") or None,
            )
        )

    zapisanych = mapowanie.zapisz_wiele(do_mapowania)

    z_aktywnoscia = sum(1 for o in osoby if o.last_activity)
    nieznane_rodzaje = sorted({o.kind for o in osoby if o.kind and o.kind not in ZNANE_RODZAJE})
    bez_rodzaju = sum(1 for o in osoby if not o.kind)
    discovery: dict[str, Any] = {
        # Potwierdzone na CXLABS 2026-07-30: pole zwraca ISO-8601 ze strefą.
        "last_activity_dostepne": z_aktywnoscia > 0,
        "last_activity_wypelnione": z_aktywnoscia,
        "last_activity_razem": len(osoby),
        # Model `kind` + `status` zamiast flag `is_*` (O17). Rozkład idzie do
        # snapshotu w całości, bo od niego zależy wycena licencji: `razem`
        # to nie liczba ludzi.
        "po_rodzaju": {},
        "nieznane_rodzaje": nieznane_rodzaje,
        "bez_rodzaju": bez_rodzaju,
        # Zapis utraty sygnału, żeby detektor nie szukał pola, którego nie ma,
        # i żeby raport nie udawał, że „niezweryfikowanych" po prostu nie było.
        "is_verified_porzucone": "brak w API 2026-10; is_email_confirmed to inne pole",
    }
    if nieznane_rodzaje:
        logger.warning(
            "[DISCOVERY] nieznane rodzaje konta: %s — policzone w `po_rodzaju`, "
            "ale detektory ich nie klasyfikują; uzupełnij ZNANE_RODZAJE",
            ", ".join(nieznane_rodzaje),
        )
    if bez_rodzaju:
        logger.warning(
            "%d użytkowników bez pola `kind` — dla nich nie da się orzec, "
            "czy zajmują płatne miejsce",
            bez_rodzaju,
        )
    logger.info(
        "[DISCOVERY] %s users.last_activity wypełnione u %d z %d",
        "✅" if z_aktywnoscia else "❌",
        z_aktywnoscia,
        len(osoby),
    )
    if z_aktywnoscia < len(osoby):
        # `null` znaczy „nie wiem", nie „nieaktywny od zawsze". ZOMBIE_ACCOUNT
        # nie może liczyć tych kont jako martwych bez sygnału z 3.7.
        logger.warning(
            "%d użytkowników bez last_activity — dla nich sygnał aktywności musi "
            "przyjść z activity logs (3.7), nie z tego pola",
            len(osoby) - z_aktywnoscia,
        )

    wynik = WynikOsob(
        osoby=tuple(osoby),
        zapisanych_mapowan=zapisanych,
        discovery=discovery,
    )
    discovery["po_rodzaju"] = wynik.po_rodzaju()
    logger.info(
        "[DISCOVERY] rodzaje kont: %s; płatne miejsca zajmuje %d z %d rekordów",
        discovery["po_rodzaju"],
        wynik.podsumowanie()["zajmujacych_miejsce"],
        len(osoby),
    )

    # Mechanizm, nie polityka: fragment jest sprawdzany, zanim ktokolwiek go
    # zobaczy. Twarde przerwanie przy jednoznacznym wycieku, licznik przy
    # podejrzeniach z treści pisanej przez klienta.
    payload = json.dumps(wynik.do_snapshotu(), ensure_ascii=False)
    waliduj_brak_pii(payload, do_mapowania)
    discovery["podejrzenia_pii_w_tekstach"] = policz_podejrzenia_pii(payload, do_mapowania)

    if discovery["podejrzenia_pii_w_tekstach"]:
        logger.warning(
            "%d tokenów z pól `name` występuje w `title` albo nazwach zespołów. "
            "Zwykle to konta serwisowe i słowa ze stanowisk, nie wyciek — "
            "ale przejrzyj to ręcznie przy BRAMIE po 3.8",
            discovery["podejrzenia_pii_w_tekstach"],
        )

    logger.info(
        "zebrano %d użytkowników, mapowań zapisanych: %d, pełnych imion w snapshocie: brak",
        len(osoby),
        zapisanych,
    )
    return wynik
