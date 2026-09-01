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

## Zanim zaczniesz, druga rzecz: ten serwer nie jest pusty

Ta instrukcja powstała dla świeżego Mikrusa. **Maszyna docelowa nim nie jest** —
sprawdzone przy pierwszym wejściu 2026-09-01. Stoi na niej produkcja CXLABS:

| co | ile |
|---|---|
| nginx | sześć vhostów, `listen 80` + `listen NNNNN default_server` |
| PM2 | dwie aplikacje Node |
| Docker | kontener n8n |
| systemd | dwie obce usługi aplikacyjne |
| Python (obcy) | dwa procesy na pętli zwrotnej |

**Co z tego wynika dla każdego kroku niżej:**

- **Oba przekierowane porty TCP są zajęte** (docker-proxy i nginx). Trzeciego
  nie ma. Dlatego krok 2 wygląda inaczej, niż wyglądał.
- **Port 8000 jest wolny** — na pętli zwrotnej, i tam zostaje.
- **`nginx -s reload` dotyka sześciu cudzych adresów.** `nginx -t` przed
  reloadem jest warunkiem, nie zwyczajem.
- **RAM jest dzielony.** Rezerwa z kroku 5 jest liczona przy działających
  cudzych procesach; ich wzrost ją zjada.
- Nic z tego, co robisz, nie ma restartować cudzej usługi. Jeśli krok tego
  wymaga — zatrzymaj się i zapytaj właściciela tamtej aplikacji.

Uzasadnienie i konsekwencje architektoniczne: **D19** w `docs/ARCHITEKTURA.md`.

---

## 1. Podstawy: Python i `uv`. Node NIE jest potrzebny

```bash
ssh mikrus                          # host i port z panelu Mikrusa, wpis w ~/.ssh/config
apt update && apt install -y curl git sqlite3
curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
```

**Co na tej maszynie już jest** (sprawdzone 2026-09-01): `curl`, `git`, `node`
v20.20.1 z `npm` 10.8.2. **Brakuje** `sqlite3` i `uv` — po to jest polecenie
wyżej.

**`python3` na serwerze to 3.10.12, a projekt wymaga 3.12** (`requires-python`
w `pyproject.toml`). To NIE jest problem i nie instaluj Pythona z `apt`: `uv`
czyta `.python-version` i sam pobiera CPythona 3.12 do środowiska projektu.
Systemowy `python3` zostaje nietknięty — na współdzielonym serwerze podmiana
interpretera pod cudzymi aplikacjami byłaby najgorszą możliwą decyzją.

**Node nie wchodzi na serwer** — i to poprawka wobec kroku 1 specyfikacji.
`claude-agent-sdk` wozi własny plik wykonywalny (`_bundled/claude`, 246 MB),
a jego `_find_cli()` sprawdza go PIERWSZY, przed szukaniem `claude` w PATH.
Sprawdzone 2026-08-25 w kodzie SDK i potwierdzone w CI.

Front też nie potrzebuje Node'a tutaj: `npm run build` wykonuje się na maszynie
deweloperskiej, a do repo idą gotowe pliki z `front/dist`.

**Uwaga na 2026-09-01:** Node v20.20.1 na tej maszynie mimo wszystko **jest** —
zainstalowany pod cudze aplikacje na PM2. Wersja wystarcza dla Vite 7, więc
zbudowanie frontu na serwerze jest drogą awaryjną, gdyby `front/dist` nie było
w repo. Nie jest drogą domyślną: build na produkcji to krok, którego nie widział
CI.

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

## 2. HTTPS — przez nginx, który już tam stoi

Specyfikacja mówi „Caddy + Caddyfile → sprawdź, czy certyfikat się wystawił".
**Na Mikrusie to nie zadziała:** kontener LXC z przekierowanymi portami, bez
własnego IPv4 i bez portu 80/443 — wyzwanie ACME nie ma jak przejść. To zostaje
z D18 i potwierdziło się w praktyce: na maszynie z sześcioma działającymi
adresami HTTPS **nie ma katalogu `/etc/letsencrypt`**.

**Zmiana wobec D18: tunel `cloudflared` też nie jest potrzebny.** Wzorzec, który
tu działa, jest prostszy i już obsługuje sześć adresów:

```
przeglądarka ──HTTPS──> Cloudflare ──HTTP──> nginx na serwerze ──> 127.0.0.1:8000
                (terminuje TLS)        (origin, port przekierowany)
```

