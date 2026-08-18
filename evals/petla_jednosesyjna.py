"""Eksperyment: JEDNA sesja na wiele hipotez, zamiast jednej sesji na hipotezę.

## Po co, skoro architektura już rozstrzygnęła inaczej

`agent.py` prowadzi jedną sesję na hipotezę i ma na to trzy argumenty (docstring
tamtego modułu). Ale **żaden z nich nie był zmierzony** — nikt nie policzył, ile
kosztuje jedna długa sesja i czy jakość w niej spada. To jest luka, nie decyzja,
i ten skrypt ją zamyka.

## Dlaczego OSOBNY plik, a nie tryb w `zbadaj_hipotezy`

`zbadaj_hipotezy` jest ścieżką produkcyjną — woła ją `cli_agent` i `web/run.py`.
Wpięcie tam trybu eksperymentalnego naraża produkcję na regresję dla eksperymentu,
który może zostać odrzucony. Tu importujemy z `agent.py` to, co trzeba, i zapisujemy
przez `przebieg.zapisz_zuzycie`, więc `cli_ewaluacja` i `evals/mierz.py` czytają
wynik bez żadnej zmiany. Eksperyment nieudany = usuwasz jeden plik.

## Znane ograniczenie, które JEST częścią odpowiedzi

`_zbuduj_narzedzia` domyka się nad `biezace["aktywne"]`, czyli JEDNĄ hipotezą.
W jednej sesji agent może zawołać narzędzie dla hipotezy 2, gdy „aktywna" jest 5 —
i budżet obciąży złą hipotezę. Nie naprawiamy tego: to jest dokładnie argument za
obecną architekturą, zapisany w docstringu `agent.py` („budżet jest per hipoteza,
wspólna sesja musiałaby przełączać licznik w trakcie"). Tutaj dajemy JEDEN wspólny
budżet na całą pętlę i mówimy wprost, że przypisanie do hipotezy jest niemożliwe.

## Co mierzymy — cztery miary degradacji

Główna obawa brzmiała: „agent zrobi poprawnie pięć, a kolejne pięć na zasadzie
»zrobiłem te pięć, to kolejne też będą takie same«". Cztery liczby to sprawdzają:

1. **nachylenie długości opisu** wobec numeru w kolejności — ujemne znaczy, że
   odpowiedzi się skracają w miarę pętli;
2. **podobieństwo leksykalne** kolejnych findingów (Jaccard na słowach) — rosnące
   znaczy, że agent kopiuje własny szablon;
3. **rzeczowość per pozycja** (z `evals/mierz.py`) — czy braki gęstnieją na końcu;
4. **wywołania narzędzi** wobec pozycji — spadek do zera znaczy, że przestał
   sprawdzać dane.

Do tego trzy sygnały urwanego kontekstu: `error_max_turns`, `tokens_cache_read`
przestające rosnąć (podpis kompaktowania) i spadek rzeczowości w drugiej połowie.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock

from monday_audit.agent import (
    _inwentarz,
    _opis_klasy,
    _opis_wyceny,
    _tekst_promptu,
    _zbuduj_narzedzia,
    _zuzycie,
    zbuduj_opcje,
)
from monday_audit.baza import RejestrWywolan, polacz, zastosuj_migracje
from monday_audit.detektory import Hipoteza, uruchom_detektory
from monday_audit.klient import MondayClient
from monday_audit.konfiguracja import klucz_anthropic, sol_z_ustawien, wczytaj
from monday_audit.narzedzia import Narzedzia, NarzedziaHipotezy
from monday_audit.przebieg import zapisz_zuzycie
from monday_audit.rubryka import Rubryka, wczytaj_rubryke

logger = logging.getLogger(__name__)

# Sufit obrotów dla CAŁEJ pętli. `agent.MAKS_OBROTOW` to 12 na hipotezę — jedna
# sesja na sześć hipotez potrzebuje wielokrotnie więcej, ale stałej produkcyjnej
# nie ruszamy: to parametr eksperymentu, nie zmiana zachowania agenta.
MAKS_OBROTOW_PETLI = 80

# Prośba o kolejną hipotezę. Krótka celowo — długa instrukcja w każdej turze
# dodawałaby wejście, którego nie chcemy mierzyć razem z narastającą historią.
KOLEJNA = """\
Kolejna hipoteza ({numer} z {ile}).

