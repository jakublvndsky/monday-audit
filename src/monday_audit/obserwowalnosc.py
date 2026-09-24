"""Trace jednej hipotezy: co wychodzi na zewnątrz i w jakim kształcie (faza 4).

## Skąd bierzemy dane

Z wyniku sesji zebranego przez nasz kod — nie ze strumienia wiadomości SDK.
Trace powstaje PO sesji z gotowych słowników, a każdy z nich przechodzi przez
`zamaskuj` i `bez_tozsamosci`. Nie ma drugiej drogi na zewnątrz.

## Co wychodzi — granica danych, zapisana wprost

Do 2026-09-24 obowiązywało „Langfuse nie dostaje niczego, czego nie mamy
u siebie". **Już nie obowiązuje** i to jest decyzja Kuby, nie dryf: tracing
narzędzi wysyła argumenty i WYNIKI narzędzi (wycinki snapshotu, które widział
model), a tych nie przechowujemy — tryb pamięci (5c) nic o osobie nie zapisuje.
Wychodzi więc:

* hipotezy z faktami i zadanie modelu (definicje klas, polecenie),
* wyniki narzędzi — w postaci, którą dostał model,
* rozstrzygnięcia modelu i nazwy reguł walidacji.

Nie wychodzi: prompt systemowy i obraz konta (hasze), pseudonimy
(`[OSOBA]`), e-maile, telefony, numery kont (wzorce).

Skutek uboczny jest równie ważny: pętla w `zbadaj_hipoteze` zostaje nietknięta.
Ta pętla ma za sobą dwie usterki klasy „przeszło testy, a nie było podpięte"
i nie jest miejscem na dokładanie gałęzi.

## Czego NIE wysyłamy

**Promptu systemowego.** Idzie sam hasz (`prompt_hash`), który już liczymy.
Prompt systemowy niesie INWENTARZ — nazwy tablic, workspace'ów i kolumn
klienta, czyli najgęstsze skupisko jego danych w całym procesie. Hasz daje to,
po co trace'owi prompt: porównywalność między runami i wiedzę, że dwa runy szły
tym samym. Treść nie daje nic ponadto, a kosztuje wysłaniem inwentarza.

## Co się dzieje, gdy maskowanie coś złapie

Trafienie to **nie sukces**. Pierwszą linią jest zasada, że dane osobowe nie
wchodzą do kontekstu modelu — więc `[E-MAIL]` w trace znaczy, że wyżej coś
puściło. Dlatego liczba trafień idzie w trzy miejsca naraz: do logu jako
ostrzeżenie, do metadanych trace'u (żeby było widać w Langfuse) i do zwracanego
obiektu (żeby wołający mógł zareagować). Cicha podmiana zamieniłaby alarm
w kosmetykę.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from monday_audit.maskowanie import MaskowanieError, bez_tozsamosci, zamaskuj
from monday_audit.osoby import MaPII

logger = logging.getLogger(__name__)

RODZAJ_GENERACJA = "generation"
RODZAJ_SPAN = "span"

# Hasze PLIKÓW, nie osób — mają kształt pseudonimu, więc `bez_tozsamosci` je omija.
KLUCZE_HASZY = frozenset({"prompt_hash", "obraz_hash"})


@dataclass(frozen=True)
class Obserwacja:
    """Jeden węzeł trace'u. `generation` to wywołanie modelu, `span` — reszta."""

    nazwa: str
    rodzaj: str = RODZAJ_SPAN
    wejscie: Any = None
    wyjscie: Any = None
    metadane: dict[str, Any] = field(default_factory=dict)
    # `usage_details` Langfuse'a: tokeny wejścia, wyjścia i cache'u.
    zuzycie: dict[str, int] = field(default_factory=dict)
    koszt_usd: float | None = None
    # `ERROR` dla narzędzia, które rzuciło — Langfuse podświetla takie węzły.
    poziom: Literal["ERROR"] | None = None
    komunikat: str | None = None


@dataclass(frozen=True)
class Trace:
    """Komplet dla jednej hipotezy, już zamaskowany.

    `trafienia_maskowania` jest w kształcie celowo: gdyby trace niósł tylko
    zamaskowane dane, nie dałoby się odróżnić „nic nie przeciekło" od
    „przeciekło i zamaskowaliśmy". To dwie bardzo różne wiadomości.
    """

    nazwa: str
    metadane: dict[str, Any] = field(default_factory=dict)
    obserwacje: tuple[Obserwacja, ...] = ()
    trafienia_maskowania: Counter[str] = field(default_factory=Counter)

    @property
    def czysty(self) -> bool:
        return not self.trafienia_maskowania


