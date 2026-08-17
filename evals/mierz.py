"""Metryki jakości: trafność, fałszywki, przeoczenia, rzeczowość.

## Po co osobny plik, a nie funkcja w `ewaluacja.py`

`ewaluacja.py` mierzy KOSZT — tokeny, cache, USD na finding. To liczby, które
biorą się z rachunku i są prawdziwe niezależnie od tego, czy analiza była dobra.
Ten plik mierzy JAKOŚĆ, czyli zgodność ze złotym zestawem, i wymaga czegoś, czego
rachunek nie ma: wzorca ustalonego przez człowieka. Zmieszanie tych dwóch rzeczy
w jednym module skończyłoby się tym, że raport pokazuje „7 znalezisk za 0,82 USD"
i wygląda jak ocena, choć nie mówi nic o tym, czy te 7 znalezisk jest prawdziwe.

## Progi z `docs/etapy/04-test.md`

* trafność ≥ 0,7
* **fałszywki ≤ 0,1** — ważniejsze od trafności. Raport, który zgłasza
  nieistniejący problem, kosztuje klienta zaufanie, a nas reputację. Przeoczenie
  boli mniej niż wymysł.
* rzeczowość: nie ma progu w specyfikacji, bo to nowa miara. Liczymy ją, żeby
  wiedzieć, czy skracanie odpowiedzi (następny krok optymalizacji) psuje treść.

## Dlaczego dopasowanie faktów, a nie sędzia LLM

Sędzia LLM na tym etapie mierzyłby własny gust i kosztowałby tyle, co sam audyt.
Tu sprawdzamy obecność LICZBY i POLA DOWODU w opisie — rzeczy weryfikowalnej.
Ocena stylu opisu to osobna warstwa z 04-test.md i osobna decyzja.

## Czego ta metryka NIE mierzy

Dopóki sekcja `pominiete` w złotym zestawie jest pusta, „przeoczenia" znaczy
**przeoczenia wobec danych snapshotu**, nie wobec rzeczywistości konta. Agent
może mieć trafność 1,0 i przegapić rzecz, o której snapshot nie wie. To jest
zapisane w raporcie jawnie, żeby nikt nie przeczytał 1,0 jako „bezbłędnie".
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Progi ze specyfikacji etapu 4. `min` znaczy „nie mniej niż", `max` — „nie
# więcej niż". Trzymamy je tu, a nie w `ewaluacja.py`, bo dotyczą jakości.
PROGI_JAKOSCI: dict[str, tuple[str, float]] = {
    "trafnosc": ("min", 0.7),
    "falszywki": ("max", 0.1),
}


def _bez_ogonkow(tekst: str) -> str:
    """`aktywności` → `aktywnosci`. Dopasowanie nie może zależeć od odmiany.

    Agent pisze „bez aktywności", zestaw mówi „bez aktywności" — ale gdyby
    napisał „nieaktywne", chcemy to zobaczyć. Stąd normalizacja, nie równość.

    ## Dlaczego `ł` osobno, a nie przez NFKD

    ZMIERZONE: `NFKD` rozkłada `ąćęńóśźż` na literę plus diakrytyk, więc
    odrzucenie znaków łączących je czyści. **`ł` przechodzi bez zmian** — to
    `U+0142`, litera z KRESKĄ, nie z diakrytykiem, i Unicode nie ma dla niej
    rozkładu. Skutek: fraza „osoba odeszła" nie łapała się na wzorzec
    `osoba odeszla`, a metryka cicho zgłaszała „bez przecieku" dla findingu,
    który spekulował wprost. Miara zawyżająca — najgroźniejszy rodzaj usterki
    w narzędziu, którym ocenia się jakość.
    """
    rozlozone = unicodedata.normalize("NFKD", tekst.lower().replace("ł", "l"))
    return "".join(z for z in rozlozone if not unicodedata.combining(z))


def _liczby_w(tekst: str) -> set[int]:
    """Wszystkie liczby całkowite w tekście, do sprawdzenia „czy podał 285 dni"."""
    return {int(x) for x in re.findall(r"\d+", tekst)}


