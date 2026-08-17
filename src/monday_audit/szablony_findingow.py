"""Findingi budowane z faktów detektora, bez wywołania modelu.

## Kiedy model jest zbędny

Rubryka ma pole `rola_agenta`. Gdy jest równe `brak`, znaczy to, że detektor już
orzekł i agent nie ma czego ustalać — `ZOMBIE_ACCOUNT` jest takim przypadkiem:
warunek to „płatne miejsce, cisza dłuższa niż okno, brak w logach", wszystko
sprawdzone SQL-em, wszystkie pola dowodu policzone.

**ZMIERZONE (2026-08-17, run `ewal-uzytkownicy-s7`):** wszystkie 7 findingów tej
klasy miało `dowod` **identyczny** z faktami detektora dla wszystkich 6 pól
wymaganych przez rubrykę. Model przepisywał JSON i pisał do niego zdanie.
Kosztowało to 0,357 USD i 1546 tokenów wyjścia na hipotezę.

`waga`, `wysilek_naprawy` i `typ_wyceny` też nie wymagają modelu — walidacja
`kontrakt.py` wymaga, żeby były **równe** wartościom z rubryki, więc jedyną
poprawną odpowiedzią jest przepisanie. Szablon przepisuje je taniej.

## Dlaczego model NIE zostaje przy rekomendacji

Rozważaliśmy wariant „szablon dla opisu, model dla rekomendacji". Odrzucony po
przeczytaniu prawdziwych rekomendacji z bazy. Dwie z nich to ten sam sens z inną
zmienną z snapshotu, ale jedna radzi **przenieść konto na `guest`** — czyli tworzy
problem, który ta sama rubryka audytuje jako `GUEST_SPRAWL`, a złoty zestaw ma na
to jawny zakaz („goscie zajmuja platne"). Model nie ma jak wiedzieć, że inna klasa
uznaje to za wadę; człowiek pisząc szablon raz — ma.

## Czego ten moduł NIE robi

**Nie liczy kwot.** `kwota_pln` zostaje `None` zawsze. Wzór `ZOMBIE_ACCOUNT`
(`liczba_kont * koszt_licencji_mies * 12`) jest na CAŁE konto, a findingi są per
konto — plus stawka bywa nieznana. Reguła „nie licz, gdy nie masz stawki" dotyczy
kodu tym mocniej niż modelu.

**Nie obsługuje klas, których nie zna.** `SZABLONY` to jawny słownik. Klasa bez
wpisu idzie do modelu jak dotąd — brak wpisu nie może cicho wyprodukować findingu
o niższej jakości.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from monday_audit.detektory import Hipoteza
from monday_audit.rubryka import Klasa

# Ile dni ciszy uznajemy za pewne. Poniżej tego finding nadal powstaje, ale
# z `pewnosc: srednia` — cisza krótsza niż kwartał może być urlopem albo
# zwolnieniem, a tego z danych nie odróżnimy.
DNI_PEWNOSCI = 120


def _dni_ciszy(fakty: dict[str, Any], teraz: datetime | None = None) -> int | None:
    """Dni od ostatniej aktywności. `None`, gdy `last_activity` nie ma.

    Liczone do DNIA AUDYTU, nie do początku okna — to liczba, którą czyta człowiek
    („konto milczy 288 dni"), a nie parametr techniczny. Rośnie z czasem, dlatego
    złoty zestaw wymaga „co najmniej N", nie równości.
    """
    surowy = fakty.get("last_activity")
    if not surowy:
        return None
    try:
        kiedy = datetime.fromisoformat(str(surowy).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ((teraz or datetime.now(tz=UTC)) - kiedy).days


def _opis_zombie(fakty: dict[str, Any], dni: int | None) -> str:
    """Opis: co, gdzie, dlaczego — z faktów, bez ani jednego zdania od modelu.

    Musi nieść cztery rzeczy, których wymaga złoty zestaw: liczbę dni ciszy, rodzaj
    konta (**tu wchodzi ADMIN** — martwe konto z pełnymi uprawnieniami to inne
    ryzyko niż martwy członek), brak śladu w logach i plan.

    Czego tu NIE MA i nie będzie: domysłu, dlaczego osoba zniknęła. Złoty zestaw
    zakazuje spekulacji („odejście, urlop, L4") i to jest zakaz, którego szablon
    nie ma jak złamać.
    """
    kind = str(fakty.get("kind") or "?")
    rola = "administratora" if kind == "admin" else "członka zespołu"
    czas = f"{dni} dni" if dni is not None else "cały okres badania"
    plan = str(fakty.get("plan_tier") or "?")

    zdania = [
        f"Konto {rola} (kind: {kind}) zajmuje płatne miejsce w planie {plan}"
        f" i nie wykazuje aktywności od {czas}.",
        f"Ostatnia aktywność: {fakty.get('last_activity') or 'brak zapisu'}."
        if fakty.get("last_activity")
        else "Konto nie ma zapisanej daty ostatniej aktywności.",
        "Nie pojawia się też jako autor w żadnym wpisie logu aktywności"
        " z badanego okna (obecnosc_w_logach: false) — to drugi, niezależny dowód.",
    ]
    if kind == "admin":
        zdania.append(
            "Konto ma uprawnienia administratora, więc martwe konto oznacza"
            " tu nie tylko koszt licencji, ale i nienadzorowany dostęp."
        )
    return " ".join(zdania)


def _rekomendacja_zombie(fakty: dict[str, Any]) -> str:
    """Dwa warianty per `kind`. Żaden nie proponuje przeniesienia na `guest`.

    Powód zapisany w docstringu modułu: model to zaproponował, a `GUEST_SPRAWL`
    audytuje to jako wadę. Szablon nie ma jak popełnić tego błędu.

    Oba warianty żądają POTWIERDZENIA przed dezaktywacją — złoty zestaw zakazuje
    „rekomendacji usunięcia bez zastrzeżenia, że trzeba to potwierdzić", i słusznie:
    z danych nie wynika, czy osoba odeszła z firmy, czy jest na długim zwolnieniu.
    """
    if str(fakty.get("kind")) == "admin":
        return (
            "Potwierdzić u właściciela konta, czy ta osoba nadal pełni rolę"
            " administratora. Jeśli nie — najpierw odebrać uprawnienia"
            " administratora, potem dezaktywować konto i zwolnić płatne miejsce."
            " Kolejność ma znaczenie: nienadzorowany dostęp administracyjny"
            " jest tu większym ryzykiem niż sam koszt licencji."
        )
    return (
        "Potwierdzić u właściciela konta, czy ta osoba nadal pracuje z monday.com."
        " Jeśli nie — dezaktywować konto, żeby zwolnić płatne miejsce."
        " Nie usuwać przed potwierdzeniem: z danych nie wynika, czy to odejście"
        " z firmy, czy długa nieobecność."
    )


def _pewnosc_zombie(fakty: dict[str, Any], dni: int | None) -> str:
    """Pewność wyliczona, nie zadeklarowana.

    `wysoka` wymaga DWÓCH niezależnych dowodów i ciszy dłuższej niż kwartał:
    `last_activity` starsze niż okno ORAZ brak w logach. Gdy `last_activity` nie ma
    wcale, zostaje jeden dowód — `srednia`, bo „nie wiem" nie jest dowodem ciszy.

    Krótsza cisza też schodzi na `srednia`: 109 dni może być urlopem macierzyńskim
    albo zwolnieniem, a tego z danych nie odróżnimy.
    """
    dwa_dowody = bool(fakty.get("last_activity")) and fakty.get("obecnosc_w_logach") is False
    if dwa_dowody and dni is not None and dni >= DNI_PEWNOSCI:
        return "wysoka"
    return "srednia"


def zombie_z_szablonu(
    hipoteza: Hipoteza, klasa: Klasa, teraz: datetime | None = None
) -> dict[str, Any]:
    """Finding `ZOMBIE_ACCOUNT` w kształcie, który przechodzi kontrakt D8.

    `dowod` bierze DOKŁADNIE pola z `klasa.dowod` — czyli to samo, co model dziś
    kopiuje z faktów. Walidacja `kontrakt.py` sprawdza pokrycie tych pól i to, czy
    żadne nie jest puste; skoro detektor je policzył, przechodzi z definicji.

    `podstawa` i `okno_od` z faktów NIE wchodzą do dowodu — rubryka ich nie
    wymaga, a nadmiarowe pola w dowodzie zaśmiecają raport klienta.
    """
    fakty = hipoteza.fakty
    dni = _dni_ciszy(fakty, teraz)
    return {
        "klasa_id": klasa.id,
        "waga": klasa.waga,
        "wysilek_naprawy": klasa.wysilek_naprawy,
        "typ_wyceny": klasa.typ_wyceny,
        # Zawsze `None`: wzór jest na całe konto, finding jest per konto, a stawki
        # w runie ewaluacyjnym nie ma. Klucz MUSI być obecny (kontrakt.py).
        "kwota_pln": None,
        "opis": _opis_zombie(fakty, dni),
        "rekomendacja": _rekomendacja_zombie(fakty),
        "dowod": {pole: fakty.get(pole) for pole in klasa.dowod},
        "pewnosc": _pewnosc_zombie(fakty, dni),
    }


# Jawny słownik, nie automat po `rola_agenta == "brak"`. Klasa może mieć `brak`
# i nadal wymagać zdania, którego nie umiemy napisać szablonem — wpis tutaj jest
# świadomą decyzją, że umiemy.
SZABLONY = {"ZOMBIE_ACCOUNT": zombie_z_szablonu}


def z_szablonu(
    hipoteza: Hipoteza, klasa: Klasa, teraz: datetime | None = None
) -> dict[str, Any] | None:
    """Finding bez modelu, albo `None` gdy dla tej klasy nie ma szablonu."""
    budowniczy = SZABLONY.get(hipoteza.klasa_id)
    return budowniczy(hipoteza, klasa, teraz) if budowniczy else None