def _zuzycie_dla_langfuse(zuzycie: dict[str, float]) -> dict[str, int]:
    """Nasze nazwy pól na nazwy, które rozumie Langfuse.

    Mapowanie jest tu, a nie w `agent.py`, bo to kształt CUDZEGO systemu.
    Gdyby Langfuse zmienił nazwy, zmiana ma dotknąć jednego miejsca, a nie
    funkcji liczącej zużycie — ta odpowiada przed D8, nie przed Langfuse'em.
    """
    return {
        "input": int(zuzycie.get("tokens_in", 0)),
        "output": int(zuzycie.get("tokens_out", 0)),
        "cache_read_input_tokens": int(zuzycie.get("tokens_cache_read", 0)),
        "cache_creation_input_tokens": int(zuzycie.get("tokens_cache_write", 0)),
    }


def zbuduj_trace(
    wynik: Any,
    *,
    run_id: str,
    snapshot_id: int,
    model: str,
    prompt_hash: str,
    wpisy: Sequence[MaPII] = (),
    nie_ludzie: frozenset[str] = frozenset(),
) -> Trace:
    """`WynikHipotezy` → zamaskowany trace. Jedyna droga danych na zewnątrz.

    `wynik` jest typowany jako `Any`, żeby ten moduł nie importował `agent.py`:
    `agent.py` ciągnie Agent SDK, a trace ma się dać zbudować i przetestować
    bez podprocesu modelu. Wymagane pola są odczytywane przez `getattr`
    z wartościami domyślnymi, więc atrapa w teście jest trzylinijkowa.
    """
    hipoteza = wynik.hipoteza
    zuzycie = dict(getattr(wynik, "zuzycie", {}) or {})
    blad = getattr(wynik, "blad", None)
    finding = getattr(wynik, "finding", None)
    odrzucona = getattr(wynik, "odrzucona", None)

    if blad:
        rozstrzygniecie = "blad"
    elif odrzucona:
        rozstrzygniecie = "odrzucona"
    elif finding:
        rozstrzygniecie = "finding"
    else:
        rozstrzygniecie = "brak"

    if finding:
        wyjscie: Any = finding
    elif odrzucona:
        wyjscie = odrzucona
    elif blad:
        # Treść błędu z API bywa fragmentem odpowiedzi, więc idzie przez
        # maskowanie tak samo jak reszta — nie jest „tylko komunikatem".
        wyjscie = {"blad": blad}
    else:
        wyjscie = None

    surowe = {
        "metadane": {
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "klasa_id": hipoteza.klasa_id,
            "obiekt_id": hipoteza.obiekt_id,
            "model": model,
            # Hasz zamiast treści — powód w docstringu modułu.
            "prompt_hash": prompt_hash,
            "rozstrzygniecie": rozstrzygniecie,
            "blokow_tekstu": getattr(wynik, "blokow_tekstu", 0),
            "znakow_finalnych": getattr(wynik, "znakow_finalnych", 0),
            "znakow_wyrzuconych": getattr(wynik, "znakow_wyrzuconych", 0),
        },
        "wejscie": hipoteza.do_zapisu(),
        "wyjscie": wyjscie,
        "narzedzia": list(getattr(wynik, "wywolania_narzedzi", []) or []),
        "przebieg_narzedzi": list(getattr(wynik, "przebieg_narzedzi", []) or []),
    }

    zamaskowane = zamaskuj(surowe, wpisy, nie_ludzie=nie_ludzie)
    if not zamaskowane.czyste:
        # OSTRZEŻENIE, nie informacja. Trafienie znaczy, że pierwsza linia
        # obrony puściła — maskowanie tylko zdążyło przed wysyłką.
        logger.warning(
            "trace hipotezy %s/%s: %s — PIERWSZA linia (brak PII w kontekście "
            "modelu) puściła, maskowanie zdążyło przed wysyłką",
            hipoteza.klasa_id,
            hipoteza.obiekt_id,
            zamaskowane.podsumowanie(),
        )

    # Bez tożsamości: pseudonim → [OSOBA] (decyzja Kuby 2026-09-23).
    czyste = bez_tozsamosci(zamaskowane.dane, pomin_klucze=KLUCZE_HASZY)
    metadane = dict(czyste["metadane"])
    metadane["trafien_maskowania"] = zamaskowane.ile
    # Ścieżki też: składają się z kluczy, a klucz bywa pseudonimem (mapa
    # `tablice_dostepne` gość → tablice). Review 2026-09-23 — tędy pseudonim
    # wychodził do Langfuse'a obok oczyszczonych danych.
    metadane["pola_z_trafieniami"] = bez_tozsamosci(list(zamaskowane.sciezki))

    obserwacje = [
        Obserwacja(
            nazwa=f"hipoteza:{hipoteza.klasa_id}",
            rodzaj=RODZAJ_GENERACJA,
            wejscie=czyste["wejscie"],
            wyjscie=czyste["wyjscie"],
            metadane={"rozstrzygniecie": rozstrzygniecie},
            zuzycie=_zuzycie_dla_langfuse(zuzycie),
            koszt_usd=float(zuzycie.get("koszt_usd", 0.0)) or None,
        )
    ]
    # Narzędzia jako osobne węzły, bo to one pokazują, CZYM agent się posłużył —
    # a przy budżetach z rubryki to najczęstsze pytanie do trace'u.
    obserwacje += _obserwacje_narzedzi(czyste["narzedzia"], czyste["przebieg_narzedzi"])

    return Trace(
        nazwa=f"hipoteza:{hipoteza.klasa_id}",
        metadane=metadane,
        obserwacje=tuple(obserwacje),
        trafienia_maskowania=zamaskowane.trafienia,
    )


