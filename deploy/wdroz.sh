#!/usr/bin/env bash
# Wdrożenie nowej wersji na serwer. Uruchamiać NA SERWERZE, jako `audyt`.
#
#     ./deploy/wdroz.sh
#
# Nic nie zgaduje i nic nie naprawia: przy pierwszym niepowodzeniu przerywa
# i mówi, co nie wyszło. Skrypt wdrożeniowy, który „radzi sobie" z błędami,
# zostawia usługę w stanie, którego nikt nie przewidział.
#
# ## Czego ten skrypt NIE robi
#
# Nie robi kopii zapasowej — to `backup.sh`, wołany z crona. Świadomie osobno:
# backup ma działać codziennie niezależnie od wdrożeń, a wdrożenie nie może
# czekać na skopiowanie bazy poza serwer.
#
# Nie odpala migracji jawnie: `cli_web` woła `przygotuj_baze()` przy każdym
# starcie, PRZED uvicornem. Dublowanie tego tutaj dałoby dwa miejsca, w których
# schemat się zmienia.

set -euo pipefail

KATALOG="${KATALOG_APLIKACJI:-/opt/monday-audit}"
USLUGA="${USLUGA:-monday-audit}"
PORT="${PORT:-8000}"

# Te dwie MUSZĄ być identyczne z `Environment=` w jednostce systemd.
#
# `uv` domyślnie trzyma interpreter i cache w katalogu domowym, a jednostka ma
# `ProtectHome=true` i ich tam nie widzi. Ten skrypt działa POZA sandboksem
# jednostki, więc zapis do /home by mu się udał — i to jest właśnie pułapka:
# wdrożenie przeszłoby, a usługa po restarcie nie wstałaby, bo interpreter
# wylądowałby w miejscu, którego nie widzi. Dzieje się to tylko wtedy, gdy uv
# musi COKOLWIEK zainstalować (nowa wersja Pythona, nowa zależność), czyli
# nie przy każdym wdrożeniu — a taki błąd jest najgorszy z możliwych.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/var/cache/monday-audit}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$KATALOG/.uv-python}"

cd "$KATALOG"

echo "==> gałąź i stan repo"
# Niezacommitowane zmiany na serwerze znaczą, że ktoś edytował kod na produkcji.
# `git pull` by je nadpisał albo wywalił konflikt w połowie wdrożenia.
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "BŁĄD: w $KATALOG są niezacommitowane zmiany. Wdrożenie przerwane." >&2
    git status --short >&2
    exit 1
fi

echo "==> pobieram kod"
git pull --ff-only

echo "==> zależności produkcyjne (bez dev)"
# `--no-dev` pomija ruff, mypy i pytest — ~120 MB, których produkcja nie
# potrzebuje. `--frozen` odtwarza wersje z `uv.lock`, więc restart nie może
# cicho podnieść innej wersji niż ta przetestowana w CI.
uv sync --frozen --no-dev

# Front jest budowany na maszynie deweloperskiej (`npm run build`) i idzie
# do repo jako gotowe pliki — dlatego na serwerze NIE MA Node'a. Jeśli katalogu
# brak, panel wystawi samo API i zaloguje ostrzeżenie; lepiej powiedzieć teraz.
if [ ! -d front/dist ]; then
    echo "UWAGA: brak front/dist — panel odda samo API." >&2
    echo "       Zbuduj front lokalnie (npm run build) i wypchnij." >&2
fi

echo "==> kolejka zadań"
# Audyt żyje w WĄTKU procesu aplikacji — osobnego workera nie ma świadomie
# (O6, budżet RAM). Restart w trakcie analizy niszczy go bezpowrotnie: klient
# traci run, za który zapłacił własnym kluczem Anthropic, a snapshot zostaje
# niedokończony. Do 2026-09-02 ten skrypt restartował usługę bez pytania,
# a ostrzeżenie „sprawdź `zadania` przed restartem" stało tylko w README —
# czyli pilnowanie tego było zadaniem człowieka, który właśnie uruchomił
# automat, żeby nie musiał niczego pilnować.
#
# Ścieżkę bazy czytam JEDNĄ linią z pliku sekretów, zamiast go źródłować:
# `source` wciągnąłby do środowiska także sól i tokeny, a stąd trafiłyby do
# każdego podprocesu.
PLIK_ENV="${PLIK_ENV:-/etc/monday-audit.env}"
BAZA="${MONDAY_AUDIT_DB:-$(sed -n 's/^MONDAY_AUDIT_DB=//p' "$PLIK_ENV" 2>/dev/null | tail -1)}"

if [ -n "$BAZA" ] && [ -r "$BAZA" ] && command -v sqlite3 >/dev/null 2>&1; then
    W_TOKU=$(sqlite3 -readonly "$BAZA" \
        "SELECT count(*) FROM zadania WHERE stan IN ('w_kolejce','zbieram','analizuje');" 2>/dev/null || echo "")
    if [ -z "$W_TOKU" ]; then
        echo "UWAGA: nie udało się odczytać kolejki z $BAZA — restartuję w ciemno." >&2
    elif [ "$W_TOKU" -gt 0 ]; then
        echo "BŁĄD: $W_TOKU zadanie/zadania w toku. Restart je zniszczy." >&2
        echo "       Zaczekaj albo, jeśli wiesz co robisz: POMIN_KOLEJKE=1 $0" >&2
        sqlite3 -readonly "$BAZA" \
            "SELECT '       ' || id || '  ' || stan || '  ' || coalesce(etap,'') FROM zadania
             WHERE stan IN ('w_kolejce','zbieram','analizuje');" >&2 2>/dev/null || true
        [ "${POMIN_KOLEJKE:-}" = "1" ] || exit 1
        echo "       POMIN_KOLEJKE=1 — restartuję mimo to." >&2
    else
        echo "    pusto, restart bezpieczny"
    fi
else
    # Nie blokuję wdrożenia brakiem narzędzia diagnostycznego — ale mówię o tym
    # głośno, bo cicha utrata tej kontroli jest dokładnie tym, co ją zepsuło.
    echo "UWAGA: nie sprawdziłem kolejki (brak sqlite3 albo nieczytelna baza)." >&2
fi

echo "==> restart usługi"
sudo systemctl restart "$USLUGA"

echo "==> czekam na /health"
# Do dziesięciu prób po sekundzie. Migracje przy starcie mogą potrwać, a
# `systemctl restart` wraca, zanim uvicorn zdąży przyjąć pierwsze żądanie.
for i in $(seq 1 10); do
    if odpowiedz=$(curl -fsS "http://127.0.0.1:${PORT}/health" 2>/dev/null); then
        echo "    $odpowiedz"
        echo "==> wdrożone."
        exit 0
    fi
    sleep 1
done

echo "BŁĄD: /health nie odpowiedział po 10 s. Usługa NIE jest zdrowa." >&2
echo "       Zobacz: journalctl -u ${USLUGA} -n 50 --no-pager" >&2
exit 1