Na serwerze nie ma `cloudflared`. TLS terminuje Cloudflare, do originu idzie
zwykły HTTP przez port przekierowany przez Mikrusa. Uzasadnienie: **D19**.

### 2a. Rekord DNS

W Cloudflare dla `cxlabs.digital` dodaj `audyt` jako **CNAME wskazujący na ten
sam cel, co istniejące subdomeny** tego serwera (podejrzyj `docs` albo `demo`),
z **włączonym proxy** (pomarańczowa chmurka). Bez proxy nie ma HTTPS, bo
certyfikatu na originie nie ma i nie będzie.

`audyt.cxlabs.digital` jest wolna — sprawdzone 2026-09-01, brak rekordu A.

### 2b. Vhost nginx

Skopiuj kształt z `/etc/nginx/sites-enabled/demo` — to najprostszy działający
przykład na tej maszynie. **Porty `listen` muszą się zgadzać z pozostałymi
vhostami**, bo to przez nie Mikrus wpuszcza ruch; podejrzyj je, nie przepisuj
z tego pliku.

```nginx
# /etc/nginx/sites-available/audyt
server {
    listen 80;
    listen [::]:80;
    listen NNNNN;          # port przekierowany, TEN SAM co w pozostałych vhostach
    listen [::]:NNNNN;
    server_name audyt.cxlabs.digital;

    # BEZ `default_server` — ten wpis ma już `docs` i dublowanie wywali nginx.

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        # Do originu przychodzi HTTP. Bez tego aplikacja widzi `http`
        # i nie ma jak się dowiedzieć, że klient jechał po HTTPS.
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/audyt /etc/nginx/sites-enabled/audyt
nginx -t && systemctl reload nginx
```

**`nginx -t` nie jest tu formalnością.** Reload dotyka sześciu cudzych adresów;
błąd składni w naszym pliku kładzie wszystkie. `&&` w tej linii jest po to, żeby
reload nie wykonał się po nieudanym teście.

### 2c. `ADRES_PUBLICZNY`

W `/etc/monday-audit.env`:

```
ADRES_PUBLICZNY=https://audyt.cxlabs.digital
```

**To jedna zmienna i ma znaczenie:** bez niej linki resetu hasła powstają
z `Request.base_url`, czyli z adresu wewnętrznego — za proxy to
`127.0.0.1:8000`. Odbiorca dostałby link, którego nie da się otworzyć.

### 2d. Drogi zapasowe, gdyby powyższe padło

| droga | kiedy | uwaga |
|---|---|---|
| subdomena `mikrus.cloud` | gdy nie chcesz dotykać cudzego nginxa | wymaga IPv6 — **jest**, kontener ma adres globalny (sprawdzone 2026-09-01) i jednostka słucha na `--host ::` |
| tunel `cloudflared` | gdy trzeba ominąć nginx w całości | `https://wiki.mikr.us/podpiecie_domeny_przez_tunel_cloudflare/` |

### Kontrola po tym kroku

Dopiero po kroku 4, gdy usługa działa:

```bash
curl -sI https://audyt.cxlabs.digital | head -3      # z zewnątrz
curl -s http://127.0.0.1:8000/health                 # z serwera
```

Jeśli zewnętrzne żądanie zwraca 502, nginx trafił do originu, ale nikt tam nie
słucha — czyli problem jest w usłudze, nie w proxy. 504 znaczy, że żądanie
przekroczyło `proxy_read_timeout` (domyślnie 60 s); przy pierwszym runie sprawdź,
czy któreś wywołanie panelu tyle trwa.

---

## 3. Kod, sekrety, baza

```bash
useradd -m -s /bin/bash audyt
mkdir -p /opt/monday-audit && chown audyt:audyt /opt/monday-audit
sudo -u audyt git clone https://github.com/jakublvndsky/monday-audit.git /opt/monday-audit
cd /opt/monday-audit && sudo -u audyt uv sync --frozen --no-dev
```

`--no-dev` pomija ruff, mypy i pytest. Zmierzone **na tym serwerze**
2026-09-01: środowisko produkcyjne to **298 MB**, z czego **262 MB** to jeden
plik `claude_agent_sdk/_bundled/claude`. Wcześniejsze „~275 MB / 246 MB"
pochodziło z macOS-a — koło wersji linuksowej, ale nie równe.

