"""Odbiorca trace'ów: co robi z awarią i czego nie wolno mu połknąć (faza 4).

Klient Langfuse jest podstawiany atrapą. Test, który wychodzi do sieci, mierzy
cudzą dostępność, a nie nasz kod — a ten plik ma pilnować jednej rzeczy:
**które wyjątki wolno zjeść, a które muszą przejść.**
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from monday_audit.prywatnosc.maskowanie import MaskowanieError
from monday_audit.tracing.trace import Obserwacja, Trace
from monday_audit.tracing.wysylka import (
    WysylkaLangfuse,
    _hak_maskujacy,
    wysylka_z_ustawien,
)


class AtrapaObserwacji:
    def __init__(self, zapis: list[dict[str, Any]]) -> None:
        self._zapis = zapis
        self.zakonczona = False

    def start_observation(self, **argumenty: Any) -> AtrapaObserwacji:
        self._zapis.append(argumenty)
        return AtrapaObserwacji(self._zapis)

    def end(self) -> None:
        self.zakonczona = True


class AtrapaKlienta:
    def __init__(self, blad: Exception | None = None) -> None:
        self.zapis: list[dict[str, Any]] = []
        self.blad = blad
        self.dosłane = False

    def start_observation(self, **argumenty: Any) -> AtrapaObserwacji:
        if self.blad:
            raise self.blad
        self.zapis.append(argumenty)
        return AtrapaObserwacji(self.zapis)

    def flush(self) -> None:
        self.dosłane = True

    def shutdown(self) -> None:
        pass


def _wysylka(klient: AtrapaKlienta) -> WysylkaLangfuse:
    """Omija `__init__`, bo ten buduje prawdziwego klienta z kluczy."""
    wysylka = object.__new__(WysylkaLangfuse)
    wysylka._klient = klient  # type: ignore[assignment]
    return wysylka


TRACE = Trace(
    nazwa="hipoteza:X",
    metadane={"model": "claude-opus-5", "run_id": "r1"},
    obserwacje=(
        Obserwacja(
            nazwa="hipoteza:X",
            rodzaj="generation",
            wejscie={"a": 1},
            wyjscie={"b": 2},
            zuzycie={"input": 10, "output": 20},
            koszt_usd=0.5,
        ),
        Obserwacja(nazwa="narzedzie:snapshot", rodzaj="span"),
    ),
)


# ── które wyjątki wolno zjeść ────────────────────────────────────────────


def test_awaria_transportu_nie_wywraca_audytu(caplog: Any) -> None:
    """Obserwowalność jest narzędziem do patrzenia na produkt, nie produktem.
    Padnięty eksport kosztuje ślad; przerwany audyt kosztuje klienta run."""
    wysylka = _wysylka(AtrapaKlienta(blad=ConnectionError("Langfuse nie odpowiada")))

    with caplog.at_level(logging.WARNING):
        wysylka.wyslij(TRACE)  # nie podnosi

    assert "audyt leci dalej" in caplog.text


def test_maskowanie_nie_jest_awaria_transportu_i_musi_przejsc() -> None:
    """NAJWAŻNIEJSZY test w tym pliku. Gdyby `MaskowanieError` wpadał do tej
    samej gałęzi co `ConnectionError`, bezpiecznik zamieniłby się w ozdobę:
    zadziałałby, zalogował ostrzeżenie i pozwolił lecieć dalej."""
    wysylka = _wysylka(AtrapaKlienta(blad=MaskowanieError("nieznany typ")))

    with pytest.raises(MaskowanieError):
        wysylka.wyslij(TRACE)


# ── kształt tego, co idzie do SDK ────────────────────────────────────────


def test_korzen_niesie_metadane_a_generacja_zuzycie() -> None:
    klient = AtrapaKlienta()

    _wysylka(klient).wyslij(TRACE)

    korzen = klient.zapis[0]
    assert korzen["as_type"] == "span"
    assert korzen["metadata"]["run_id"] == "r1"

    generacja = klient.zapis[1]
    assert generacja["as_type"] == "generation"
    assert generacja["usage_details"] == {"input": 10, "output": 20}
    assert generacja["cost_details"] == {"total": 0.5}
    assert generacja["model"] == "claude-opus-5"


def test_span_narzedzia_nie_dostaje_modelu_ani_kosztu() -> None:
    """Span nie ma czego nieść — a `model` przy narzędziu sugerowałby, że
    wywołanie narzędzia kosztowało tokeny."""
    klient = AtrapaKlienta()

    _wysylka(klient).wyslij(TRACE)

    span = klient.zapis[2]
    assert span["as_type"] == "span"
    assert "model" not in span
    assert "usage_details" not in span


def test_zamkniecie_dosyla_bufor() -> None:
    """Bez `flush` krótki proces CLI kończy się przed eksportem."""
    klient = AtrapaKlienta()

    _wysylka(klient).zamknij()

    assert klient.dosłane


# ── hak maskujący jako druga siatka ──────────────────────────────────────


def test_hak_maskuje_to_co_sdk_zebralo_poza_nami() -> None:
    assert _hak_maskujacy(data={"x": "lead@obcy.test"}) == {"x": "[E-MAIL]"}


def test_hak_podnosi_wyjatek_a_langfuse_odrzuca_paczke() -> None:
    """Ich warstwa odrzuca CAŁĄ paczkę eksportu przy wyjątku z funkcji
    maskującej. To jest właśnie to, czego chcemy — zawodzimy zamknięte."""

    class Cos:
        pass

    with pytest.raises(MaskowanieError):
        _hak_maskujacy(data={"x": Cos()})


# ── brak konfiguracji to stan poprawny ───────────────────────────────────


def test_bez_kluczy_nie_ma_wysylki() -> None:
    class BezLangfuse:
        langfuse_wlaczony = False

    assert wysylka_z_ustawien(BezLangfuse()) is None  # type: ignore[arg-type]


def test_konstruktor_odmawia_bez_konfiguracji() -> None:
    class BezLangfuse:
        langfuse_wlaczony = False

    with pytest.raises(ValueError, match="nie jest skonfigurowany"):
        WysylkaLangfuse(BezLangfuse())  # type: ignore[arg-type]