def _obserwacje_narzedzi(nazwy: list[Any], przebieg: list[Any]) -> list[Obserwacja]:
    """Węzły narzędzi: z pełnym przebiegiem, a gdy go nie ma — same nazwy.

    ## Dlaczego pełny przebieg (zgłoszone przez Kubę 2026-09-24)

    Trace pokazywał `narzedzie:zapytaj_snapshot:aktywnosc_tablicy` bez wejścia
    i wyjścia, więc nie dało się ocenić, czy model zapytał o właściwą rzecz i co
    dostał. Teraz węzeł niesie argumenty i wynik w postaci, którą widział model
    — już po `zamaskuj` i `bez_tozsamosci`, bo przebieg jest częścią `surowe`.

    Czas: SDK Langfuse nie przyjmuje czasu STARTU obserwacji, a trace powstaje
    po sesji, więc oś czasu w Langfuse jest płaska. Faktyczny start i czas
    trwania idą do metadanych węzła.
    """
    if not przebieg:
        return [Obserwacja(nazwa=f"narzedzie:{nazwa}", rodzaj=RODZAJ_SPAN) for nazwa in nazwy]
    obserwacje = []
    for numer, wpis in enumerate(przebieg, start=1):
        argumenty = wpis.get("argumenty") or {}
        cel = argumenty.get("pytanie") or argumenty.get("zakres") or argumenty.get("board_id")
        obserwacje.append(
            Obserwacja(
                nazwa=f"narzedzie:{wpis.get('narzedzie')}" + (f":{cel}" if cel else ""),
                rodzaj=RODZAJ_SPAN,
                wejscie=argumenty,
                wyjscie=wpis.get("wynik"),
                metadane={"kolejnosc": numer, "start": wpis.get("start"), "ms": wpis.get("ms")},
                poziom="ERROR" if wpis.get("blad") else None,
                komunikat=wpis.get("blad"),
            )
        )
    return obserwacje


class Wysylka(Protocol):
    """Odbiorca trace'ów. Protokół, bo `zbuduj_trace` nie ma znać Langfuse'a."""

    def wyslij(self, trace: Trace) -> None: ...

    def zamknij(self) -> None: ...


# ── nowa ścieżka: jedna sesja na całe konto (faza 5b) ────────────────────