@dataclass(frozen=True, slots=True)
class Finding:
    """Znalezisko z bazy, zredukowane do tego, co mierzymy."""

    klasa_id: str
    obiekt: str
    opis: str
    rekomendacja: str
    dowod: dict[str, Any]

    @property
    def tekst(self) -> str:
        """Opis plus rekomendacja — fakt może być w jednym albo drugim."""
        return f"{self.opis}\n{self.rekomendacja}"


@dataclass(slots=True)
class OcenaPozycji:
    """Wynik dla jednej pozycji złotego zestawu."""

    klasa_id: str
    obiekt: str
    znalezione: bool
    # Fakty z `musi_zawierac`, których w findingu NIE MA. Puste = rzeczowo.
    brakujace_fakty: list[str] = field(default_factory=list)
    # Zakazy z `nie_powinno_zawierac`, które przeciekły.
    przeciekle: list[str] = field(default_factory=list)

    @property
    def rzeczowa(self) -> bool:
        return self.znalezione and not self.brakujace_fakty and not self.przeciekle


@dataclass(slots=True)
class Wynik:
    """Metryki dla jednego runu wobec jednego złotego zestawu."""

    run_id: str
    zestaw: str
    oczekiwanych: int
    trafionych: int
    falszywek: int
    findingow: int
    pozycje: list[OcenaPozycji] = field(default_factory=list)
    # Findingi zgłoszone w klasie z sekcji `niedopuszczalne`.
    zgloszone_niedopuszczalne: list[str] = field(default_factory=list)
    # Klasy, których zestaw nie opisuje wcale — ani jako oczekiwane, ani jako
    # niedopuszczalne. NIE liczą się do fałszywek: brak wpisu w zestawie znaczy
    # „nie wiem", a nie „błąd". Raportujemy je, bo to dziury w zestawie.
    poza_zestawem: dict[str, int] = field(default_factory=dict)
    pominietych_w_zestawie: int = 0
    # Ile hipotez run DOSTAŁ w każdej klasie. Bez tego trafność kłamie: run
    # z `--na-klase 2` widzi 2 z 7 kont, więc jego maksymalna możliwa trafność
    # to 0,29 i niska liczba mówi o zawężeniu próbki, nie o jakości agenta.
    # ZMIERZONE na `ewal-4klasy`: trafność 0,25 przy 2 trafionych z 2 możliwych.
    hipotez_na_klase: dict[str, int] = field(default_factory=dict)

    @property
    def trafnosc(self) -> float:
        """Ile z oczekiwanych agent znalazł."""
        return self.trafionych / self.oczekiwanych if self.oczekiwanych else 0.0

    @property
    def odsetek_falszywek(self) -> float:
        """Fałszywki wobec WSZYSTKICH findingów runu, nie wobec zestawu.

        Mianownikiem są findingi, bo pytamy „jaka część tego, co agent zgłosił,
        jest wymysłem". Gdyby mianownikiem był zestaw, agent zgłaszający 100
        bzdur przy 8 oczekiwanych miałby 12,5 zamiast 1,0.
        """
        return self.falszywek / self.findingow if self.findingow else 0.0

    @property
    def rzeczowosc(self) -> float:
        """Ile z TRAFIONYCH pozycji niesie wszystkie wymagane fakty i żadnego zakazu.

        Liczona po trafionych, nie po wszystkich — pozycja nieznaleziona nie ma
        jak być nierzeczowa, i wliczanie jej dwa razy karałoby za to samo.
        """
        trafione = [p for p in self.pozycje if p.znalezione]
        if not trafione:
            return 0.0
        return sum(1 for p in trafione if p.rzeczowa) / len(trafione)

    @property
    def osiagalna_trafnosc(self) -> float:
        """Najwyższa trafność, jaką run MÓGŁ osiągnąć przy swojej próbce hipotez.

        Pozycja zestawu, której odpowiadającej hipotezy agent nie dostał, nie jest
        przeoczeniem — jest poza zasięgiem. Bez tej liczby `trafnosc` 0,25 czyta
        się jako „agent przegapił trzy czwarte", gdy prawda może być „zbadał dwa
        konta z siedmiu i trafił oba".

        Gdy `hipotez_na_klase` jest puste (brak danych o próbce), zwracamy 1.0 —
        czyli nie stawiamy żadnego zastrzeżenia, którego nie umiemy uzasadnić.
        """
        if not self.hipotez_na_klase or not self.oczekiwanych:
            return 1.0
        osiagalne = 0
        for klasa in {p.klasa_id for p in self.pozycje}:
            w_klasie = sum(1 for p in self.pozycje if p.klasa_id == klasa)
            osiagalne += min(w_klasie, self.hipotez_na_klase.get(klasa, 0))
        return osiagalne / self.oczekiwanych

    @property
    def trafnosc_w_zasiegu(self) -> float:
        """Trafność liczona tylko po pozycjach, które run miał szansę zbadać.

        To liczba, którą porównuje się z progiem 0,7 przy zawężonej próbce.
        `trafnosc` zostaje jako miara pokrycia CAŁEGO zestawu.
        """
        osiagalne = self.osiagalna_trafnosc * self.oczekiwanych
        return self.trafionych / osiagalne if osiagalne else 0.0

    @property
    def progi_spelnione(self) -> dict[str, bool]:
        wartosci = {"trafnosc": self.trafnosc, "falszywki": self.odsetek_falszywek}
        return {
            nazwa: (wartosci[nazwa] >= prog if kier == "min" else wartosci[nazwa] <= prog)
            for nazwa, (kier, prog) in PROGI_JAKOSCI.items()
        }

    def do_slownika(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "zestaw": self.zestaw,
            "oczekiwanych": self.oczekiwanych,
            "trafionych": self.trafionych,
            "falszywek": self.falszywek,
            "findingow": self.findingow,
            "trafnosc": round(self.trafnosc, 3),
            "trafnosc_w_zasiegu": round(self.trafnosc_w_zasiegu, 3),
            "osiagalna_trafnosc": round(self.osiagalna_trafnosc, 3),
            "hipotez_na_klase": self.hipotez_na_klase,
            "falszywki": round(self.odsetek_falszywek, 3),
            "rzeczowosc": round(self.rzeczowosc, 3),
            "progi_spelnione": self.progi_spelnione,
            "zgloszone_niedopuszczalne": self.zgloszone_niedopuszczalne,
            "poza_zestawem": self.poza_zestawem,
            "pominietych_w_zestawie": self.pominietych_w_zestawie,
            "pozycje": [
                {
                    "klasa_id": p.klasa_id,
                    "obiekt": p.obiekt,
                    "znalezione": p.znalezione,
                    "brakujace_fakty": p.brakujace_fakty,
                    "przeciekle": p.przeciekle,
                }
                for p in self.pozycje
            ],
        }


