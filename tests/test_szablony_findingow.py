"""Testy findingów budowanych bez modelu.

Szablon zastępuje model, więc musi trzymać ten sam kontrakt i tę samą jakość.
Testy sprawdzają jedno i drugie — i robią to za zero USD, bo szablon jest
deterministyczny. To jest cała zaleta tej ścieżki: regresję łapie pytest, nie run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from monday_audit.detektory import Hipoteza
from monday_audit.kontrakt import waliduj
from monday_audit.rubryka import wczytaj_rubryke
from monday_audit.szablony_findingow import DNI_PEWNOSCI, z_szablonu, zombie_z_szablonu

RUBRYKA = wczytaj_rubryke()
KLASA = RUBRYKA.po_id["ZOMBIE_ACCOUNT"]
# Punkt odniesienia dla dni ciszy — bez niego test starzeje się z każdym dniem.
TERAZ = datetime(2026, 8, 17, tzinfo=UTC)


def hipoteza(**nadpisz: Any) -> Hipoteza:
    fakty: dict[str, Any] = {
        "user_hash": "abc123def456",
        "kind": "member",
        "status": "ACTIVE",
        "last_activity": "2026-01-01T10:00:00Z",  # 227 PEŁNYCH dni przed TERAZ
        "obecnosc_w_logach": False,
        "plan_tier": "enterprise",
        "podstawa": "last_activity starsze niż okno",
        "okno_od": "2026-05-16T11:31:48+00:00",
    }
    fakty.update(nadpisz)
    return Hipoteza(
        klasa_id="ZOMBIE_ACCOUNT",
        obiekt_id=str(fakty["user_hash"]),
        fakty=fakty,
        budzet_wywolan=0,
    )


def test_finding_z_szablonu_przechodzi_kontrakt() -> None:
    """Kontrakt D8 nie odróżnia szablonu od modelu — i nie ma odróżniać.

    Gdyby szablon wymagał złagodzenia walidacji, byłby obejściem, nie zamiennikiem.
    """
    f = zombie_z_szablonu(hipoteza(), KLASA, TERAZ)
    wynik = waliduj({"findings": [f], "hipotezy_odrzucone": []}, RUBRYKA, {})

    assert len(wynik.przyjete) == 1
    assert wynik.odrzucone == []


def test_dowod_pokrywa_dokladnie_pola_rubryki() -> None:
    """Ani mniej (walidacja odrzuci), ani więcej (śmieci w raporcie klienta).

    `podstawa` i `okno_od` są w faktach detektora, ale rubryka ich nie wymaga —
    nie mają prawa wejść do dowodu.
    """
    f = zombie_z_szablonu(hipoteza(), KLASA, TERAZ)

    assert set(f["dowod"]) == set(KLASA.dowod)
    assert "podstawa" not in f["dowod"]
    assert "okno_od" not in f["dowod"]


def test_admin_jest_nazwany_wprost() -> None:
    """Martwe konto ADMINA to inne ryzyko niż martwy członek zespołu.

    Złoty zestaw wymaga tego faktu przy pozycji `d7e61e3a53234ee5`
    (`"administrator|typu admin|kind: admin"`). Gdyby szablon go zgubił,
    rzeczowość spadłaby — a ten test łapie to bez uruchamiania miernika.
    """
    f = zombie_z_szablonu(hipoteza(kind="admin"), KLASA, TERAZ)
    tekst = f["opis"] + f["rekomendacja"]

    assert "admin" in tekst
    assert "uprawnienia administratora" in tekst
    assert f["dowod"]["kind"] == "admin"


def test_rekomendacja_nigdy_nie_proponuje_guest() -> None:
    """Model to zaproponował, a `GUEST_SPRAWL` audytuje to jako wadę.

    ZMIERZONE: jedna z prawdziwych rekomendacji z bazy radziła „przenieść na rolę
    bezpłatną (guest/view-only)" — czyli tworzyć problem, który ta sama rubryka
    zgłasza w innej klasie, a złoty zestaw ma na to jawny zakaz.
    """
    for kind in ("member", "admin"):
        r = zombie_z_szablonu(hipoteza(kind=kind), KLASA, TERAZ)["rekomendacja"]
        assert "guest" not in r.lower()
        assert "view-only" not in r.lower()
        assert "view_only" not in r.lower()


def test_rekomendacja_zawsze_zada_potwierdzenia() -> None:
    """Złoty zestaw zakazuje „usunięcia bez zastrzeżenia, że trzeba potwierdzić".

    Z danych nie wynika, czy to odejście z firmy, czy długie zwolnienie — i to
    jest granica, której szablon nie ma prawa przekroczyć.
    """
    for kind in ("member", "admin"):
        r = zombie_z_szablonu(hipoteza(kind=kind), KLASA, TERAZ)["rekomendacja"]
        assert "otwierdzi" in r  # „Potwierdzić" / „potwierdzeniem"


def test_opis_nie_spekuluje_o_przyczynie() -> None:
    """Zakaz z zestawu: żadnego „odeszła z firmy", „urlop", „L4".

    Szablon nie ma jak tego napisać, ale test pilnuje, żeby nikt tego nie dopisał
    przy okazji „ulepszania" opisu.
    """
    for kind in ("member", "admin"):
        f = zombie_z_szablonu(hipoteza(kind=kind), KLASA, TERAZ)
        tekst = (f["opis"] + f["rekomendacja"]).lower()
        for zakazane in ("odeszła", "odeszla", "zwolnieni", "l4", "prawdopodobnie"):
            assert zakazane not in tekst, f"spekulacja w opisie: {zakazane}"


def test_liczba_dni_ciszy_jest_w_opisie() -> None:
    """Bez liczby dni finding nie odpowiada na „co i od kiedy"."""
    f = zombie_z_szablonu(hipoteza(), KLASA, TERAZ)

    # 227, nie 228: `timedelta.days` liczy pełne dni, a od 1 stycznia 10:00
    # do 17 sierpnia 00:00 brakuje dziesięciu godzin do 228.
    assert "227 dni" in f["opis"]


