"""Reguła dowodu — wspólna dla uwag (nowa ścieżka) i findingów (stary panel).

Wydzielona z `kontrakt.py` (2026-09-25, podział repo). Zakaz twardy „finding
bez pola `dowod` nie przechodzi walidacji" żyje TU, w jednym miejscu: `uwagi`
i `stary_panel.kontrakt` wołają tę samą `sprawdz_dowod`, zamiast kopiować
regułę, która rozjechałaby się przy pierwszej zmianie.
"""

from __future__ import annotations

from typing import Any

from monday_audit.detekcja.rubryka import Klasa

# Nazwy reguł. Idą do `findings_odrzucone.regula`, więc eval z etapu 4 może
# policzyć „ile razy agent zapomniał dowodu" jednym GROUP BY.
REGULA_KLASA_NIEZNANA = "klasa_id nie istnieje w rubryce"
REGULA_KLASA_DO_WERYFIKACJI = "klasa ma status do_weryfikacji"
REGULA_DOWOD_PUSTY = "dowod pusty albo nie jest obiektem"
REGULA_DOWOD_NIEPELNY = "dowod nie pokrywa pol wymaganych przez klase"
REGULA_KWOTA_PRZY_RYZYKU = "kwota_pln podana przy typ_wyceny ryzyko"
REGULA_KWOTA_UJEMNA = "kwota_pln nie jest liczba dodatnia"
REGULA_KWOTA_BEZ_PODSTAWY = "kwota_pln podana bez stawki albo na stawce przeterminowanej"
REGULA_SLOWNIK = "waga, wysilek_naprawy albo pewnosc poza slownikiem"
REGULA_NIEZGODNA_Z_RUBRYKA = "waga albo wysilek_naprawy inne niz w rubryce"
REGULA_BRAK_POLA = "brak pola wymaganego przez kontrakt"
REGULA_PUSTY_TEKST = "opis albo rekomendacja puste"


class KontraktError(RuntimeError):
    """Odpowiedź agenta nie da się w ogóle zwalidować — zła struktura korzenia."""


def _liczba(wartosc: Any) -> float | None:
    if isinstance(wartosc, bool) or not isinstance(wartosc, (int, float)):
        return None
    return float(wartosc)


# Pola opisujące ROZKŁAD aktywności w czasie. Puste są wtedy i tylko wtedy, gdy
# aktywności nie było wcale — a wtedy nie ma czego rozkładać.
#
# Lista jest ZAMKNIĘTA celowo. „Dowolne puste pole, gdy jest `wpisow: 0`" byłoby
# furtką: agent mógłby pominąć `items_count` albo `nazwa` i schować się za ciszą.
POLA_ROZKLADU = frozenset({"kubelki_dni", "po_klasie", "najnowszy_at"})

# (klasa, pole) → pole kolekcji, które wolno oddać jako `{"nie_zmierzone": powód}`.
# Decyzja Kuby 2026-09-24 (zmienia O31): GUEST_SPRAWL bez danych o dostępie
# gości przechodzi z JAWNYM „nie zmierzone". Lista ZAMKNIĘTA, jak POLA_ROZKLADU:
# znacznik dopuszczony wszędzie pozwoliłby modelowi obejść każde pole listowe.
POLA_NIEZMIERZALNE = frozenset({("GUEST_SPRAWL", "tablice_dostepne")})
ZNACZNIK_NIE_ZMIERZONE = "nie_zmierzone"

# Nazwy, pod którymi agent podaje licznik wpisów W OKNIE.
#
# ZMIERZONE na pełnym runie `pelny-etap4-a` (2026-08-19, 80 hipotez): agent użył
# TRZECH różnych nazw dla tej samej liczby — `wpisow_w_oknie`, `wpisow_w_oknie_90d`,
# i (w innych findingach) `wpisow`. Prompt mówi `"wpisow": 0` DOSŁOWNIE, a mimo to
# nazwy się rozjechały. To ta sama lekcja co w kroku 2 etapu 4: **instrukcja
# w prompcie nie jest guardrailem.**
#
# Lista jest ZAMKNIĘTA i celowo NIE zawiera `wpisow_od_utworzenia` ani
# `wpisow_przed_oknem` — one znaczą coś przeciwnego. Tablica z
# `wpisow_od_utworzenia: 40` NIE jest cicha, tylko cicha w oknie; gdyby te nazwy
# tu weszły, `wpisow_przed_oknem: 0` (tablica świeża) usprawiedliwiałoby pusty
# rozkład, czyli wyjątek działałby dokładnie odwrotnie niż ma.
#
# Wzorzec, nie lista podnazw: dopisywanie nazwy po każdym runie to gra w kotka
# i myszkę. Wariant musi zaczynać się od `wpisow_w_oknie` — czyli nazwa MUSI
# jawnie mówić „w oknie", a nie „od utworzenia".
NAZWY_LICZNIKA_WPISOW = ("wpisow", "wpisow_w_oknie")
PREFIKS_LICZNIKA_W_OKNIE = "wpisow_w_oknie"