def wczytaj_zestaw(sciezka: Path) -> dict[str, Any]:
    """YAML złotego zestawu. `pominiete` może być pustą listą — to normalne."""
    dane = yaml.safe_load(sciezka.read_text(encoding="utf-8"))
    if not isinstance(dane, dict):
        raise TypeError(f"{sciezka}: oczekiwano mapy na najwyższym poziomie")
    return dane


def _obiekt_findingu(dowod: dict[str, Any]) -> str:
    """Identyfikator bytu, o którym mówi finding — wyciągnięty z dowodu.

    Nie ma go w kolumnie, bo kontrakt D8 nie wymaga osobnego pola: każda klasa
    nazywa swój byt inaczej, zgodnie z własnymi polami dowodu z rubryki.

    ## `board_ids` jako lista — klasy o RELACJACH między tablicami

    `DUPLICATE_STRUCTURE` opisuje parę tablic, nie tablicę, więc jego dowód niesie
    `board_ids: ["5093364928", "5093573344"]`. Detektor buduje z tego `obiekt_id`
    w formacie `id+id` (identyfikatory posortowane), i tak samo składamy tu — bo
    miernik dopasowuje po parze (klasa, obiekt) i musi trafić w to samo.

    ZMIERZONA USTERKA (2026-08-17, przed runem `ewal-tablice-s7`): pierwsza wersja
    sprawdzała tylko `board_id` w liczbie pojedynczej, więc wszystkim findingom tej
    klasy przypisywała `"konto"` i trafność wyszłaby 0,0 przy dobrym runie. Złapane
    odczytem prawdziwych findingów ze starego runu, nie testem.

    Sortowanie jest konieczne: agent może wypisać identyfikatory w innej kolejności
    niż detektor, a `"a+b"` i `"b+a"` to dla dopasowania dwa różne napisy.
    """
    ids = dowod.get("board_ids")
    if isinstance(ids, list) and ids:
        return "+".join(sorted(str(x) for x in ids))
    return str(dowod.get("user_hash") or dowod.get("board_id") or dowod.get("obiekt") or "konto")


