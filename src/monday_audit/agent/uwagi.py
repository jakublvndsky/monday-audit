"""Uwaga krytyczna: jedna kategoria zamiast trzynastu klas (plan, faza 5b-1).

Wytyczne zastępują rubrykę z wagami, wysiłkiem i pewnością **jedną kategorią**.
Ten moduł jest tym, co z niej zostaje po stronie wyjścia.

## Co znika, a co zostaje — i dlaczego to NIE to samo

Z findingu (D8) znikają pola **oceniające**:

    waga, wysilek_naprawy, pewnosc     → jedna kategoria, więc nie ma czego stopniować
    typ_wyceny, kwota_pln              → nie wyceniamy znalezisk (decyzja Kuby 2026-09-23)

Zostaje `klasa_id`, i to nie jest niekonsekwencja. Po uproszczeniu **klasa
przestaje być kategorią w raporcie, a zostaje POCHODZENIEM**: mówi, który
detektor to znalazł, a przez to — jakich faktów wymaga dowód. Bez niej
`sprawdz_dowod` nie ma czego sprawdzać, a „finding bez dowodu nie przechodzi
walidacji" staje się zdaniem bez egzekucji.

Innymi słowy: **raport widzi jedną kategorię, walidacja widzi trzynaście.**

## Dlaczego osobny moduł, a nie zmiana w `kontrakt.py`

Stara ścieżka ma dalej działać (decyzja Kuby 2026-09-23: „zostawmy na razie").
Przepisanie `kontrakt.py` zabrałoby jedyną działającą analizę na czas, w którym
nowa jeszcze się nie obroniła. Dlatego nowy kształt stoi obok, a regułę dowodu
**dzieli** z `kontrakt.sprawdz_dowod` — nie kopiuje. Kopia rozjechałaby się przy
pierwszej zmianie, dokładnie jak literał `personal_agent_member` między `osoby`
a `pulpit` (O44).

## Czego ten moduł NIE robi

Nie ocenia, czy uwaga jest MĄDRA. Sprawdza, czy jest kompletna i czy stoi na
faktach, które wskazał detektor. Ocena trafności to człowiek.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from monday_audit.agent.dowod import (
    REGULA_BRAK_POLA,
    REGULA_KLASA_DO_WERYFIKACJI,
    REGULA_KLASA_NIEZNANA,
    REGULA_PUSTY_TEKST,
    KontraktError,
    sprawdz_dowod,
)
from monday_audit.detekcja.rubryka import STATUS_DO_WERYFIKACJI, Rubryka

logger = logging.getLogger(__name__)

# Cztery pola zamiast dziewięciu. `klasa_id` jest POCHODZENIEM, nie kategorią —
# patrz docstring modułu.
POLA_UWAGI = ("klasa_id", "opis", "rekomendacja", "dowod")


@dataclass(frozen=True, slots=True)
class OdrzuconaUwaga:
    """Uwaga, która nie przeszła. Trzymamy treść, nie tylko powód —
    bez niej nie da się poprawić promptu, a tylko policzyć porażki."""

    klasa_id: str | None
    regula: str
    powod: str
    uwaga: dict[str, Any]


@dataclass
class WynikUwag:
    przyjete: list[dict[str, Any]] = field(default_factory=list)
    odrzucone: list[OdrzuconaUwaga] = field(default_factory=list)
    pominiete: list[dict[str, Any]] = field(default_factory=list)

    @property
    def odsetek_odrzuconych(self) -> float:
        razem = len(self.przyjete) + len(self.odrzucone)
        return round(len(self.odrzucone) / razem, 4) if razem else 0.0

    def opis(self) -> str:
        return (
            f"uwagi: {len(self.przyjete)} przyjęte, {len(self.odrzucone)} odrzucone "
            f"({self.odsetek_odrzuconych:.0%}), pominiętych przez model: "
            f"{len(self.pominiete)}"
        )


def _sprawdz_uwage(surowa: Any, rubryka: Rubryka) -> tuple[str, str] | None:
    """`(regula, powod)` przy odrzuceniu albo `None`, gdy uwaga przechodzi.

    Kolejność sprawdzeń jak w `kontrakt._sprawdz_finding` i z tego samego
    powodu: najpierw struktura, potem pochodzenie, na końcu dowód. Odwrotna
    zgłaszałaby „nieznana klasa" dla czegoś, co nie jest nawet obiektem.
    """
    if not isinstance(surowa, dict):
        return REGULA_BRAK_POLA, "uwaga nie jest obiektem"

    brakujace = [p for p in POLA_UWAGI if p not in surowa]
    if brakujace:
        return REGULA_BRAK_POLA, f"brak pól: {', '.join(brakujace)}"

    klasa_id = str(surowa["klasa_id"])
    klasa = rubryka.po_id.get(klasa_id)
    if klasa is None:
        return REGULA_KLASA_NIEZNANA, f"klasa {klasa_id} nie jest w rubryce {rubryka.wersja}"
    if klasa.status == STATUS_DO_WERYFIKACJI:
        return (
            REGULA_KLASA_DO_WERYFIKACJI,
            f"klasa {klasa_id} ma status {STATUS_DO_WERYFIKACJI} — nie wolno jej raportować",
        )

    for pole in ("opis", "rekomendacja"):
        if not str(surowa[pole] or "").strip():
            return REGULA_PUSTY_TEKST, f"{pole} jest puste"

    # Zakaz twardy z `CLAUDE.md`. WSPÓŁDZIELONY z `kontrakt`, nie skopiowany.
    return sprawdz_dowod(surowa["dowod"], klasa)


def waliduj_uwagi(odpowiedz: Any, rubryka: Rubryka) -> WynikUwag:
    """Odpowiedź modelu → uwagi przyjęte i odrzucone.

    Podnosi `KontraktError`, gdy korzeń jest tak zły, że nie ma czego walidować.
    To nie to samo co uwaga odrzucona: jedna zła uwaga jest normalnym wynikiem,
    a odpowiedź bez struktury znaczy, że sesja poszła nie tak i trzeba to
    zobaczyć, a nie policzyć jako „zero uwag".
    """
    if not isinstance(odpowiedz, dict):
        raise KontraktError("odpowiedź modelu nie jest obiektem")

    surowe = odpowiedz.get("uwagi")
    if surowe is None:
        raise KontraktError("odpowiedź modelu nie ma pola `uwagi`")
    if not isinstance(surowe, list):
        raise KontraktError("pole `uwagi` nie jest listą")

    wynik = WynikUwag()
    for surowa in surowe:
        odrzut = _sprawdz_uwage(surowa, rubryka)
        if odrzut is None:
            wynik.przyjete.append(surowa)
            continue
        regula, powod = odrzut
        klasa_id = surowa.get("klasa_id") if isinstance(surowa, dict) else None
        wynik.odrzucone.append(
            OdrzuconaUwaga(
                klasa_id=str(klasa_id) if klasa_id is not None else None,
                regula=regula,
                powod=powod,
                uwaga=surowa if isinstance(surowa, dict) else {"surowe": repr(surowa)[:200]},
            )
        )
        # WARNING, nie DEBUG: odsetek odrzuconych jest metryką jakości, a nie
        # szumem. Cicha porażka walidacji uczy ignorować walidację.
        logger.warning("uwaga odrzucona (%s): %s", regula, powod)

    pominiete = odpowiedz.get("pominiete")
    if isinstance(pominiete, list):
        # Hipotezy, których model NIE uznał za uwagi. To nie są porażki —
        # to jest ta połowa pracy, za którą płacimy, żeby dowiedzieć się,
        # że czegoś NIE ma. Bez tej listy raport nie odróżnia „sprawdzone
        # i czyste" od „niesprawdzone".
        wynik.pominiete.extend(p for p in pominiete if isinstance(p, dict))

    return wynik
