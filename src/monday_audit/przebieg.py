"""Zapis snapshotu i przebieg całego collectora (etap 3.8).

Jedno miejsce, które składa wynik pięciu collectorów w jeden niemutowalny
snapshot i domyka run. Za tym stoi **BRAMA** z `03-build.md`: warstwa agentowa
nie powstaje, dopóki człowiek nie przejrzy snapshotu ręcznie.

Kolejność operacji nie jest dowolna:

1. **Najpierw wiersz w `runy`.** `wywolania.run_id` to NOT NULL REFERENCES,
   a `polacz()` włącza klucze obce — więc bez otwartego runu pierwsze
   zapytanie do monday nie miałoby gdzie się zalogować.
2. Dopiero potem collectory, w kolejności zależności: konto → osoby →
   tablice → automatyzacje → logi. Logi potrzebują tablic (próbka po
   `items_count`) i pseudonimów osób (heurystyka człowiek/automat).
3. **Walidacja antyprzeciekowa na ZŁOŻONYM payloadzie**, z pełnym mapowaniem
   z bazy. Poszczególne collectory sprawdzają tylko to, co widzą u siebie;
   dopiero tutaj da się skonfrontować cały snapshot z listą nazwisk.
4. Zapis snapshotu i domknięcie runu z agregatami dla etapu 6.

Snapshot jest niemutowalny (trigger z D7), więc pomyłka w składaniu nie da
się poprawić w miejscu — trzeba zapisać nowy. Dlatego walidacja idzie
PRZED insertem.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

from monday_audit.automatyzacje import MAKS_SOND, zbierz_automatyzacje
from monday_audit.baza import MapowanieOsob, RejestrWywolan
from monday_audit.klient import MondayClient, Postep
from monday_audit.konto import Zakres, rozpoznaj_konto
from monday_audit.logi import TOP_PO_ITEMACH, Z_OGONA, zbierz_logi
from monday_audit.osoby import waliduj_brak_pii, zbierz_osoby, zredaguj_pii
from monday_audit.tablice import zbierz_tablice

logger = logging.getLogger(__name__)

DNI_OKNA = 90


def collector_ver() -> str:
    """Wersja collectora do snapshotu — jeden z czterech elementów pinowania.

    Czytana z metadanych pakietu, żeby nie rozjechała się z `pyproject.toml`.
    """
    try:
        return version("monday-audit")
    except PackageNotFoundError:  # pragma: no cover — pakiet zawsze instalowany przez uv
        return "nieznana"


@dataclass(frozen=True, slots=True)
class RaportRunu:
    """Wypis z runu, wymagany przez 3.8."""

    run_id: str
    snapshot_id: int
    client_id: str
    zakres: str
    wywolan: int
    complexity: int
    sekund: float
    bajtow_payloadu: int
    zastrzezenia: tuple[str, ...]
    discovery: dict[str, Any]
    liczby: dict[str, Any] = field(default_factory=dict)

    def opis(self) -> str:
        linie = [
            "── RAPORT Z RUNU ──────────────────────────────────────",
            f"  run_id        : {self.run_id}",
            f"  snapshot_id   : {self.snapshot_id}",
            f"  klient        : {self.client_id}",
            f"  zakres        : {self.zakres}",
            f"  wywołania     : {self.wywolan}",
            f"  complexity    : {self.complexity:,}".replace(",", " "),
            f"  czas          : {self.sekund:.1f} s",
            f"  payload       : {self.bajtow_payloadu / 1024:.1f} KiB",
            "",
            "  ZEBRANE:",
        ]
        linie += [f"    {klucz:<28} {wartosc}" for klucz, wartosc in self.liczby.items()]

        linie += ["", "  DISCOVERY:"]
        linie += [f"    {klucz:<28} {wartosc}" for klucz, wartosc in self.discovery.items()]

        if self.zastrzezenia:
            linie += ["", "  ZASTRZEŻENIA (czego nie widać):"]
            linie += [f"    - {z}" for z in self.zastrzezenia]

        return "\n".join(linie)


def otworz_run(con: sqlite3.Connection, *, run_id: str, client_id: str) -> RejestrWywolan:
    """Zakłada wiersz w `runy` i zwraca rejestr wywołań podpięty do niego."""
    con.execute(
        "INSERT INTO runy (run_id, client_id, status, started_at) VALUES (?, ?, 'w_toku', ?)",
        (run_id, client_id, datetime.now(tz=UTC).isoformat()),
    )
    con.commit()
    logger.info("otwarto run %s dla klienta %s", run_id, client_id)
    return RejestrWywolan(con, run_id)


def zapisz_snapshot(
    con: sqlite3.Connection, *, client_id: str, payload: dict[str, Any], run_at: str
) -> int:
    """Zapisuje snapshot i zwraca jego id. Snapshot jest niemutowalny (D7)."""
    tresc = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    kursor = con.execute(
        "INSERT INTO snapshots (client_id, run_at, collector_ver, payload) VALUES (?, ?, ?, ?)",
        (client_id, run_at, collector_ver(), tresc),
    )
    con.commit()
    snapshot_id = int(kursor.lastrowid or 0)
    logger.info("zapisano snapshot %d (%d B)", snapshot_id, len(tresc.encode("utf-8")))
    return snapshot_id


def domknij_run(
    con: sqlite3.Connection,
    *,
    run_id: str,
    snapshot_id: int,
    wywolan: int,
    complexity: int,
    status: str = "zakonczony",
) -> None:
    con.execute(
        "UPDATE runy SET snapshot_id = ?, wywolania_monday = ?, complexity_suma = ?, "
        "status = ?, finished_at = ? WHERE run_id = ?",
        (snapshot_id, wywolan, complexity, status, datetime.now(tz=UTC).isoformat(), run_id),
    )
    con.commit()


def _uwagi_o_zakresie(zakres: Zakres) -> tuple[str, ...]:
    """Czego zawężenie zakresu NIE obejmuje — bo API nie pozwala.

    Dwa zapytania są z natury na poziomie konta i nie mają filtra po
    workspace: lista użytkowników i statystyki automatyzacji. Przy audycie
    zawężonym trzeba to powiedzieć wprost, żeby nikt nie czytał liczb
    jako „w tym workspace".
    """
    if not zakres.zawezony:
        return ()
    return (
        "lista użytkowników jest z natury na poziomie konta — `users` nie ma "
        "filtra po workspace ani po tablicy",
        "statystyki uruchomień automatyzacji są na poziomie konta — filtr "
        "`board_id` w API jest zepsuty (Int32, OTWARTE.md O12)",
    )


async def wykonaj_run(
    *,
    token: str,
    con: sqlite3.Connection,
    client_id: str,
    zakres: Zakres,
    sol: bytes,
    run_id: str | None = None,
    postep: Callable[[Postep], None] | None = None,
    dni_okna: int = DNI_OKNA,
    top_logow: int = TOP_PO_ITEMACH,
    z_ogona: int = Z_OGONA,
    maks_sond: int = MAKS_SOND,
    budzet_wywolan: int = 400,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RaportRunu:
    """Przepuszcza cały collector i zapisuje snapshot. Zwraca raport z runu."""
    start = time.monotonic()
    teraz = datetime.now(tz=UTC)
    run_at = teraz.isoformat()
    run_id = run_id or f"{client_id}-{teraz.strftime('%Y%m%dT%H%M%SZ')}"

    rejestr = otworz_run(con, run_id=run_id, client_id=client_id)
    mapowanie = MapowanieOsob(con, client_id)
    okno_od = (teraz - timedelta(days=dni_okna)).isoformat()

    async with MondayClient(
        token,
        rejestr,
        budzet_wywolan=budzet_wywolan,
        postep=postep,
        transport=transport,
    ) as klient:
        konto = await rozpoznaj_konto(klient, zakres)
        osoby = await zbierz_osoby(klient, client_id=client_id, sol=sol, mapowanie=mapowanie)
        tablice = await zbierz_tablice(klient, zakres, client_id=client_id, sol=sol)
        automaty = await zbierz_automatyzacje(
            klient,
            board_ids=[t.board_id for t in tablice.tablice],
            od=okno_od,
            do=run_at,
            maks_sond=maks_sond,
        )
        logi = await zbierz_logi(
            klient,
            tablice.tablice,
            client_id=client_id,
            sol=sol,
            znane_hashe=osoby.hashe,
            od=okno_od,
            do=run_at,
            top=top_logow,
            z_ogona=z_ogona,
        )
        wywolan = klient.liczba_wywolan
        complexity = klient.complexity_suma

    uwagi = _uwagi_o_zakresie(zakres)
    payload: dict[str, Any] = {
        "meta": {
            "client_id": client_id,
            "run_id": run_id,
            "run_at": run_at,
            "collector_ver": collector_ver(),
            "okno_dni": dni_okna,
            "okno_od": okno_od,
            "uwagi_o_zakresie": list(uwagi),
        },
        "konto": konto.do_snapshotu(),
        "uzytkownicy": osoby.do_snapshotu(),
        "tablice": tablice.do_snapshotu(),
        "automatyzacje": automaty.do_snapshotu(),
        "aktywnosc": logi.do_snapshotu(),
    }

    # Redakcja PRZED walidacją: klient potrafi nazwać tablicę albo zespół
    # imieniem osoby, a wtedy PII wchodzi przez treść, którą sam napisał.
    # Podmieniamy na pseudonim tej samej osoby, zamiast wycinać nazwę —
    # fakt, że obiekt jest nazwany po kimś, jest sygnałem audytowym.
    wpisy_pii = mapowanie.wczytaj()
    payload, zredagowane = zredaguj_pii(payload, wpisy_pii)
    payload["meta"]["zredagowanych_pii"] = len(zredagowane)
    if zredagowane:
        logger.warning(
            "zredagowano PII w %d miejscach treści pisanej przez klienta: %s",
            len(zredagowane),
            ", ".join(sorted(zredagowane)[:10]),
        )

    # Pełny skan antyprzeciekowy: dopiero tu mamy i cały payload, i wszystkie
    # nazwiska. Idzie PRZED zapisem, bo snapshot jest niemutowalny (D7).
    waliduj_brak_pii(json.dumps(payload, ensure_ascii=False), wpisy_pii)

    snapshot_id = zapisz_snapshot(con, client_id=client_id, payload=payload, run_at=run_at)
    domknij_run(
        con,
        run_id=run_id,
        snapshot_id=snapshot_id,
        wywolan=wywolan,
        complexity=complexity,
    )

    tresc = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return RaportRunu(
        run_id=run_id,
        snapshot_id=snapshot_id,
        client_id=client_id,
        zakres=zakres.opis(),
        wywolan=wywolan,
        complexity=complexity,
        sekund=time.monotonic() - start,
        bajtow_payloadu=len(tresc.encode("utf-8")),
        zastrzezenia=tuple(konto.zastrzezenia) + uwagi,
        discovery={
            **{f"osoby.{k}": v for k, v in osoby.discovery.items()},
            **{f"tablice.{k}": v for k, v in tablice.discovery.items()},
            **{f"automatyzacje.{k}": v for k, v in automaty.discovery.items()},
            **{f"logi.{k}": v for k, v in logi.discovery.items()},
        },
        liczby={
            "uzytkownikow": osoby.podsumowanie()["razem"],
            "mapowan_pii": osoby.zapisanych_mapowan,
            "tablic": tablice.podsumowanie()["razem"],
            "tablic_w_koszu_pominietych": tablice.usunietych,
            "kolumn_razem": tablice.podsumowanie()["kolumn_suma"],
            "itemow_razem": tablice.podsumowanie()["itemow_suma"],
            "uruchomien_automatyzacji": automaty.uruchomien_razem,
            "automatyzacji_z_bledami": len(automaty.automatyzacje_z_bledami),
            "tablic_sondowanych_automaty": len(automaty.sondy),
            "tablic_z_logami": logi.podsumowanie()["tablic_zbadanych"],
            "tablic_pozornie_zywych": logi.podsumowanie()["tablic_pozornie_zywych"],
            "wpisow_w_logach": logi.podsumowanie()["wpisow_razem"],
            "zredagowanych_pii_w_tresci": len(zredagowane),
        },
    )
