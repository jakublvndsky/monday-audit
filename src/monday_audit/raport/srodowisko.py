"""Środowisko Jinja i zasoby szablonów — wspólne dla wszystkich dokumentów.

Wydzielone z `raport.py` (2026-09-25, podział repo): raport uwag, stary raport,
pulpit i ewaluacja budują szablony TYM SAMYM środowiskiem, bo inaczej
autoescaping i polityka `tojson` rozjechałyby się między dokumentami.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

# Wzorzec zasobu przy pakiecie — ten sam, którym `baza.py` znajduje migracje.
KATALOG_SZABLONOW = Path(__file__).parent / "szablony"
KATALOG_ZASOBOW = KATALOG_SZABLONOW / "zasoby"


def zasob_data_uri(nazwa: str, *, katalog: Path = KATALOG_ZASOBOW) -> str | None:
    """Plik z `szablony/zasoby/` jako `data:` URI. Brak pliku to nie błąd.

    Raport musi otwierać się z dysku i drukować u kogoś bez dostępu do naszej
    sieci, więc każdy obrazek jedzie w treści dokumentu. Gdy zasobu nie ma,
    szablon po prostu go nie pokazuje — dokument zostaje czytelny.
    """
    sciezka = katalog / nazwa
    if not sciezka.is_file():
        logger.warning("brak zasobu %s — raport wyjdzie bez niego", sciezka)
        return None
    typ = "image/svg+xml" if sciezka.suffix == ".svg" else f"image/{sciezka.suffix.lstrip('.')}"
    return f"data:{typ};base64," + base64.b64encode(sciezka.read_bytes()).decode("ascii")


# Etykiety pól dowodu, które po deanonimizacji przestały pasować do nazwy.
# `user_hash: Maciej Zieliński` w dokumencie dla klienta czyta się jak usterka —
# wartość jest już nazwiskiem, a podpis nadal mówi o haszu.
ETYKIETY_DOWODU = {
    "user_hash": "konto",
    "guest_hash": "konta gości",
    "top_kontrybutor_hash": "najaktywniejsza osoba",
}

# Wartości ze słowników rubryki są bez polskich znaków, bo służą też jako
# klucze w SQL i w YAML-u. W dokumencie pokazujemy je poprawnie.
SLOWNIE = {
    "srednia": "średnia",
    "sredni": "średni",
    "niska": "niska",
    "niski": "niski",
    "wysoka": "wysoka",
    "wysoki": "wysoki",
    "krytyczna": "krytyczna",
}


def etykieta(klucz: str) -> str:
    """Nazwa pola dowodu do pokazania człowiekowi."""
    return ETYKIETY_DOWODU.get(klucz, klucz.replace("_", " "))


def slownie(wartosc: str) -> str:
    """Wartość ze słownika rubryki z polskimi znakami."""
    return SLOWNIE.get(wartosc, wartosc)


def odmiana(liczba: int, jeden: str, kilka: str, wiele: str) -> str:
    """Polska odmiana po liczbie: 1 pole, 2–4 pola, 5+ pól.

    Bez tego dokument mówi „Dowód (7 pola)". Drobiazg, ale raport ma być
    wiarygodny, a pierwszy sygnał niedbałości podważa resztę.
    """
    if liczba == 1:
        return jeden
    if 2 <= liczba % 10 <= 4 and liczba % 100 not in range(12, 15):
        return kilka
    return wiele


def srodowisko(katalog: Path) -> Environment:
    """Jinja z JAWNYM autoescapingiem. Publiczne, bo używa jej też `pulpit`.

    Nazwa bez podkreślnika świadomie: funkcja prywatna wołana z innego modułu
    to sprzeczność, którą Kuba wyłapał już raz przy `_payload` w `narzedzia.py`.
    Skoro panele budują szablony tym samym środowiskiem — a muszą, bo inaczej
    autoescaping i polityka `tojson` rozjechałyby się między dokumentami —
    to jest część API tego modułu.

    `Environment` domyślnie ma `autoescape=False`. W dokumencie niosącym nazwy
    tablic i kolumn klienta to znaczy, że nazwa `Oferty <b>2026</b>` rozwala
    układ strony, a `<script>` staje się skryptem. Pilnuje tego test — bo to
    jedna flaga, którą łatwo zgubić przy refaktorze.
    """
    srodowisko = Environment(
        loader=FileSystemLoader(katalog),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # `tojson` domyślnie woła `json.dumps` z `ensure_ascii=True`, czyli
    # „Anna Górniak" wychodzi jako „Anna Górniak". W zagnieżdżonym dowodzie
    # to znaczy nieczytelne nazwiska i nazwy tablic w dokumencie dla klienta.
    # Bezpieczeństwo zostaje: jinja i tak escapuje `<`, `>`, `&` w tym wyjściu.
    srodowisko.policies["json.dumps_kwargs"] = {
        "ensure_ascii": False,
        "indent": 2,
        "sort_keys": True,
    }
    srodowisko.filters["etykieta"] = etykieta
    srodowisko.filters["slownie"] = slownie
    srodowisko.globals["odmiana"] = odmiana
    return srodowisko