def wczytaj_findingi(con: sqlite3.Connection, run_id: str) -> list[Finding]:
    """Znaleziska runu. `obiekt` wyciągamy z dowodu — nie ma go w kolumnie."""
    wynik: list[Finding] = []
    for wiersz in con.execute(
        "SELECT klasa_id, opis, rekomendacja, dowod FROM findings WHERE run_id = ?",
        (run_id,),
    ):
        try:
            dowod = json.loads(wiersz["dowod"] or "{}")
        except json.JSONDecodeError:
            dowod = {}
        obiekt = _obiekt_findingu(dowod if isinstance(dowod, dict) else {})
        wynik.append(
            Finding(
                klasa_id=wiersz["klasa_id"],
                obiekt=obiekt,
                opis=wiersz["opis"] or "",
                rekomendacja=wiersz["rekomendacja"] or "",
                dowod=dowod if isinstance(dowod, dict) else {},
            )
        )
    return wynik


# Fakty ze zestawu opisane są zdaniem po polsku („liczba dni bez aktywności
# (co najmniej 285)"). Sprawdzamy je, wyciągając z opisu to, co weryfikowalne:
# liczbę w nawiasie i słowa kluczowe. Reszta zdania jest dla człowieka.
_LICZBA_W_NAWIASIE = re.compile(r"\(\s*(?:co najmniej\s*)?(\d+)\s*\)")
# Nawias zawierający „albo"/„lub" — alternatywa wariantów, z których wystarczy
# jeden. Bez tego wzorca „(kind member albo admin)" byłoby koniunkcją.
_ALTERNATYWA = re.compile(r"\(([^)]*\s(?:albo|lub)\s[^)]*)\)")


