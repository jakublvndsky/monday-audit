# Wdrożenie na Mikrusa — instrukcja krok po kroku

Ten katalog zawiera wszystko, co da się przygotować **bez dostępu do serwera**.
Kroki niżej wykonuje człowiek przez SSH.

Kolejność jest z `docs/etapy/05-deploy.md`, ale **trzy kroki tamtej
specyfikacji są nieaktualne** i tutaj są poprawione — powody przy każdym.

---

## Zanim zaczniesz: warunek, który nie jest techniczny

> **Nie zakładaj konta klienta**, dopóki O23 jest otwarte.

Panel niesie imiona, nazwiska i e-maile pracowników klienta. O23 wymienia cztery
pytania bez odpowiedzi: wygasanie dostępu, kasowanie konta po zakończeniu
relacji, logi wejść, nazwiska pod URL-em. Do ich rozstrzygnięcia panel jest
**tylko dla zespołu** — konta `@cxlabs.digital`, zakładane przez
`--dodaj-osobe`.

Komenda `--dodaj-klienta` działa i nikt jej nie zablokował. To decyzja, nie
mechanizm — i dlatego stoi tu, na początku.

---

## 1. Podstawy: Python i `uv`. Node NIE jest potrzebny

```bash
ssh cxlabsNN@srvNN.mikr.us          # port SSH z panelu Mikrusa
apt update && apt install -y curl git sqlite3
curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
```

**Node nie wchodzi na serwer** — i to poprawka wobec kroku 1 specyfikacji.
`claude-agent-sdk` wozi własny plik wykonywalny (`_bundled/claude`, 246 MB),
a jego `_find_cli()` sprawdza go PIERWSZY, przed szukaniem `claude` w PATH.
Sprawdzone 2026-08-25 w kodzie SDK i potwierdzone w CI.

Front też nie potrzebuje Node'a tutaj: `npm run build` wykonuje się na maszynie
deweloperskiej, a do repo idą gotowe pliki z `front/dist`.

**`UV_INSTALL_DIR` nie jest ozdobą.** Domyślnie instalator kładzie binarkę
w `~/.local/bin/uv`, czyli w katalogu domowym tego, kto uruchomił polecenie.
Jednostka systemd woła `/usr/local/bin/uv` ścieżką bezwzględną — bo `ExecStart`
nie przeszukuje `PATH` użytkownika, a usługa startuje jako `audyt`, nie jako
root. Bez tej zmiennej `systemctl start` kończy się `status=203/EXEC`
i wygląda na błąd aplikacji, którym nie jest.

**Kontrola po tym kroku** — sprawdza ŚCIEŻKĘ, nie samą obecność `uv`:

```bash
/usr/local/bin/uv --version && python3 --version
```

---

## 2. HTTPS — Caddy ODPADA

Specyfikacja mówi „Caddy + Caddyfile → sprawdź, czy certyfikat się wystawił".
**Na Mikrusie to nie zadziała:** Mikr.us to kontener LXC z *przekierowanymi
portami*, bez własnego IPv4 i bez portu 80/443. Wyzwanie ACME nie ma jak przejść.

Dwie drogi, obie bez Caddy:

### 2a. Najpierw subdomena Mikrusa (działa od razu, zero konfiguracji)

Aplikacja słucha na IPv6 (`--host ::`), a Mikr.us daje HTTPS i certyfikat sam:

```
https://cxlabsNN-8000.mikrus.cloud
```

Nie instaluj certbota ani nie konfiguruj certyfikatów — to jest jawnie
odradzane w wiki Mikrusa.

### 2b. Potem `audyt.cxlabs.digital` przez tunel Cloudflare

Domena `cxlabs.digital` **już stoi na Cloudflare** (sprawdzone 2026-08-25:
nagłówek `server: cloudflare`, węzeł WAW), a `audyt.cxlabs.digital` jest wolna.

Tunel `cloudflared` jest właściwą drogą, bo **nie wymaga otwartych portów** —
łączy się wychodząco z serwera do Cloudflare. Instrukcja:
`https://wiki.mikr.us/podpiecie_domeny_przez_tunel_cloudflare/`