def test_pewnosc_wysoka_wymaga_dwoch_dowodow_i_kwartalu() -> None:
    """`wysoka` tylko przy `last_activity` starszym niż okno ORAZ braku w logach."""
    assert zombie_z_szablonu(hipoteza(), KLASA, TERAZ)["pewnosc"] == "wysoka"


def test_krotka_cisza_schodzi_na_srednia() -> None:
    """Sto dni może być urlopem macierzyńskim — tego z danych nie odróżnimy."""
    swieze = datetime(2026, 6, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    f = zombie_z_szablonu(hipoteza(last_activity=swieze), KLASA, TERAZ)

    assert f["pewnosc"] == "srednia"


def test_brak_last_activity_to_jeden_dowod_wiec_srednia() -> None:
    """„Nie wiem, kiedy był aktywny" nie jest dowodem ciszy.

    Detektor wpuszcza takie konta (`last_activity IS NULL` + brak w logach), ale
    zostaje wtedy JEDEN dowód, nie dwa.
    """
    f = zombie_z_szablonu(hipoteza(last_activity=None), KLASA, TERAZ)

    assert f["pewnosc"] == "srednia"
    assert "nie ma zapisanej daty" in f["opis"]


def test_kwota_pln_jest_obecna_i_pusta() -> None:
    """Klucz MUSI być, wartość MUSI być `None`.

    Kontrakt rozróżnia brak klucza od `null`, a wzór tej klasy jest na całe konto,
    nie na jedno miejsce — więc szablon nie ma czego policzyć.
    """
    f = zombie_z_szablonu(hipoteza(), KLASA, TERAZ)

    assert "kwota_pln" in f
    assert f["kwota_pln"] is None


def test_waga_i_typ_wyceny_przepisane_z_rubryki() -> None:
    """Walidacja wymaga równości z rubryką, więc jedyną poprawną drogą jest kopia."""
    f = zombie_z_szablonu(hipoteza(), KLASA, TERAZ)

    assert f["waga"] == KLASA.waga
    assert f["wysilek_naprawy"] == KLASA.wysilek_naprawy
    assert f["typ_wyceny"] == KLASA.typ_wyceny


def test_klasa_bez_szablonu_wraca_none() -> None:
    """Brak wpisu w `SZABLONY` NIE MOŻE cicho wyprodukować findingu.

    Klasa nieznana szablonowi idzie do modelu — `None` jest sygnałem „nie umiem",
    a nie wartością domyślną.
    """
    obca = Hipoteza(
        klasa_id="BOARD_GHOST",
        obiekt_id="555",
        fakty={"board_id": "555"},
        budzet_wywolan=4,
    )

    assert z_szablonu(obca, RUBRYKA.po_id["BOARD_GHOST"]) is None


def test_prog_pewnosci_jest_stala_a_nie_liczba_w_kodzie() -> None:
    """Próg ma być jawny, żeby dał się zmienić w jednym miejscu i zmierzyć.

    120 dni to kwartał: krócej niż to może być urlopem macierzyńskim albo długim
    zwolnieniem, a tego z danych nie odróżnimy.
    """
    assert DNI_PEWNOSCI == 120
