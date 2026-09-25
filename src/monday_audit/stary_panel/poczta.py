"""Wysyłka maila resetującego hasło. `smtplib` ze stdlib, bez nowej zależności.

## Dlaczego to w ogóle istnieje

Reset z panelu (D16 aneks) wymaga sesji, a kto zgubił hasło, sesji nie ma. Brakowało
drogi dla osoby, która **nie może się zalogować** — a jedynym dowodem tożsamości,
jaki mamy bez SSO (O24), jest skrzynka pocztowa w domenie `@cxlabs.digital`.

## Zakaz z CLAUDE.md nie jest naruszony

`smtplib`, `email.message` i `ssl` są w bibliotece standardowej Pythona. Żadnej
nowej zależności nie dodajemy — sprawdzone przed napisaniem tego pliku.

## Tryb awaryjny, gdy SMTP nie jest skonfigurowany

Bez `SMTP_HOST` w środowisku **nie wybuchamy** — link ląduje w logu serwera
z wyraźnym ostrzeżeniem. Powód jest praktyczny: mechanizm ma dać się uruchomić
i przetestować, zanim ktoś skonfiguruje pocztę, a brak konfiguracji nie może
oznaczać „nikt nigdy nie odzyska hasła".

To **nie jest** stan docelowy dla wystawionej aplikacji: link w logu widzi każdy,
kto ma dostęp do logów. Dlatego log mówi o tym wprost, a nie po cichu.

## Czego tu nie ma

**Hasła w mailu.** Mail niesie LINK, nie hasło. Hasło wysłane mailem zostaje
w skrzynce na zawsze, także po tym, jak przestanie być aktualne. Link umiera po
30 minutach i po jednym użyciu.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from monday_audit.konfiguracja import UstawieniaPoczty

logger = logging.getLogger(__name__)

TEMAT = "CXLABS — nowe hasło do panelu audytu"

TRESC = """\
Ktoś (prawdopodobnie Ty) poprosił o nowe hasło do panelu audytu CXLABS.

Otwórz ten link, żeby dostać nowe hasło:

{link}

Link jest ważny {minut} minut i działa jeden raz.

Jeśli to nie Ty prosiłeś o reset, zignoruj tę wiadomość — Twoje hasło
pozostaje bez zmian, a link wygaśnie sam.

--
Ta wiadomość jest wysyłana automatycznie. Nie odpowiadaj na nią.
"""


class PocztaError(RuntimeError):
    """Nie udało się wysłać. Wołający decyduje, czy to zatrzymuje żądanie."""


def wyslij_link_resetu(ustawienia: UstawieniaPoczty, *, email: str, link: str, minut: int) -> bool:
    """Wysyła link. Zwraca `False`, gdy poczta nie jest skonfigurowana.

    `False` **nie jest błędem** — to tryb awaryjny opisany w docstringu modułu:
    link idzie do logu, żeby brak konfiguracji SMTP nie zamykał drogi odzyskania
    hasła. Wołający nie zmienia z tego powodu odpowiedzi HTTP, bo odpowiedź musi
    być identyczna w każdym przypadku (inaczej brama zdradza, które konta istnieją).
    """
    tresc = TRESC.format(link=link, minut=minut)

    if not ustawienia.smtp_host:
        # Wyraźnie i głośno: to stan do naprawy przed wystawieniem aplikacji.
        logger.warning(
            "SMTP nieskonfigurowany — link resetu NIE został wysłany mailem. "
            "Link poniżej trafia do logu, co jest trybem awaryjnym, nie docelowym."
        )
        logger.warning("link resetu dla %s: %s", email, link)
        return False

    wiadomosc = EmailMessage()
    wiadomosc["Subject"] = TEMAT
    wiadomosc["From"] = ustawienia.smtp_nadawca or ustawienia.smtp_user or ""
    wiadomosc["To"] = email
    wiadomosc.set_content(tresc)

    try:
        # STARTTLS na 587 (Gmail, Microsoft 365, większość dostawców). Port 465
        # oznacza SMTPS — połączenie szyfrowane od pierwszego bajtu.
        if ustawienia.smtp_port == 465:
            with smtplib.SMTP_SSL(
                ustawienia.smtp_host,
                ustawienia.smtp_port,
                context=ssl.create_default_context(),
                timeout=20,
            ) as serwer:
                _zaloguj_i_wyslij(serwer, ustawienia, wiadomosc)
        else:
            with smtplib.SMTP(ustawienia.smtp_host, ustawienia.smtp_port, timeout=20) as serwer:
                serwer.starttls(context=ssl.create_default_context())
                _zaloguj_i_wyslij(serwer, ustawienia, wiadomosc)
    except (smtplib.SMTPException, OSError) as blad:
        # Nie wpuszczamy treści wyjątku do odpowiedzi HTTP — może zdradzić, czy
        # adres istnieje po stronie dostawcy poczty.
        # `logger.error`, nie `logger.exception` (TRY400 wyciszone świadomie):
        # ślad stosu z `smtplib` niesie adres serwera, login i czasem odpowiedź
        # dostawcy o adresacie. Zapisujemy TYP błędu, bo to wystarcza do diagnozy,
        # a nie chcemy drogi do konta w logu.
        logger.error(  # noqa: TRY400
            "nie udało się wysłać maila resetu: %s", type(blad).__name__
        )
        raise PocztaError("wysyłka maila nie powiodła się") from blad

    # Logujemy adresata, nie treść i nie link — log nie ma być drogą do konta.
    logger.info("wysłano link resetu na %s", email)
    return True


def _zaloguj_i_wyslij(
    serwer: smtplib.SMTP, ustawienia: UstawieniaPoczty, wiadomosc: EmailMessage
) -> None:
    """Logowanie tylko wtedy, gdy podano dane — własny relay często ich nie chce."""
    if ustawienia.smtp_user and ustawienia.smtp_haslo:
        serwer.login(ustawienia.smtp_user, ustawienia.smtp_haslo.get_secret_value())
    serwer.send_message(wiadomosc)
