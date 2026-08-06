"""Uwierzytelnianie do aplikacji webowej — konta, sesje, limit prób.

Dwie role, jeden mechanizm: hash hasła plus sesja w bazie.

- **klient** — jedno hasło na klienta, wzorem Docs Publishera. Widzi WYŁĄCZNIE
  swój audyt.
- **zespol** — e-mail z hasłem, **per osoba, nie jedno wspólne**. Wspólne hasło
  uniemożliwia powiedzenie, kto odpalił audyt za 1,71 USD.

## Rola decyduje o danych, i decyduje o tym SERWER

Ten moduł zwraca `Sesja` z rolą i `client_id`. Endpointy biorą je **stąd**,
nigdy z parametru zapytania — bo parametr przychodzi od przeglądarki, a ta
należy do odbiorcy. To ta sama zasada, którą 3.12 zapisało jako „filtrowanie
w SQL, nie w szablonie", tylko przeniesiona o warstwę wyżej (D16).

## Trzy decyzje, każda z konkretnego ryzyka

1. **Sesje w bazie, nie w bezstanowym podpisanym ciasteczku.** Odebranie
   dostępu działa natychmiast — kasujesz wiersz. Przy bezstanowym trzeba czekać,
   aż token wygaśnie, a pod tym linkiem leżą dane osobowe klienta (O23).
2. **Trzymamy HASH tokenu sesji, nie sam token.** Wyciek bazy nie daje wtedy
   gotowych ciasteczek do podstawienia.
3. **Limit prób.** Hasło klienta jest JEDYNĄ bramą do jego danych osobowych,
   więc bez limitu da się je odgadnąć. Liczony per identyfikator **i** per IP:
   pierwsze chroni konto, drugie utrudnia zgadywanie po wielu kontach naraz.

## Czego tu nie ma

**Klucza API klienta.** Nie przechodzi przez ten moduł ani przez bazę — patrz
migracja 006 i D11.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

ROLA_KLIENT = "klient"
ROLA_ZESPOL = "zespol"
ROLE = (ROLA_KLIENT, ROLA_ZESPOL)

# scrypt ze stdlib. Parametry z zaleceń OWASP dla interaktywnego logowania:
# ~64 MB pamięci i ~0,1 s na sprawdzenie. Wolno Z ZAŁOŻENIA — atakujący
# zgadujący hasła płaci ten sam koszt, a użytkownik płaci go raz.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DLUGOSC_KLUCZA = 32

# Ile sesja żyje bez użycia. Krótko, bo pod linkiem są dane osobowe klienta.
GODZIN_SESJI = 12

# Limit prób: ile nieudanych w jakim okienku blokuje dalsze.
MAKS_PROB = 5
OKNO_PROB_MINUT = 15

# Sylaby do generowania haseł dla klientów. Bez liter, które mylą się przez
# telefon (`l`/`I`, `o`/`0`), bo hasło trzeba czasem podać głosem.
SYLABY = (
    "aro",
    "bie",
    "cze",
    "dal",
    "eko",
    "fen",
    "gil",
    "hor",
    "ise",
    "jun",
    "kap",
    "lem",
    "mod",
    "nor",
    "ost",
    "pel",
    "rym",
    "sol",
    "tan",
    "uro",
    "wek",
    "zil",
    "bra",
    "cyn",
    "dre",
    "fla",
    "gro",
    "hel",
)


class DostepError(RuntimeError):
    """Uwierzytelnienie nie przeszło. Komunikat BEZ szczegółów dla odbiorcy."""


class ZbytWieleProbError(DostepError):
    """Przekroczony limit prób logowania."""


@dataclass(frozen=True, slots=True)
class Sesja:
    """Kto pyta. **Jedyne** źródło roli i `client_id` dla endpointów.

    Endpoint, który bierze `client_id` z parametru zapytania zamiast stąd,
    otwiera drogę do cudzych danych przez podmianę jednej liczby w URL-u.
    """

    konto_id: int
    rola: str
    client_id: str | None
    email: str | None

    @property
    def to_zespol(self) -> bool:
        return self.rola == ROLA_ZESPOL

    @property
    def to_klient(self) -> bool:
        return self.rola == ROLA_KLIENT

    def widzi_klienta(self, client_id: str) -> bool:
        """Czy ta sesja ma prawo do danych tego klienta.

        Zespół widzi wszystkich; klient wyłącznie siebie. Wywołanie tego
        zamiast porównywania ról w każdym endpoincie robi z tej reguły
        jedno miejsce do zepsucia, nie pięć.
        """
        return self.to_zespol or self.client_id == client_id


def zahaszuj_haslo(haslo: str, *, sol: bytes | None = None) -> tuple[str, str]:
    """Zwraca `(hash, sól)` w hex. Sól losowa per hasło."""
    uzyta = sol or secrets.token_bytes(16)
    klucz = hashlib.scrypt(
        haslo.encode("utf-8"),
        salt=uzyta,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=DLUGOSC_KLUCZA,
    )
    return klucz.hex(), uzyta.hex()


def sprawdz_haslo(haslo: str, *, hash_hex: str, sol_hex: str) -> bool:
    """Porównanie w CZASIE STAŁYM.

    `==` na hashach przecieka informację przez czas wykonania. `compare_digest`
    nie — i to jest cała różnica między „porównujemy hasła" a „porównujemy
    hasła bezpiecznie".
    """
    policzony, _ = zahaszuj_haslo(haslo, sol=bytes.fromhex(sol_hex))
    return hmac.compare_digest(policzony, hash_hex)


def _hash_tokenu(token: str) -> str:
    """SHA-256 tokenu sesji. W bazie trzymamy to, nie sam token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utworz_konto(
    con: sqlite3.Connection,
    *,
    rola: str,
    haslo: str,
    client_id: str | None = None,
    email: str | None = None,
    wazne_dni: int | None = None,
) -> int:
    """Zakłada konto dostępu. Hasło NIE jest zapisywane, tylko jego hash."""
    if rola not in ROLE:
        raise DostepError(f"nieznana rola {rola!r}; dozwolone: {', '.join(ROLE)}")
    if rola == ROLA_KLIENT and not client_id:
        raise DostepError("konto klienta musi mieć `client_id` — inaczej widziałoby wszystko")
    if rola == ROLA_ZESPOL and not email:
        raise DostepError("konto zespołu musi mieć e-mail — hasła są per osoba, nie wspólne")

    hash_hasla, sol = zahaszuj_haslo(haslo)
    teraz = datetime.now(tz=UTC)
    with con:
        kursor = con.execute(
            "INSERT INTO konta_dostepu (rola, client_id, email, hash_hasla, sol_hasla, "
            "utworzono, wazne_do) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rola,
                client_id,
                email,
                hash_hasla,
                sol,
                teraz.isoformat(),
                (teraz + timedelta(days=wazne_dni)).isoformat() if wazne_dni else None,
            ),
        )
    # Logujemy fakt, nie hasło. Ani jego długość — to też informacja.
    logger.info("utworzono konto dostępu: rola=%s klient=%s email=%s", rola, client_id, email)
    return int(kursor.lastrowid or 0)


