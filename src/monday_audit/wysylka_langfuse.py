"""Odbiorca trace'ów: Langfuse Cloud (plan, faza 4).

Osobny moduł od `obserwowalnosc.py` celowo — tam mieszka decyzja, CO wychodzi,
tutaj wyłącznie DOKĄD. Dzięki temu budowniczy trace'u i jego testy nie ciągną
SDK, a wymiana odbiorcy nie dotyka niczego, co decyduje o zawartości.

## Maskowanie jest tu wpięte DWA razy i to nie jest pomyłka

1. **Przed oddaniem czegokolwiek SDK** — `zbuduj_trace` woła `zamaskuj`, więc
   do `wyslij()` przychodzą dane już czyste. To jest pierwsza linia i ta, na
   której polegamy.
2. **W haku `mask` klienta Langfuse** — ich warstwa odrzuca CAŁĄ paczkę
   eksportu, jeśli funkcja maskująca rzuci wyjątkiem. To druga siatka, za
   darmo, na wypadek gdyby SDK zebrało coś poza naszą drogą.

Punkt 2 nie zastępuje punktu 1. Gdyby maskowanie istniało tylko w haku,
najtwardszy wymóg tej fazy stałby na zachowaniu cudzej biblioteki — a to
zachowanie może się zmienić przy podbiciu wersji i nikt tego nie zauważy.

## Awaria Langfuse'a nie może wywrócić audytu

Obserwowalność jest narzędziem do patrzenia na produkt, nie produktem.
Padnięty eksport trace'u kosztuje nas ślad; przerwany audyt kosztuje klienta
run. Dlatego `wyslij()` łapie wyjątki transportu i loguje — ale **nie łapie
`MaskowanieError`**, bo to nie jest awaria transportu, tylko zadziałanie
bezpiecznika.
"""

from __future__ import annotations

import logging
from typing import Any

from monday_audit.konfiguracja import Ustawienia
from monday_audit.maskowanie import MaskowanieError, zamaskuj
from monday_audit.obserwowalnosc import RODZAJ_GENERACJA, Trace

logger = logging.getLogger(__name__)


def _hak_maskujacy(*, data: Any, **_: Any) -> Any:
    """Druga siatka, wpinana w klienta Langfuse.

    Sygnaturę narzuca SDK (`data` jako argument nazwany). Wyjątek stąd
    powoduje po ich stronie odrzucenie całej paczki eksportu — czyli dokładnie
    to, czego chcemy: zawodzimy zamknięte.
    """
    return zamaskuj(data).dane


class WysylkaLangfuse:
    """Trace → Langfuse Cloud. Jeden klient na proces, zamykany jawnie."""

    def __init__(self, ustawienia: Ustawienia) -> None:
        if not ustawienia.langfuse_wlaczony:
            raise ValueError("Langfuse nie jest skonfigurowany — nie twórz tego obiektu")
        # Import w środku, nie na górze modułu: brak pakietu ma boleć dopiero
        # tego, kto faktycznie włączył wysyłkę, a nie każdego, kto zaimportuje
        # cokolwiek z `monday_audit`.
        from langfuse import Langfuse

        klucz_publiczny = ustawienia.langfuse_public_key
        klucz_tajny = ustawienia.langfuse_secret_key
        adres = ustawienia.langfuse_base_url
        # `None` albo pusty adres oddałby wybór regionu bibliotece — patrz
        # `Ustawienia._puste_to_brak`. `langfuse_wlaczony` to już wyklucza.
        assert klucz_publiczny is not None and klucz_tajny is not None and adres  # noqa: S101

        self._klient = Langfuse(
            public_key=klucz_publiczny.get_secret_value(),
            secret_key=klucz_tajny.get_secret_value(),
            base_url=adres,
            mask=_hak_maskujacy,
            # SDK potrafi doczepiać się do bibliotek trzecich przez OTel
            # i wysyłać to, czego mu nie daliśmy. W tej fazie wychodzi
            # WYŁĄCZNIE to, co zbudował `zbuduj_trace`.
            blocked_instrumentation_scopes=["httpx", "requests", "urllib3"],
        )

    def wyslij(self, trace: Trace) -> None:
        """Jeden trace. Korzeń niesie nazwę i metadane, dzieci — resztę."""
        try:
            korzen = self._klient.start_observation(
                name=trace.nazwa,
                as_type="span",
                metadata=trace.metadane,
            )
            try:
                for obserwacja in trace.obserwacje:
                    # Dwie jawne gałęzie zamiast `as_type=` policzonego
                    # wyrażeniem, każda zamykająca własną obserwację. Powód nie
                    # jest kosmetyczny i ma dwie warstwy:
                    #
                    #   * `start_observation` ma po jednym przeciążeniu na
                    #     rodzaj, więc warunkowy napis rozsadza je naraz i mypy
                    #     poddaje się komunikatem „too many unions",
                    #   * wspólna zmienna wymagałaby adnotacji klasą bazową,
                    #     a ta (`LangfuseObservationWrapper`) siedzi w module
                    #     prywatnym SDK. Opieranie się o cudze `_` to proszenie
                    #     się o awarię przy podbiciu wersji.
                    #
                    # Przy okazji widać, że span NIE bierze modelu, zużycia ani
                    # kosztu — bo nie ma czego nieść.
                    if obserwacja.rodzaj == RODZAJ_GENERACJA:
                        korzen.start_observation(
                            name=obserwacja.nazwa,
                            as_type="generation",
                            input=obserwacja.wejscie,
                            output=obserwacja.wyjscie,
                            metadata=obserwacja.metadane or None,
                            model=trace.metadane.get("model"),
                            usage_details=obserwacja.zuzycie or None,
                            cost_details=(
                                {"total": obserwacja.koszt_usd} if obserwacja.koszt_usd else None
                            ),
                        ).end()
                    else:
                        korzen.start_observation(
                            name=obserwacja.nazwa,
                            as_type="span",
                            input=obserwacja.wejscie,
                            output=obserwacja.wyjscie,
                            metadata=obserwacja.metadane or None,
                        ).end()
            finally:
                korzen.end()
        except MaskowanieError:
            # NIE łapiemy tego jako awarii transportu. Bezpiecznik zadziałał
            # i ma być słychać — cisza w tym miejscu zamieniłaby go w ozdobę.
            raise
        except Exception as blad:  # transport, autoryzacja, limit po ich stronie
            logger.warning(
                "nie udało się wysłać trace'u %s do Langfuse (%s: %s) — "
                "audyt leci dalej, tracimy ślad, nie run",
                trace.nazwa,
                type(blad).__name__,
                blad,
            )

    def zamknij(self) -> None:
        """Dosyła bufor. Bez tego krótki proces CLI kończy się przed eksportem."""
        try:
            self._klient.flush()
            self._klient.shutdown()
        except Exception as blad:  # zamykanie nie ma prawa wywrócić runu
            logger.warning("zamykanie Langfuse: %s: %s", type(blad).__name__, blad)


def wysylka_z_ustawien(ustawienia: Ustawienia) -> WysylkaLangfuse | None:
    """`None` znaczy „nie skonfigurowano", czyli wysyłki nie ma.

    Brak kluczy jest STANEM DOMYŚLNYM i poprawnym. Audyt bez trace'ów działa
    tak samo — traci tylko podgląd.
    """
    if not ustawienia.langfuse_wlaczony:
        logger.info("Langfuse nieskonfigurowany — trace'y nie wychodzą poza serwer")
        return None
    return WysylkaLangfuse(ustawienia)
