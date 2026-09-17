# Czytanie POJEDYNCZEJ zmiennej z pliku sekretów. Plik do źródłowania, nie do
# uruchamiania:
#
#     . "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
#     BAZA=$(czytaj_env MONDAY_AUDIT_DB /etc/monday-audit.env)
#
# ## Dlaczego nie `source` całego pliku
#
# `source` wciągnąłby do środowiska także sól i tokeny, a stąd trafiłyby do
# KAŻDEGO podprocesu. Czytamy jedną linię i nic poza nią — z tego samego powodu
# jednostka kontroli zdrowia nie ma `EnvironmentFile=`.
#
# ## Dlaczego osobny plik
#
# Ta sama funkcja stała w dwóch kopiach — w `wdroz.sh` i w `kontrola-zdrowia.sh`
# — i zdążyły się już rozjechać w klasie znaków zdejmowanych z końców wartości.
# Jedno miejsce znaczy jedną poprawkę, a nie dwie, z których jedna zostanie
# zapomniana.

czytaj_env() {
    local klucz="$1"
    local plik="${2:-/etc/monday-audit.env}"
    [ -r "$plik" ] || return 0

    # `tail -1`: wygrywa OSTATNIE przypisanie, tak samo jak przy źródłowaniu.
    # Potem z końców lecą białe znaki i cudzysłowy — `KLUCZ="wartość"` jest
    # w plikach env normalne, a ścieżka czy URL z cudzysłowami po prostu nie
    # zadziała.
    sed -n "s/^${klucz}=//p" "$plik" 2>/dev/null \
        | tail -1 \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
              -e 's/^["'"'"']//' -e 's/["'"'"']$//'
}