def _fakt_obecny(fakt: str, finding: Finding) -> bool:
    """Czy finding niesie ten fakt.

    ## Fakt można podać na kilka sposobów i wszystkie są dobre

    ZMIERZONE na runie `ewal-uzytkownicy-s7`: rzeczowość wyszła 0,143, a odczyt
    findingów pokazał, że są rzeczowe. Agent pisał „konto typu member" tam, gdzie
    zestaw mówił „zajmuje płatne miejsce", i „nie pojawia się w logach" tam, gdzie
    zestaw mówił „brak śladu w logach". Fakt był podany — brakowało moich słów.

    Miara ZANIŻAJĄCA jest równie zła jak zawyżająca: kazałaby wydłużać opisy,
    żeby trafić w sformułowania zestawu, a cel jest odwrotny — krótko i rzeczowo.

    Dlatego fakt może być spełniony na trzy sposoby, a nie tylko dosłownie:
    wariantami zapisanymi po `|` w zestawie, słowami z opisu, albo POLEM DOWODU
    o właściwej wartości. Ostatnia droga jest najmocniejsza: `kind: member`
    w dowodzie to fakt sprawdzalny maszynowo, nie kwestia sformułowania.

    ## Warianty rozdzielone `|`

    Zestaw może napisać `że konto zajmuje płatne miejsce|typu member|kind: admin`.
    Wystarczy JEDEN wariant. To pozostaje wymaganiem twardym — nie „coś podobnego",
    tylko jedna z wypisanych możliwości.

    Trzy drogi, w tej kolejności:

    1. **liczba w nawiasie** — „(co najmniej 285)" znaczy: w tekście musi być
       liczba ≥ 285. Nie równość, bo cisza rośnie z każdym dniem i finding
       policzony dzień później poda 286. Równość dawałaby fałszywe alarmy.
    2. **słowa kluczowe** — pozostałe słowa faktu dłuższe niż 3 znaki muszą
       wystąpić w tekście (bez ogonków). Progu „ile procent" nie ma: wszystkie,
       bo fakty są krótkie i celowo napisane słowami, których agent użyje.
    3. **pole dowodu** — gdy fakt nazywa pole (`kind`, `last_activity`),
       wystarczy jego obecność w dowodzie findingu.
    """
    # Warianty rozdzielone `|` — wystarczy jeden. Rozbijamy PRZED czymkolwiek
    # innym, bo każdy wariant może mieć własny nawias z liczbą albo alternatywą.
    if "|" in fakt:
        return any(_fakt_obecny(w.strip(), finding) for w in fakt.split("|") if w.strip())

    tekst = _bez_ogonkow(finding.tekst)
    dopasowanie = _LICZBA_W_NAWIASIE.search(fakt)
    if dopasowanie:
        prog = int(dopasowanie.group(1))
        # Liczba ≥ progu gdziekolwiek w tekście. Bierzemy też dowód, bo agent
        # czasem podaje datę w opisie, a liczbę dni tylko w dowodzie.
        wszystkie = _liczby_w(tekst) | _liczby_w(json.dumps(finding.dowod))
        if not any(x >= prog for x in wszystkie):
            return False
        # Liczba to nie wszystko — „285 dni" i „285 elementów" to nie to samo.
        # Sprawdzamy jeszcze słowa faktu poza nawiasem.
        fakt = _LICZBA_W_NAWIASIE.sub("", fakt)

    # Nawias z „albo" to ALTERNATYWA, nie koniunkcja. „(kind member albo admin)"
    # znaczy: jedno z dwóch. Konto jest albo członkiem, albo adminem — nigdy
    # oboma, więc wymaganie obu naraz nie dałoby się spełnić nigdy.
    #
    # ZMIERZONA USTERKA: pierwsza wersja wymagała koniunkcji i przepuszczała ten
    # fakt tylko wtedy, gdy finding miał w dowodzie klucz `kind` — czyli metryka
    # sprawdzała obecność POLA, choć w komentarzu twierdziła, że sprawdza treść.
    # Test z liczbą 290 przechodził przypadkiem, test z 400 (bez `kind`
    # w dowodzie) obnażył różnicę.
    alternatywy = _ALTERNATYWA.search(fakt)
    if alternatywy:
        warianty = [
            _bez_ogonkow(w) for w in re.split(r"\s+albo\s+|\s+lub\s+", alternatywy.group(1))
        ]
        # Wystarczy JEDEN wariant. Człon wspólny (tu: „kind") bierzemy z pierwszego.
        if not any(w.split()[-1] in tekst for w in warianty if w.split()):
            return False
        fakt = _ALTERNATYWA.sub("", fakt)

    slowa = [s for s in re.findall(r"[\w]+", _bez_ogonkow(fakt)) if len(s) > 3]
    # Słowa łączące, które nie niosą treści — pominięcie ich w findingu nie jest
    # brakiem faktu. Lista jest krótka i celowo nie rośnie: im dłuższa, tym
    # bardziej metryka mierzy tę listę, a nie finding.
    puste = {"liczba", "jakiekolwiek", "albo", "oraz", "tego", "ktory", "ktora", "ktore"}
    istotne = [s for s in slowa if s not in puste]
    if not istotne:
        return True
    if all(s in tekst for s in istotne):
        return True

    # Ostatnia droga: fakt nazywa pole dowodu — po kluczu ALBO po jego wartości.
    #
    # Wartość liczy się na równi z tekstem, bo `{"kind": "member"}` w dowodzie
    # jest twardszym potwierdzeniem faktu „konto zajmuje płatne miejsce" niż
    # jakiekolwiek zdanie: to pole ze snapshotu, nie sformułowanie agenta.
    #
    # Zapis `pole: wartosc` w zestawie („kind: member") wymaga OBU — samo
    # `{"kind": "guest"}` nie potwierdza faktu o płatnym miejscu.
    if ":" in fakt:
        pole, _, oczekiwana = fakt.partition(":")
        klucz = _bez_ogonkow(pole).strip()
        chciana = _bez_ogonkow(oczekiwana).strip()
        for k, v in finding.dowod.items():
            if _bez_ogonkow(k).strip() == klucz and chciana in _bez_ogonkow(str(v)):
                return True
        return False

    klucze = {_bez_ogonkow(k) for k in finding.dowod}
    wartosci = " ".join(_bez_ogonkow(str(v)) for v in finding.dowod.values())
    return any(s in klucze or s in wartosci for s in istotne)