def _cisza_jest_dowodem(pole: str, dowod: dict[str, Any]) -> bool:
    """Czy puste pole rozkładu jest usprawiedliwione zerową aktywnością.

    ## ZMIERZONA USTERKA, którą to naprawia

    Pierwszy pełny audyt odrzucił na walidacji 9 z 36 findingów — 0,25 wobec progu
    ≤0,15 z etapu 4. **Wszystkie dziewięć to `BOARD_GHOST` z pustymi
    `kubelki_dni`, `po_klasie`, `najnowszy_at`.**

    Przyczyna nie była w prompcie: 84 z 100 tablic snapshotu ma `wpisow: 0`.
    Collector je zbadał (`urwane: False`), tylko w oknie 90 dni **nie było na nich
    żadnej aktywności**. Rubryka wymagała rozkładu, którego fizycznie nie ma —
    a agent słusznie go nie wymyślił. Płaciliśmy więc za 9 sesji, których wynik
    trafiał do kosza (~0,7 USD na run).

    ## Dlaczego to nie osłabia granicy „finding bez dowodu nie istnieje"

    **`wpisow: 0` jest dowodem MOCNIEJSZYM niż rozkład.** „Zero wpisów w 90 dni"
    mówi o martwej tablicy więcej niż jakikolwiek histogram — to absolutna cisza,
    nie wygasanie.

    Dwa warunki trzymają wyjątek wąsko:

    1. pole musi być na zamkniętej liście `POLA_ROZKLADU`;
    2. licznik wpisów musi **być w dowodzie i wynosić 0**. Brak pola NIE jest
       zerem — agent, który je pominie, dostaje odrzucenie jak dotąd. Inaczej
       „nie wiem" udawałoby „nie ma".

    ## Dlaczego licznik ma DWIE dopuszczalne nazwy

    ZMIERZONE na pełnym runie `pelny-etap4-a` (2026-08-19, 80 hipotez): 5 z 6
    odrzuceń walidacyjnych to `BOARD_GHOST`, w którym agent podał
    **`wpisow_w_oknie: 0`** zamiast `wpisow: 0` — i przy okazji dołożył
    `wpisow_przed_oknem: 40` oraz `ostatni_wpis_przed_oknem_at`.

    Jego nazwa jest **precyzyjniejsza od naszej**: „zero wpisów W OKNIE, czterdzieści
    przed oknem" mówi o tablicy więcej niż samo „zero wpisów", bo rozróżnia tablicę
    nigdy nieużywaną od porzuconej. Wąskie dopasowanie do jednej nazwy karało go za
    dokładność, a odrzucone findingi kosztowały ~0,35 USD na run.

    Lista nazw jest ZAMKNIĘTA, tak jak `POLA_ROZKLADU` — „dowolny klucz zawierający
    `wpisow`" byłoby furtką.
    """
    if pole not in POLA_ROZKLADU:
        return False
    for nazwa, wartosc in dowod.items():
        # Dokładna nazwa ALBO wariant zaczynający się od `wpisow_w_oknie`
        # (`wpisow_w_oknie_90d` i podobne). `wpisow_od_utworzenia` i
        # `wpisow_przed_oknem` NIE łapią się — mówią o czymś przeciwnym.
        if nazwa not in NAZWY_LICZNIKA_WPISOW and not nazwa.startswith(PREFIKS_LICZNIKA_W_OKNIE):
            continue
        # `is not None` przed porównaniem: `dowod.get` zwraca `None` dla braku pola,
        # a `None == 0` jest fałszem — ale wolę, żeby warunek był czytelny wprost.
        if wartosc is not None and _liczba(wartosc) == 0:
            return True
    return False


