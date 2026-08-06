"""Serwer aplikacji webowej i zarządzanie kontami dostępu (D16).

    uv run python -m monday_audit.cli_web --serwuj
    uv run python -m monday_audit.cli_web --dodaj-klienta kancelaria-ekologiczna
    uv run python -m monday_audit.cli_web --dodaj-osobe jle@cxlabs.digital

`--dodaj-klienta` **wypisuje wygenerowane hasło raz i tylko raz** — w bazie leży
jego hash, więc nie da się go później odczytać. To celowe: hasło, które można
odzyskać z bazy, jest hasłem, które wycieknie razem z bazą. Gubione hasło
zastępujemy nowym.

Serwer nasłuchuje domyślnie na `127.0.0.1`, nie na `0.0.0.0`. Wystawienie na
świat wymaga Caddy z TLS-em i decyzji z O23 (dane osobowe klienta pod URL-em),
więc domyślne „tylko lokalnie" ma tu być niewygodne.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from monday_audit.baza import polacz
from monday_audit.dostep import (
    ROLA_KLIENT,
    ROLA_ZESPOL,
    DostepError,
    utworz_konto,
    wygeneruj_haslo,
)
from monday_audit.konfiguracja import wczytaj
from monday_audit.web.api import przygotuj_baze, zbuduj_aplikacje

logger = logging.getLogger(__name__)

PORT = 8000


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monday_audit.cli_web",
        description="Serwer panelu audytu i konta dostępu.",
    )
    parser.add_argument("--serwuj", action="store_true", help="uruchom serwer")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="domyślnie tylko lokalnie; wystawienie na świat wymaga TLS-a i O23",
    )
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--dodaj-klienta",
        metavar="CLIENT_ID",
        help="zakłada konto klienta i wypisuje hasło RAZ — w bazie jest tylko hash",
    )
    parser.add_argument(
        "--dodaj-osobe",
        metavar="EMAIL",
        help="zakłada konto zespołu; hasła są per osoba, nie wspólne",
    )
    parser.add_argument(
        "--wazne-dni",
        type=int,
        default=None,
        metavar="N",
        help="termin ważności konta klienta (O23 chce terminu)",
    )
    parser.add_argument("--baza", type=Path, default=None)
    parser.add_argument("--plik-env", type=Path, default=None, metavar="PLIK")
    return parser


def _dodaj(
    baza: Path,
    *,
    rola: str,
    client_id: str | None = None,
    email: str | None = None,
    wazne_dni: int | None = None,
) -> int:
    haslo = wygeneruj_haslo()
    con = polacz(baza)
    try:
        utworz_konto(
            con,
            rola=rola,
            haslo=haslo,
            client_id=client_id,
            email=email,
            wazne_dni=wazne_dni,
        )
    except DostepError as blad:
        print(f"  BŁĄD: {blad}", file=sys.stderr)
        return 1
    finally:
        con.close()

    kto = client_id or email
    print(f"\n  konto utworzone: {rola} / {kto}")
    print(f"  hasło:  {haslo}")
    print("\n  Zapisz je TERAZ — w bazie jest tylko hash, więc nie da się go odczytać.")
    if rola == ROLA_KLIENT:
        print("  Klient wpisuje je na stronie, a potem wkleja swój klucz API monday.")
        print("  Klucza NIE zapisujemy: żyje w pamięci runu i ginie razem z nim (D11).")
    return 0


def uruchom(argumenty: argparse.Namespace) -> int:
    ustawienia = wczytaj(argumenty.plik_env)
    baza = (argumenty.baza or ustawienia.monday_audit_db).absolute()
    przygotuj_baze(baza)

    if argumenty.dodaj_klienta:
        return _dodaj(
            baza,
            rola=ROLA_KLIENT,
            client_id=argumenty.dodaj_klienta,
            wazne_dni=argumenty.wazne_dni,
        )
    if argumenty.dodaj_osobe:
        if not argumenty.dodaj_osobe.endswith("@cxlabs.digital"):
            # Nie autoryzacja, a sanity check: konto zespołu z obcym adresem to
            # prawie zawsze literówka. Prawdziwe SSO na domenę to O24.
            print(
                "  BŁĄD: konto zespołu wymaga adresu @cxlabs.digital "
                "(kontrola domeny na poważnie: O24)",
                file=sys.stderr,
            )
            return 1
        return _dodaj(baza, rola=ROLA_ZESPOL, email=argumenty.dodaj_osobe)

    if not argumenty.serwuj:
        zbuduj_parser().print_help()
        return 1

    import uvicorn

    print(f"  panel:  http://{argumenty.host}:{argumenty.port}")
    print(f"  baza:   {baza}")
    if argumenty.host != "127.0.0.1":
        print("\n  UWAGA: nasłuch poza localhostem. Bez TLS-a ciasteczko `Secure`")
        print("  nie dojdzie, a pod tym adresem są dane osobowe klienta (O23).")
    uvicorn.run(
        zbuduj_aplikacje(baza=baza, ustawienia=ustawienia),
        host=argumenty.host,
        port=argumenty.port,
        log_level="info",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")
    return uruchom(zbuduj_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
