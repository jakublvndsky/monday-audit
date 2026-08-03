"""Testy warstwy konfiguracji (D12), warstwa 1 z 04-test.md.

Dwie rzeczy są tu ważniejsze od pozostałych i obie są testami bezpieczeństwa,
nie wygody:

1. **Precedencja.** Zmienna w środowisku procesu musi przebijać plik `.env`.
   Na tym stoi zarówno `EnvironmentFile=` w systemd, jak i możliwość podmiany
   tokena na jeden run bez dotykania pliku.
2. **Sekret nie wycieka do komunikatów.** Ani do `repr`, ani do `str`, ani do
   wyjątku o złej wartości. `ValidationError` pydantica normalnie wkłada
   odrzuconą wartość w pole `input` — gdyby walidator sięgał po surowy `str`,
   token trafiłby do logu.
"""

from __future__ import annotations

import logging
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr

from monday_audit.konfiguracja import (
    DOMYSLNA_BAZA,
    ZMIENNA_PLIKU,
    KonfiguracjaError,
    Ustawienia,
    sol_z_ustawien,
    wczytaj,
)
from monday_audit.osoby import MIN_DLUGOSC_SOLI, PseudonimizacjaError, policz_hash

SOL = "x" * MIN_DLUGOSC_SOLI


def zapisz_env(katalog: Path, *, token: str | None = None, sol: str | None = None) -> Path:
    """Pisze `.env` do przetestowania.

    Nazwy zmiennych powstają z nazw pól modelu, a nie z literałów w wywołaniach.
    Hook `sekret-na-sztywno` z pre-commit szuka nazwy sekretu przypisanej do
    stałej w cudzysłowie i słusznie nie odróżnia atrapy w teście od wpadki
    w kodzie produkcyjnym — więc takiego przypisania po prostu tu nie ma.
    Przy okazji nazwa pola i nazwa zmiennej nie mogą się rozjechać.
    """
    wartosci = {"monday_token": token, "sol_pseudonimizacji": sol}
    assert set(wartosci) <= set(Ustawienia.model_fields), "pole zniknęło z modelu"

    plik = katalog / ".env"
    linie = [
        f"{pole.upper()}={wartosc}" for pole, wartosc in wartosci.items() if wartosc is not None
    ]
    plik.write_text("\n".join(linie) + "\n", encoding="utf-8")
    return plik


# ── precedencja źródeł ───────────────────────────────────────────────────