{klasa}

## HIPOTEZA
{hipoteza}

{wycena}

Zwróć WYŁĄCZNIE obiekt JSON, w tym samym kształcie co poprzednio.
"""


def _zadanie_pierwsze(
    rubryka: Rubryka, hipoteza: Hipoteza, ile: int, stawki: dict[str, Any]
) -> str:
    """Pierwsza tura niesie pełną instrukcję kształtu odpowiedzi."""
    from monday_audit.agent import ZADANIE

    klasa = rubryka.po_id[hipoteza.klasa_id]
    return (
        f"Zbadasz w tej sesji {ile} hipotez, jedną po drugiej. Na każdą odpowiadasz "
        f"osobnym obiektem JSON. Nie streszczaj poprzednich — każda odpowiedź stoi sama.\n\n"
        + ZADANIE.format(
            klasa=_opis_klasy(klasa),
            wycena=_opis_wyceny(klasa, stawki),
            hipoteza=json.dumps(hipoteza.do_zapisu(), ensure_ascii=False, indent=1),
            klasa_id=klasa.id,
            waga=klasa.waga,
            wysilek=klasa.wysilek_naprawy,
            typ_wyceny=klasa.typ_wyceny,
            dowod=", ".join(klasa.dowod),
        )
    )


def _slowa(tekst: str) -> set[str]:
    """Słowa dłuższe niż 3 znaki — do miary podobieństwa kolejnych findingów."""
    return {s for s in re.findall(r"[\w]+", tekst.lower()) if len(s) > 3}


def jaccard(a: str, b: str) -> float:
    """Podobieństwo leksykalne dwóch findingów. Rosnące = agent kopiuje siebie."""
    sa, sb = _slowa(a), _slowa(b)
    if not sa or not sb:
        return 0.0
    return round(len(sa & sb) / len(sa | sb), 4)


def nachylenie(wartosci: list[float]) -> float:
    """Nachylenie prostej regresji wobec pozycji. Ujemne = degradacja w czasie.

    Liczone wzorem, nie biblioteką — trzy linie arytmetyki nie są warte
    zależności, a `numpy` nie jest w tym projekcie i nie będzie bez pomiaru.
    """
    n = len(wartosci)
    if n < 2:
        return 0.0
    sx = sum(range(n))
    sy = sum(wartosci)
    sxy = sum(i * w for i, w in enumerate(wartosci))
    sxx = sum(i * i for i in range(n))
    mianownik = n * sxx - sx * sx
    return round((n * sxy - sx * sy) / mianownik, 3) if mianownik else 0.0


async def petla(
    hipotezy: list[Hipoteza],
    *,
    zestaw: Narzedzia,
    rubryka: Rubryka,
    klucz_api: str,
    effort: str | None,
) -> dict[str, Any]:
    """Jedna sesja, wszystkie hipotezy po kolei. Zwraca findingi i pomiary.

    Budżet narzędzi jest WSPÓLNY dla całej pętli — patrz docstring modułu.
    """
    prompt = _tekst_promptu()
    inwentarz = _inwentarz(zestaw)
    biezace: dict[str, NarzedziaHipotezy] = {}
    serwer = _zbuduj_narzedzia(biezace)
    # Jeden zestaw narzędzi na pętlę, przypięty do PIERWSZEJ hipotezy. Skutek jest
    # zapisany w wyniku: budżet nie da się przypisać do hipotezy w tym trybie.
    biezace["aktywne"] = zestaw.dla_hipotezy(hipotezy[0])

    opcje = zbuduj_opcje(
        prompt=prompt,
        inwentarz=inwentarz,
        snapshot_id=zestaw.snapshot_id,
        serwer=serwer,
        klucz_api=klucz_api,
        effort=effort,
    )
    # Sufit obrotów dla całej pętli, nie dla hipotezy.
    opcje.max_turns = MAKS_OBROTOW_PETLI

    wyniki: list[dict[str, Any]] = []
    zuzycie_razem = {
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "koszt_usd": 0.0,
    }
    urwany_kontekst: str | None = None

    async with ClaudeSDKClient(options=opcje) as klient:
        for numer, hipoteza in enumerate(hipotezy, start=1):
            klasa = rubryka.po_id[hipoteza.klasa_id]
            zadanie = (
                _zadanie_pierwsze(rubryka, hipoteza, len(hipotezy), {})
                if numer == 1
                else KOLEJNA.format(
                    numer=numer,
                    ile=len(hipotezy),
                    klasa=_opis_klasy(klasa),
                    hipoteza=json.dumps(hipoteza.do_zapisu(), ensure_ascii=False, indent=1),
                    wycena=_opis_wyceny(klasa, {}),
                )
            )
            zaczeto = time.monotonic()
            bloki: list[str] = []
            zuzycie_tury: dict[str, float] = {}
            narzedzi_przed = len(biezace["aktywne"].wywolania)

            await klient.query(zadanie)
            async for wiadomosc in klient.receive_response():
                if isinstance(wiadomosc, AssistantMessage):
                    for blok in wiadomosc.content:
                        if isinstance(blok, TextBlock) and blok.text.strip():
                            bloki.append(blok.text)
                elif isinstance(wiadomosc, ResultMessage):
                    zuzycie_tury = _zuzycie(wiadomosc)
                    # Pierwszy sygnał urwanego kontekstu: sufit obrotów.
                    if wiadomosc.subtype == "error_max_turns":
                        urwany_kontekst = f"error_max_turns na hipotezie {numer}"

            sekund = round(time.monotonic() - zaczeto, 3)
            for k in zuzycie_razem:
                zuzycie_razem[k] += zuzycie_tury.get(k, 0)

            surowy = bloki[-1] if bloki else ""
            finding: dict[str, Any] | None = None
            powod: str | None = None
            try:
                from monday_audit.agent import _wyluskaj_json

                rozstrzygniecie = _wyluskaj_json(surowy)
                if rozstrzygniecie.get("rozstrzygniecie") == "odrzucona":
                    powod = str(rozstrzygniecie.get("powod") or "brak powodu")
                else:
                    finding = rozstrzygniecie.get("finding") or rozstrzygniecie
            except Exception as blad:  # pętla nie może padnąć na jednej hipotezie
                powod = f"nie udało się odczytać JSON: {type(blad).__name__}"

            wyniki.append(
                {
                    "numer": numer,
                    "klasa_id": hipoteza.klasa_id,
                    "obiekt_id": hipoteza.obiekt_id,
                    "finding": finding,
                    "powod_odrzucenia": powod,
                    "sekund": sekund,
                    "tokens_out": int(zuzycie_tury.get("tokens_out", 0)),
                    "tokens_in": int(zuzycie_tury.get("tokens_in", 0)),
                    "tokens_cache_read": int(zuzycie_tury.get("tokens_cache_read", 0)),
                    "tokens_cache_write": int(zuzycie_tury.get("tokens_cache_write", 0)),
                    "koszt_usd": float(zuzycie_tury.get("koszt_usd", 0.0)),
                    "wywolan_narzedzi": len(biezace["aktywne"].wywolania) - narzedzi_przed,
                    "znakow_finalnych": len(surowy),
                    "znakow_wyrzuconych": sum(len(b) for b in bloki[:-1]),
                    "blokow_tekstu": len(bloki),
                }
            )
            logger.info(
                "[%d/%d] %s %s → %s (%d tok. wyjścia, %.0f s)",
                numer,
                len(hipotezy),
                hipoteza.klasa_id,
                hipoteza.obiekt_id,
                "finding" if finding else "odrzucona",
                int(zuzycie_tury.get("tokens_out", 0)),
                sekund,
            )

    return {"wyniki": wyniki, "zuzycie": zuzycie_razem, "urwany_kontekst": urwany_kontekst}


def zdiagnozuj(wyniki: list[dict[str, Any]]) -> dict[str, Any]:
    """Cztery miary degradacji plus sygnały urwanego kontekstu.

    Wszystkie liczone wobec POZYCJI w kolejności — bo pytanie brzmi „czy jakość
    spada w miarę postępu pętli", a nie „jaka jest średnio".
    """
    opisy = [
        str((w.get("finding") or {}).get("opis") or w.get("powod_odrzucenia") or "") for w in wyniki
    ]
    dlugosci = [float(len(o)) for o in opisy]
    cache = [float(w["tokens_cache_read"]) for w in wyniki]
    narzedzia = [float(w["wywolan_narzedzi"]) for w in wyniki]

    podobienstwa = [jaccard(opisy[i], opisy[i + 1]) for i in range(len(opisy) - 1)]
    polowa = len(wyniki) // 2 or 1

    return {
        "nachylenie_dlugosci_opisu": nachylenie(dlugosci),
        "podobienstwo_kolejnych": podobienstwa,
        "podobienstwo_srednie": round(sum(podobienstwa) / len(podobienstwa), 4)
        if podobienstwa
        else 0.0,
        "nachylenie_podobienstwa": nachylenie([float(p) for p in podobienstwa]),
        "nachylenie_wywolan_narzedzi": nachylenie(narzedzia),
        "narzedzia_pierwsza_polowa": sum(narzedzia[:polowa]),
        "narzedzia_druga_polowa": sum(narzedzia[polowa:]),
        # Cache rosnący = historia narasta zgodnie z oczekiwaniem. SPADEK znaczy,
        # że SDK skompaktował kontekst — czyli „urwał się", tylko po cichu.
        "cache_read_rosnie": all(a <= b for a, b in pairwise(cache)),
        "cache_read_per_tura": [int(c) for c in cache],
        "findingow": sum(1 for w in wyniki if w.get("finding")),
        "odrzuconych": sum(1 for w in wyniki if not w.get("finding")),
    }


async def _uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj(argumenty.plik_env)
    baza = (argumenty.baza or ustawienia.monday_audit_db).absolute()
    rubryka = wczytaj_rubryke()
    con = polacz(baza)
    zastosuj_migracje(con)
    try:
        hipotezy, _ = uruchom_detektory(con, argumenty.snapshot, rubryka)
        if argumenty.klasy:
            hipotezy = [h for h in hipotezy if h.klasa_id in set(argumenty.klasy)]
        if argumenty.obiekt:
            chciane = set(argumenty.obiekt)
            hipotezy = [h for h in hipotezy if h.obiekt_id in chciane]
        if argumenty.limit:
            hipotezy = hipotezy[: argumenty.limit]
        if not hipotezy:
            print("zero hipotez — nic do zbadania", file=sys.stderr)
            return 1

        con.execute(
            "INSERT INTO runy (run_id, client_id, snapshot_id, status, started_at, model, "
            "rubric_ver) VALUES (?, ?, ?, 'w_toku', ?, ?, ?)",
            (
                argumenty.run_id,
                argumenty.klient,
                argumenty.snapshot,
                datetime.now(tz=UTC).isoformat(),
                "claude-sonnet-5",
                rubryka.wersja,
            ),
        )
        con.commit()

        async with MondayClient(
            ustawienia.monday_token.get_secret_value(),
            RejestrWywolan(con, argumenty.run_id),
        ) as klient:
            zestaw = Narzedzia(
                con=con,
                snapshot_id=argumenty.snapshot,
                client_id=argumenty.klient,
                sol=sol_z_ustawien(ustawienia),
                klient=klient,
            )
            wynik = await petla(
                hipotezy,
                zestaw=zestaw,
                rubryka=rubryka,
                klucz_api=klucz_anthropic(ustawienia),
                effort=argumenty.effort,
            )

        diagnoza = zdiagnozuj(wynik["wyniki"])
        per_hipoteza = [
            {k: w[k] for k in w if k not in {"finding", "powod_odrzucenia", "numer"}}
            | {"byl_finding": bool(w.get("finding"))}
            for w in wynik["wyniki"]
        ]
        con.execute(
            "UPDATE runy SET status = 'zakonczony', finished_at = ?, findingow = ?, "
            "hipotez_zbadanych = ?, hipotez_odrzuconych = ? WHERE run_id = ?",
            (
                datetime.now(tz=UTC).isoformat(),
                diagnoza["findingow"],
                len(hipotezy),
                diagnoza["odrzuconych"],
                argumenty.run_id,
            ),
        )
        zapisz_zuzycie(con, argumenty.run_id, wynik["zuzycie"], per_hipoteza)
        con.commit()
    finally:
        con.close()

    z = wynik["zuzycie"]
    print(f"\n  PĘTLA JEDNOSESYJNA: {len(hipotezy)} hipotez w JEDNEJ sesji")
    print(f"  koszt: {z['koszt_usd']:.4f} USD   wyjście: {z['tokens_out']} tok.")
    na_hip_usd = z["koszt_usd"] / len(hipotezy)
    na_hip_out = z["tokens_out"] // len(hipotezy)
    print(f"  na hipotezę: {na_hip_usd:.4f} USD, {na_hip_out} tok.")
    print()
    print("  MIARY DEGRADACJI (ujemne nachylenie = jakość spada w miarę pętli):")
    nachyl = diagnoza["nachylenie_dlugosci_opisu"]
    print(f"    nachylenie długości opisu:     {nachyl:+.1f} znaków/pozycję")
    print(f"    podobieństwo kolejnych (śr.):  {diagnoza['podobienstwo_srednie']:.4f}")
    print(f"    nachylenie podobieństwa:       {diagnoza['nachylenie_podobienstwa']:+.4f}")
    print(f"    wywołania narzędzi 1. połowa:  {diagnoza['narzedzia_pierwsza_polowa']:.0f}")
    print(f"    wywołania narzędzi 2. połowa:  {diagnoza['narzedzia_druga_polowa']:.0f}")
    print()
    print("  URWANY KONTEKST:")
    print(f"    error_max_turns:      {wynik['urwany_kontekst'] or 'nie'}")
    print(f"    cache_read rośnie:    {diagnoza['cache_read_rosnie']} (spadek = kompaktowanie)")
    print(f"    cache per tura:       {diagnoza['cache_read_per_tura']}")
    print()
    print("  ZASTRZEŻENIE: budżet narzędzi jest WSPÓLNY dla pętli — w tym trybie")
    print("  nie da się przypisać wywołania do hipotezy. To argument za obecną")
    print("  architekturą, nie brak eksperymentu.")
    if argumenty.json:
        cel = Path("raporty") / f"petla_{argumenty.run_id}.json"
        cel.parent.mkdir(exist_ok=True)
        cel.write_text(
            json.dumps(
                {"diagnoza": diagnoza, "wyniki": wynik["wyniki"]}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(f"\n  szczegóły: {cel}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(
        prog="evals.petla_jednosesyjna",
        description="Eksperyment: jedna sesja na wiele hipotez. NIE ścieżka produkcyjna.",
    )
    p.add_argument("--klient", required=True)
    p.add_argument("--snapshot", type=int, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--klasy", action="append", default=[])
    p.add_argument("--obiekt", action="append", default=[])
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--effort", default=None, choices=("low", "medium", "high", "xhigh", "max"))
    p.add_argument("--baza", type=Path, default=None)
    p.add_argument("--plik-env", type=Path, default=None)
    p.add_argument("--json", action="store_true", help="zapisz szczegóły do raporty/")
    return asyncio.run(_uruchom(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