# Zakazy z `nie_powinno_zawierac` są opisami, nie wzorcami — „spekulacja
# o przyczynie nieobecności" nie da się dopasować dosłownie. Mapujemy je na
# konkretne, weryfikowalne sygnały. Zakaz bez wpisu tutaj jest RAPORTOWANY jako
# niesprawdzony, nie cicho pomijany — inaczej metryka kłamałaby przez pominięcie.
SYGNALY_ZAKAZOW: dict[str, tuple[str, ...]] = {
    "spekulacja": (
        "prawdopodobnie odesz",
        "zapewne odesz",
        "osoba odeszla",
        "przebywa na urlopie",
        "jest na urlopie",
        "na zwolnieniu",
    ),
    "imie": (),  # obsługiwane osobno — wzorcem, nie listą fraz
    "zajmuja platne": ("goscie zajmuja platne", "gosc zajmuje platne"),
    "masowego usuniecia": ("usunac wszystkich gosci", "usunac wszystkie konta gosci"),
    "nadplaty": (),  # obsługiwane osobno, przez `czego_nie_wolno`
}

# E-mail i „Imię Nazwisko" — granica PII. To jedyny zakaz sprawdzany wzorcem,
# bo jedyny, który da się tak sprawdzić bez zgadywania.
_MAIL = re.compile(r"[\w.+-]+@[\w-]+\.\w{2,}")


def _zakaz_przeciekl(zakaz: str, finding: Finding) -> bool | None:
    """`True` = przeciekło, `False` = czysto, `None` = nie umiemy sprawdzić.

    `None` jest istotne: zakaz, którego nie sprawdzamy, nie może być liczony
    jako spełniony. Raport pokazuje je osobno jako „niesprawdzone".
    """
    tekst = _bez_ogonkow(finding.tekst)
    zn = _bez_ogonkow(zakaz)

    if "imie" in zn or "nazwisko" in zn or "mail" in zn:
        return bool(_MAIL.search(finding.tekst))

    for klucz, frazy in SYGNALY_ZAKAZOW.items():
        if klucz in zn and frazy:
            return any(f in tekst for f in frazy)
    return None


def hipotez_na_klase(con: sqlite3.Connection, run_id: str) -> dict[str, int]:
    """Ile hipotez run DOSTAŁ w każdej klasie — z `zuzycie_hipotez`.

    Ta tabela istnieje od migracji 010, więc runy starsze niż 2026-08-11 nie mają
    w niej wierszy. Pusty słownik znaczy „nie wiem, ile było próbki", a nie
    „zero hipotez" — i `osiagalna_trafnosc` traktuje go właśnie tak.
    """
    return {
        w["klasa_id"]: w["n"]
        for w in con.execute(
            "SELECT klasa_id, COUNT(*) AS n FROM zuzycie_hipotez WHERE run_id = ? GROUP BY 1",
            (run_id,),
        )
    }