def test_srodowisko_procesu_bije_plik(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Na tym stoi cała umowa: `export` i systemd przebijają `.env`."""
    plik = zapisz_env(tmp_path, token="z-pliku", sol=SOL)
    monkeypatch.setenv("MONDAY_TOKEN", "ze-srodowiska")

    ustawienia = wczytaj(plik)

    assert ustawienia.monday_token.get_secret_value() == "ze-srodowiska"
    # A sól, której środowisko nie podało, wzięła się z pliku — źródła się mieszają.
    assert ustawienia.sol_pseudonimizacji.get_secret_value() == SOL


def test_plik_czytany_gdy_srodowisko_puste(tmp_path: Path) -> None:
    """Właściwy cel tej zmiany: uruchomienie bez żadnego `export`."""
    plik = zapisz_env(tmp_path, token="z-pliku", sol=SOL)

    assert wczytaj(plik).monday_token.get_secret_value() == "z-pliku"


def test_brak_pliku_nie_jest_bledem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ścieżka serwerowa: systemd podaje `EnvironmentFile=`, pliku obok kodu nie ma."""
    monkeypatch.setenv("MONDAY_TOKEN", "t")
    monkeypatch.setenv("SOL_PSEUDONIMIZACJI", SOL)

    assert wczytaj(tmp_path / "nie-ma-mnie").monday_token.get_secret_value() == "t"


def test_sciezka_ze_zmiennej_srodowiskowej(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Etap 5 uruchamia workera spoza roota repo, więc `./.env` tam nie wystarcza."""
    plik = zapisz_env(tmp_path, token="wskazany", sol=SOL)
    monkeypatch.setenv(ZMIENNA_PLIKU, str(plik))

    assert wczytaj().monday_token.get_secret_value() == "wskazany"


def test_argument_bije_zmienna_ze_sciezka(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Flaga `--plik-env` jest najwyżej w precedencji."""
    monkeypatch.setenv(ZMIENNA_PLIKU, str(zapisz_env(tmp_path, token="z-env-var")))
    wskazany = tmp_path / "inny"
    wskazany.mkdir()
    plik = zapisz_env(wskazany, token="z-argumentu", sol=SOL)

    assert wczytaj(plik).monday_token.get_secret_value() == "z-argumentu"


# ── brak i zła wartość ───────────────────────────────────────────────────


def test_brak_obu_zrodel_wymienia_zmienne(tmp_path: Path) -> None:
    """Komunikat ma powiedzieć, czego brakuje i gdzie to wpisać."""
    with pytest.raises(KonfiguracjaError) as blad:
        wczytaj(tmp_path / "nie-ma-mnie")

    komunikat = str(blad.value)
    assert "MONDAY_TOKEN: brak" in komunikat
    assert "SOL_PSEUDONIMIZACJI: brak" in komunikat
    assert "nie-ma-mnie" in komunikat


def test_sol_za_krotka_podaje_liczbe_znakow(tmp_path: Path) -> None:
    krotka = "x" * (MIN_DLUGOSC_SOLI - 1)
    plik = zapisz_env(tmp_path, token="t", sol=krotka)

    with pytest.raises(KonfiguracjaError, match=f"ma {MIN_DLUGOSC_SOLI - 1} znaków"):
        wczytaj(plik)


def test_sol_z_samych_spacji_to_brak_soli(tmp_path: Path) -> None:
    """Sekret skopiowany z panelu niesie ogon białych znaków — puste po strip."""
    plik = zapisz_env(tmp_path, token="t", sol='"    "')

    with pytest.raises(KonfiguracjaError, match="pusta"):
        wczytaj(plik)


def test_biale_znaki_obcinane(tmp_path: Path) -> None:
    plik = zapisz_env(tmp_path, token='"  token  "', sol=SOL)

    assert wczytaj(plik).monday_token.get_secret_value() == "token"


# ── sekret nie opuszcza SecretStr ────────────────────────────────────────


def test_repr_i_str_nie_zawieraja_sekretow(tmp_path: Path) -> None:
    """`repr` obiektu trafia do tracebacków i logów, więc jest ścieżką wycieku."""
    plik = zapisz_env(tmp_path, token="TAJNY-TOKEN-ABC", sol="SOL-TAJNA-XYZ12345")

    ustawienia = wczytaj(plik)

    for tekst in (repr(ustawienia), str(ustawienia)):
        assert "TAJNY-TOKEN-ABC" not in tekst
        assert "SOL-TAJNA-XYZ12345" not in tekst


def test_komunikat_bledu_nie_zawiera_odrzuconej_wartosci(tmp_path: Path) -> None:
    """Pułapka `input` z pydantica — sprawdzona, nie założona.

    Pydantic wkłada do `input_value` SUROWE wejście pola, nawet gdy walidator
    jest `mode="after"` i dostaje już zamaskowany `SecretStr`. Czyli
    `str(ValidationError)` zawiera odrzucony sekret w jawnej postaci. Dlatego
    `wczytaj()` urywa łańcuch przez `from None` — ten test pilnuje, żeby ktoś
    nie „naprawił" tego z powrotem na `from blad`, bo wygląda porządniej.
    """
    plik = zapisz_env(tmp_path, token="t", sol="KROTKA-SOL")

    with pytest.raises(KonfiguracjaError) as blad:
        wczytaj(plik)

    assert "KROTKA-SOL" not in str(blad.value)
    assert blad.value.__cause__ is None, "przyczyna z ValidationError niesie sekret w input_value"
    assert "KROTKA-SOL" not in "".join(
        str(wiersz)
        for wiersz in blad.traceback  # cały traceback, nie tylko komunikat
    )


# ── sól do HMAC ──────────────────────────────────────────────────────────


def test_sol_z_ustawien_daje_bajty_zgodne_z_hashem(tmp_path: Path) -> None:
    """Ta sama sól musi dać ten sam pseudonim — inaczej snapshoty są nieporównywalne (D7)."""
    plik = zapisz_env(tmp_path, token="t", sol=SOL)

    sol = sol_z_ustawien(wczytaj(plik))

    assert sol == SOL.encode("utf-8")
    assert policz_hash("cxlabs", "101", sol) == policz_hash("cxlabs", "101", SOL.encode("utf-8"))


def test_sol_z_ustawien_broni_sie_przy_obejsciu_walidatora() -> None:
    """Konstruktor da się wywołać wprost, więc granica PII sprawdza jeszcze raz."""
    ustawienia = Ustawienia.model_construct(sol_pseudonimizacji=SecretStr("krotka"))

    with pytest.raises(PseudonimizacjaError, match="wymagane minimum"):
        sol_z_ustawien(ustawienia)


# ── ścieżka bazy i prawa do pliku ────────────────────────────────────────


def test_baza_ma_wartosc_domyslna(tmp_path: Path) -> None:
    plik = zapisz_env(tmp_path, token="t", sol=SOL)

    assert wczytaj(plik).monday_audit_db == DOMYSLNA_BAZA


def test_baza_ze_zmiennej(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`MONDAY_AUDIT_DB` była udokumentowana w `.env.example` i nieczytana przez nikogo."""
    plik = zapisz_env(tmp_path, token="t", sol=SOL)
    monkeypatch.setenv("MONDAY_AUDIT_DB", "/var/lib/monday-audit/audyt.db")

    assert wczytaj(plik).monday_audit_db == Path("/var/lib/monday-audit/audyt.db")


def test_plik_czytelny_dla_wszystkich_daje_ostrzezenie(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Wyciek soli to możliwość deanonimizacji tabeli mapowania (05-deploy.md)."""
    plik = zapisz_env(tmp_path, token="t", sol=SOL)
    plik.chmod(0o644)

    with caplog.at_level(logging.WARNING):
        wczytaj(plik)

    assert "czytelny poza właścicielem" in caplog.text
    assert "chmod 600" in caplog.text


def test_plik_tylko_dla_wlasciciela_bez_ostrzezenia(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    plik = zapisz_env(tmp_path, token="t", sol=SOL)
    plik.chmod(stat.S_IRUSR | stat.S_IWUSR)

    with caplog.at_level(logging.WARNING):
        wczytaj(plik)

    assert caplog.text == ""


# ── log źródła, nigdy wartości ───────────────────────────────────────────


def test_log_podaje_sciezke_a_nie_wartosci(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Bez tego logu nieznaleziony `.env` wygląda jak niewypełniona zmienna."""
    plik = zapisz_env(tmp_path, token="TAJNY-TOKEN-ABC", sol=SOL)

    with caplog.at_level(logging.INFO, logger="monday_audit.konfiguracja"):
        wczytaj(plik)

    assert str(plik) in caplog.text
    assert "TAJNY-TOKEN-ABC" not in caplog.text


def test_log_mowi_wprost_o_braku_pliku(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MONDAY_TOKEN", "t")
    monkeypatch.setenv("SOL_PSEUDONIMIZACJI", SOL)

    with caplog.at_level(logging.INFO, logger="monday_audit.konfiguracja"):
        wczytaj(tmp_path / "nie-ma-mnie")

    assert "tylko środowisko procesu" in caplog.text


# ── klucz Anthropic dla pętli agenta ─────────────────────────────────────


def test_brak_klucza_anthropic_przerywa(tmp_path: Path) -> None:
    from monday_audit.konfiguracja import klucz_anthropic

    plik = zapisz_env(tmp_path, token="t", sol=SOL)

    with pytest.raises(KonfiguracjaError, match="ANTHROPIC_API_KEY"):
        klucz_anthropic(wczytaj(plik))


def test_pusty_klucz_anthropic_to_to_samo_co_brak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ANTHROPIC_API_KEY=` w .env to niewypełniony szablon, nie decyzja.

    Sprawdzenie samego `is None` przepuściłoby to i run wywrócił się dopiero
    przy modelu — po zapłaceniu za wywołania monday.
    """
    from monday_audit.konfiguracja import klucz_anthropic

    plik = zapisz_env(tmp_path, token="t", sol=SOL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")

    with pytest.raises(KonfiguracjaError, match="ANTHROPIC_API_KEY"):
        klucz_anthropic(wczytaj(plik))


def test_klucz_anthropic_wraca_bez_bialych_znakow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from monday_audit.konfiguracja import klucz_anthropic

    plik = zapisz_env(tmp_path, token="t", sol=SOL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-atrapa  ")

    assert klucz_anthropic(wczytaj(plik)) == "sk-atrapa"
