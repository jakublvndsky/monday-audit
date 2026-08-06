"""Front: kontrakt typów i spójność marki.

Testy Pythona pilnujące rzeczy, których `tsc` nie widzi — bo TypeScript nie
zna dataclass, a Python nie czyta `.ts`.

Dwie granice:

1. **`front/src/api.ts` jest aktualny** wobec `pulpit.py`. Ręczne typy
   rozjechałyby się przy pierwszej zmianie pola, i to CICHO: objawiłoby się
   `undefined` w interfejsie u klienta, nie błędem u nas.
2. **Front i raport mają te same tokeny marki.** D14 obiecuje podmianę
   firmowego CSS w jednym miejscu; `front/src/marka.css` jest KOPIĄ pliku
   `.j2` (Vite nie czyta jinja), więc rozjazd trzeba wyłapać testem.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from monday_audit.generuj_typy import zbuduj_tresc
from monday_audit.pulpit import KLUCZE_WEWNETRZNE

KORZEN = Path(__file__).resolve().parent.parent
API_TS = KORZEN / "front" / "src" / "api.ts"
MARKA_CSS = KORZEN / "front" / "src" / "marka.css"
MARKA_J2 = KORZEN / "src" / "monday_audit" / "szablony" / "_marka.css.j2"
PULPIT_J2 = KORZEN / "src" / "monday_audit" / "szablony" / "_pulpit.css.j2"


def test_typy_frontu_sa_aktualne() -> None:
    """Wzorzec `ruff format --check`: rozjazd zatrzymuje CI, nie klienta.

    Gdy ten test padnie, uruchom:
        uv run python -m monday_audit.generuj_typy
    """
    assert API_TS.is_file(), "brak api.ts — uruchom generator"

    assert API_TS.read_text(encoding="utf-8") == zbuduj_tresc(), (
        "front/src/api.ts jest nieaktualny wobec pulpit.py — "
        "uruchom `uv run python -m monday_audit.generuj_typy`"
    )


def test_klucze_wewnetrzne_sa_opcjonalne_w_typach() -> None:
    """W payloadzie klienta tych kluczy NIE MA, więc typ musi na to pozwalać.

    Gdyby były wymagane, front zakładałby ich obecność i czytał `undefined`
    jak wartość — a to najcichszy rodzaj usterki: nic nie wybucha, tylko
    liczby są złe.
    """
    tresc = API_TS.read_text(encoding="utf-8")

    for klucz in KLUCZE_WEWNETRZNE:
        assert f"  {klucz}?:" in tresc, f"{klucz} musi być opcjonalny w api.ts"


def _tokeny(tekst: str) -> set[str]:
    return set(re.findall(r"--[a-z0-9-]+(?=\s*:)", tekst))


def test_front_ma_te_same_tokeny_co_raport() -> None:
    """D14: firmowy CSS podmienia się w JEDNYM miejscu.

    `marka.css` jest kopią `_marka.css.j2`, bo Vite nie czyta jinja. Kopia bez
    testu rozjechałaby się przy pierwszej zmianie marki — i to cicho, bo nikt
    nie porównuje dwóch arkuszy linijka w linijkę.
    """
    z_szablonow = _tokeny(MARKA_J2.read_text(encoding="utf-8")) | _tokeny(
        PULPIT_J2.read_text(encoding="utf-8")
    )
    z_frontu = _tokeny(MARKA_CSS.read_text(encoding="utf-8"))

    brakujace = z_szablonow - z_frontu
    assert brakujace == set(), f"front stracił tokeny marki: {sorted(brakujace)}"


@pytest.mark.parametrize(
    "token", ["--cx-ink", "--cx-lime", "--space-5", "--radius-lg", "--font-display"]
)
def test_kluczowe_tokeny_marki_istnieja(token: str) -> None:
    """Wybrane wprost, żeby test padł czytelnie, gdy ktoś wyczyści arkusz."""
    assert f"{token}:" in MARKA_CSS.read_text(encoding="utf-8")


def test_front_nie_osadza_fontow() -> None:
    """Ta sama granica licencyjna co w raporcie (D14).

    Clash Display wolno osadzać tylko tak, żeby odbiorca nie mógł go wyjąć —
    a plik CSS i bundle JS to tekst.
    """
    for plik in (MARKA_CSS, KORZEN / "front" / "src" / "aplikacja.css"):
        tresc = plik.read_text(encoding="utf-8")
        assert "@font-face" not in tresc, f"{plik.name} osadza font"
        assert "fonts.googleapis" not in tresc, f"{plik.name} ciągnie Google Fonts"


def test_front_nie_trzyma_klucza_api_w_przegladarce() -> None:
    """Klucz nie może przeżyć zamknięcia karty.

    `localStorage` i `sessionStorage` są czytelne dla każdego skryptu na stronie,
    więc klucz o pełnych uprawnieniach do konta klienta nie ma tam czego szukać.
    Trzymamy go w stanie komponentu i czyścimy zaraz po wysłaniu (D11).
    """
    zrodla = list((KORZEN / "front" / "src").rglob("*.tsx")) + list(
        (KORZEN / "front" / "src").rglob("*.ts")
    )
    assert zrodla, "nie znalazłem źródeł frontu"

    # Szukamy UŻYCIA, nie wzmianki: komentarz wyjaśniający, dlaczego czegoś nie
    # robimy, jest wartościowy i nie może wywalać testu. Pierwsza wersja tego
    # testu padała na własnym komentarzu w `Audyt.tsx`.
    uzycie = re.compile(r"\b(?:local|session)Storage\s*[.\[]")
    for plik in zrodla:
        # Usuwamy komentarze `//` i `/* */`, potem szukamy wywołania.
        tresc = plik.read_text(encoding="utf-8")
        bez_komentarzy = re.sub(r"//[^\n]*|/\*.*?\*/", "", tresc, flags=re.DOTALL)
        trafienie = uzycie.search(bez_komentarzy)
        assert trafienie is None, f"{plik.name} używa {trafienie.group(0) if trafienie else ''}"


def test_klucz_api_idzie_w_ciele_nie_w_adresie() -> None:
    """Adresy trafiają do logów serwera i do historii przeglądarki."""
    klient = (KORZEN / "front" / "src" / "klient.ts").read_text(encoding="utf-8")

    # Wywołanie `odpalAudyt` musi wkładać klucz do `body`, a nie do ścieżki.
    fragment = klient[klient.index("odpalAudyt") : klient.index("stanAudytu")]
    assert "klucz_api: kluczApi" in fragment
    assert "klucz_api=" not in fragment, "klucz w query stringu"