Po podpięciu ustaw w `/etc/monday-audit.env`:

```
ADRES_PUBLICZNY=https://audyt.cxlabs.digital
```

**To jedna zmienna i ma znaczenie:** bez niej linki resetu hasła powstają
z `Request.base_url`, czyli z adresu wewnętrznego, który za tunelem jest
`127.0.0.1:8000`. Klient dostałby link, którego nie da się otworzyć.

---

## 3. Kod, sekrety, baza

```bash
useradd -m -s /bin/bash audyt
mkdir -p /opt/monday-audit && chown audyt:audyt /opt/monday-audit
sudo -u audyt git clone https://github.com/jakublvndsky/monday-audit.git /opt/monday-audit
cd /opt/monday-audit && sudo -u audyt uv sync --frozen --no-dev
```

`--no-dev` pomija ruff, mypy i pytest (~120 MB). Zmierzone: środowisko
produkcyjne to **~275 MB**, z czego 246 MB to plik SDK.

### Sekrety

```bash
cp .env.example /etc/monday-audit.env
chmod 600 /etc/monday-audit.env
chown audyt:audyt /etc/monday-audit.env
nano /etc/monday-audit.env
```

**Wymagane** (bez nich aplikacja nie wstanie):

| zmienna | uwaga |
|---|---|
| `MONDAY_TOKEN` | token read-only admina; audyty z panelu i tak przyjmują klucz z przeglądarki, ale konfiguracja wymaga tego pola |
| `SOL_PSEUDONIMIZACJI` | **wyciek = deanonimizacja całej `osoby_mapowanie`**; stąd `chmod 600` |
| `MONDAY_AUDIT_DB` | **ścieżka ABSOLUTNA**, np. `/opt/monday-audit/monday_audit.db` — domyślna jest relatywna do katalogu roboczego |

**Przed wystawieniem panelu** (O29 — bez tego link resetu hasła trafia tylko
do journala, gdzie widzi go każdy z dostępem do serwera):

```
SMTP_HOST=…
SMTP_USER=…
SMTP_HASLO=…        # hasło APLIKACJI Google, nie hasło do konta
ADRES_PUBLICZNY=…   # gdy stoisz za tunelem
```

`ANTHROPIC_API_KEY` jest **opcjonalny**: przy
`KLUCZ_MODELU_OD_KLIENTA_WYMAGANY=true` (domyślnie) klucz podaje klient
w panelu i koszt idzie na jego rachunek (O36).

### Baza

Migracje aplikują się **same** przy starcie — `cli_web` woła `przygotuj_baze()`
przed uvicornem. Kontrola od zera:

```bash
sudo -u audyt uv run --frozen python -c "
from pathlib import Path
from monday_audit.baza import polacz, zastosuj_migracje
con = polacz(Path('/opt/monday-audit/monday_audit.db'))
print('zastosowane migracje:', zastosuj_migracje(con))"
```

Ma wypisać `[1, 2, …, 12]` na czystej bazie, `[]` na już zmigrowanej.

---

## 4. Usługa systemd

```bash
cp deploy/monday-audit.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now monday-audit
```

**Kontrola:**

```bash
curl -s http://127.0.0.1:8000/health     # {"status":"ok","migracja":12}
systemctl status monday-audit
journalctl -u monday-audit -n 30 --no-pager
```

`/health` jest publiczny i nie wymaga sesji — czyta go systemd
(`ExecStartPost`), skrypt wdrożenia i monitoring. Mówi wyłącznie o stanie
procesu: czy baza odpowiada i na której migracji stoi. **Ani słowa o klientach.**

### Pierwsze konto zespołu

```bash
cd /opt/monday-audit
sudo -u audyt uv run --frozen python -m monday_audit.cli_web \
    --dodaj-osobe jle@cxlabs.digital
```

Hasło wypisze się **raz**. Wymagana domena `@cxlabs.digital`.

---

## 5. Pomiar RAM — O6, i to jest krok, nie formalność

O6 jest otwarte od początku projektu i mówi wprost: *„Co nadal wymaga pomiaru
Kuby: realna rezerwa na Mikrusie."*

Zmierzone lokalnie (macOS, 2026-08-25) — dla porównania:

| co | RSS |
|---|---|
| aplikacja web + FastAPI + import SDK | 67 MB |
| + detektory na snapshocie (24 hipotezy) | 71 MB |
| podproces `claude` w trakcie analizy | 130–210 MB |
| **szczyt** | **~280 MB** |

To 2,5× mniej niż budżet projektowy (~720 MB), ale pomiar jest z macOS-a.
**Na serwerze sprawdź, ile ZOSTAJE:**

```bash
free -m                                  # przed audytem
systemctl status monday-audit | grep Memory
# w trakcie analizy, w drugim oknie:
watch -n 2 'free -m | head -2; ps -o rss=,comm= -u audyt | sort -rn | head -4'
```

**Jeśli rezerwa < 800 MB** — O6 każe zawęzić sampling activity logs
(`--top-logow`, `--z-ogona` w collectorze).

---

## 6. Kopie zapasowe

```bash
mkdir -p /var/backups/monday-audit
crontab -u audyt -e
```

```cron
# 03:15 codziennie. CEL_ZDALNY musi wskazywać POZA Mikrusa.
15 3 * * * CEL_ZDALNY=kuba@backup.example:/kopie/monday-audit /opt/monday-audit/deploy/backup.sh >> /var/log/monday-audit-backup.log 2>&1
```

**Test odtworzenia — RAZ, RĘCZNIE, przed pierwszym audytem klienta:**

```bash
gunzip -k /var/backups/monday-audit/monday_audit_*.db.gz
./deploy/backup.sh --sprawdz /var/backups/monday-audit/monday_audit_*.db
```

Sprawdza integralność **i zawartość**. Sam `integrity_check` przepuszcza pustą,
poprawną bazę — a to jest gorsze niż brak kopii, bo wygląda na kopię.
Zweryfikowane: na pustej bazie skrypt kończy się kodem 1.

Kopia zawiera `osoby_mapowanie`, czyli dane osobowe pracowników klienta, bez
szyfrowania. Cel musi być prywatny.

---

## 7. Wdrożenie kolejnej wersji

```bash
cd /opt/monday-audit && sudo -u audyt ./deploy/wdroz.sh
```

Skrypt: sprawdza brak lokalnych zmian → `git pull --ff-only` →
`uv sync --frozen --no-dev` → restart → czeka na `/health` do 10 s.
Przerywa przy pierwszym niepowodzeniu i mówi, co nie wyszło.

**Przeczytaj go przed pierwszym uruchomieniem.** Restartuje usługę; skryptu,
który to robi, nie testuje się na produkcji „na próbę".

---

## 8. Pierwszy run produkcyjny

Na koncie **CXLABS**, nie klienta. Przez panel: nowy audyt → workspace →
tablice → zbierz dane → zatwierdź.

Zanim wypuścisz cokolwiek do klienta, uruchom **bramę promocji**:

```bash
uv run python evals/brama.py --run <run_id>-agent \
    --zestaw evals/zloty_zestaw/acme_snapshot7.yaml
```

Kody wyjścia: `0` wolno, `1` bloker, `2` decyzja człowieka (regresja wobec
baseline'u). Brama wymienia też, czego **nie** sprawdziła — ta lista nie znaczy
„przeszło".

---

## Co zostaje otwarte po tym wdrożeniu

| pozycja | stan |
|---|---|
| **O6** | rezerwa RAM na Mikrusie — pomiar w kroku 5 |
| **O23** | cztery pytania o dane osobowe pod URL-em; do tego czasu **brak kont klientów** |
| **O25** | zrzuty pamięci (`LimitCORE=0` w jednostce załatwia część) i swap — sprawdź `swapon --show` |
| **O26** | brak „odetnij dostęp teraz"; ręcznie: `aktywne = 0` plus `DELETE FROM sesje` |
| **O29** | SMTP — bez niego link resetu widzi każdy z dostępem do journala |
| powtarzalność 0,797 | próg 0,8, liczba sprzed filtra BOARD_GHOST; dwa runy ≈ 3 USD |
| kolejka zadań | audyt żyje w wątku procesu; **restart w trakcie analizy ją niszczy** — sprawdź `zadania` przed restartem |
