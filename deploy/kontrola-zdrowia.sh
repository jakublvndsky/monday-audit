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

set -uo pipefail

PORT="${PORT:-8000}"
ADRES_PUBLICZNY="${ADRES_PUBLICZNY:-}"
PLIK_ENV="${PLIK_ENV:-/etc/monday-audit.env}"
KATALOG="${KATALOG_APLIKACJI:-/opt/monday-audit}"
URL_CZUWAKA="${URL_CZUWAKA:-}"
LIMIT_S="${LIMIT_S:-15}"

# Adres publiczny bierzemy z pliku sekretów, żeby nie mieć drugiego źródła
# prawdy. Zdejmujemy cudzysłowy — `KLUCZ="wartość"` jest w plikach env
# normalne, a URL z cudzysłowami po prostu nie zadziała.
if [ -z "$ADRES_PUBLICZNY" ] && [ -r "$PLIK_ENV" ]; then
    ADRES_PUBLICZNY=$(sed -n 's/^ADRES_PUBLICZNY=//p' "$PLIK_ENV" 2>/dev/null \
        | tail -1 | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//')
fi

problemy=0
zglos() { echo "KONTROLA: $*" >&2; problemy=$((problemy + 1)); }

# ── 1. lokalnie ──────────────────────────────────────────────────────────
lokalna=$(curl -fsS -m "$LIMIT_S" "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo "")
if [ -z "$lokalna" ]; then
    zglos "/health po 127.0.0.1:${PORT} nie odpowiada — usługa martwa albo baza nie otwiera się"
else
    case "$lokalna" in
        *'"status":"ok"'*) echo "lokalnie: ok" ;;
        *) zglos "/health odpowiada, ale nie jest ok: $lokalna" ;;
    esac
fi

# ── 2. migracja ──────────────────────────────────────────────────────────
#
# Baza w tyle za kodem to awaria cicha: aplikacja wstaje, panel się otwiera,
# a zapytania do brakującej kolumny wywalają się dopiero w trakcie audytu —
# czyli po tym, jak klient wydał swój budżet wywołań monday.
if [ -n "$lokalna" ] && [ -d "$KATALOG/src/monday_audit/migracje" ]; then
    w_kodzie=$(find "$KATALOG/src/monday_audit/migracje" -name '*.sql' -type f | wc -l | tr -d ' ')
    w_bazie=$(printf '%s' "$lokalna" | sed -n 's/.*"migracja":\([0-9]*\).*/\1/p')
    if [ -z "$w_bazie" ]; then
        zglos "nie umiem odczytać numeru migracji z: $lokalna"
    elif [ "$w_bazie" != "$w_kodzie" ]; then
        zglos "baza stoi na migracji $w_bazie, a w kodzie jest ich $w_kodzie"
    else
        echo "migracja: $w_bazie z $w_kodzie"
    fi
fi

# ── 3. publicznie, czyli cała droga ──────────────────────────────────────
if [ -z "$ADRES_PUBLICZNY" ]; then
    echo "UWAGA: ADRES_PUBLICZNY pusty — sprawdzam tylko pętlę zwrotną, a to" >&2
    echo "       NIE wykryje awarii DNS, certyfikatu ani proxy operatora." >&2
else
    kod=$(curl -s -o /dev/null -m "$LIMIT_S" -w '%{http_code}' "${ADRES_PUBLICZNY}/health" 2>/dev/null || echo "000")
    case "$kod" in
        200) echo "publicznie: 200" ;;
        000) zglos "$ADRES_PUBLICZNY nie odpowiada wcale — DNS, TLS albo proxy operatora" ;;
        502|504) zglos "$ADRES_PUBLICZNY zwraca $kod — nginx trafia do originu, ale aplikacja nie odpowiada" ;;
        *)   zglos "$ADRES_PUBLICZNY zwraca $kod zamiast 200" ;;
    esac
fi

# ── czuwak ───────────────────────────────────────────────────────────────
if [ -z "$URL_CZUWAKA" ]; then
    echo "UWAGA: URL_CZUWAKA nieustawiony — wynik tej kontroli trafia WYŁĄCZNIE" >&2
    echo "       do journala. Awaria całej maszyny nie zostanie zgłoszona nikomu." >&2
elif [ "$problemy" -eq 0 ]; then
    # Pingujemy TYLKO przy pełnym sukcesie. Ping „jestem, ale coś nie działa"
    # zamieniłby czuwaka w licznik uruchomień skryptu.
    curl -fsS -m 10 -o /dev/null "$URL_CZUWAKA" 2>/dev/null \
        || echo "UWAGA: kontrola przeszła, ale ping do czuwaka nie wyszedł." >&2
fi

if [ "$problemy" -gt 0 ]; then
    echo "KONTROLA: $problemy problem(y). Zobacz: journalctl -u monday-audit -n 50" >&2
    exit 1
fi
echo "kontrola zdrowia: bez zastrzeżeń"
