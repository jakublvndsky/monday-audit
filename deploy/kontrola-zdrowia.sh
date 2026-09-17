#!/usr/bin/env bash
# Kontrola zdrowia panelu. Uruchamiana z timera systemd (Z4 z etapu 6).
#
#     ./deploy/kontrola-zdrowia.sh
#
# ## Czego ta kontrola NIE potrafi i dlaczego to jest ważne
#
# Działa NA TEJ SAMEJ MASZYNIE co panel. Wykryje martwą usługę, uszkodzoną bazę
# i rozjechaną migrację — czyli awarie, które zdarzają się najczęściej. **Nie
# wykryje śmierci samej maszyny ani zerwanej sieci**, bo wtedy nie ma jej kto
# uruchomić. Monitor, który milczy razem z tym, co monitoruje, jest wart tyle,
# co jego brak.
#
# Domyka to `URL_CZUWAKA` (dead man's switch): serwer PINGUJE NA ZEWNĄTRZ po
# każdej udanej kontroli, a usługa zewnętrzna krzyczy, gdy pingi ustaną.
# Odwrócenie kierunku jest tu całą sztuczką — nie wymaga otwartych portów
# i wykrywa śmierć maszyny, której lokalny monitor z definicji nie zgłosi.
#
# **Bez `URL_CZUWAKA` ta kontrola pisze wyłącznie do journala**, którego nikt
# nie czyta z własnej woli. Skrypt mówi o tym przy każdym uruchomieniu — i ma
# mówić, dopóki czuwak nie zostanie skonfigurowany.
#
# ## Co sprawdza
#
#   1. `/health` po pętli zwrotnej      — czy proces żyje i baza odpowiada
#   2. numer migracji                    — czy baza nie została w tyle za kodem
#   3. `/health` pod publicznym adresem  — czy działa CAŁA droga: Cloudflare,
#                                          proxy operatora, nginx, aplikacja
#
# Punkt 3 jest osobny od punktu 1 celowo. 2026-09-02 zdarzyło się dokładnie to,
# co on wykrywa: aplikacja odpowiadała lokalnie, a z zewnątrz przychodziło 404,
# bo host przestał być podpięty u operatora. Kontrola pytająca tylko lokalnie
# pokazałaby wtedy „wszystko w porządku".
#
# Komplet idzie DWA RAZY, gdy pierwsze przejście cokolwiek zgłosi — patrz
# `PAUZA_S` niżej.

set -uo pipefail

KATALOG_SKRYPTU="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# `czytaj_env` — jedna linia z pliku sekretów, bez źródłowania całości.
# Wspólne z `wdroz.sh`; uzasadnienie w nagłówku `_env.sh`.
# shellcheck source=deploy/_env.sh
. "$KATALOG_SKRYPTU/_env.sh"

PORT="${PORT:-8000}"
ADRES_PUBLICZNY="${ADRES_PUBLICZNY:-}"
PLIK_ENV="${PLIK_ENV:-/etc/monday-audit.env}"
KATALOG="${KATALOG_APLIKACJI:-/opt/monday-audit}"
KATALOG_MIGRACJI="$KATALOG/src/monday_audit/migracje"
URL_CZUWAKA="${URL_CZUWAKA:-}"
LIMIT_S="${LIMIT_S:-15}"

# Czuwak siedzi za internetem, czyli na najwolniejszym odcinku z całej trójki.
# Osobna zmienna, ale domyślnie ten sam limit: przy sztywnej liczbie podniesienie
# `LIMIT_S` na wolnym łączu omijałoby akurat to wywołanie, które pada pierwsze —
# a nieudany ping uruchamia czuwaka, czyli alarm o maszynie, która żyje.
LIMIT_CZUWAKA_S="${LIMIT_CZUWAKA_S:-$LIMIT_S}"

# Przerwa przed powtórką. Jedno nieudane żądanie to jeszcze nie awaria:
# `wdroz.sh` restartuje usługę w trakcie wdrożenia, a timer potrafi trafić
# dokładnie w to okno. `wdroz.sh` czeka na `/health` z tego samego powodu
# (dziesięć prób po sekundzie). Fałszywy alarm uczy ludzi ignorować monitor,
# a wtedy prawdziwy też przejdzie bez echa.
PAUZA_S="${PAUZA_S:-5}"