> **`--no-dev` trzeba powtarzać przy KAŻDYM wywołaniu `uv run`, nie tylko przy
> `uv sync`.** `uv run` synchronizuje środowisko przed uruchomieniem komendy
> i domyślnie bierze grupę `dev`. Zmierzone tu: po `uv sync --frozen --no-dev`
> środowisko ma 298 MB i zero narzędzi deweloperskich, a jedno `uv run --frozen`
> bez tej flagi dociąga 24 pakiety i robi z tego **405 MB**. Dlatego wszystkie
> polecenia niżej — i `ExecStart` w jednostce systemd — mają `--no-dev`.

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
sudo -u audyt uv run --frozen --no-dev python -c "
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
sudo -u audyt uv run --frozen --no-dev python -m monday_audit.cli_web \
    --dodaj-osobe jle@cxlabs.digital
```

Hasło wypisze się **raz**. Wymagana domena `@cxlabs.digital`.

---

## 5. RAM — O6 ZMIERZONE 2026-09-01, zostaje kontrola pod obciążeniem

O6 było otwarte od początku projektu: *„realna rezerwa na Mikrusie"*. Pierwsze
wejście na serwer ją dało.

| co | wartość | próg |
|---|---|---|
| RAM łącznie | 2048 MB | — |
| **dostępny przy działających cudzych aplikacjach** | **1390 MB** | > 800 MB ✅ |
| swap | 2 GB, nieużywany | patrz niżej |
| dysk wolny | 16 GB z 25 GB | środowisko zajęło 298 MB ✅ |

Dla porównania pomiar lokalny (macOS, 2026-08-25): aplikacja web z detektorami
**71 MB**, podproces `claude` w trakcie analizy **130–210 MB**, szczyt **~280 MB**.
Mieści się z zapasem — **sampling activity logs nie wymaga zawężania**, warunek
z O6 nie zadziałał.

**Czego ten pomiar nie mówi.** Jest zrobiony w spoczynku. Cudze aplikacje
(nginx, dwie na PM2, n8n w Dockerze, dwie usługi Pythona) mogą rosnąć niezależnie
od nas, a szczyt naszego runu i ich szczyt nie muszą wypaść w różnych momentach.
**Powtórz pomiar w trakcie pierwszego prawdziwego runu:**

```bash
watch -n 2 'free -m | head -2; ps -o rss=,comm= --sort=-rss | head -6'
```

**Swap jest włączony i to dotyczy O25.** 2 GB, na razie nietknięte. Punkt 2 z O25
(„strona pamięci z kluczem może wylądować na dysku") przestaje być hipotetyczny —
maszyna ma gdzie swapować. Nie da się tego załatwić kodem aplikacji; właściwą
odpowiedzią pozostaje OAuth z ograniczonym zakresem, nie kolejna warstwa
ostrożności wokół klucza o pełnych uprawnieniach.

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
uv run --frozen --no-dev python evals/brama.py --run <run_id>-agent \
    --zestaw evals/zloty_zestaw/acme_snapshot7.yaml
```

Kody wyjścia: `0` wolno, `1` bloker, `2` decyzja człowieka (regresja wobec
baseline'u). Brama wymienia też, czego **nie** sprawdziła — ta lista nie znaczy
„przeszło".

---

## Co zostaje otwarte po tym wdrożeniu

| pozycja | stan |
|---|---|
| **O6** | **zamknięte 2026-09-01** — 1390 MB dostępne, próg 800 MB. Zostaje kontrola pod obciążeniem (krok 5) |
| **O23** | cztery pytania o dane osobowe pod URL-em; do tego czasu **brak kont klientów** |
| **O25** | zrzuty pamięci (`LimitCORE=0` w jednostce załatwia część) oraz swap — **jest, 2 GB**, więc punkt 2 z O25 jest realny, nie hipotetyczny |
| **O26** | brak „odetnij dostęp teraz"; ręcznie: `aktywne = 0` plus `DELETE FROM sesje` |
| **O29** | SMTP — bez niego link resetu widzi każdy z dostępem do journala |
| powtarzalność 0,797 | próg 0,8, liczba sprzed filtra BOARD_GHOST; dwa runy ≈ 3 USD |
| **serwer współdzielony** | sześć cudzych vhostów, dwie aplikacje PM2, n8n w Dockerze (D19). Każdy `nginx -s reload` i każdy pomiar RAM dotyczy także ich |
| kolejka zadań | audyt żyje w wątku procesu; **restart w trakcie analizy ją niszczy** — sprawdź `zadania` przed restartem |