def ocen(
    findingi: list[Finding],
    zestaw: dict[str, Any],
    *,
    run_id: str,
    nazwa_zestawu: str,
    na_klase: dict[str, int] | None = None,
) -> Wynik:
    """Zestawia findingi runu ze złotym zestawem.

    Dopasowanie po parze (`klasa_id`, `obiekt`). Dla klas o całym koncie
    (`GUEST_SPRAWL`, `PLAN_MISMATCH`) obiektem jest `konto` po obu stronach.
    """
    oczekiwane = list(zestaw.get("oczekiwane") or [])
    niedopuszczalne = list(zestaw.get("niedopuszczalne") or [])
    pominiete = list(zestaw.get("pominiete") or [])

    po_kluczu: dict[tuple[str, str], Finding] = {(f.klasa_id, f.obiekt): f for f in findingi}
    uzyte: set[tuple[str, str]] = set()
    pozycje: list[OcenaPozycji] = []

    for poz in oczekiwane:
        klucz = (str(poz["klasa_id"]), str(poz["obiekt"]))
        finding = po_kluczu.get(klucz)
        if finding is None:
            pozycje.append(OcenaPozycji(klasa_id=klucz[0], obiekt=klucz[1], znalezione=False))
            continue
        uzyte.add(klucz)
        brakujace = [f for f in (poz.get("musi_zawierac") or []) if not _fakt_obecny(f, finding)]
        przeciekle = [
            z for z in (poz.get("nie_powinno_zawierac") or []) if _zakaz_przeciekl(z, finding)
        ]
        pozycje.append(
            OcenaPozycji(
                klasa_id=klucz[0],
                obiekt=klucz[1],
                znalezione=True,
                brakujace_fakty=brakujace,
                przeciekle=przeciekle,
            )
        )

    # Fałszywki: findingi na OBIEKCIE, którego zestaw zakazuje — nie w całej klasie.
    #
    # ZMIERZONA USTERKA (2026-08-17): pierwsza wersja liczyła po klasie, więc przy
    # zestawie zakazującym 4 tablic `BOARD_OVERCOMPLEX` uznała za fałszywki
    # WSZYSTKIE 12 findingów tej klasy z runu z 11 sierpnia — w tym poprawne
    # findingi na zupełnie innych tablicach. Fałszywki wyszły 0,444 zamiast 0,083.
    # Miara ZAWYŻAJĄCA na metryce, która ma pierwszeństwo nad trafnością.
    #
    # Zakaz całej klasy nadal da się wyrazić: pozycja z `obiekt: "*"`. Wtedy jest
    # to decyzja jawna („ta klasa nie ma prawa wystąpić na tym koncie"), a nie
    # skutek uboczny wskazania jednego obiektu.
    zakazane_klasy = {str(p["klasa_id"]) for p in niedopuszczalne}
    zakazane_pary = {(str(p["klasa_id"]), str(p["obiekt"])) for p in niedopuszczalne}
    klasy_calkiem = {k for k, o in zakazane_pary if o == "*"}
    falszywe = [
        f
        for f in findingi
        if (f.klasa_id, f.obiekt) in zakazane_pary or f.klasa_id in klasy_calkiem
    ]

    # Klasy, o których zestaw milczy. Nie fałszywki — dziury w zestawie.
    opisane = {str(p["klasa_id"]) for p in oczekiwane} | zakazane_klasy
    poza: dict[str, int] = {}
    for f in findingi:
        if f.klasa_id not in opisane:
            poza[f.klasa_id] = poza.get(f.klasa_id, 0) + 1

    return Wynik(
        run_id=run_id,
        zestaw=nazwa_zestawu,
        oczekiwanych=len(oczekiwane),
        trafionych=sum(1 for p in pozycje if p.znalezione),
        falszywek=len(falszywe),
        findingow=len(findingi),
        pozycje=pozycje,
        # Klasa I obiekt — „BOARD_OVERCOMPLEX" bez identyfikatora nie mówi, co
        # poprawić, gdy zestaw zakazuje czterech tablic z szesnastu.
        zgloszone_niedopuszczalne=sorted({f"{f.klasa_id} {f.obiekt}" for f in falszywe}),
        poza_zestawem=poza,
        pominietych_w_zestawie=len(pominiete),
        hipotez_na_klase=dict(na_klase or {}),
    )


