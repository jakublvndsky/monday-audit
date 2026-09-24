"""Maskowanie PII przed wysłaniem czegokolwiek poza nasz serwer (plan, faza 4).

Powstaje **przed** podłączeniem Langfuse'a, nie po. Pierwszy trace wysłany bez
maskowania jest nie do cofnięcia, więc kolejność jest wymogiem, a nie stylem.

## Czym to NIE jest

To nie jest pierwsza linia obrony. Pierwszą pozostaje zasada z `CLAUDE.md`:
dane osobowe w ogóle nie wchodzą do kontekstu modelu. Warstwa tutaj łapie to,
co mimo tego przeciekło — i **dlatego liczy trafienia zamiast po cichu
podmieniać.** `[E-MAIL]` w trace to nie sukces maskowania, tylko sygnał, że
wyżej coś puściło. Cicha redakcja zamieniłaby alarm w kosmetykę.

## Dwa źródła PII i dwie różne odpowiedzi

1. **Ludzie z konta klienta** — znamy ich, mamy tabelę mapowania. Idą przez
   `zredaguj_pii` z `osoby.py` i dostają **pseudonim** (`[OSOBA:a1b2…]`), bo
   pseudonim zachowuje tożsamość między polami: widać, że ta sama osoba
   występuje w trzech miejscach, bez wiedzy kto to. **Do trace'ów pseudonim
   już nie wychodzi** — `bez_tozsamosci` zamienia go na `[OSOBA]` /
   `[IMIĘ] [NAZWISKO]` (decyzja Kuby z 2026-09-23).
2. **Klienci naszego klienta** — leady w itemach. Ich nie znamy i nie mamy jak
   poznać, więc zostaje wzorzec i zamiennik **bez tożsamości** (`[E-MAIL]`).
   Dwa różne maile dadzą ten sam `[E-MAIL]` i to jest cena, nie usterka.

## Czego ta warstwa NIE złapie

Powiedziane wprost, bo plan tego wymaga, a przemilczenie byłoby obietnicą,
której kod nie dotrzymuje:

- **imienia i nazwiska w treści pisanej przez klienta**, jeśli ta osoba nie
  jest użytkownikiem konta. „Tablica Jana Kowalskiego" przejdzie, o ile Jan
  Kowalski nie ma konta w monday. Wzorzec na „dwa słowa z wielkiej litery"
  zjadłby połowę nazw tablic, więc go nie ma.
- **numeru w gołym ciągu cyfr** — patrz `WZORZEC_TELEFONU` niżej.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from monday_audit.osoby import WZORZEC_EMAILA, MaPII, unikalny_klucz, zredaguj_pii

# Zamienniki BEZ tożsamości. Kuba poprosił wprost o taki kształt: „zamiast
# maila widzimy [E-MAIL]". Świadomie nie doklejamy tu hasza — osoba spoza konta
# nie ma wpisu w tabeli mapowania, więc hasz nie miałby czego identyfikować,
# a wyglądałby, jakby miał.
ZAMIENNIK_EMAILA = "[E-MAIL]"
ZAMIENNIK_TELEFONU = "[TELEFON]"
ZAMIENNIK_IBANU = "[IBAN]"

# ── telefon: wzorzec celowo WĄSKI ────────────────────────────────────────
#
# Goły ciąg cyfr NIE jest maskowany i to jest decyzja, nie niedopatrzenie.
# Identyfikatory monday to ciągi 9-10 cyfr (`9445123456`), a trace bez nich
# jest bezużyteczny — nie da się w nim wskazać tablicy ani itemu. Maskowanie
# gołych ciągów zamieniłoby każdy identyfikator w `[TELEFON]`.
#
# Dlatego wymagamy ZNAKU, którego identyfikator nie ma: prefiksu `+` albo
# separatorów grupujących.
#
# ZMIERZONE testem, nie założone: pierwsza wersja miała regułę „cyfry
# z separatorami" bez dolnego progu i zamieniała `2026-09-22 12:00`
# w `2026-[TELEFON]:00`. Stąd trzy ograniczenia, każde z własnym powodem:
#
#   * `(?=(?:\d[  -]?){9})` — co najmniej DZIEWIĘĆ cyfr. Odcina daty
#     i godziny, bo `09-22 12` to sześć.
#   * pierwsza grupa 2-3 cyfry — odcina rok. `2026-09-22` nie ma jak się
#     zacząć, bo `20` nie jest ciągnięte separatorem.
#   * `-` w lookbehindzie — odcina ogon daty. Bez tego dopasowanie ruszało
#     od `09` w `2026-09-22`.
#
# Kropka NIE jest separatorem, choć `501.234.567` się zdarza. Powód:
# `192.168.100.101` to dwanaście cyfr w czterech grupach i wpadłoby jako
# telefon. Adres IP w trace jest bardziej prawdopodobny niż telefon pisany
# kropkami, więc wybór padł na mniej fałszywych trafień.
#
# Co z tego wypada świadomie: `501234567` wpisane bez spacji w kolumnie
# telefonu. Ryzyko jest małe, bo z itemów czytamy WYŁĄCZNIE id, daty, grupę
# i kolumnę etapu (O46) — kolumna telefonu nie jest pobierana w ogóle.
WZORZEC_TELEFONU = re.compile(
    r"(?<![\w.-])(?:"
    r"\+\d[\d  .()-]{7,16}\d"  # międzynarodowy: +48 501 234 567
    r"|(?=(?:\d[  -]?){9})\d{2,3}(?:[  -]\d{2,3}){2,4}"  # 501-234-567
    r")(?![\d-])"
)

# IBAN: dwie litery kraju, dwie cyfry kontrolne, potem 11-30 znaków
# alfanumerycznych, opcjonalnie w grupach po cztery.
# Ogon `(?:sep?[A-Z0-9]{1,4})?`, nie `sep?[A-Z0-9]{0,4}`: stara postać pasowała
# do samej spacji ZA numerem i zjadała ją („[IBAN]jutro", review 2026-09-24).
WZORZEC_IBANU = re.compile(r"\b[A-Z]{2}\d{2}(?:[  ]?[A-Z0-9]{4}){2,8}(?:[  ]?[A-Z0-9]{1,4})?\b")

# Długość IBAN-u bez separatorów wg ISO 13616: od 15 (Norwegia) do 34 znaków.
# ZMIERZONE 2026-09-24: numery zamówień w nazwach tablic klienta
# („ZO12345678901-…", 13 znaków) dawały alarm maskowania w KAŻDYM runie
# (16–20 trafień), a fałszywy alarm powtarzany co run uczy go ignorować.
# Sumy kontrolnej celowo NIE sprawdzamy: IBAN z literówką to wciąż numer
# konta, a nadmiar maskowania kosztuje mniej niż przepuszczony numer.
DLUGOSC_IBANU = range(15, 35)
_SEPARATOR_IBANU = re.compile("[ \u00a0]")


def _to_iban(trafienie: str) -> bool:
    return len(_SEPARATOR_IBANU.sub("", trafienie)) in DLUGOSC_IBANU


def _zamiennik_ibanu(trafienie: re.Match[str]) -> str:
    return ZAMIENNIK_IBANU if _to_iban(trafienie.group(0)) else trafienie.group(0)


# Kolejność ma znaczenie: IBAN przed telefonem, bo `PL61 1090 1014 0000` to
# także cyfry w grupach i telefon zjadłby jego ogon. E-mail przed oboma, bo
# adres potrafi nieść cyfry z kropkami.
WZORCE: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("email", WZORZEC_EMAILA, ZAMIENNIK_EMAILA),
    ("iban", WZORZEC_IBANU, ZAMIENNIK_IBANU),
    ("telefon", WZORZEC_TELEFONU, ZAMIENNIK_TELEFONU),
)

# Typy, które umiemy przejść na wylot. Cokolwiek innego przerywa maskowanie —
# patrz `MaskowanieError`.
PROSTE_TYPY = (str, int, float, bool, type(None))


class MaskowanieError(RuntimeError):
    """Warstwa nie potrafi przetworzyć payloadu, więc payload nie wychodzi.

    ZAWODZIMY ZAMKNIĘTE. To jedyny sensowny kierunek błędu, gdy po drugiej
    stronie jest firma trzecia: trace, którego nie wysłaliśmy, kosztuje nas
    lukę w obserwowalności, a trace wysłany „na wszelki wypadek" kosztuje
    klienta jego dane. Wysłanie na wszelki wypadek byłoby wysłaniem.
    """


@dataclass(frozen=True)
class Zamaskowane:
    """Wynik maskowania: dane, co w nich podmieniono i gdzie.

    `trafienia` jest osobno od `sciezki`, bo odpowiadają na różne pytania.
    Licznik mówi „czy coś przeciekło i ile tego było" i idzie do metryki.
    Ścieżki mówią „w którym polu", czyli gdzie szukać przyczyny w kodzie.

    Ani jedno, ani drugie **nie niesie znalezionej wartości** — raport
    o wycieku PII, który sam wpisuje PII do logu, nie jest zabezpieczeniem.
    To ta sama zasada, co w `waliduj_brak_pii` w `osoby.py`.
    """

    dane: Any
    trafienia: Counter[str] = field(default_factory=Counter)
    sciezki: tuple[str, ...] = ()

    @property
    def czyste(self) -> bool:
        """Nic nie trzeba było maskować — czyli warstwa wyżej zadziałała."""
        return not self.trafienia

    @property
    def ile(self) -> int:
        return sum(self.trafienia.values())

    def podsumowanie(self) -> str:
        """Jedno zdanie do logu. Bez wartości, z kategoriami i liczbami."""
        if self.czyste:
            return "maskowanie: czysto"
        rozbicie = ", ".join(f"{kat} {ile}" for kat, ile in sorted(self.trafienia.items()))
        return f"maskowanie: {self.ile} trafień ({rozbicie}) w {len(self.sciezki)} polach"


# ── trace'y bez tożsamości (decyzja Kuby 2026-09-23) ─────────────────────
#
# `zamaskuj` zostawia pseudonim (`[OSOBA:a1b2…]`, gołe `user_hash`), bo
# pseudonim zachowuje tożsamość między polami. Dla Langfuse'a Kuba zdecydował
# inaczej: do trace'u ma trafiać `[OSOBA]` / `[IMIĘ] [NAZWISKO]`, bez hasza.
# Hasz liczony stałą solą da się odwrócić, mając dostęp do konta — więc jest
# daną osobową, a trace'om wystarczy wiedza, ŻE chodzi o osobę.
#
# Cena, nazwana wprost: w trace nie widać już, że dwa pola dotyczą TEJ SAMEJ
# osoby.
ZAMIENNIK_OSOBY = "[OSOBA]"
ZAMIENNIK_IMIENIA = "[IMIĘ] [NAZWISKO]"
_ZREDAGOWANA_OSOBA = re.compile(r"\[OSOBA:[^\]]*\]")
_ZREDAGOWANY_EMAIL = re.compile(r"\[EMAIL:[^\]]*\]")
_PSEUDONIM = re.compile(r"\b[0-9a-f]{16}\b")


def _bez_tozsamosci_tekst(tekst: str) -> str:
    tekst = _ZREDAGOWANA_OSOBA.sub(ZAMIENNIK_IMIENIA, tekst)
    tekst = _ZREDAGOWANY_EMAIL.sub(ZAMIENNIK_EMAILA, tekst)
    return _PSEUDONIM.sub(ZAMIENNIK_OSOBY, tekst)


def bez_tozsamosci(dane: Any, *, pomin_klucze: frozenset[str] = frozenset()) -> Any:
    """Pseudonimy → `[OSOBA]`, zredagowane nazwiska → `[IMIĘ] [NAZWISKO]`.

    Wołane PO `zamaskuj`, tylko dla trace'ów. Klucze z `pomin_klucze` zostają
    nietknięte — `prompt_hash` i `obraz_hash` mają ten sam kształt co
    pseudonim (16 znaków szesnastkowych), a są haszem PLIKU, nie osoby.
    Klucze słowników przechodzą tę samą zamianę co wartości (review
    2026-09-23: treść bywa kluczem).
    """
    if isinstance(dane, str):
        return _bez_tozsamosci_tekst(dane)
    if isinstance(dane, dict):
        wynik: dict[Any, Any] = {}
        for klucz, pod in dane.items():
            if klucz in pomin_klucze:
                wynik[klucz] = pod
                continue
            czysty = _bez_tozsamosci_tekst(klucz) if isinstance(klucz, str) else klucz
            wynik[unikalny_klucz(czysty, wynik)] = bez_tozsamosci(pod, pomin_klucze=pomin_klucze)
        return wynik
    if isinstance(dane, (list, tuple)):
        return [bez_tozsamosci(pod, pomin_klucze=pomin_klucze) for pod in dane]
    return dane


def zamaskuj_tekst(tekst: str) -> tuple[str, Counter[str]]:
    """Wzorce na jednym napisie. Publiczna, bo testy wzorców mają być krótkie."""
    trafienia: Counter[str] = Counter()
    wynik = tekst
    for nazwa, wzorzec, zamiennik in WZORCE:
        if nazwa == "iban":
            # Liczymy tylko to, co faktycznie podmieniono — ciąg krótszy od
            # IBAN-u zostaje i nie jest trafieniem.
            ile = sum(1 for m in wzorzec.finditer(wynik) if _to_iban(m.group(0)))
            wynik = wzorzec.sub(_zamiennik_ibanu, wynik)
        else:
            wynik, ile = wzorzec.subn(zamiennik, wynik)
        if ile:
            trafienia[nazwa] += ile
    return wynik, trafienia


def zamaskuj(
    dane: Any,
    wpisy: Sequence[MaPII] = (),
    *,
    sciezka: str = "",
    nie_ludzie: frozenset[str] = frozenset(),
) -> Zamaskowane:
    """Wszystko, co wychodzi poza serwer, przechodzi tędy. Bez wyjątków.

    Dwa przebiegi, w tej kolejności:

    1. `zredaguj_pii` — znani ludzie z konta na pseudonimy z tożsamością,
    2. wzorce — reszta na zamienniki bez tożsamości.

    Kolejność nie jest dowolna. Gdyby wzorce szły pierwsze, mail użytkownika
    konta zamieniłby się w `[E-MAIL]` i **stracilibyśmy informację, że to ta
    sama osoba, co w sąsiednim polu** — a to jest dokładnie to, po co w ogóle
    trzymamy tabelę mapowania.

    Podnosi `MaskowanieError` na typie, którego nie umie przejść. To nie jest
    nadgorliwość: obiekt, którego nie rozumiemy, mógłby mieć `__str__`
    wypisujący cokolwiek, a maskowanie po `str()` na oślep jest zgadywaniem.

    ## Klucze słowników są treścią tak samo jak wartości

    Pierwsza wersja maskowała tylko wartości — i przepuszczała dokładnie to, po
    co bramka do modelu powstała. Nazwa grupy i etykieta etapu siedzą w
    `rozklad` jako KLUCZE (`{"Leady od jan@firma.pl": 12}`), komunikat monday
    w `powody_bledow` też. Wychodziły bez zmian, a licznik mówił „czysto"
    (review 2026-09-23). Teraz klucz przechodzi przez te same wzorce, a ścieżka
    w `sciezki` jest składana z klucza JUŻ zamaskowanego — inaczej raport
    o wycieku sam by go wypisywał.
    """
    zredagowane, _ = zredaguj_pii(dane, wpisy, sciezka=sciezka, nie_ludzie=nie_ludzie)

    trafienia: Counter[str] = Counter()
    sciezki: list[str] = []

    def klucz_maskowany(klucz: Any, gdzie: str) -> Any:
        if isinstance(klucz, str):
            nowy, znalezione = zamaskuj_tekst(klucz)
            if znalezione:
                trafienia.update(znalezione)
                sciezki.append(f"{gdzie}.{nowy} (klucz)" if gdzie else f"{nowy} (klucz)")
            return nowy
        if isinstance(klucz, PROSTE_TYPY):
            return klucz
        raise MaskowanieError(
            f"nie umiem zamaskować klucza typu {type(klucz).__name__} "
            f"w polu {gdzie or '(korzeń)'} — trace nie wychodzi (wartości nie loguję)"
        )

    def przejdz(wartosc: Any, gdzie: str) -> Any:
        if isinstance(wartosc, str):
            nowy, znalezione = zamaskuj_tekst(wartosc)
            if znalezione:
                trafienia.update(znalezione)
                sciezki.append(gdzie or "(korzeń)")
            return nowy
        # `bool` przed `int` nie jest tu potrzebne, bo oba tylko przepuszczamy,
        # ale kolejność sprawdzeń zostaje jawna dla czytającego.
        if isinstance(wartosc, PROSTE_TYPY):
            return wartosc
        if isinstance(wartosc, dict):
            slownik: dict[Any, Any] = {}
            for klucz, pod in wartosc.items():
                czysty = unikalny_klucz(klucz_maskowany(klucz, gdzie), slownik)
                slownik[czysty] = przejdz(pod, f"{gdzie}.{czysty}" if gdzie else str(czysty))
            return slownik
        if isinstance(wartosc, (list, tuple)):
            return [przejdz(pod, f"{gdzie}[{numer}]") for numer, pod in enumerate(wartosc)]
        raise MaskowanieError(
            f"nie umiem zamaskować wartości typu {type(wartosc).__name__} "
            f"w polu {gdzie or '(korzeń)'} — trace nie wychodzi (wartości nie loguję)"
        )

    return Zamaskowane(
        dane=przejdz(zredagowane, sciezka),
        trafienia=trafienia,
        sciezki=tuple(sciezki),
    )
