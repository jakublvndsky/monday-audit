#!/usr/bin/env bash
# Kopia zapasowa bazy POZA serwer. Do crona, raz na dobę.
#
#     ./deploy/backup.sh                 # kopia + wysyłka
#     ./deploy/backup.sh --sprawdz PLIK  # test odtworzenia (osobno, ręcznie)
#
# ## Dlaczego to jest ważniejsze, niż wygląda
#
# `05-deploy.md`: „Snapshoty są niemutowalne i są jedynym źródłem case studies
# z liczbami. Ich utrata jest nieodwracalna — nie da się odtworzyć stanu konta
# klienta z przeszłości."
#
# Snapshot to zamrożony obraz konta klienta z konkretnej daty. Kiedy go
# stracimy, nie odzyskamy go ŻADNYM sposobem: konto klienta już wygląda inaczej,
# a audytu z sierpnia nie da się powtórzyć we wrześniu.
#
# ## `.backup`, nie `cp`
#
# Kopiowanie pliku SQLite w trakcie zapisu daje uszkodzoną kopię — i to bez
# ostrzeżenia, bo `cp` kończy się kodem 0. `.backup` używa API SQLite, które
# widzi spójny stan także przy otwartych transakcjach.
#
# ## Baza zawiera dane osobowe
#
# `osoby_mapowanie` to imiona, nazwiska i e-maile pracowników klienta, bez
# szyfrowania. Kopia jest tak samo wrażliwa jak oryginał: cel musi być
# prywatny, a nie „gdziekolwiek, byle poza serwerem".

set -euo pipefail

BAZA="${MONDAY_AUDIT_DB:-/opt/monday-audit/monday_audit.db}"
KATALOG_KOPII="${KATALOG_KOPII:-/var/backups/monday-audit}"
# Cel poza serwerem — `scp`/`rsync`. PUSTY oznacza „tylko kopia lokalna",
# co jest lepsze niż nic, ale NIE jest kopią zapasową: awaria dysku Mikrusa
# zabiera oryginał i kopię razem.
CEL_ZDALNY="${CEL_ZDALNY:-}"
ILE_TRZYMAC="${ILE_TRZYMAC:-14}"

# ── test odtworzenia ─────────────────────────────────────────────────────
#
# `05-deploy.md` wymaga tego raz, ręcznie, PRZED pierwszym audytem klienta.
# Kopia, której nikt nie próbował odtworzyć, jest założeniem, nie kopią —
# ten sam wniosek co przy guardrailach: mechanizm bez pomiaru nie jest
# mechanizmem.
if [ "${1:-}" = "--sprawdz" ]; then
    PLIK="${2:?podaj plik kopii do sprawdzenia}"
    echo "==> test odtworzenia: $PLIK"

    TYMCZASOWY=$(mktemp -d)
    trap 'rm -rf "$TYMCZASOWY"' EXIT
    cp "$PLIK" "$TYMCZASOWY/proba.db"

    echo "--> integralność"
    wynik=$(sqlite3 "$TYMCZASOWY/proba.db" "PRAGMA integrity_check;")
    [ "$wynik" = "ok" ] || { echo "BŁĄD: integrity_check = $wynik" >&2; exit 1; }

    echo "--> zawartość (czy to na pewno TA baza)"
    # Trzy liczby, które muszą być niezerowe w działającej bazie. Plik może
    # przejść `integrity_check` i być pustą, poprawną bazą — a to jest gorsze
    # niż brak kopii, bo wygląda na kopię.
    for tabela in snapshots runy _migracje; do
        ile=$(sqlite3 "$TYMCZASOWY/proba.db" "SELECT COUNT(*) FROM $tabela;" 2>/dev/null || echo 0)
        printf "    %-12s %s wierszy\n" "$tabela" "$ile"
        [ "$ile" -gt 0 ] || { echo "BŁĄD: $tabela jest pusta" >&2; exit 1; }
    done

    echo "==> kopia odtwarzalna."
    exit 0
fi

# ── kopia ────────────────────────────────────────────────────────────────

[ -f "$BAZA" ] || { echo "BŁĄD: nie ma bazy $BAZA" >&2; exit 1; }
mkdir -p "$KATALOG_KOPII"

ZNACZNIK=$(date -u +%Y%m%dT%H%M%SZ)
KOPIA="$KATALOG_KOPII/monday_audit_${ZNACZNIK}.db"

echo "==> kopia: $KOPIA"
sqlite3 "$BAZA" ".backup '$KOPIA'"

# Sprawdzenie NATYCHMIAST po kopii, nie „kiedyś". Uszkodzona kopia wykryta
# tydzień później to tydzień fałszywego poczucia bezpieczeństwa.
wynik=$(sqlite3 "$KOPIA" "PRAGMA integrity_check;")
[ "$wynik" = "ok" ] || { echo "BŁĄD: świeża kopia uszkodzona ($wynik)" >&2; exit 1; }

# `600` — kopia zawiera `osoby_mapowanie`, czyli dane osobowe pracowników
# klienta. Domyślne prawa dałyby ją każdemu procesowi na maszynie.
chmod 600 "$KOPIA"
gzip -f "$KOPIA"
KOPIA="${KOPIA}.gz"
echo "    $(du -h "$KOPIA" | cut -f1)"

if [ -n "$CEL_ZDALNY" ]; then
    echo "==> wysyłam poza serwer: $CEL_ZDALNY"
    scp -q "$KOPIA" "$CEL_ZDALNY/"
    echo "    wysłane."
else
    echo "UWAGA: CEL_ZDALNY pusty — kopia została NA TYM SERWERZE." >&2
    echo "       Awaria dysku zabierze oryginał i kopię razem." >&2
fi

# Sprzątanie starych kopii. Mikrus 2.1 ma 10 GB dysku, a baza rośnie z każdym
# audytem — bez tego katalog kopii wypełni dysk i usługa przestanie zapisywać.
echo "==> zostawiam $ILE_TRZYMAC najnowszych"
ls -1t "$KATALOG_KOPII"/monday_audit_*.db.gz 2>/dev/null \
    | tail -n "+$((ILE_TRZYMAC + 1))" \
    | while read -r stara; do
        echo "    usuwam $(basename "$stara")"
        rm -f "$stara"
    done

echo "==> gotowe."
