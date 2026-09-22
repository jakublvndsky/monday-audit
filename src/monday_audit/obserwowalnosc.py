"""Trace jednej hipotezy: co wychodzi na zewnątrz i w jakim kształcie (faza 4).

## Skąd bierzemy dane

Z `WynikHipotezy`, czyli z tego, co **i tak już zapisujemy do własnej bazy** —
nie ze strumienia wiadomości SDK. To nie jest wygoda, tylko granica: Langfuse
nie dostaje niczego, czego nie mamy u siebie. Gdyby trace powstawał z podglądu
strumienia, powstałby drugi, równoległy zbiór danych o kliencie, o którym
`docs/ARCHITEKTURA.md` nic nie mówi.

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

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from monday_audit.maskowanie import zamaskuj
from monday_audit.osoby import MaPII

logger = logging.getLogger(__name__)

RODZAJ_GENERACJA = "generation"
RODZAJ_SPAN = "span"


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
    }

    zamaskowane = zamaskuj(surowe, wpisy)
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

    czyste = zamaskowane.dane
    metadane = dict(czyste["metadane"])
    metadane["trafien_maskowania"] = zamaskowane.ile
    metadane["pola_z_trafieniami"] = list(zamaskowane.sciezki)

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
    obserwacje += [
        Obserwacja(nazwa=f"narzedzie:{nazwa}", rodzaj=RODZAJ_SPAN) for nazwa in czyste["narzedzia"]
    ]

    return Trace(
        nazwa=f"hipoteza:{hipoteza.klasa_id}",
        metadane=metadane,
        obserwacje=tuple(obserwacje),
        trafienia_maskowania=zamaskowane.trafienia,
    )


class Wysylka(Protocol):
    """Odbiorca trace'ów. Protokół, bo `zbuduj_trace` nie ma znać Langfuse'a."""

    def wyslij(self, trace: Trace) -> None: ...

    def zamknij(self) -> None: ...
