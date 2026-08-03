"""Narzędzia agenta — TYLKO CZYTAJĄCE, bez wyjątków (etap 3.10).

**Nie ma tu MCP monday i to jest zmiana wobec D4.** Powód jest zmierzony,
nie estetyczny: `@mondaydotcomorg/monday-api-mcp@3.3.0` z flagą `--read-only`
wystawia te same 92 narzędzia co bez flagi (w tym `create_item`, `delete_item`,
`all_api_write` i `execute_code`), a wywołanie `create_board` oraz
`all_api_write` z surową mutacją **przeszło do API monday** — nie udało się
wyłącznie dlatego, że token był atrapą (401). Cały filar D4 („read-only
wymuszony na poziomie serwera to mechanizm, nie polityka") był nieprawdziwy.

Zamiast tego narzędzia idą przez `MondayClient`, który **odrzuca mutacje
strukturalnie**: `przygotuj_zapytanie()` przerywa na `mutation` i `subscription`
niezależnie od wielkości liter i wiodących spacji. To jest „odebranie
możliwości" z D6, a nie filtrowanie — w tej ścieżce kodu nie ma jak wysłać
zapisu. Dodatkowo, gratis: licznik wywołań, hamulec complexity, retry
z backoffem i zapis KAŻDEGO wywołania do tabeli `wywolania` (D10), czego MCP
nie robił.

Trzy zasady z 3.10, każda zaimplementowana jako mechanizm:

1. **Każde narzędzie przycina wyjście.** Surowa odpowiedź API w kontekście to
   główna przyczyna, dla której agenci przestają rozumować. Sufity są jawne
   i każde urwanie jest odnotowane w wyniku, nie przemilczane.
2. **Licznik per hipoteza.** Budżet pochodzi z rubryki. Wyczerpanie zwraca
   KOMUNIKAT, nie wyjątek — agent ma domknąć hipotezę z tym, co ma.
3. **Token nigdy w kontekście modelu.** Żyje w `MondayClient`, wczytany
   z konfiguracji (D12). Żadne narzędzie go nie zwraca ani nie loguje.

Większość pytań agenta odpowiada SNAPSHOT, nie monday. Przy mapowaniu
`rola_agenta` wszystkich 11 klas wyszło, że do API trzeba wejść tylko po dwie
rzeczy: próbkę wypełnienia kolumn (`BOARD_OVERCOMPLEX`, jedyny wyjątek od D5)
i szczegół activity logu z datami (`ENGAGEMENT_DROP`, `PROCESS_BYPASS`).
Dostępy gościa dla `GUEST_SPRAWL` czyta się ze snapshotu — i musi tak zostać,
bo zapytanie monday o konkretną osobę wymagałoby odwrócenia pseudonimu przez
tabelę mapowania, do której agent nie ma i nie może mieć drogi (D6).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from monday_audit.detektory import Hipoteza
from monday_audit.klient import MondayClient
from monday_audit.logi import ZAPYTANIE_LOGOW, klasa_zdarzenia, na_iso
from monday_audit.osoby import policz_hash, waliduj_brak_pii

logger = logging.getLogger(__name__)

# Sufity wyjścia. Nie są polityką, są warunkiem tego, żeby agent nadal
# rozumował — 300 wpisów logu w kontekście to nie więcej informacji,
# to mniej uwagi.
LIMIT_PROBKI_ITEMOW = 25
LIMIT_WPISOW_LOGU = 40
LIMIT_TABLIC_W_ODPOWIEDZI = 30
LIMIT_OSOB_W_ODPOWIEDZI = 30

# Typ kolumny, którego NIE MA w `column_values`. Tytuł itemu siedzi
# w `items { name }`, więc licząc wypełnienie po `column_values` wypada
# zawsze zero i każda tablica dostawałaby jedną fałszywą martwą kolumnę.
# Zmierzone na tablicy 5097454411: `name` pokazywało 0/15 przy 15 itemach
# z tytułami.
TYP_KOLUMNY_TYTUL = "name"

# Świadomie BEZ pola `text` w wyniku. Pobieramy je, żeby policzyć wypełnienie,
# i natychmiast wyrzucamy — do agenta idą wyłącznie liczby. Dzięki temu granica
# PII zostaje nietknięta, bo nie ma czego redagować (decyzja z 2026-08-03).
ZAPYTANIE_PROBKI = """
query ($ids: [ID!], $limit: Int!) {
  boards (ids: $ids) {
    id
    columns { id title type }
    items_page (limit: $limit) {
      cursor
      items { id column_values { id text } }
    }
  }
}
"""


class NarzedzieError(RuntimeError):
    """Narzędzie wywołano niepoprawnie — zła nazwa pytania albo brak zakresu."""


@dataclass
class Budzet:
    """Licznik wywołań monday per hipoteza. Budżet z rubryki.

    Liczy WYŁĄCZNIE wejścia do API monday. Odczyty snapshotu są darmowe:
    nie kosztują wywołania, nie zużywają dziennego limitu klienta i nie ma
    powodu ich racjonować. Ogranicza je przycinanie wyjścia, nie licznik.
    """

    limit: int
    zuzyte: int = 0

    @property
    def zostalo(self) -> int:
        return max(0, self.limit - self.zuzyte)

    @property
    def wyczerpany(self) -> bool:
        return self.zostalo == 0

    def wez(self, ile: int = 1) -> bool:
        """Rezerwuje wywołania. `False` = nie ma budżetu, nie wołaj API."""
        if self.zostalo < ile:
            return False
        self.zuzyte += ile
        return True


@dataclass(frozen=True, slots=True)
class Wynik:
    """Odpowiedź narzędzia. `urwane` i `budzet` są częścią odpowiedzi.

    Agent musi wiedzieć, że patrzy na wycinek — inaczej napisze finding
    o całości na podstawie trzydziestu pierwszych tablic.
    """

    dane: dict[str, Any]
    urwane: bool = False
    komunikat: str | None = None
    budzet_zostalo: int = 0

    def do_modelu(self) -> dict[str, Any]:
        """Kształt, który faktycznie idzie do kontekstu modelu."""
        wynik: dict[str, Any] = {"dane": self.dane, "budzet_zostalo": self.budzet_zostalo}
        if self.urwane:
            wynik["UWAGA"] = "wynik URWANY sufitem — nie opisuj tego jako całości"
        if self.komunikat:
            wynik["komunikat"] = self.komunikat
        return wynik


# Predefiniowane pytania do snapshotu. **Nie ma tu surowego SQL-a** i to jest
# wymóg 3.10: agent wybiera pytanie z listy, nie pisze zapytania. Gdyby pisał,
# treść od klienta (nazwa tablicy z prompt injection) mogłaby wpłynąć na to,
# co zapytanie robi.
PYTANIA = (
    "tablica",
    "aktywnosc_tablicy",
    "kolumny_tablicy",
    "osoba",
    "tablice_osoby",
    "automatyzacja",
    "tablice_workspace",
    "podsumowanie",
)


@dataclass
class Narzedzia:
    """Zestaw narzędzi na jeden run. Budżety są per hipoteza, nie tutaj."""

    con: sqlite3.Connection
    snapshot_id: int
    client_id: str
    sol: bytes
    klient: MondayClient | None = None

    def dla_hipotezy(self, hipoteza: Hipoteza) -> NarzedziaHipotezy:
        return NarzedziaHipotezy(
            zestaw=self,
            hipoteza=hipoteza,
            budzet=Budzet(limit=hipoteza.budzet_wywolan),
        )

    def _payload(self, sciezka: str) -> Any:
        wiersz = self.con.execute(
            "SELECT json_extract(payload, ?) AS wycinek FROM snapshots WHERE id = ?",
            (sciezka, self.snapshot_id),
        ).fetchone()
        if wiersz is None:
            raise NarzedzieError(f"snapshot {self.snapshot_id} nie istnieje")
        return json.loads(wiersz["wycinek"]) if wiersz["wycinek"] else None


@dataclass
class NarzedziaHipotezy:
    """Narzędzia z licznikiem przypisanym do jednej hipotezy."""

    zestaw: Narzedzia
    hipoteza: Hipoteza
    budzet: Budzet
    wywolania: list[str] = field(default_factory=list)

    # ── snapshot: darmowe, ale przycinane ────────────────────────────────

    def pobierz_inwentarz(self, zakres: str) -> Wynik:
        """Sekcja snapshotu w postaci podsumowania, nie surowej listy.

        Sygnatura z 3.10. `zakres` to nazwa sekcji: `konto`, `uzytkownicy`,
        `tablice`, `automatyzacje`, `aktywnosc`. Zwracamy PODSUMOWANIA
        i discovery, nie pełne listy — pełna lista 105 tablic w kontekście
        modelu to nie wiedza, to szum. Po szczegół obiektu jest
        `zapytaj_snapshot`.
        """
        self.wywolania.append(f"pobierz_inwentarz:{zakres}")
        dozwolone = ("konto", "uzytkownicy", "tablice", "automatyzacje", "aktywnosc")
        if zakres not in dozwolone:
            raise NarzedzieError(f"nieznany zakres {zakres!r}; dozwolone: {', '.join(dozwolone)}")

        sekcja = self.zestaw._payload(f"$.{zakres}") or {}
        dane: dict[str, Any] = {"zakres": zakres}
        for klucz in ("podsumowanie", "discovery", "plan", "konto", "zakres", "zastrzezenia"):
            if isinstance(sekcja, dict) and klucz in sekcja and klucz != zakres:
                dane[klucz] = sekcja[klucz]
        if zakres == "automatyzacje" and isinstance(sekcja, dict):
            dane["uruchomienia"] = sekcja.get("uruchomienia")
        return Wynik(dane=dane, budzet_zostalo=self.budzet.zostalo)

    def zapytaj_snapshot(self, pytanie: str, obiekt_id: str | None = None) -> Wynik:
        """Predefiniowane pytanie do snapshotu. Bez surowego SQL-a (3.10).

        Odstępstwo od sygnatury w 3.10: doszedł `obiekt_id`. Bez niego każde
        pytanie musiałoby zwracać wszystko, a wtedy przycinanie wyjścia
        traci sens — agent pyta o KONKRETNĄ tablicę z hipotezy.
        """
        self.wywolania.append(f"zapytaj_snapshot:{pytanie}")
        if pytanie not in PYTANIA:
            raise NarzedzieError(f"nieznane pytanie {pytanie!r}; dozwolone: {', '.join(PYTANIA)}")

        metoda = getattr(self, f"_pytanie_{pytanie}")
        wymaga_id = pytanie != "podsumowanie"
        if wymaga_id and not obiekt_id:
            raise NarzedzieError(f"pytanie {pytanie!r} wymaga `obiekt_id`")
        return metoda(obiekt_id) if wymaga_id else metoda()

    def _tablice(self) -> list[dict[str, Any]]:
        return self.zestaw._payload("$.tablice.tablice") or []

    def _aktywnosc(self) -> list[dict[str, Any]]:
        return self.zestaw._payload("$.aktywnosc.aktywnosc_tablic") or []

    def _osoby(self) -> list[dict[str, Any]]:
        return self.zestaw._payload("$.uzytkownicy.uzytkownicy") or []

    def _pytanie_tablica(self, board_id: str) -> Wynik:
        tablica = next(
            (t for t in self._tablice() if str(t.get("board_id")) == str(board_id)), None
        )
        if tablica is None:
            return Wynik(
                dane={"board_id": board_id},
                komunikat="tej tablicy nie ma w snapshocie — jest poza zakresem audytu",
                budzet_zostalo=self.budzet.zostalo,
            )
        # Kolumny osobno, przez `kolumny_tablicy` — tutaj tylko ich liczba.
        skrocona = {k: v for k, v in tablica.items() if k != "kolumny"}
        skrocona["kolumn"] = len(tablica.get("kolumny") or [])
        return Wynik(dane=skrocona, budzet_zostalo=self.budzet.zostalo)

    def _pytanie_aktywnosc_tablicy(self, board_id: str) -> Wynik:
        sygnal = next(
            (a for a in self._aktywnosc() if str(a.get("board_id")) == str(board_id)), None
        )
        if sygnal is None:
            return Wynik(
                dane={"board_id": board_id},
                komunikat=(
                    "tej tablicy NIE próbkowano pod activity log — to nie znaczy "
                    "braku aktywności, tylko że nikt jej nie sprawdzał"
                ),
                budzet_zostalo=self.budzet.zostalo,
            )
        return Wynik(dane=dict(sygnal), budzet_zostalo=self.budzet.zostalo)

    def _pytanie_kolumny_tablicy(self, board_id: str) -> Wynik:
        tablica = next(
            (t for t in self._tablice() if str(t.get("board_id")) == str(board_id)), None
        )
        kolumny = (tablica or {}).get("kolumny") or []
        po_typie = Counter(str(k.get("type")) for k in kolumny)
        return Wynik(
            dane={
                "board_id": board_id,
                "kolumn": len(kolumny),
                "po_typie": dict(sorted(po_typie.items())),
                "kolumny": [
                    {"id": k.get("id"), "title": k.get("title"), "type": k.get("type")}
                    for k in kolumny
                ],
            },
            budzet_zostalo=self.budzet.zostalo,
        )

    def _pytanie_osoba(self, user_hash: str) -> Wynik:
        osoba = next((o for o in self._osoby() if str(o.get("user_hash")) == str(user_hash)), None)
        if osoba is None:
            return Wynik(
                dane={"user_hash": user_hash},
                komunikat="tego pseudonimu nie ma w snapshocie",
                budzet_zostalo=self.budzet.zostalo,
            )
        return Wynik(dane=dict(osoba), budzet_zostalo=self.budzet.zostalo)

    def _pytanie_tablice_osoby(self, user_hash: str) -> Wynik:
        """Do czego dana osoba ma wlot — z subskrypcji i własności w snapshocie.

        To jedyna droga do tej informacji i musi tak zostać: zapytanie monday
        o konkretnego użytkownika wymagałoby odwrócenia pseudonimu przez tabelę
        mapowania, a do niej agent nie ma narzędzia (D6).
        """
        znalezione = [
            {
                "board_id": t.get("board_id"),
                "nazwa": t.get("nazwa"),
                "rola": "wlasciciel" if user_hash in (t.get("owners") or []) else "subskrybent",
                "workspace_id": t.get("workspace_id"),
            }
            for t in self._tablice()
            if user_hash in (t.get("owners") or []) or user_hash in (t.get("subscribers") or [])
        ]
        urwane = len(znalezione) > LIMIT_TABLIC_W_ODPOWIEDZI
        return Wynik(
            dane={
                "user_hash": user_hash,
                "tablic": len(znalezione),
                "tablice": znalezione[:LIMIT_TABLIC_W_ODPOWIEDZI],
                "uwaga_o_zakresie": (
                    "widoczne tylko tablice objęte zakresem audytu; "
                    "dostępy poza zakresem nie są znane"
                ),
            },
            urwane=urwane,
            budzet_zostalo=self.budzet.zostalo,
        )

    def _pytanie_automatyzacja(self, automation_id: str) -> Wynik:
        statystyki = self.zestaw._payload("$.automatyzacje.statystyki_automatyzacji") or []
        rekord = next(
            (a for a in statystyki if str(a.get("automation_id")) == str(automation_id)), None
        )
        if rekord is None:
            return Wynik(
                dane={"automation_id": automation_id},
                komunikat=(
                    "tej automatyzacji nie ma w statystykach — API pokazuje tylko te, "
                    "które się uruchamiały; listy automatyzacji nie ma wcale (O12)"
                ),
                budzet_zostalo=self.budzet.zostalo,
            )
        return Wynik(dane=dict(rekord), budzet_zostalo=self.budzet.zostalo)

    def _pytanie_tablice_workspace(self, workspace_id: str) -> Wynik:
        w_workspace = [
            {
                "board_id": t.get("board_id"),
                "nazwa": t.get("nazwa"),
                "typ": t.get("typ"),
                "items_count": t.get("items_count"),
                "kolumn": len(t.get("kolumny") or []),
                "created_at": t.get("created_at"),
            }
            for t in self._tablice()
            if str(t.get("workspace_id")) == str(workspace_id)
        ]
        w_workspace.sort(key=lambda t: str(t["board_id"]))
        return Wynik(
            dane={
                "workspace_id": workspace_id,
                "tablic": len(w_workspace),
                "tablice": w_workspace[:LIMIT_TABLIC_W_ODPOWIEDZI],
            },
            urwane=len(w_workspace) > LIMIT_TABLIC_W_ODPOWIEDZI,
            budzet_zostalo=self.budzet.zostalo,
        )

    def _pytanie_podsumowanie(self) -> Wynik:
        return Wynik(
            dane={
                "meta": self.zestaw._payload("$.meta"),
                "konto": self.zestaw._payload("$.konto.plan"),
                "uzytkownicy": self.zestaw._payload("$.uzytkownicy.podsumowanie"),
                "tablice": self.zestaw._payload("$.tablice.podsumowanie"),
                "automatyzacje": self.zestaw._payload("$.automatyzacje.podsumowanie"),
                "aktywnosc": self.zestaw._payload("$.aktywnosc.podsumowanie"),
            },
            budzet_zostalo=self.budzet.zostalo,
        )

    # ── monday na żywo: kosztuje budżet ──────────────────────────────────

    async def probka_kolumn(self, board_id: str) -> Wynik:
        """Wypełnienie kolumn na próbce itemów — JEDYNY wyjątek od D5.

        **Zwraca wyłącznie LICZBY.** Wartości kolumn są pobierane, żeby
        policzyć wypełnienie, i natychmiast wyrzucane. Do kontekstu modelu nie
        idzie ani jedna wartość, więc granica PII zostaje nietknięta — nie ma
        czego redagować (decyzja człowieka z 2026-08-03).

        Kolumna typu `name` jest wyłączona z oceny: tytuł itemu siedzi
        w `items { name }`, nie w `column_values`, więc wypadałaby zawsze
        jako martwa. Zmierzone na tablicy 5097454411 — 0/15 przy 15 itemach
        z tytułami.
        """
        self.wywolania.append(f"probka_kolumn:{board_id}")
        if self.zestaw.klient is None:
            raise NarzedzieError("brak klienta monday — narzędzie na żywo niedostępne")
        if not self.budzet.wez():
            return self._wyczerpany("probka_kolumn")

        dane = await self.zestaw.klient.query(
            ZAPYTANIE_PROBKI,
            {"ids": [str(board_id)], "limit": LIMIT_PROBKI_ITEMOW},
            etykieta="probka_kolumn",
        )
        tablice = dane.get("boards") or []
        if not tablice:
            return Wynik(
                dane={"board_id": board_id},
                komunikat="tablica niedostępna dla tokena albo nie istnieje",
                budzet_zostalo=self.budzet.zostalo,
            )

        tablica = tablice[0]
        kolumny = tablica.get("columns") or []
        strona = tablica.get("items_page") or {}
        itemy = strona.get("items") or []

        wypelnione: Counter[str] = Counter()
        for item in itemy:
            for wartosc in item.get("column_values") or []:
                if (wartosc.get("text") or "").strip():
                    wypelnione[str(wartosc.get("id"))] += 1
        # `itemy` i `wartosc` wychodzą z zasięgu tutaj. Poniżej są już tylko liczby.

        oceniane = [k for k in kolumny if k.get("type") != TYP_KOLUMNY_TYTUL]
        raport = [
            {
                "type": k.get("type"),
                "title": k.get("title"),
                "wypelnionych": wypelnione.get(str(k.get("id")), 0),
                "martwa": wypelnione.get(str(k.get("id")), 0) == 0,
            }
            for k in oceniane
        ]
        raport.sort(key=lambda k: (k["wypelnionych"], str(k["title"])))

        # Tytuły kolumn pisze KLIENT, a tu przychodzą prosto z API, omijając
        # walidację, którą collector stosuje do snapshotu. Kolumna nazwana
        # adresem e-mail wsadziłaby PII do kontekstu modelu tą ścieżką.
        # Pusta lista wpisów jak w 3.6: bez tabeli mapowania (D6) łapiemy
        # wzorzec e-maila, nie nazwiska.
        waliduj_brak_pii(json.dumps(raport, ensure_ascii=False), [])

        return Wynik(
            dane={
                "board_id": str(tablica.get("id")),
                "rozmiar_probki": len(itemy),
                "kolumn_ocenianych": len(oceniane),
                "kolumn_pominietych": len(kolumny) - len(oceniane),
                "kolumny_martwe": [k["title"] for k in raport if k["martwa"]],
                "kolumny": raport,
                "probka_pelna": strona.get("cursor") is None,
            },
            # Kursor niepusty znaczy, że itemów jest więcej niż w próbce.
            urwane=strona.get("cursor") is not None,
            budzet_zostalo=self.budzet.zostalo,
        )

    async def log_tablicy(self, board_id: str, od: str, do: str) -> Wynik:
        """Szczegół activity logu z datami — dla ENGAGEMENT_DROP i PROCESS_BYPASS.

        Autorzy są PSEUDONIMIZOWANI tą samą solą co w 3.4, więc do kontekstu
        modelu nie wchodzi żaden identyfikator osoby. Pole `data` nie jest
        pobierane wcale — zawiera wartości kolumn i nazwy itemów, czyli treść
        klienta (D5, granica PII).
        """
        self.wywolania.append(f"log_tablicy:{board_id}")
        if self.zestaw.klient is None:
            raise NarzedzieError("brak klienta monday — narzędzie na żywo niedostępne")
        if not self.budzet.wez():
            return self._wyczerpany("log_tablicy")

        dane = await self.zestaw.klient.query(
            ZAPYTANIE_LOGOW,
            {"ids": [str(board_id)], "limit": LIMIT_WPISOW_LOGU, "p": 1, "od": od, "do": do},
            etykieta="log_tablicy",
        )
        tablice = dane.get("boards") or []
        wpisy = (tablice[0].get("activity_logs") or []) if tablice else []

        po_dniu: Counter[str] = Counter()
        po_kliencie: Counter[str] = Counter()
        zdarzenia: list[dict[str, Any]] = []
        for wpis in wpisy:
            kiedy = na_iso(wpis.get("created_at"))
            dzien = (kiedy or "")[:10]
            if dzien:
                po_dniu[dzien] += 1
            autor = wpis.get("user_id")
            pseudonim = (
                policz_hash(self.zestaw.client_id, str(autor), self.zestaw.sol) if autor else None
            )
            if pseudonim:
                po_kliencie[pseudonim] += 1
            zdarzenia.append(
                {
                    "at": kiedy,
                    "event": wpis.get("event"),
                    "klasa": klasa_zdarzenia(str(wpis.get("event") or "")),
                    "autor": pseudonim,
                }
            )

        return Wynik(
            dane={
                "board_id": str(board_id),
                "okno": {"od": od, "do": do},
                "wpisow": len(zdarzenia),
                "po_dniu": dict(sorted(po_dniu.items())),
                "udzial_autorow": dict(sorted(po_kliencie.items())),
                "zdarzenia": zdarzenia,
            },
            urwane=len(wpisy) >= LIMIT_WPISOW_LOGU,
            budzet_zostalo=self.budzet.zostalo,
        )

    def _wyczerpany(self, narzedzie: str) -> Wynik:
        """Wyczerpany budżet to KOMUNIKAT, nie wyjątek (3.10).

        Wyjątek przerwałby pętlę agenta i hipoteza zostałaby bez rozstrzygnięcia.
        Komunikat pozwala mu domknąć ją tym, co już ma — a `hipotezy_odrzucone`
        i tak wymaga uzasadnienia, więc brak danych będzie widoczny.
        """
        logger.info(
            "budżet hipotezy %s/%s wyczerpany (%d wywołań) — %s nie wchodzi do API",
            self.hipoteza.klasa_id,
            self.hipoteza.obiekt_id,
            self.budzet.limit,
            narzedzie,
        )
        return Wynik(
            dane={},
            komunikat=(
                f"budżet {self.budzet.limit} wywołań na tę hipotezę jest wyczerpany — "
                f"domknij ją tym, co masz, albo odrzuć z uzasadnieniem"
            ),
            budzet_zostalo=0,
        )
