"""Ewaluacja etapu 4 — rozbicie kosztu i jakość, jako dane i jako HTML.

## Po co ten moduł istnieje

Pierwszy pełny audyt kosztował **7,09 USD i 62 minuty** (86 hipotez, 27 znalezisk,
2026-08-11). Etap 4 ma to obniżyć, ale przy ~7 USD za każdy pomiar kontrolny
**zgadywanie jest drogie** — trzeba wiedzieć, gdzie te pieniądze idą, zanim się
cokolwiek utnie.

Ten moduł czyta `zuzycie_hipotez` (migracja 010) i odpowiada na trzy pytania:

1. **Na co idzie koszt** — wejście, wyjście, odczyt i zapis cache. Bez rozdzielenia
   cache nie wiadomo nawet, czy caching się opłaca.
2. **Które KLASY są drogie** — 32 hipotezy `BOARD_GHOST` to 60% rachunku czy 20%?
   Od tego zależy, czy warto tam eksperymentować z tańszym modelem.
3. **Ile płacimy za odrzucenia** — hipoteza obalona też kosztuje. Jeśli większość
   kończy się odrzuceniem, płacimy głównie za dowiadywanie się, że czegoś NIE MA.

## Czego tu nie ma

**Oceny, czy finding jest poprawny.** To robi złoty zestaw (`evals/zloty_zestaw/`),
nie ten moduł i nie sędzia LLM — tak mówi specyfikacja etapu 4. Dopóki złotego
zestawu nie ma, sekcja jakości w raporcie mówi wprost „brak miary", zamiast
pokazywać zera: **zero wygląda jak wynik**, a brak miary nie.

## Renderer jest wspólny z raportem i panelem

`raport.srodowisko()` jest jawnie publiczne właśnie do tego. Gdyby ten moduł miał
własne środowisko jinja, autoescaping i polityka `tojson` rozjechałyby się między
dokumentami — a raport ewaluacji niesie nazwy tablic klienta, więc obowiązuje go ta
sama granica co raport (D14).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from monday_audit.raport import KATALOG_SZABLONOW, LOGO, srodowisko, zasob_data_uri
from monday_audit.rubryka import Rubryka

logger = logging.getLogger(__name__)

SZABLON = "ewaluacja.html.j2"

# Progi z `docs/etapy/04-test.md`. Trzymane tutaj, bo raport ma pokazywać, co jest
# PONIŻEJ progu — a próg wpisany w szablon nie da się sprawdzić testem.
PROGI = {
    "trafnosc": 0.7,
    "falszywki": 0.1,
    "odrzucenia": 0.15,
    "powtarzalnosc": 0.8,
}


@dataclass(frozen=True, slots=True)
class KosztKlasy:
    """Jedna klasa rubryki: ile hipotez, ile kosztowały, ile z nich dało finding."""

    klasa_id: str
    hipotez: int
    findingow: int
    koszt_usd: float
    sekund: float
    tokens_in: int
    tokens_cache_read: int
    wywolan_narzedzi: int
    # Z rubryki, nie z bazy: czy agent ma tu co ustalać. `brak` znaczy, że detektor
    # już orzekł — takie klasy są pierwszym kandydatem na tańszy model.
    rola_agenta_brak: bool

    @property
    def na_hipoteze_usd(self) -> float:
        return round(self.koszt_usd / self.hipotez, 4) if self.hipotez else 0.0

    @property
    def na_hipoteze_sekund(self) -> float:
        return round(self.sekund / self.hipotez, 1) if self.hipotez else 0.0

    @property
    def odrzuconych(self) -> int:
        return self.hipotez - self.findingow


@dataclass(frozen=True, slots=True)
class Zuzycie:
    """Rozbicie kosztu jednego runu. Wszystko z bazy, nic z szacunku."""

    run_id: str
    client_id: str
    model: str | None
    rozliczenie: str | None
    hipotez: int
    findingow: int
    # DWIE różne liczby, które łatwo pomylić — i pomyliłem je w pierwszej wersji
    # tego modułu:
    #   `hipotez_odrzuconych` = agent obalił hipotezę („to nie jest problem");
    #   `odrzuconych_walidacja` = finding NIE PRZESZEDŁ kontraktu D8.
    # Etap 4 stawia próg (≤0,15) na DRUGĄ z nich, bo ona mówi o tym, czy prompt
    # trzyma kontrakt. Pierwsza mówi o czym innym: ile anomalii okazało się
    # niegroźnych, co jest normalne i pożądane.
    hipotez_odrzuconych: int
    odrzuconych_walidacja: int
    koszt_usd: float
    sekund: float
    tokens_in: int
    tokens_out: int
    tokens_cache_read: int
    tokens_cache_write: int
    klasy: tuple[KosztKlasy, ...]

    @property
    def ma_rozbicie(self) -> bool:
        """Czy run ma dane per hipoteza.

        Runy sprzed migracji 010 ich nie mają i **nie zgadujemy** — raport musi
        powiedzieć „brak rozbicia", nie podzielić sumy po równo.
        """
        return bool(self.klasy)

    @property
    def wejscie_razem(self) -> int:
        """Całe wejście: świeże plus z cache. Sam `tokens_in` zaniża przy cachingu."""
        return self.tokens_in + self.tokens_cache_read + self.tokens_cache_write

    @property
    def udzial_cache(self) -> float | None:
        """Jaka część wejścia poszła z cache. `None`, gdy nie ma czego dzielić.

        To jest liczba, której nikt dotąd nie widział: jeśli jest niska przy 86
        hipotezach na tym samym inwentarzu, caching nie działa i to jest najtańsze
        możliwe cięcie.
        """
        if not self.wejscie_razem:
            return None
        return round(100 * self.tokens_cache_read / self.wejscie_razem, 1)

    @property
    def odsetek_walidacji(self) -> float | None:
        """Findingi odrzucone przez kontrakt D8 / wszystkie zgłoszone przez agenta.

        **To jest metryka z progiem etapu 4** (≤0,15): wyżej znaczy, że prompt nie
        trzyma kontraktu. Mianownikiem są findingi ZGŁOSZONE, nie hipotezy — agent
        nie zgłasza findingu dla hipotezy, którą sam obalił.
        """
        zgloszone = self.findingow + self.odrzuconych_walidacja
        if not zgloszone:
            return None
        return round(self.odrzuconych_walidacja / zgloszone, 3)

    @property
    def odsetek_obalonych(self) -> float | None:
        """Hipotezy obalone przez agenta / wszystkie zbadane.

        **Nie ma progu i nie jest wadą.** Detektory wzbudzają hipotezy szeroko,
        a agent ma je weryfikować — wysoki odsetek obalonych znaczy, że robi to,
        po co jest. Liczba jest tu, bo obalona hipoteza też kosztuje: to ona mówi,
        jaką część rachunku płacimy za dowiedzenie się, że czegoś NIE MA.
        """
        if not self.hipotez:
            return None
        return round(self.hipotez_odrzuconych / self.hipotez, 3)

    @property
    def usd_na_finding(self) -> float | None:
        """Ile kosztuje jedno znalezisko. Miara, którą rozumie osoba nietechniczna."""
        if not self.findingow:
            return None
        return round(self.koszt_usd / self.findingow, 3)


def zbierz_zuzycie(con: sqlite3.Connection, run_id: str, rubryka: Rubryka) -> Zuzycie:
    """Czyta `runy` i `zuzycie_hipotez`. Nie liczy nic, czego nie ma w bazie."""
    run = con.execute(
        "SELECT run_id, client_id, model, rozliczenie, hipotez_zbadanych, findingow, "
        "hipotez_odrzuconych, odrzuconych_walidacja, koszt_usd, sekund_agenta, "
        "tokens_in, tokens_out, tokens_cache_read, tokens_cache_write "
        "FROM runy WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise ValueError(f"nie ma runu {run_id}")

    klasy: list[KosztKlasy] = []
    for w in con.execute(
        "SELECT klasa_id, COUNT(*) hipotez, SUM(byl_finding) findingow, "
        "SUM(koszt_usd) koszt, SUM(sekund) sekund, SUM(tokens_in) tin, "
        "SUM(tokens_cache_read) tcr, SUM(wywolan_narzedzi) wyw "
        "FROM zuzycie_hipotez WHERE run_id = ? GROUP BY klasa_id "
        "ORDER BY SUM(koszt_usd) DESC",
        (run_id,),
    ):
        kid = str(w["klasa_id"])
        klasa = rubryka.po_id.get(kid)
        klasy.append(
            KosztKlasy(
                klasa_id=kid,
                hipotez=int(w["hipotez"]),
                findingow=int(w["findingow"] or 0),
                koszt_usd=round(float(w["koszt"] or 0.0), 6),
                sekund=round(float(w["sekund"] or 0.0), 1),
                tokens_in=int(w["tin"] or 0),
                tokens_cache_read=int(w["tcr"] or 0),
                wywolan_narzedzi=int(w["wyw"] or 0),
                rola_agenta_brak=(str(klasa.rola_agenta).strip() == "brak" if klasa else False),
            )
        )

    return Zuzycie(
        run_id=str(run["run_id"]),
        client_id=str(run["client_id"]),
        model=str(run["model"]) if run["model"] else None,
        rozliczenie=str(run["rozliczenie"]) if run["rozliczenie"] else None,
        hipotez=int(run["hipotez_zbadanych"] or 0),
        findingow=int(run["findingow"] or 0),
        hipotez_odrzuconych=int(run["hipotez_odrzuconych"] or 0),
        odrzuconych_walidacja=int(run["odrzuconych_walidacja"] or 0),
        koszt_usd=round(float(run["koszt_usd"] or 0.0), 6),
        sekund=round(float(run["sekund_agenta"] or 0.0), 1),
        tokens_in=int(run["tokens_in"] or 0),
        tokens_out=int(run["tokens_out"] or 0),
        tokens_cache_read=int(run["tokens_cache_read"] or 0),
        tokens_cache_write=int(run["tokens_cache_write"] or 0),
        klasy=tuple(klasy),
    )


def udzial_w_rachunku(z: Zuzycie) -> dict[str, float]:
    """Procent rachunku per klasa. Suma musi dać ~100%, inaczej rozbicie gubi koszt.

    Ta kolumna odsiewa kandydatów, którzy wyglądają na oszczędność i nie są nią:
    klasa z 8% udziału nie zwróci kosztu eksperymentu (~7 USD za run kontrolny).
    """
    suma = sum(k.koszt_usd for k in z.klasy)
    if not suma:
        return {}
    return {k.klasa_id: round(100 * k.koszt_usd / suma, 1) for k in z.klasy}


def wyrenderuj(
    z: Zuzycie,
    *,
    poprzedni: Zuzycie | None = None,
    katalog: Path = KATALOG_SZABLONOW,
) -> str:
    """Raport HTML. Reużywa środowiska jinja z `raport` — jedno źródło marki (D14).

    `poprzedni` daje kolumnę porównania: baseline obok eksperymentu. Bez tej pary
    każdy eksperyment wygląda na sukces, bo koszt zawsze da się obniżyć — pytanie
    tylko, czy nie za cenę trafności.
    """
    szablon = srodowisko(katalog).get_template(SZABLON)
    return szablon.render(
        z=z,
        poprzedni=poprzedni,
        udzialy=udzial_w_rachunku(z),
        progi=PROGI,
        logo=zasob_data_uri(LOGO, katalog=katalog / "zasoby"),
    )
