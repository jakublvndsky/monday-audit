"""Rozwijanie `user_hash` na imiona i e-maile — WYŁĄCZNIE w rendererze (3.12).

Cała reszta systemu widzi hashe. Ten moduł jest jedynym konsumentem
`MapowanieOsob.wczytaj()` poza walidacją antyprzeciekową z 3.8, i docstring
tamtej klasy wprost tego przewiduje: **nie owijaj tego w narzędzie agenta
i nie dopisuj kolejnych czytników „na potrzeby debugowania".**

Granica PII z D6 dotyczy **kontekstu modelu**, nie dokumentu. Agent nigdy nie
widzi nazwisk; renderer jest zwykłym kodem i działa po zakończeniu analizy.
Raport, który mówi „konto 05677b1ab370bae1 jest martwe", jest bezużyteczny —
klient nie wie, o kogo chodzi, więc rekomendacja „zwolnij to miejsce" nie
da się wykonać.

## Dwa miejsca, w których siedzą hashe

Oba trzeba obsłużyć, bo pominięcie któregokolwiek zostawia surowy hash
w dokumencie:

1. **`dowod` pod kluczami zawierającymi `hash`** — `user_hash`,
   `guest_hash[]`, `top_kontrybutor_hash`. Wartość skalarna albo lista.
2. **Wolny tekst `opis` i `rekomendacja`.** Zmierzone, nie założone: agent
   pisze „Konto administratora (hash 05677b1ab370bae1) ma status ACTIVE".
   W raporcie z pełnego runu ten jeden hash występuje cztery razy.

## Hash nierozwiązany nie przechodzi cicho

Zamieniamy go na `[nieznane konto 05677b1a…]` i liczymy. Surowy hash
w dokumencie dla klienta to usterka, nie kosmetyka — dlatego test na to
stoi w warstwie granic, obok testów PII.

Skrócony prefiks zostawiamy świadomie: osoba z CXLABS musi mieć jak
odnaleźć taki wpis w bazie, a osiem znaków to za mało, żeby dokument
przestał być czytelny.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from monday_audit.baza import MapowanieOsob
from monday_audit.osoby import DLUGOSC_HASHA

logger = logging.getLogger(__name__)

# Nasz format: HMAC-SHA256 przycięty do 16 znaków hex (`osoby.DLUGOSC_HASHA`).
# `\b` po obu stronach, żeby nie trafić w środek dłuższego ciągu.
#
# Fałszywe trafienia są tu nieistotne: identyfikatory monday są liczbowe
# (`5097387646`), a identyfikatory kolumn mają postać `text_mkq...`. Goły ciąg
# 16 znaków hex małymi literami to nasz hash i nic innego.
WZORZEC_HASHA = re.compile(rf"\b[0-9a-f]{{{DLUGOSC_HASHA}}}\b")

# Ten sam hash, ale razem z poprzedzającym go słowem „hash". Agent pisze
# „Konto administratora (hash 05677b1a…) ma status ACTIVE" — po samej podmianie
# w dokumencie dla klienta zostałoby „(hash Jan Kowalski)", co czyta się jak
# usterka. Zjadamy więc to słowo razem z hashem.
WZORZEC_W_ZDANIU = re.compile(rf"(?:hash(?:e|u|em|a)?\s+)?({WZORZEC_HASHA.pattern})", re.IGNORECASE)

# Ile znaków hasha zostaje w oznaczeniu nierozwiązanego konta.
DLUGOSC_PREFIKSU = 8


class Deanonimizacja:
    """Słownik hash → nazwisko, zbudowany raz na raport.

    Akumuluje hashe, których nie umiał rozwinąć, żeby na koniec dało się
    powiedzieć ILE ich było. Cicha podmiana na „nieznane konto" bez licznika
    ukryłaby niekompletne mapowanie — a to znaczy, że snapshot i tabela PII
    się rozjechały.
    """

    def __init__(self, con: sqlite3.Connection, client_id: str, *, z_emailem: bool = False) -> None:
        wpisy = MapowanieOsob(con, client_id).wczytaj()
        self._po_hashu = {
            w.user_hash: self._etykieta(w.imie_nazwisko, w.email, z_emailem=z_emailem)
            for w in wpisy
        }
        self._nieznane: set[str] = set()
        logger.info("deanonimizacja: %d mapowań dla klienta %s", len(self._po_hashu), client_id)

    @staticmethod
    def _etykieta(imie: str | None, email: str | None, *, z_emailem: bool) -> str:
        """Nazwisko, a gdy go nie ma — e-mail. Puste mapowanie to nie nazwa.

        `osoby_mapowanie` dopuszcza NULL w obu kolumnach, bo monday nie zawsze
        oddaje nazwę. Zwrócenie wtedy pustego stringa dałoby w raporcie
        „konto  jest martwe" — czyli usterkę wyglądającą jak literówka.
        """
        nazwa = (imie or "").strip()
        adres = (email or "").strip()
        if nazwa and adres and z_emailem:
            return f"{nazwa} ({adres})"
        if nazwa:
            return nazwa
        if adres:
            return adres
        return "konto bez nazwy w monday"

    @property
    def nieznane(self) -> tuple[str, ...]:
        return tuple(sorted(self._nieznane))

    def nazwa(self, haszyk: str) -> str:
        """Rozwija jeden hash. Nieznany dostaje oznaczenie i idzie do licznika."""
        znaleziona = self._po_hashu.get(haszyk)
        if znaleziona is not None:
            return znaleziona
        self._nieznane.add(haszyk)
        return f"[nieznane konto {haszyk[:DLUGOSC_PREFIKSU]}…]"

    def tekst(self, tresc: str) -> str:
        """Podmienia hashe WEWNĄTRZ zdania — `opis` i `rekomendacja` od agenta."""
        return WZORZEC_W_ZDANIU.sub(lambda m: self.nazwa(m.group(1)), tresc)

    def wartosc(self, wartosc: Any, *, klucz: str = "") -> Any:
        """Rekurencyjnie rozwija strukturę `dowod`.

        Dwa tryby, bo dowód miesza jedno z drugim:

        - klucz zawiera `hash` → wartość JEST hashem (albo listą hashy)
          i rozwijamy ją wprost, także gdy mapowania nie ma
        - każdy inny string → przepuszczamy przez `tekst()`, bo hash może
          siedzieć w środku zdania, np. w `powody_bledow`
        """
        if isinstance(wartosc, str):
            return self.nazwa(wartosc) if self._to_klucz_hasha(klucz) else self.tekst(wartosc)
        if isinstance(wartosc, dict):
            # KLUCZE TEŻ. `tablice_dostepne` w dowodzie `GUEST_SPRAWL` to mapa
            # user_hash → lista tablic, czyli hash jest nazwą pola, nie wartością.
            # Rekurencja po samych wartościach przepuszczała je do dokumentu —
            # złapał to test granicy na prawdziwym runie, nie test jednostkowy.
            return {
                self._nazwa_klucza(k): self.wartosc(v, klucz=str(k)) for k, v in wartosc.items()
            }
        if isinstance(wartosc, list):
            # Klucz przechodzi w dół: `guest_hash` to lista hashy, więc każdy
            # jej element jest hashem, mimo że sam nie ma nazwy klucza.
            return [self.wartosc(v, klucz=klucz) for v in wartosc]
        return wartosc

    def _nazwa_klucza(self, klucz: Any) -> Any:
        """Rozwija klucz, który SAM jest hashem. Zwykłe nazwy pól zostawia.

        Dopasowanie musi być pełne (`fullmatch`), nie częściowe: `user_hash`
        jest nazwą pola i ma zostać nazwą pola, a `3d33df9ab55c5059` jest
        osobą i ma zostać nazwiskiem.
        """
        if isinstance(klucz, str) and WZORZEC_HASHA.fullmatch(klucz):
            return self.nazwa(klucz)
        return klucz

    @staticmethod
    def _to_klucz_hasha(klucz: str) -> bool:
        """Konwencja z rubryki: `user_hash`, `guest_hash[]`, `top_kontrybutor_hash`."""
        return "hash" in klucz.lower()

    def podsumuj(self) -> None:
        """Ostrzeżenie na koniec raportu, jeśli mapowanie było niekompletne."""
        if not self._nieznane:
            return
        logger.warning(
            "%d hashy bez wpisu w `osoby_mapowanie` — w raporcie są oznaczone jako "
            "nieznane konto. Najczęstsza przyczyna: snapshot z innej soli albo "
            "z innego klienta niż podany. Prefiksy: %s",
            len(self._nieznane),
            ", ".join(h[:DLUGOSC_PREFIKSU] for h in self.nieznane),
        )
