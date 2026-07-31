"""Collector — konto, plan i zakres audytu (etap 3.3).

Pierwszy krok każdego runu i jedyne miejsce, które rozstrzyga, **co wolno
wyaudytować tym tokenem.**

Zapis w `03-build.md` mówi: „Sprawdź `is_admin`. Jeśli false — przerwij".
Rozpoznanie z 2026-07-30 pokazało, że brama binarna zabija audyt własnego
konta CXLABS (token bez admina) i odmawia klientom, których admin wystawił
token o zawężonym dostępie. **Decyzja Kuby zapisana w `OTWARTE.md` O8:**
zamiast bramy — deklarowany zakres plus zapis pokrycia.

Intencja oryginału zostaje nienaruszona. Groźny nie jest brak admina,
groźny jest **audyt niepełny udający pełny**. Więc: zakres jest jawny
na wejściu, ograniczenia widoczności lądują w snapshocie jako `zastrzezenia`,
a przerwanie następuje tylko wtedy, gdy ktoś prosi o całe konto tokenem,
który całego konta nie widzi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from monday_audit.klient import MondayClient, ZapytanieError

logger = logging.getLogger(__name__)

# ŚWIADOMIE BEZ `me { name }` — to imię i nazwisko, czyli PII, której zakaz
# twardy z CLAUDE.md nie wpuszcza do snapshotu ani do kontekstu modelu.
# ŚWIADOMIE BEZ `me { id }` — przy audycie klienta to identyfikator konkretnej
# osoby z jego konta, a pseudonimizacja powstaje dopiero w 3.4. Snapshot ma być
# czysty od pierwszego zapisu, nie od 3.4. Do walidacji zakresu wystarczą
# `is_admin` i `is_guest`; tożsamość posiadacza tokena nie jest do niczego
# potrzebna. `account.name` zostaje — to nazwa firmy, nie osoby.
ZAPYTANIE_KONTO = """
query {
  me {
    is_admin
    is_guest
    account { id name slug plan { period tier max_users } }
  }
}
"""

# Dzienne limity wywołań, WYŁĄCZNIE potwierdzone w dokumentacji monday
# (skill `monday-graphql`). `basic` i `standard` są nieobecne celowo: nie znamy
# ich limitów, a zgadnięcie dałoby budżet oparty na założeniu. Nieznany tier
# zostawia budżet na wartości domyślnej i ląduje w `zastrzezenia`.
LIMITY_DZIENNE = {"free": 1_000, "pro": 10_000, "enterprise": 25_000}

# „To limit konta klienta, nie nasz. Przerwij przy 50%." — przekroczenie
# spowalnia jego integracje w środku dnia roboczego.
UDZIAL_LIMITU = 0.5


class ZakresError(RuntimeError):
    """Żądany zakres audytu jest nieosiągalny tym tokenem."""


@dataclass(frozen=True, slots=True)
class Zakres:
    """Deklaracja zakresu runu — wejście, nie wynik wykrywania.

    Jawność jest tu mechanizmem: nie da się „przypadkiem" wyaudytować całego
    konta tokenem, który go nie widzi, bo intencja musi być zapisana wprost.
    """

    typ: Literal["cale_konto", "workspace", "tablice"]
    workspace_ids: tuple[str, ...] = ()
    board_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.typ == "workspace" and not self.workspace_ids:
            raise ZakresError("zakres `workspace` bez ani jednego workspace_id nic nie audytuje")
        if self.typ == "tablice" and not self.board_ids:
            raise ZakresError("zakres `tablice` bez ani jednego board_id nic nie audytuje")
        if self.typ == "cale_konto" and (self.workspace_ids or self.board_ids):
            raise ZakresError("zakres `cale_konto` nie przyjmuje listy identyfikatorów")
        if self.typ == "workspace" and self.board_ids:
            raise ZakresError("zakres `workspace` nie przyjmuje board_ids — wybierz jeden tryb")
        if self.typ == "tablice" and self.workspace_ids:
            raise ZakresError("zakres `tablice` nie przyjmuje workspace_ids — wybierz jeden tryb")

    @classmethod
    def cale_konto(cls) -> Zakres:
        return cls(typ="cale_konto")

    @classmethod
    def workspace(cls, *workspace_ids: str | int) -> Zakres:
        return cls(typ="workspace", workspace_ids=tuple(str(w) for w in workspace_ids))

    @classmethod
    def tablice(cls, *board_ids: str | int) -> Zakres:
        """Najwęższy możliwy zakres: wskazane tablice i nic poza nimi.

        Dodane na wyraźne życzenie: przy audycie cudzego konta chcemy móc
        pokazać, że run dotknął dokładnie tych obiektów, które wskazał
        właściciel — ani jednego więcej.
        """
        return cls(typ="tablice", board_ids=tuple(str(b) for b in board_ids))

    @property
    def zawezony(self) -> bool:
        return self.typ != "cale_konto"

    def opis(self) -> str:
        if self.typ == "cale_konto":
            return "całe konto"
        if self.typ == "workspace":
            return f"{len(self.workspace_ids)} workspace'ów"
        return f"{len(self.board_ids)} tablic"


@dataclass(frozen=True, slots=True)
class Konto:
    """Metadane konta plus to, czego tym tokenem NIE widać."""

    account_id: str
    nazwa: str
    slug: str
    is_admin: bool
    is_guest: bool
    tier: str | None
    period: str | None
    max_users: int | None
    zakres: Zakres
    zastrzezenia: tuple[str, ...] = ()

    @property
    def pokrycie_pelne(self) -> bool:
        """Zakres to całe konto I token ma admina.

        UWAGA: to NIE znaczy „widzieliśmy wszystko". Czy admin widzi prywatne
        tablice innych osób, jest w `OTWARTE.md` O8 jako niepotwierdzone —
        dlatego zastrzeżenia jadą w snapshocie obok tej flagi, nie zamiast niej.
        """
        return self.is_admin and self.zakres.typ == "cale_konto"

    def do_snapshotu(self) -> dict[str, Any]:
        return {
            "konto": {"id": self.account_id, "nazwa": self.nazwa, "slug": self.slug},
            "plan": None
            if self.tier is None
            else {"tier": self.tier, "period": self.period, "max_users": self.max_users},
            "uprawnienia": {"is_admin": self.is_admin, "is_guest": self.is_guest},
            "zakres": {
                "typ": self.zakres.typ,
                "workspace_ids": list(self.zakres.workspace_ids),
                "board_ids": list(self.zakres.board_ids),
            },
            "pokrycie_pelne": self.pokrycie_pelne,
            "zastrzezenia": list(self.zastrzezenia),
        }


def budzet_z_planu(tier: str | None) -> int | None:
    """Połowa dziennego limitu dla potwierdzonych tierów, `None` dla reszty."""
    if not tier:
        return None
    limit = LIMITY_DZIENNE.get(tier.lower())
    if limit is None:
        return None
    return int(limit * UDZIAL_LIMITU)


def _wyluskaj_konto(dane: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    ja = dane.get("me")
    if not isinstance(ja, dict):
        raise ZapytanieError(f"odpowiedź bez pola `me`: {str(dane)[:200]}")

    konto = ja.get("account")
    if not isinstance(konto, dict):
        raise ZapytanieError(f"odpowiedź bez pola `me.account`: {str(dane)[:200]}")

    return ja, konto


def _zastrzezenia(
    *, is_admin: bool, is_guest: bool, tier: str | None, zakres: Zakres
) -> tuple[str, ...]:
    """Lista tego, czego nie widać — wejście dla detektorów i dla raportu.

    Token nie mówi, co pominął (skill `monday-graphql`). Jeśli my też nie
    powiemy, powstaje audyt niepełny udający pełny.
    """
    lista: list[str] = []

    if not is_admin:
        lista.append(
            "token bez uprawnień admina — widoczne tylko workspace'y i tablice "
            "tego użytkownika; prywatne tablice innych osób są niewidoczne"
        )
    if is_guest:
        lista.append("token gościa — zakres jeszcze węższy niż zwykłego członka konta")
    if zakres.zawezony:
        lista.append(f"audyt zawężony do {zakres.opis()} — reszta konta nie była sprawdzana")
    if tier is None:
        lista.append(
            "plan konta niedostępny — dzienny limit wywołań nieznany, "
            "budżet został na wartości domyślnej"
        )
    elif budzet_z_planu(tier) is None:
        lista.append(
            f"dzienny limit wywołań dla planu `{tier}` nie jest potwierdzony — "
            "budżet został na wartości domyślnej"
        )

    return tuple(lista)


async def rozpoznaj_konto(
    klient: MondayClient,
    zakres: Zakres,
    *,
    dostosuj_budzet: bool = True,
) -> Konto:
    """Pobiera metadane konta i sprawdza, czy żądany zakres jest osiągalny.

    Przerywa **tylko** przy żądaniu całego konta tokenem bez admina — bo to
    jedyny przypadek, w którym wynik byłby cicho niepełny. Zakres zawężony
    przechodzi z adnotacją, nie z odmową (`OTWARTE.md` O8).
    """
    dane = await klient.query(ZAPYTANIE_KONTO, etykieta="konto")
    ja, surowe_konto = _wyluskaj_konto(dane)

    is_admin = bool(ja.get("is_admin"))
    is_guest = bool(ja.get("is_guest"))
    plan = surowe_konto.get("plan")
    plan = plan if isinstance(plan, dict) else None
    tier = plan.get("tier") if plan else None

    logger.info(
        "[DISCOVERY] %s account.plan %s",
        "✅" if plan else "❌",
        f"tier={tier}" if plan else "= null, budżet zostaje domyślny",
    )

    if zakres.typ == "cale_konto" and not is_admin:
        raise ZakresError(
            "żądano audytu całego konta, ale token nie ma uprawnień admina — "
            "taki run zwróciłby niepełny wynik bez informacji, co pominął. "
            "Albo zdobądź token admina, albo zadeklaruj zakres przez "
            "Zakres.workspace(...)"
        )

    konto = Konto(
        account_id=str(surowe_konto.get("id", "")),
        nazwa=str(surowe_konto.get("name", "")),
        slug=str(surowe_konto.get("slug", "")),
        is_admin=is_admin,
        is_guest=is_guest,
        tier=tier,
        period=plan.get("period") if plan else None,
        max_users=plan.get("max_users") if plan else None,
        zakres=zakres,
        zastrzezenia=_zastrzezenia(is_admin=is_admin, is_guest=is_guest, tier=tier, zakres=zakres),
    )

    budzet = budzet_z_planu(tier)
    if budzet is not None and dostosuj_budzet:
        # Plan determinuje dzienny limit wywołań dla reszty runu (3.3).
        klient.ustaw_budzet(budzet)

    logger.info(
        "konto %s (%s), zakres: %s, admin: %s, pokrycie pełne: %s, zastrzeżeń: %d",
        konto.nazwa,
        konto.slug,
        zakres.opis(),
        is_admin,
        konto.pokrycie_pelne,
        len(konto.zastrzezenia),
    )
    return konto