# Obie zmienne mogą przyjść ze środowiska albo z pliku sekretów. Pliku NIE
# źródłujemy i jednostka NIE ma `EnvironmentFile=`: wciągnęłoby to do środowiska
# tej kontroli — i każdego jej `curl`-a — także sól pseudonimizacji i tokeny,
# których ona nie potrzebuje.
[ -n "$ADRES_PUBLICZNY" ] || ADRES_PUBLICZNY=$(czytaj_env ADRES_PUBLICZNY "$PLIK_ENV")
[ -n "$URL_CZUWAKA" ] || URL_CZUWAKA=$(czytaj_env URL_CZUWAKA "$PLIK_ENV")

# To samo, co z tą zmienną robi aplikacja (`web/api.py`, `_adres_publiczny`):
# białe znaki i KOŃCOWY UKOŚNIK precz. Bez tego `https://host/` daje
# `https://host//health`, czyli 404 — fałszywy alarm nie do odróżnienia od
# awarii z 2026-09-02, dla której ten punkt w ogóle powstał.
ADRES_PUBLICZNY=$(printf '%s' "$ADRES_PUBLICZNY" \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's:/*$::')

# `<3>` i `<4>` to priorytety sysloga; systemd czyta je z początku linii
# (`SyslogLevelPrefix`, domyślnie włączone). Bez nich KAŻDA linia — łącznie
# z „usługa martwa" — ląduje w journalu jako `info`, więc
# `journalctl -p err -u monday-audit-kontrola` nie pokazuje NICZEGO. W terminalu
# przedrostek byłby śmieciem, więc dajemy go tylko wtedy, gdy wyjście faktycznie
# idzie do journala — systemd ustawia wtedy `JOURNAL_STREAM`.
if [ -n "${JOURNAL_STREAM:-}" ]; then
    PRIO_BLAD="<3>"
    PRIO_UWAGA="<4>"
else
    PRIO_BLAD=""
    PRIO_UWAGA=""
fi

problemy=0
publiczna_sprawdzona=0
zglos() { echo "${PRIO_BLAD}KONTROLA: $*" >&2; problemy=$((problemy + 1)); }
ostrzez() { echo "${PRIO_UWAGA}UWAGA: $*" >&2; }

