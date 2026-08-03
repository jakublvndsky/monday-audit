"""Walidacja kontraktu wyjściowego agenta (D8, etap 3.11).

To jest miejsce, w którym zakaz twardy „finding bez pola `dowod` nie przechodzi
walidacji" przestaje być zdaniem w dokumentacji i staje się kodem.

**Odrzucenie jest logowane i zapisywane, nie ukrywane.** Odsetek odrzuconych
findingów to główna metryka jakości w etapie 4, a sam licznik nie mówi, CZY
agent myli klasy, zapomina dowodu, czy wymyśla kwoty — to trzy różne poprawki
promptu. Dlatego odrzucony finding ląduje w `findings_odrzucone` razem
z regułą, która go złapała.

Walidacja jest **względem rubryki**, nie względem stałej listy pól. Klasa
deklaruje w `dowod`, jakich faktów wymaga, i to jest jedyne źródło prawdy:
`PROCESS_BYPASS` potrzebuje siedmiu pól, `ZOMBIE_ACCOUNT` sześciu. Zaszycie
tego w kodzie oznaczałoby dwa źródła prawdy, które się rozjadą przy pierwszej
zmianie rubryki.

Czego walidacja NIE robi: nie ocenia, czy finding jest MĄDRY. Sprawdza, czy
jest kompletny i zgodny z rubryką. Ocena trafności to etap 4 i człowiek.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from monday_audit.rubryka import STATUS_DO_WERYFIKACJI, Rubryka

logger = logging.getLogger(__name__)

# Nazwy reguł. Idą do `findings_odrzucone.regula`, więc eval z etapu 4 może
# policzyć „ile razy agent zapomniał dowodu" jednym GROUP BY.
REGULA_KLASA_NIEZNANA = "klasa_id nie istnieje w rubryce"
REGULA_KLASA_DO_WERYFIKACJI = "klasa ma status do_weryfikacji"
REGULA_DOWOD_PUSTY = "dowod pusty albo nie jest obiektem"
REGULA_DOWOD_NIEPELNY = "dowod nie pokrywa pol wymaganych przez klase"
REGULA_KWOTA_PRZY_RYZYKU = "kwota_pln podana przy typ_wyceny ryzyko"
REGULA_KWOTA_UJEMNA = "kwota_pln nie jest liczba dodatnia"
REGULA_SLOWNIK = "waga, wysilek_naprawy albo pewnosc poza slownikiem"
REGULA_NIEZGODNA_Z_RUBRYKA = "waga albo wysilek_naprawy inne niz w rubryce"
REGULA_BRAK_POLA = "brak pola wymaganego przez kontrakt"
REGULA_PUSTY_TEKST = "opis albo rekomendacja puste"

PEWNOSC = ("wysoka", "srednia", "niska")

# Pola findingu z D8. `kwota_pln` może być `null`, ale musi być obecne —
# brak klucza i `null` to dwie różne rzeczy, a tylko jedna jest świadoma.
POLA_FINDINGU = (
    "klasa_id",
    "waga",
    "wysilek_naprawy",
    "typ_wyceny",
    "kwota_pln",
    "opis",
    "rekomendacja",
    "dowod",
    "pewnosc",
)


class KontraktError(RuntimeError):
    """Odpowiedź agenta nie da się w ogóle zwalidować — zła struktura korzenia."""


@dataclass(frozen=True, slots=True)
class Odrzucony:
    """Finding, który nie przeszedł. Trzymamy treść, nie tylko powód."""

    klasa_id: str | None
    regula: str
    powod: str
    finding: dict[str, Any]


@dataclass
class WynikWalidacji:
    przyjete: list[dict[str, Any]] = field(default_factory=list)
    odrzucone: list[Odrzucony] = field(default_factory=list)
    hipotezy_odrzucone: list[dict[str, Any]] = field(default_factory=list)
    zuzycie: dict[str, int] = field(default_factory=dict)

    @property
    def odsetek_odrzuconych(self) -> float:
        """Główna metryka jakości z etapu 4. Bez findingów zwraca 0.0."""
        razem = len(self.przyjete) + len(self.odrzucone)
        return round(len(self.odrzucone) / razem, 4) if razem else 0.0

    def opis(self) -> str:
        return (
            f"findingi: {len(self.przyjete)} przyjęte, {len(self.odrzucone)} odrzucone "
            f"({self.odsetek_odrzuconych:.0%}), hipotez odrzuconych przez agenta: "
            f"{len(self.hipotezy_odrzucone)}"
        )


def _liczba(wartosc: Any) -> float | None:
    if isinstance(wartosc, bool) or not isinstance(wartosc, (int, float)):
        return None
    return float(wartosc)


def _sprawdz_finding(surowy: Any, rubryka: Rubryka) -> tuple[str, str] | None:
    """Zwraca `(regula, powod)` przy odrzuceniu albo `None`, gdy finding przechodzi.

    Kolejność sprawdzeń jest celowa: najpierw struktura, potem klasa, potem
    zgodność z rubryką. Odwrotna kolejność zgłaszałaby „nieznana klasa"
    dla findingu, który jest w ogóle nie-obiektem.
    """
    if not isinstance(surowy, dict):
        return REGULA_BRAK_POLA, "finding nie jest obiektem"

    brakujace = [p for p in POLA_FINDINGU if p not in surowy]
    if brakujace:
        return REGULA_BRAK_POLA, f"brak pól: {', '.join(brakujace)}"

    klasa_id = str(surowy["klasa_id"])
    klasa = rubryka.po_id.get(klasa_id)
    if klasa is None:
        return REGULA_KLASA_NIEZNANA, f"klasa {klasa_id} nie jest w rubryce {rubryka.wersja}"
    if klasa.status == STATUS_DO_WERYFIKACJI:
        return (
            REGULA_KLASA_DO_WERYFIKACJI,
            f"klasa {klasa_id} ma status {STATUS_DO_WERYFIKACJI} — nie wolno jej raportować",
        )

    # `dowod` — zakaz twardy z CLAUDE.md. Pusty obiekt jest tak samo zły
    # jak brak pola: obie sytuacje znaczą „agent nie wskazał faktu".
    dowod = surowy["dowod"]
    if not isinstance(dowod, dict) or not dowod:
        return REGULA_DOWOD_PUSTY, "dowod musi być niepustym obiektem"

    wymagane = {p.rstrip("[]") for p in klasa.dowod}
    obecne = {k.rstrip("[]") for k in dowod}
    niepokryte = sorted(wymagane - obecne)
    if niepokryte:
        return (
            REGULA_DOWOD_NIEPELNY,
            f"klasa {klasa_id} wymaga w dowodzie: {', '.join(niepokryte)}",
        )
    # Klucz obecny, ale puste znaczy tyle samo co brak.
    puste = sorted(k for k in dowod if dowod[k] in (None, "", [], {}))
    if puste:
        return REGULA_DOWOD_NIEPELNY, f"pola dowodu są puste: {', '.join(puste)}"

    # Wycena. `kwota_pln` przy `ryzyko` to wymyślona liczba w raporcie —
    # dokładnie to, co podważa całą wiarygodność u pierwszego klienta,
    # który ją sprawdzi.
    kwota = _liczba(surowy["kwota_pln"])
    if klasa.typ_wyceny == "ryzyko" and surowy["kwota_pln"] is not None:
        return (
            REGULA_KWOTA_PRZY_RYZYKU,
            f"klasa {klasa_id} ma typ_wyceny ryzyko, więc kwota_pln musi być null",
        )
    if kwota is not None and kwota <= 0:
        return REGULA_KWOTA_UJEMNA, "kwota_pln musi być liczbą dodatnią albo null"

    # Słowniki i zgodność z rubryką. Waga i wysiłek NIE należą do agenta —
    # są w definicji klasy i agent ma je tylko przepisać.
    if str(surowy["pewnosc"]) not in PEWNOSC:
        return REGULA_SLOWNIK, f"pewnosc musi być jedną z: {', '.join(PEWNOSC)}"
    if str(surowy["waga"]) != klasa.waga:
        return (
            REGULA_NIEZGODNA_Z_RUBRYKA,
            f"klasa {klasa_id} ma wagę {klasa.waga}, a finding podaje {surowy['waga']}",
        )
    if str(surowy["wysilek_naprawy"]) != klasa.wysilek_naprawy:
        return (
            REGULA_NIEZGODNA_Z_RUBRYKA,
            f"klasa {klasa_id} ma wysilek_naprawy {klasa.wysilek_naprawy}, "
            f"a finding podaje {surowy['wysilek_naprawy']}",
        )
    if str(surowy["typ_wyceny"]) != klasa.typ_wyceny:
        return (
            REGULA_NIEZGODNA_Z_RUBRYKA,
            f"klasa {klasa_id} ma typ_wyceny {klasa.typ_wyceny}, "
            f"a finding podaje {surowy['typ_wyceny']}",
        )

    for pole in ("opis", "rekomendacja"):
        if not str(surowy[pole] or "").strip():
            return REGULA_PUSTY_TEKST, f"{pole} jest puste"

    return None


def waliduj(odpowiedz: dict[str, Any], rubryka: Rubryka) -> WynikWalidacji:
    """Waliduje odpowiedź agenta wobec D8 i rubryki.

    **`hipotezy_odrzucone` jest obowiązkowe** i to nie jest formalność:
    agent, który potwierdza wszystko, jest bezużyteczny, a bez tego pola nie
    da się tego zauważyć. Brak klucza przerywa walidację całej odpowiedzi,
    a nie odrzuca pojedynczy finding — to błąd na poziomie kontraktu.
    """
    if not isinstance(odpowiedz, dict):
        raise KontraktError("odpowiedź agenta nie jest obiektem JSON")
    if "findings" not in odpowiedz or not isinstance(odpowiedz["findings"], list):
        raise KontraktError("brak listy `findings`")
    if "hipotezy_odrzucone" not in odpowiedz:
        raise KontraktError(
            "brak obowiązkowego pola `hipotezy_odrzucone` (D8) — agent musi "
            "raportować, czego nie potwierdził i dlaczego"
        )
    if not isinstance(odpowiedz["hipotezy_odrzucone"], list):
        raise KontraktError("`hipotezy_odrzucone` musi być listą")

    wersja = str(odpowiedz.get("rubric_version") or "")
    if wersja and wersja != rubryka.wersja:
        # Nie przerywamy: agent mógł dostać rubrykę i przepisać wersję z błędem,
        # a findingi mogą być dobre. Ale to musi być widoczne.
        logger.warning(
            "agent podał rubric_version %r, a walidujemy wobec %r", wersja, rubryka.wersja
        )

    wynik = WynikWalidacji(
        hipotezy_odrzucone=list(odpowiedz["hipotezy_odrzucone"]),
        zuzycie=dict(odpowiedz.get("zuzycie") or {}),
    )
    for surowy in odpowiedz["findings"]:
        blad = _sprawdz_finding(surowy, rubryka)
        if blad is None:
            wynik.przyjete.append(surowy)
            continue
        regula, powod = blad
        klasa_id = surowy.get("klasa_id") if isinstance(surowy, dict) else None
        wynik.odrzucone.append(
            Odrzucony(
                klasa_id=str(klasa_id) if klasa_id else None,
                regula=regula,
                powod=powod,
                finding=surowy if isinstance(surowy, dict) else {"surowe": repr(surowy)},
            )
        )
        # Loguj, nie ukrywaj (3.11).
        logger.warning("finding ODRZUCONY [%s]: %s", regula, powod)

    if not wynik.hipotezy_odrzucone:
        logger.warning(
            "agent nie odrzucił ANI JEDNEJ hipotezy — D8 wymaga tego pola niepustego, "
            "a agent potwierdzający wszystko jest bezużyteczny"
        )

    logger.info("%s", wynik.opis())
    return wynik


def zapisz_odrzucone(
    con: sqlite3.Connection,
    odrzucone: list[Odrzucony],
    *,
    run_id: str,
    snapshot_id: int,
) -> int:
    """Zapisuje odrzucone findingi z treścią. Jedna transakcja."""
    if not odrzucone:
        return 0
    with con:
        con.executemany(
            "INSERT INTO findings_odrzucone (run_id, snapshot_id, klasa_id, regula, powod, "
            "finding) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    snapshot_id,
                    o.klasa_id,
                    o.regula,
                    o.powod,
                    json.dumps(o.finding, ensure_ascii=False),
                )
                for o in odrzucone
            ],
        )
    return len(odrzucone)


def zapisz_findingi(
    con: sqlite3.Connection,
    przyjete: list[dict[str, Any]],
    *,
    run_id: str,
    snapshot_id: int,
    rubryka: Rubryka,
) -> int:
    """Zapisuje przyjęte findingi. `widocznosc` i `trop` bierzemy Z RUBRYKI.

    Agent ich nie podaje i nie ma podawać: to decyzje biznesowe z etapu 1,
    a nie ustalenia z badania hipotezy. Gdyby je podawał, mógłby oznaczyć
    finding wewnętrzny jako klientowski.
    """
    if not przyjete:
        return 0
    wiersze = []
    for f in przyjete:
        klasa = rubryka.po_id[str(f["klasa_id"])]
        wiersze.append(
            (
                run_id,
                snapshot_id,
                klasa.id,
                rubryka.wersja,
                klasa.waga,
                klasa.wysilek_naprawy,
                klasa.typ_wyceny,
                _liczba(f["kwota_pln"]),
                klasa.widocznosc,
                str(f["opis"]),
                str(f["rekomendacja"]),
                json.dumps(f["dowod"], ensure_ascii=False),
                str(f["pewnosc"]),
                f.get("trop_sprzedazowy"),
            )
        )
    with con:
        con.executemany(
            "INSERT INTO findings (run_id, snapshot_id, klasa_id, rubric_ver, waga, wysilek, "
            "typ_wyceny, kwota_pln, widocznosc, opis, rekomendacja, dowod, pewnosc, trop) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            wiersze,
        )
    return len(wiersze)
