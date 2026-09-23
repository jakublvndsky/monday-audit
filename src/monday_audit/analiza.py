"""Jedna sesja na całe konto zamiast jednej na hipotezę (plan, faza 5b-2).

## Co się zmienia wobec `agent.zbadaj_hipotezy`

Tamta pętla otwiera **sesję na każdą hipotezę** i daje jej budżet z rubryki.
Ta funkcja otwiera **jedną sesję na cały run**. Powód jest zmierzony, nie
estetyczny: dokument agregatów z fazy 5a ma ~21 tys. znaków, czyli całe konto
mieści się w jednym kontekście. Model widzi wtedy konto jako całość — a bez
tego nie da się odpowiedzieć na pytanie „co to za konto", które jest sednem
tej fazy.

Cena jest realna i trzeba ją nazwać: **jedna zła sesja psuje cały wynik**,
podczas gdy wcześniej psuła jedną hipotezę z dwudziestu czterech. Dlatego
odpowiedź bez struktury podnosi `KontraktError` zamiast wracać jako „zero
uwag" — patrz `uwagi.waliduj_uwagi`.

## Czego NIE przepisujemy

Cała kanciasta hydraulika zostaje wspólna z `agent.py`: budowa opcji sesji
(`zbuduj_opcje`), serwer narzędzi i **bramka `_brama_narzedzi`**, która widzi
każde wywołanie narzędzia. To są rzeczy, które już raz przeszły przez usterki
klasy „przeszło testy, a nie było podpięte" i nie ma powodu budować ich drugi
raz obok.

Narzędzia okazały się przenośne bez przebudowy: `NarzedziaHipotezy` nie
ogranicza dostępu do obiektu swojej hipotezy — `obiekt_id` jest PARAMETREM
wywołania. Hipoteza służy tam licznikowi, nie kontroli dostępu. Dlatego sesja
na cały run dostaje jedną `NarzedziaHipotezy` ze **wspólnym budżetem**,
zbudowaną na hipotezie zbiorczej.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from monday_audit.agent import (
    MODEL,
    AgentError,
    _tekst_promptu,
    _wyluskaj_json,
    _zuzycie,
    zbuduj_opcje,
)
from monday_audit.detektory import Hipoteza
from monday_audit.narzedzia import Narzedzia
from monday_audit.rubryka import Rubryka, wczytaj_rubryke
from monday_audit.szablony_findingow import z_szablonu
from monday_audit.uwagi import POLA_UWAGI

logger = logging.getLogger(__name__)

SCIEZKA_PROMPTU_ANALIZY = Path("docs/PROMPT_ANALIZY.md")

# Sufit wywołań narzędzi na CAŁĄ sesję. Zastępuje sumę budżetów per hipoteza
# z rubryki. Liczba jest zachowawcza: przy jednej sesji model nie musi
# dopytywać o każdą hipotezę osobno, bo obraz konta ma już w kontekście.
BUDZET_NARZEDZI = 30

# Więcej obrotów niż w sesji per hipoteza (12), bo tu jest do rozstrzygnięcia
# kilkadziesiąt hipotez, a nie jedna.
MAKS_OBROTOW_ANALIZY = 40


def rozdziel_hipotezy(
    hipotezy: list[Hipoteza], rubryka: Rubryka
) -> tuple[list[Hipoteza], list[dict[str, Any]]]:
    """Hipotezy → (do modelu, uwagi gotowe z szablonu).

    ## Regresja, którą to naprawia — ZMIERZONA na pierwszym runie 5b-2

    Pierwsza wersja `zbadaj_konto` wysyłała do modelu WSZYSTKIE hipotezy.
    Na snapshocie 1 było ich 24, z czego **8 to `ZOMBIE_ACCOUNT`, rozstrzygalne
    szablonem**. Stara ścieżka nigdy nie wysyła ich do modelu, bo szablon daje
    trafność 1,000 za 0 USD (`agent.zbadaj_hipotezy`, gałąź „ścieżka bez
    modelu"). Nowa wysłała — i **model zgubił `obecnosc_w_logach` w 7 z 8**,
    choć fakt był w danych. Wartość wynosiła `false`, a model potraktował ją
    jak „nie ma o czym mówić" i pominął pole. 7 z 9 odrzuceń walidacji tamtego
    runu miało tę jedną przyczynę.

    Wniosek szerszy niż ta klasa: w 5b-2 przeniosłem hydraulikę starej ścieżki,
    ale nie jej wiedzę. Szablon jest wiedzą — mówi „tej klasy model nie
    poprawia, tylko psuje".

    Uwaga z szablonu jest przycinana do czterech pól nowego kształtu, żeby
    raport nie niósł `waga` ani `kwota_pln`, które szablon wciąż produkuje dla
    starej ścieżki. `zrodlo` mówi czytającemu, że tego nie pisał model.
    """
    do_modelu: list[Hipoteza] = []
    z_szablonow: list[dict[str, Any]] = []
    for hipoteza in hipotezy:
        klasa = rubryka.po_id.get(hipoteza.klasa_id)
        gotowy = z_szablonu(hipoteza, klasa) if klasa is not None else None
        if gotowy is None:
            do_modelu.append(hipoteza)
            continue
        uwaga = {pole: gotowy[pole] for pole in POLA_UWAGI if pole in gotowy}
        uwaga["zrodlo"] = "szablon"
        z_szablonow.append(uwaga)
    return do_modelu, z_szablonow


def zbuduj_zadanie(
    hipotezy: list[Hipoteza],
    wejscie: dict[str, Any],
    rubryka: Rubryka | None = None,
) -> str:
    """Treść zadania: obraz konta plus wszystkie hipotezy naraz.

    Zastrzeżenia idą NA POCZĄTKU, nie w przypisie. Model czytający liczby bez
    ich ograniczeń napisze uwagę opartą na liczbie, o której nie wie, że jest
    niepełna — a to jest dokładnie ten rodzaj błędu, którego nie widać
    w wyniku, dopóki nie zobaczy go klient.

    ## `dowod_wymagany` przy każdej hipotezie

    ZMIERZONE na pierwszym runie: `PLAN_MISMATCH` miał w faktach WSZYSTKIE pięć
    pól wymaganych przez klasę, a model wpisał do dowodu trzy — bo nikt mu nie
    powiedział, które są obowiązkowe. Stara ścieżka podaje je w zadaniu od
    zawsze (`dowod=", ".join(klasa.dowod)`); pierwsza wersja nowej zgubiła to
    przy przenoszeniu. Walidacja sprawdza pola z rubryki, więc model musi je
    znać — inaczej walidacja karze go za brak informacji, której mu nie daliśmy.
    """
    rubryka = rubryka or wczytaj_rubryke()
    zastrzezenia = wejscie.get("zastrzezenia") or []
    obraz = {k: v for k, v in wejscie.items() if k != "zastrzezenia"}

    opisane = []
    for hipoteza in hipotezy:
        zapis = hipoteza.do_zapisu()
        klasa = rubryka.po_id.get(hipoteza.klasa_id)
        zapis["dowod_wymagany"] = [p.rstrip("[]") for p in klasa.dowod] if klasa else []
        opisane.append(zapis)

    czesci = [
        "## CZEGO TE LICZBY NIE OBEJMUJĄ",
        "",
        *(f"- {u}" for u in zastrzezenia),
        "",
        "## OBRAZ KONTA",
        "",
        json.dumps(obraz, ensure_ascii=False, indent=1),
        "",
        f"## HIPOTEZY DO ROZSTRZYGNIĘCIA ({len(hipotezy)})",
        "",
        json.dumps(opisane, ensure_ascii=False, indent=1),
        "",
        f"Rozstrzygnij wszystkie {len(hipotezy)}. Suma `uwagi` i `pominiete` "
        f"musi wynosić {len(hipotezy)}.",
    ]
    return "\n".join(czesci)


async def zbadaj_konto(
    hipotezy: list[Hipoteza],
    *,
    zestaw: Narzedzia,
    wejscie: dict[str, Any],
    klucz_api: str,
    model: str = MODEL,
    effort: str | None = None,
    sciezka_promptu: Path = SCIEZKA_PROMPTU_ANALIZY,
    budzet_narzedzi: int = BUDZET_NARZEDZI,
    rubryka: Rubryka | None = None,
) -> dict[str, Any]:
    """Jedna sesja, wszystkie hipotezy. Zwraca surową odpowiedź modelu.

    **Nie waliduje** — to robi `uwagi.waliduj_uwagi`. Rozdzielenie jest to samo
    co w starej ścieżce i z tego samego powodu: gdyby sesja poprawiała własne
    odpowiedzi, odsetek odrzuconych mierzyłby jakość naszych łatek, a nie
    jakość modelu.

    Zużycie wraca pod kluczem `zuzycie`, żeby wołający mógł porównać koszt
    faktyczny z szacunkiem (faza 5b-3).
    """
    # Import w środku: `claude_agent_sdk` ciągnie podproces i nie ma powodu,
    # żeby obciążał każdego, kto importuje cokolwiek z pakietu.
    from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock

    from monday_audit.agent import _zbuduj_narzedzia

    if not hipotezy:
        raise AgentError("brak hipotez — nie ma czego analizować")

    prompt = _tekst_promptu(sciezka_promptu)
    # Hipoteza ZBIORCZA: nośnik wspólnego budżetu, nie obiekt do zbadania.
    # `NarzedziaHipotezy` używa jej do licznika, a nie do kontroli dostępu.
    zbiorcza = Hipoteza(
        klasa_id="ANALIZA_KONTA",
        obiekt_id="konto",
        fakty={},
        budzet_wywolan=budzet_narzedzi,
    )
    narzedzia_sesji = zestaw.dla_hipotezy(zbiorcza)
    biezace = {"aktywne": narzedzia_sesji}
    serwer = _zbuduj_narzedzia(biezace)

    opcje = zbuduj_opcje(
        prompt=prompt,
        # Obraz konta idzie w ZADANIU, nie w prompcie systemowym. Prompt jest
        # prefiksem cache'u, więc wstawienie tam danych konta zresetowałoby
        # cache przy każdym kliencie i unieważniło `prompt_hash`.
        inwentarz="(obraz konta jest w treści zadania)",
        snapshot_id=zestaw.snapshot_id,
        serwer=serwer,
        klucz_api=klucz_api,
        model=model,
        effort=effort,
    )
    opcje.max_turns = MAKS_OBROTOW_ANALIZY
    # Bramka narzędzi MUSI zostać podpięta także tutaj. `zbuduj_opcje` ją
    # wstawia; ten assert pilnuje, żeby zmiana tamtej funkcji nie rozbroiła
    # tej ścieżki po cichu. To ta sama klasa usterki co `can_use_tool`,
    # który nigdy nie był wołany.
    if not opcje.hooks:
        raise AgentError("opcje sesji bez bramki narzędzi — nie uruchamiam")

    zadanie = zbuduj_zadanie(hipotezy, wejscie, rubryka)
    bloki: list[str] = []
    zuzycie: dict[str, float] = {}
    blad: str | None = None

    async with ClaudeSDKClient(options=opcje) as klient:
        await klient.query(zadanie)
        async for wiadomosc in klient.receive_response():
            if isinstance(wiadomosc, AssistantMessage):
                for blok in wiadomosc.content:
                    if isinstance(blok, TextBlock) and blok.text.strip():
                        bloki.append(blok.text)
            elif isinstance(wiadomosc, ResultMessage):
                zuzycie = _zuzycie(wiadomosc)
                if wiadomosc.is_error:
                    # Błąd API wraca jako `is_error`, NIE jako wyjątek — i to
                    # przy `subtype='success'`, co jest mylące. Bez tego
                    # sprawdzenia zły klucz objawiałby się jako „nie znalazłem
                    # JSON-a". Zmierzone 2026-08-05.
                    blad = str(getattr(wiadomosc, "result", "") or "błąd API")

    if blad:
        raise AgentError(f"sesja analizy padła: {blad}")

    odpowiedz = _wyluskaj_json(bloki[-1] if bloki else "")
    odpowiedz["zuzycie"] = zuzycie
    odpowiedz["wywolania_narzedzi"] = list(narzedzia_sesji.wywolania)

    ile_uwag = len(odpowiedz.get("uwagi") or [])
    ile_pominietych = len(odpowiedz.get("pominiete") or [])
    if ile_uwag + ile_pominietych != len(hipotezy):
        # OSTRZEŻENIE, nie wyjątek. Hipoteza, której model nie tknął, jest
        # stratą, ale nie unieważnia pozostałych rozstrzygnięć. Cisza byłaby
        # gorsza: raport wyglądałby na kompletny.
        logger.warning(
            "model rozstrzygnął %d z %d hipotez (%d uwag, %d pominiętych) — "
            "reszta przepadła bez śladu",
            ile_uwag + ile_pominietych,
            len(hipotezy),
            ile_uwag,
            ile_pominietych,
        )
    return odpowiedz


__all__ = ["BUDZET_NARZEDZI", "SCIEZKA_PROMPTU_ANALIZY", "zbadaj_konto", "zbuduj_zadanie"]
