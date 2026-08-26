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