def zmierz(baza: Path, run_id: str, zestaw: Path) -> Wynik:
    """Wejście dla CLI i dla raportu HTML."""
    from monday_audit.baza import polacz

    con = polacz(baza)
    try:
        return ocen(
            wczytaj_findingi(con, run_id),
            wczytaj_zestaw(zestaw),
            run_id=run_id,
            nazwa_zestawu=zestaw.name,
            na_klase=hipotez_na_klase(con, run_id),
        )
    finally:
        con.close()


def _main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Metryki jakości wobec złotego zestawu")
    p.add_argument("--baza", type=Path, default=Path("monday_audit.db"))
    p.add_argument("--run", required=True, help="run_id agenta")
    p.add_argument("--zestaw", type=Path, required=True)
    p.add_argument("--json", action="store_true", help="wypisz surowy JSON")
    a = p.parse_args()

    wynik = zmierz(a.baza, a.run, a.zestaw)
    if a.json:
        print(json.dumps(wynik.do_slownika(), ensure_ascii=False, indent=2))
        return 0

    print(f"\n  run {wynik.run_id} wobec {wynik.zestaw}\n")
    print(f"  findingów w runie:  {wynik.findingow}")
    print(f"  oczekiwanych:       {wynik.oczekiwanych}")
    print(f"  trafionych:         {wynik.trafionych}")
    for nazwa, (kier, prog) in PROGI_JAKOSCI.items():
        wart = wynik.trafnosc if nazwa == "trafnosc" else wynik.odsetek_falszywek
        ok = "OK " if wynik.progi_spelnione[nazwa] else "PONIŻEJ PROGU"
        znak = "≥" if kier == "min" else "≤"
        print(f"  {nazwa:<18} {wart:.3f}   (próg {znak} {prog})  {ok}")
    print(f"  rzeczowość:         {wynik.rzeczowosc:.3f}   (bez progu — nowa miara)")

    if wynik.osiagalna_trafnosc < 1.0:
        # Bez tego zastrzeżenia liczba wyżej kłamie: run z zawężoną próbką nie
        # miał jak zobaczyć części zestawu, a wygląda, jakby ją przegapił.
        maks = wynik.osiagalna_trafnosc
        print()
        print("  UWAGA: próbka zawężona — run dostał hipotezy tylko dla części zestawu.")
        print(f"  maksymalna osiągalna trafność:  {maks:.3f}")
        w_zasiegu = wynik.trafnosc_w_zasiegu
        print(f"  trafność W ZASIĘGU próbki:      {w_zasiegu:.3f}  ← ta idzie do progu")
        widoczne = ", ".join(f"{k}×{v}" for k, v in sorted(wynik.hipotez_na_klase.items()))
        print(f"  hipotez w runie: {widoczne}")

    if wynik.zgloszone_niedopuszczalne:
        print(f"\n  FAŁSZYWKI w klasach: {', '.join(wynik.zgloszone_niedopuszczalne)}")
    if wynik.poza_zestawem:
        poza = ", ".join(f"{k}×{v}" for k, v in sorted(wynik.poza_zestawem.items()))
        print(f"\n  poza zestawem (nie liczone do metryk): {poza}")
    if not wynik.pominietych_w_zestawie:
        print("\n  UWAGA: sekcja `pominiete` jest pusta — trafność mierzy zgodność")
        print("  z DANYMI snapshotu, nie z rzeczywistością konta.")

    braki = [p for p in wynik.pozycje if p.znalezione and (p.brakujace_fakty or p.przeciekle)]
    if braki:
        print("\n  rzeczowość — czego brakuje:")
        for poz in braki:
            print(f"    {poz.klasa_id} {poz.obiekt}")
            for f in poz.brakujace_fakty:
                print(f"      brak: {f}")
            for z in poz.przeciekle:
                print(f"      PRZECIEK: {z}")

    nieznalezione = [p for p in wynik.pozycje if not p.znalezione]
    if nieznalezione:
        print("\n  nieznalezione:")
        for poz in nieznalezione:
            print(f"    {poz.klasa_id} {poz.obiekt}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