kontroluj() {
    problemy=0
    publiczna_sprawdzona=0

    # ── 1. lokalnie ──────────────────────────────────────────────────────
    lokalna=$(curl -fsS -m "$LIMIT_S" "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo "")
    if [ -z "$lokalna" ]; then
        zglos "/health po 127.0.0.1:${PORT} nie odpowiada — usługa martwa albo baza nie otwiera się"
    else
        case "$lokalna" in
            *'"status":"ok"'*) echo "lokalnie: ok" ;;
            *) zglos "/health odpowiada, ale nie jest ok: $lokalna" ;;
        esac
    fi

    # ── 2. migracja ──────────────────────────────────────────────────────
    #
    # Baza w tyle za kodem to awaria cicha: aplikacja wstaje, panel się otwiera,
    # a zapytania do brakującej kolumny wywalają się dopiero w trakcie audytu —
    # czyli po tym, jak klient wydał swój budżet wywołań monday.
    #
    # Porównujemy NUMER z NUMEREM. `/health` oddaje `MAX(numer)` z `_migracje`,
    # więc po drugiej stronie musi stać najwyższy numer pliku, a nie ich liczba.
    # Przy luce w numeracji (cofnięta 013, numer zajęty na gałęzi) te dwie
    # wielkości rozjeżdżają się na stałe i kontrola świeci na czerwono bez
    # powodu; odwrotnie jest gorzej — przypadkowy `.sql` w katalogu dopasowałby
    # licznik do nieaktualnej bazy i ukrył prawdziwy rozjazd.
    if [ -z "$lokalna" ]; then
        :   # punkt 1 już to zgłosił, a bez odpowiedzi nie ma czego porównywać
    elif [ ! -d "$KATALOG_MIGRACJI" ]; then
        # Cicha utrata kontroli jest dokładnie tym, co ją psuje — patrz
        # `sprawdz_kolejke` w `wdroz.sh`. Mówimy głośno, ale nie blokujemy.
        ostrzez "brak $KATALOG_MIGRACJI — NIE sprawdziłem, czy baza nadąża za kodem"
    else
        w_kodzie=$(find "$KATALOG_MIGRACJI" -maxdepth 1 -name '*.sql' -type f \
            | sed -e 's#.*/##' -e 's/^0*\([0-9][0-9]*\)_.*/\1/' \
            | grep -E '^[0-9]+$' | sort -n | tail -1)
        w_bazie=$(printf '%s' "$lokalna" | sed -n 's/.*"migracja":\([0-9]*\).*/\1/p')
        if [ -z "$w_kodzie" ]; then
            ostrzez "w $KATALOG_MIGRACJI nie ma ponumerowanych plików .sql — nie mam z czym porównać"
        elif [ -z "$w_bazie" ]; then
            zglos "nie umiem odczytać numeru migracji z: $lokalna"
        elif [ "$w_bazie" != "$w_kodzie" ]; then
            zglos "baza stoi na migracji $w_bazie, a najwyższa w kodzie to $w_kodzie"
        else
            echo "migracja: $w_bazie"
        fi
    fi

    # ── 3. publicznie, czyli cała droga ──────────────────────────────────
    if [ -z "$ADRES_PUBLICZNY" ]; then
        ostrzez "ADRES_PUBLICZNY pusty — sprawdzam tylko pętlę zwrotną, a to NIE wykryje awarii DNS, certyfikatu ani proxy operatora"
    else
        # Bez `|| echo "000"`. Przy braku połączenia curl SAM wypisuje `000`
        # przez `-w`, a dopisana druga trójka sklejała się z pierwszą w `000000`
        # — gałąź `000` niżej nigdy się nie dopasowywała i awaria „nie odpowiada
        # wcale" dostawała opis „zwraca 000000 zamiast 200".
        kod=$(curl -s -o /dev/null -m "$LIMIT_S" -w '%{http_code}' "${ADRES_PUBLICZNY}/health" 2>/dev/null)
        publiczna_sprawdzona=1
        case "${kod:-000}" in
            200) echo "publicznie: 200" ;;
            000) zglos "$ADRES_PUBLICZNY nie odpowiada wcale — DNS, TLS albo proxy operatora" ;;
            502|504) zglos "$ADRES_PUBLICZNY zwraca $kod — nginx trafia do originu, ale aplikacja nie odpowiada" ;;
            *)   zglos "$ADRES_PUBLICZNY zwraca $kod zamiast 200" ;;
        esac
    fi
}

kontroluj
if [ "$problemy" -gt 0 ]; then
    ostrzez "pierwsze przejście zgłosiło $problemy — powtarzam za ${PAUZA_S}s, bo to mogło być okno wdrożenia"
    sleep "$PAUZA_S"
    kontroluj
fi

# ── czuwak ───────────────────────────────────────────────────────────────
if [ -z "$URL_CZUWAKA" ]; then
    ostrzez "URL_CZUWAKA nieustawiony — wynik tej kontroli trafia WYŁĄCZNIE do journala"
    ostrzez "       Awaria całej maszyny nie zostanie zgłoszona nikomu."
elif [ "$problemy" -gt 0 ]; then
    # Pingujemy TYLKO przy pełnym sukcesie. Ping „jestem, ale coś nie działa"
    # zamieniłby czuwaka w licznik uruchomień skryptu.
    :
elif [ "$publiczna_sprawdzona" -eq 0 ]; then
    # Zielony ping ma znaczyć „cała droga sprawdzona i działa". Bez
    # ADRES_PUBLICZNY sprawdzona jest sama pętla zwrotna, więc ping mówiłby
    # nieprawdę — a to jedyny sygnał, który ktokolwiek zobaczy bez zaglądania
    # do journala. Lepiej, żeby czuwak krzyknął o niedokończonej konfiguracji,
    # niż żeby milczał przez tydzień nad martwym nginksem.
    ostrzez "kontrola lokalna przeszła, ale ADRES_PUBLICZNY jest pusty — NIE pinguję czuwaka"
else
    curl -fsS -m "$LIMIT_CZUWAKA_S" -o /dev/null "$URL_CZUWAKA" 2>/dev/null \
        || ostrzez "kontrola przeszła, ale ping do czuwaka nie wyszedł."
fi

if [ "$problemy" -gt 0 ]; then
    echo "${PRIO_BLAD}KONTROLA: $problemy problem(y). Zobacz: journalctl -u monday-audit -n 50" >&2
    exit 1
fi
echo "kontrola zdrowia: bez zastrzeżeń"