def wygeneruj_haslo(slow: int = 4) -> str:
    """Hasło do przekazania klientowi — losowe, czytelne przez telefon.

    Nie generujemy `x8#Kq!2v`: takie hasło klient wkleja z maila i nigdy nie
    przepisze, a przez telefon nie da się go podać. Cztery losowe słowa mają
    większą entropię i dają się wypowiedzieć.
    """
    return "-".join(secrets.choice(SYLABY) + str(secrets.randbelow(90) + 10) for _ in range(slow))


def _za_duzo_prob(con: sqlite3.Connection, identyfikator: str, ip: str | None) -> bool:
    od = (datetime.now(tz=UTC) - timedelta(minutes=OKNO_PROB_MINUT)).isoformat()
    wiersz = con.execute(
        "SELECT COUNT(*) n FROM proby_logowania "
        "WHERE udana = 0 AND kiedy >= ? AND (identyfikator = ? OR (? IS NOT NULL AND ip = ?))",
        (od, identyfikator, ip, ip),
    ).fetchone()
    return int(wiersz["n"]) >= MAKS_PROB


def _zapisz_probe(
    con: sqlite3.Connection, identyfikator: str, ip: str | None, *, udana: bool
) -> None:
    with con:
        con.execute(
            "INSERT INTO proby_logowania (identyfikator, ip, kiedy, udana) VALUES (?, ?, ?, ?)",
            (identyfikator, ip, datetime.now(tz=UTC).isoformat(), int(udana)),
        )