def hasz_obrazu(wejscie: dict[str, Any]) -> str:
    """Hasz obrazu konta — to, co idzie do trace'u ZAMIAST obrazu.

    W starej ścieżce inwentarz jedzie w prompcie systemowym i reguła z
    `CLAUDE.md` mówi: wychodzi sam hasz. W nowej ten sam inwentarz jedzie
    w treści ZADANIA (`analiza.zbuduj_zadanie`), więc wysłanie zadania w całości
    obeszłoby tę regułę bocznymi drzwiami. Hasz daje to, po co trace'owi obraz:
    wiedzę, że dwa runy analizowały ten sam stan konta.

    `sort_keys=True`, bo kolejność kluczy w słowniku nie jest treścią — bez tego
    ten sam obraz dawałby różne hasze i porównanie między runami by kłamało.
    """
    tresc = json.dumps(wejscie, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(tresc.encode()).hexdigest()[:16]


def zbuduj_trace_analizy(
    *,
    run_id: str,
    snapshot_id: int,
    model: str,
    prompt_hash: str,
    obraz_hash: str,
    hipotezy: list[dict[str, Any]],
    odpowiedz: dict[str, Any] | None,
    z_szablonu: int,
    przyjetych: int = 0,
    odrzucone_reguly: Sequence[str] = (),
    szacunek_usd: float | None = None,
    blad: str | None = None,
    zadanie: str | None = None,
    wpisy: Sequence[MaPII] = (),
    nie_ludzie: frozenset[str] = frozenset(),
) -> Trace:
    """Sesja analizy → zamaskowany trace. Druga droga na zewnątrz, tą samą bramką.

    `wpisy` to znane osoby konta, jak w `zbuduj_trace` — druga siatka na imię
    i nazwisko, które przeciekło do kontekstu mimo pseudonimizacji.

    Przyjmuje gotowe słowniki, a nie obiekty z `analiza` i `uwagi`, z tego
    samego powodu co `zbuduj_trace`: ten moduł ma się dać zbudować
    i przetestować bez Agent SDK.

    Co wychodzi, a co NIE:

    * **wejście generacji to rozmowa** `[system, user]`, gdy znamy `zadanie`:
      system to sam hasz promptu, user — zadanie z obrazem konta zastąpionym
      haszem (`analiza.zbuduj_zadanie(obraz_zastepczy=…)`). Bez `zadanie` —
      same hipotezy, jak przed 2026-09-24,
    * **obraz konta NIE wychodzi**, idzie `obraz_hash` — powód w `hasz_obrazu`,
    * **narzędzia z wejściem i wyjściem** (`_obserwacje_narzedzi`),
    * **wyjście to rozstrzygnięcia MODELU**, bez uwag z szablonów. Szablon nie
      jest wywołaniem modelu, a trace generacji opisuje wywołanie modelu —
      wmieszanie szablonów kazałoby czytającemu przypisać modelowi coś, czego
      nie napisał. Ich liczba jest w metadanych.

    `odrzucone_reguly` to nazwy reguł walidacji, nie treść uwag — czyli nasze
    stałe, bez danych klienta. Dzięki nim trace mówi nie tylko „odrzucono 9",
    ale „odrzucono 9 za brak pola w dowodzie", a to są dwie różne poprawki.
    """
    odpowiedz = odpowiedz or {}
    zuzycie = dict(odpowiedz.get("zuzycie") or {})
    if blad:
        rozstrzygniecie = "blad"
        wyjscie: Any = {"blad": blad}
    else:
        rozstrzygniecie = "zakonczona"
        wyjscie = {
            "uwagi": list(odpowiedz.get("uwagi") or []),
            "pominiete": list(odpowiedz.get("pominiete") or []),
        }

    surowe = {
        "metadane": {
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "model": model,
            "prompt_hash": prompt_hash,
            "obraz_hash": obraz_hash,
            "rozstrzygniecie": rozstrzygniecie,
            "hipotez_do_modelu": len(hipotezy),
            "uwag_z_szablonu": z_szablonu,
            "uwag_przyjetych": przyjetych,
            "uwag_odrzuconych": len(odrzucone_reguly),
            "odrzucone_reguly": dict(Counter(odrzucone_reguly)),
            "szacunek_usd": szacunek_usd,
        },
        # Wejście jako ROZMOWA, gdy znamy treść zadania — Langfuse umie wtedy
        # pokazać i odtworzyć wywołanie. Prompt systemowy i obraz konta tylko
        # jako hasze (reguła z `CLAUDE.md`), więc odtworzenie nie jest 1:1.
        "wejscie": (
            [
                {"role": "system", "content": f"[prompt systemowy — tylko hasz {prompt_hash}]"},
                {"role": "user", "content": zadanie},
            ]
            if zadanie is not None
            else {"hipotezy": hipotezy}
        ),
        "wyjscie": wyjscie,
        "narzedzia": list(odpowiedz.get("wywolania_narzedzi") or []),
        "przebieg_narzedzi": list(odpowiedz.get("przebieg_narzedzi") or []),
    }

    zamaskowane = zamaskuj(surowe, wpisy, nie_ludzie=nie_ludzie)
    if not zamaskowane.czyste:
        logger.warning(
            "trace analizy %s: %s — PIERWSZA linia (brak PII w kontekście modelu) "
            "puściła, maskowanie zdążyło przed wysyłką",
            run_id,
            zamaskowane.podsumowanie(),
        )

    # Bez tożsamości: pseudonim → [OSOBA] (decyzja Kuby 2026-09-23).
    czyste = bez_tozsamosci(zamaskowane.dane, pomin_klucze=KLUCZE_HASZY)
    metadane = dict(czyste["metadane"])
    metadane["trafien_maskowania"] = zamaskowane.ile
    # Ścieżki też: składają się z kluczy, a klucz bywa pseudonimem (mapa
    # `tablice_dostepne` gość → tablice). Review 2026-09-23 — tędy pseudonim
    # wychodził do Langfuse'a obok oczyszczonych danych.
    metadane["pola_z_trafieniami"] = bez_tozsamosci(list(zamaskowane.sciezki))

    obserwacje = [
        Obserwacja(
            nazwa="analiza:sesja",
            rodzaj=RODZAJ_GENERACJA,
            wejscie=czyste["wejscie"],
            wyjscie=czyste["wyjscie"],
            metadane={"rozstrzygniecie": rozstrzygniecie},
            zuzycie=_zuzycie_dla_langfuse(zuzycie),
            koszt_usd=float(zuzycie.get("koszt_usd", 0.0)) or None,
        )
    ]
    obserwacje += _obserwacje_narzedzi(czyste["narzedzia"], czyste["przebieg_narzedzi"])
    return Trace(
        nazwa="analiza:konto",
        metadane=metadane,
        obserwacje=tuple(obserwacje),
        trafienia_maskowania=zamaskowane.trafienia,
    )


def wyslij_bezpiecznie(slad: Wysylka | None, budowa: Callable[[], Trace], *, opis: str) -> None:
    """Zbuduj i wyślij trace. NIGDY nie wywraca runu — ale nie milczy.

    Wydzielone z `agent._wyslij_slad`, bo nowa ścieżka potrzebuje DOKŁADNIE tej
    samej reguły, a dwie kopie reguły o bezpieczniku to dwie okazje, żeby jedna
    z nich zaczęła łapać `MaskowanieError` razem z błędami sieci.

    Warstwy odpowiedzialności są rozdzielone celowo:

    * `WysylkaLangfuse.wyslij` **nie połyka** `MaskowanieError` — w środku
      odbiorcy zrównanie bezpiecznika z awarią sieci zamieniłoby go w ozdobę,
    * tutaj decyzja jest odwrotna: audyt ma się dokończyć, bo klient zapłacił
      za run, nie za trace'y.

    Dwa poziomy logu. `MaskowanieError` to ERROR ze śladem stosu — payload
    zawierał coś, czego nie umiemy zamaskować, i trace NIE wyszedł. Reszta to
    WARNING: stracony ślad, nic więcej.

    `budowa` jest funkcją, a nie gotowym trace'em, bo `MaskowanieError` rodzi się
    zwykle przy BUDOWIE, nie przy wysyłce — i też musi trafić pod ten `try`.
    """
    if slad is None:
        return
    try:
        slad.wyslij(budowa())
    except MaskowanieError:
        # `exception`, nie `error`: ślad stosu pokazuje, KTÓRE pole wywróciło
        # maskowanie. Nie niesie wartości — Python nie wypisuje w nim zmiennych
        # lokalnych, a komunikat `MaskowanieError` jest budowany bez danych:
        # ścieżka w nim składa się z kluczy JUŻ zamaskowanych (`zamaskuj`).
        logger.exception(
            "%s: trace NIE wyszedł — maskowanie nie poradziło sobie z payloadem. "
            "Audyt leci dalej, ale to jest do obejrzenia",
            opis,
        )
    except Exception as blad:  # obserwowalność nie jest produktem
        logger.warning("%s: nie udało się wysłać trace'u (%s: %s)", opis, type(blad).__name__, blad)