def sprawdz_dowod(dowod: Any, klasa: Klasa) -> tuple[str, str] | None:
    """Zakaz twardy z `CLAUDE.md`: bez dowodu finding nie istnieje.

    **Publiczna i wydzielona, bo korzystają z niej DWIE ścieżki** — stara
    (`_sprawdz_finding`, findingi z rubryką) i nowa (`uwagi.py`, jedna
    kategoria). Kopia tej reguły w drugim miejscu rozjechałaby się przy
    pierwszej zmianie — dokładnie tak, jak rozjechał się literał
    `personal_agent_member` między `osoby` a `pulpit` (O44).

    Zwraca `(regula, powod)` przy odrzuceniu albo `None`, gdy dowód przechodzi.
    """
    if not isinstance(dowod, dict) or not dowod:
        # Pusty obiekt jest tak samo zły jak brak pola: obie sytuacje znaczą
        # „agent nie wskazał faktu".
        return REGULA_DOWOD_PUSTY, "dowod musi być niepustym obiektem"

    wymagane = {p.rstrip("[]") for p in klasa.dowod}
    obecne = {k.rstrip("[]") for k in dowod}
    niepokryte = sorted(wymagane - obecne)
    if niepokryte:
        return (
            REGULA_DOWOD_NIEPELNY,
            f"klasa {klasa.id} wymaga w dowodzie: {', '.join(niepokryte)}",
        )
    # Klucz obecny, ale puste znaczy tyle samo co brak — Z JEDNYM wyjątkiem
    # opisanym w `_cisza_jest_dowodem`.
    puste = sorted(
        k for k in dowod if dowod[k] in (None, "", [], {}) and not _cisza_jest_dowodem(k, dowod)
    )
    if puste:
        return REGULA_DOWOD_NIEPELNY, f"pola dowodu są puste: {', '.join(puste)}"

    # Pole oznaczone w rubryce `[]` jest KOLEKCJĄ. ZMIERZONE na runie
    # `analiza-20260923T122843Z`: model wpisał do `tablice_dostepne[]` zdanie
    # „dla wszystkich 11 nieaktywnych gości lista pusta w danych" — napis jest
    # niepusty, więc przeszedł, i obszedł decyzję Kuby z O31 (bez wiedzy
    # o dostępie gości ta uwaga ma być odrzucana). Opis braku danych nie jest
    # daną. Mapa jest dopuszczalna (`tablice_dostepne` bywa mapą gość → lista
    # tablic), ale mapa samych pustych list to ta sama pustka w innym kształcie.
    zle_ksztalty = sorted(
        pole.rstrip("[]")
        for pole in klasa.dowod
        if pole.endswith("[]")
        and not _cisza_jest_dowodem(pole.rstrip("[]"), dowod)
        and not _niezmierzone(klasa.id, pole.rstrip("[]"), _pole_dowodu(dowod, pole.rstrip("[]")))
        and not _niepusta_kolekcja(_pole_dowodu(dowod, pole.rstrip("[]")))
    )
    if zle_ksztalty:
        return (
            REGULA_DOWOD_NIEPELNY,
            f"pola listowe dowodu nie są niepustą listą: {', '.join(zle_ksztalty)}",
        )
    return None


def _pole_dowodu(dowod: dict[str, Any], nazwa: str) -> Any:
    """Wartość pola niezależnie od tego, czy model zapisał klucz z `[]`, czy bez."""
    return dowod[nazwa] if nazwa in dowod else dowod.get(f"{nazwa}[]")


def _niezmierzone(klasa_id: str, pole: str, wartosc: Any) -> bool:
    """`{"nie_zmierzone": "<powód>"}` na polu z zamkniętej listy — i tylko tam."""
    return (
        (klasa_id, pole) in POLA_NIEZMIERZALNE
        and isinstance(wartosc, dict)
        and set(wartosc) == {ZNACZNIK_NIE_ZMIERZONE}
        and isinstance(wartosc[ZNACZNIK_NIE_ZMIERZONE], str)
        and bool(wartosc[ZNACZNIK_NIE_ZMIERZONE].strip())
    )


def _niepusta_kolekcja(wartosc: Any) -> bool:
    if isinstance(wartosc, list):
        return bool(wartosc)
    if isinstance(wartosc, dict):
        # Znacznik poza zamkniętą listą NIE jest treścią — inaczej model wpisałby
        # `{"nie_zmierzone": "…"}` w dowolne pole listowe i przeszedł.
        if ZNACZNIK_NIE_ZMIERZONE in wartosc:
            return False
        return any(
            _niepusta_kolekcja(v) if isinstance(v, list | dict) else v not in (None, "")
            for v in wartosc.values()
        )
    return False