def zaloguj(
    con: sqlite3.Connection,
    *,
    haslo: str,
    client_id: str | None = None,
    email: str | None = None,
    ip: str | None = None,
) -> str:
    """Sprawdza hasło i zwraca token sesji. Rzuca `DostepError` przy porażce.

    **Komunikat błędu jest zawsze taki sam**, niezależnie od tego, czy konto
    nie istnieje, czy hasło jest złe. Rozróżnianie tych przypadków mówi
    atakującemu, które identyfikatory są prawdziwe.
    """
    identyfikator = email or client_id or "?"
    if _za_duzo_prob(con, identyfikator, ip):
        logger.warning("zbyt wiele prób logowania dla %s", identyfikator)
        raise ZbytWieleProbError(f"zbyt wiele nieudanych prób — odczekaj {OKNO_PROB_MINUT} minut")

    if email:
        wiersz = con.execute(
            "SELECT * FROM konta_dostepu WHERE email = ? AND rola = ? AND aktywne = 1",
            (email, ROLA_ZESPOL),
        ).fetchone()
    else:
        wiersz = con.execute(
            "SELECT * FROM konta_dostepu WHERE client_id = ? AND rola = ? AND aktywne = 1",
            (client_id, ROLA_KLIENT),
        ).fetchone()

    teraz = datetime.now(tz=UTC)
    poprawne = (
        wiersz is not None
        and (wiersz["wazne_do"] is None or str(wiersz["wazne_do"]) > teraz.isoformat())
        and sprawdz_haslo(haslo, hash_hex=wiersz["hash_hasla"], sol_hex=wiersz["sol_hasla"])
    )
    _zapisz_probe(con, identyfikator, ip, udana=poprawne)
    if not poprawne or wiersz is None:
        raise DostepError("nieprawidłowe dane dostępu")

    token = secrets.token_urlsafe(32)
    with con:
        con.execute(
            "INSERT INTO sesje (hash_tokenu, konto_id, utworzono, wazna_do, ip) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                _hash_tokenu(token),
                int(wiersz["id"]),
                teraz.isoformat(),
                (teraz + timedelta(hours=GODZIN_SESJI)).isoformat(),
                ip,
            ),
        )
    logger.info("zalogowano: rola=%s klient=%s", wiersz["rola"], wiersz["client_id"])
    return token


def wczytaj_sesje(con: sqlite3.Connection, token: str | None) -> Sesja | None:
    """Sesja z tokenu z ciasteczka, albo `None`. Odświeża `ostatnie_uzycie`."""
    if not token:
        return None
    wiersz = con.execute(
        "SELECT s.hash_tokenu, k.id, k.rola, k.client_id, k.email, s.wazna_do "
        "FROM sesje s JOIN konta_dostepu k ON k.id = s.konto_id "
        "WHERE s.hash_tokenu = ? AND k.aktywne = 1",
        (_hash_tokenu(token),),
    ).fetchone()
    if wiersz is None:
        return None

    teraz = datetime.now(tz=UTC).isoformat()
    if str(wiersz["wazna_do"]) <= teraz:
        # Wygasła — kasujemy od razu, żeby tabela nie rosła śmieciami.
        with con:
            con.execute("DELETE FROM sesje WHERE hash_tokenu = ?", (wiersz["hash_tokenu"],))
        return None

    with con:
        con.execute(
            "UPDATE sesje SET ostatnie_uzycie = ? WHERE hash_tokenu = ?",
            (teraz, wiersz["hash_tokenu"]),
        )
    return Sesja(
        konto_id=int(wiersz["id"]),
        rola=str(wiersz["rola"]),
        client_id=wiersz["client_id"],
        email=wiersz["email"],
    )


def wyloguj(con: sqlite3.Connection, token: str | None) -> None:
    """Kasuje sesję. Natychmiast, bo sesje są w bazie, a nie w tokenie."""
    if not token:
        return
    with con:
        con.execute("DELETE FROM sesje WHERE hash_tokenu = ?", (_hash_tokenu(token),))
