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
przeglądarka ─HTTPS─> Cloudflare ─> Cytrus (backend.strony.me) ─HTTP─> nginx ─> 127.0.0.1:8000
                    (terminuje TLS)      (proxy Mikrusa)         (port przekierowany)
```

Na serwerze nie ma `cloudflared`. TLS terminuje Cloudflare, do originu idzie
zwykły HTTP. Uzasadnienie: **D19**.

### 2a. Rekord DNS — strefa jest w OVH, NIE w Cloudflare

> **Poprawka 2026-09-02.** Ten krok mówił „w Cloudflare dodaj rekord". Błąd
> pomiaru z mojej strony: wniosek o Cloudflare brał się z nagłówków `cf-ray`
> i `server: cloudflare` przy `docs.cxlabs.digital`. To prawda o **ścieżce
> żądania**, nie o strefie DNS. Autorytatywne serwery dla `cxlabs.digital` to
> `dns200.anycast.me` i `ns200.anycast.me`, czyli **OVH** — sprawdzone przez
> `dig NS` i `SOA` (`tech.ovh.net`). Cloudflare stoi wyłącznie przed
> `backend.strony.me`, czyli przed proxy Mikrusa, i nie jest niczym, co
> konfigurujemy.

W panelu **OVH**, w strefie `cxlabs.digital`, rekord wygląda tak samo jak
`docs` i `demo`:

| pole | wartość |
|---|---|
| typ | `CNAME` |
| nazwa | `audyt` |
| cel | `backend.strony.me.` |

**Sam rekord DNS NIE wystarczy.** Host trzeba jeszcze **podpiąć u operatora
Mikrusa** — to ono wydaje certyfikat dla tej konkretnej nazwy. Zmierzone
2026-09-02: z rekordem DNS, ale bez podpięcia, `curl` kończy się
`sslv3 alert handshake failure`, a nie 404. Po podpięciu certyfikat to
`CN=audyt.cxlabs.digital` od Google Trust Services. Na originie nie ma
i nie będzie żadnego certyfikatu.

**Adres kanoniczny to `audyt.cxlabs.digital`, pisownia polska** — decyzja
z 2026-09-02. Po drodze powstał najpierw rekord `audit` (angielski) i przez
chwilę panel stał pod nim; zapisane, żeby nikt nie szukał powodu, gdy
w historii repo zobaczy obie pisownie.

**Kontrola, czy rekord w ogóle istnieje** — pytaj serwer autorytatywny, nie
swój resolver, bo cache pokazuje stan sprzed zmiany:

```bash
dig @dns200.anycast.me audyt.cxlabs.digital CNAME +short   # ma zwrócić backend.strony.me.
dig @dns200.anycast.me audyt.cxlabs.digital A              # NXDOMAIN = rekordu nie ma
```

### 2b. Vhost nginx

Gotowy plik jest w repo: **`deploy/nginx-audyt.conf`**, z komentarzami przy
każdej nieoczywistej linii. Jedyne, co trzeba podmienić, to `NNNNN` — **port
przekierowany musi się zgadzać z pozostałymi vhostami**, bo to przez niego
Mikrus wpuszcza ruch. Podejrzyj go na maszynie, nie przepisuj z dokumentacji:

```bash
grep -h listen /etc/nginx/sites-enabled/* | sort -u
```

```bash
cp deploy/nginx-audyt.conf /etc/nginx/sites-available/audyt
nano /etc/nginx/sites-available/audyt          # podmień NNNNN
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
| subdomena `mikrus.cloud` | gdy nie chcesz dotykać cudzego nginxa | wymaga nasłuchu na globalnym IPv6 kontenera. Adres **jest**, ale jednostka celowo słucha tylko na `127.0.0.1` — trzeba by wrócić do `--host ::` i **najpierw postawić zaporę**, bo nftables ma `policy accept` bez reguł (patrz komentarz w jednostce) |
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

### Najpierw klucz wdrożeniowy — anonimowy HTTPS tu nie wystarcza

Repo jest publiczne, więc `git clone https://…` wygląda na wystarczające.
**Nie jest** — sprawdzone 2026-09-02. Z tego serwera anonimowy `git-upload-pack`
dostaje od GitHuba **401** i `www-authenticate: Basic realm="GitHub"`, przez co
`git pull` pyta o hasło i przerywa wdrożenie. Pierwsze żądanie (`info/refs`)
przechodzi z kodem 200, dopiero drugie jest odrzucane — a `curl` na ten sam
adres dostaje 200, więc objaw wygląda na problem z uprawnieniami do repo i nim
nie jest.

Ten sam anonimowy `ls-remote` z innego adresu IP działa. Najprawdopodobniej to
limit GitHuba dla współdzielonego IPv4 Mikrusa, na którym siedzi wiele
kontenerów. Mechanizmu nie potwierdzimy bez logów GitHuba i **nie ma to
znaczenia**: anonimowy dostęp jest kruchy i zależy od cudzego ruchu z tego
samego adresu, więc wdrożenie nie może na nim stać.

```bash
# Klucz BEZ hasła — usługa pobiera kod bez człowieka przy klawiaturze.
# Dlatego w GitHubie dodaj go jako Deploy key TEGO repo, `Allow write access`
# WYŁĄCZONE: wyciek daje wtedy tyle, ile i tak jest jawne.
sudo -u audyt install -d -m 700 /home/audyt/.ssh
sudo -u audyt ssh-keygen -t ed25519 -N "" -C "deploy-monday-audit@$(hostname)" \
    -f /home/audyt/.ssh/id_ed25519

# Klucz hosta przypinamy PO SPRAWDZENIU odcisku u źródła, nie na ślepo:
# ssh-keyscan bierze to, co przyjdzie po sieci, a api.github.com/meta podaje
# odciski po HTTPS z certyfikatem.
curl -s https://api.github.com/meta | grep -A4 ssh_key_fingerprints
ssh-keyscan -t ed25519 github.com | sudo -u audyt tee -a /home/audyt/.ssh/known_hosts
sudo -u audyt ssh-keygen -lf /home/audyt/.ssh/known_hosts   # porównaj z SHA256_ED25519

cat /home/audyt/.ssh/id_ed25519.pub    # to wklejasz w GitHubie
```

Sprawdzenie, że działa — ma odpowiedzieć nazwą repo, nie nazwą konta:

```bash
sudo -u audyt ssh -T git@github.com < /dev/null
# Hi jakublvndsky/monday-audit! You've successfully authenticated…
```

`< /dev/null` nie jest ozdobą: bez tego `ssh` zjada resztę skryptu ze
standardowego wejścia i kolejne polecenia po cichu nie wykonują się.

### Dopiero teraz kod

```bash
useradd -m -s /bin/bash audyt
mkdir -p /opt/monday-audit && chown audyt:audyt /opt/monday-audit
sudo -u audyt git clone git@github.com:jakublvndsky/monday-audit.git /opt/monday-audit
mkdir -p /var/cache/monday-audit && chown audyt:audyt /var/cache/monday-audit
cd /opt/monday-audit && sudo -u audyt env \
    UV_PYTHON_INSTALL_DIR=/opt/monday-audit/.uv-python \
    UV_CACHE_DIR=/var/cache/monday-audit \
    /usr/local/bin/uv sync --frozen --no-dev
```

> **Te dwie zmienne nie są ozdobą i muszą być TAKIE SAME jak w jednostce
> systemd.** `uv` domyślnie trzyma w katalogu domowym nie tylko cache, ale
> **sam interpreter** — `.venv/bin/python` to dowiązanie do
> `~/.local/share/uv/python/…`. Jednostka ma `ProtectHome=true`, więc nie
> zobaczyłaby ani jednego, ani drugiego. Pierwszy start na tym serwerze padł
> dokładnie na tym: `Failed to initialize cache at /home/audyt/.cache/uv:
> Permission denied`. Bez `UV_PYTHON_INSTALL_DIR` naprawienie samego cache'u
> przesunęłoby awarię o jeden krok dalej, na interpreter.

`--no-dev` pomija ruff, mypy i pytest. Zmierzone **na tym serwerze**
2026-09-01 — i całość jest **trzy razy większa, niż mówił budżet dysku**, bo
budżet liczył samo `.venv`:

| co | gdzie | rozmiar |
|---|---|---|
| `.venv` | `/opt/monday-audit/.venv` | **298 MB** |
| z tego `claude_agent_sdk/_bundled/claude` | — | 262 MB (jeden plik) |
| interpreter pobrany przez `uv` | `.uv-python` | **108 MB** |
| cache `uv` | `/var/cache/monday-audit` | **do ~400 MB** |
| **razem** | | **~800 MB** |

Wcześniejsze „~275 MB" pochodziło z macOS-a i dotyczyło wyłącznie `.venv`.
Przy 16 GB wolnego to nadal bez znaczenia, ale na planie 1.0 (5 GB) byłaby to
różnica między „6% dysku" a „16%". Cache można potem przyciąć przez
`uv cache prune`.

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

# Bez tego `deploy/wdroz.sh` przerwie na restarcie: działa jako `audyt`,
# a restart wymaga roota. Reguła wymienia pełne polecenia, nie samo
# `systemctl` — uzasadnienie w nagłówku pliku.
# Walidacja PRZED instalacją, nie po. Błąd składni w /etc/sudoers.d/ psuje
# `sudo` dla WSZYSTKICH użytkowników maszyny — łącznie z tym, który miałby
# to cofnąć. Sprawdzamy więc kopię w repo i dopiero sprawdzony plik kładziemy
# na miejsce.
visudo -c -f deploy/sudoers-monday-audit \
    && install -m 440 -o root -g root deploy/sudoers-monday-audit /etc/sudoers.d/monday-audit
```

**Sprawdź jednostkę przez `systemd-analyze verify`, nie tylko przez
`systemctl status`:**

```bash
systemd-analyze verify monday-audit.service
```

`systemctl start` **nie zgłasza** kluczy, których nie rozumie — po prostu je
pomija. Tak przeszła niezauważona zła sekcja `StartLimitIntervalSec`
(poprawione 2026-09-01): limit prób startu wyglądał na ustawiony i nie
obowiązywał. Kontrola, że obowiązuje teraz:

```bash
systemctl show monday-audit -p StartLimitBurst -p StartLimitIntervalUSec
# StartLimitIntervalUSec=10min
# StartLimitBurst=5
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
sudo -u audyt /usr/local/bin/uv run --frozen --no-dev python -m monday_audit.cli_web \
    --plik-env /etc/monday-audit.env \
    --dodaj-osobe jle@cxlabs.digital
```

Hasło wypisze się **raz**. Wymagana domena `@cxlabs.digital`.

> **`--plik-env` jest obowiązkowe i wcześniej go tu nie było.** Bez niego
> komenda kończy się `KonfiguracjaError: konfiguracja niekompletna
> [MONDAY_TOKEN: brak; SOL_PSEUDONIMIZACJI: brak]` — sprawdzone na serwerze
> 2026-09-02. Powód: sekrety żyją w `/etc/monday-audit.env`, a ten plik podaje
> **jednostka systemd** przez `EnvironmentFile`. Wywołanie z powłoki nie
> przechodzi przez systemd, więc `wczytaj()` szuka domyślnego
> `/opt/monday-audit/.env`, którego nie ma i nie ma go być.
>
> Dotyczy **każdego** ręcznego wywołania `cli_web` i `cli`, nie tylko tego.
> Alternatywnie `MONDAY_AUDIT_ENV_FILE=/etc/monday-audit.env` w środowisku.
>
> Osobna sprawa, warta zapisania: `--dodaj-osobe` ładuje pełne `Ustawienia`,
> więc **wymaga `MONDAY_TOKEN` i `SOL_PSEUDONIMIZACJI`, choć zakłada tylko
> konto w panelu**. Puste `MONDAY_TOKEN=` w pliku wystarcza (pydantic widzi
> klucz, nie brak), ale to przypadek, nie projekt. Uporządkowanie tego to
> zmiana w `uruchom()`, nie w instrukcji.

---

## 4b. Kontrola zdrowia — bo `Restart=on-failure` nikogo nie powiadamia

Usługa sama się wskrzesza, ale **po pięciu nieudanych próbach w 10 minut systemd
przestaje próbować** i nikt się o tym nie dowie. To pozycja **Z4** z etapu 6.

```bash
cp deploy/monday-audit-kontrola.service /etc/systemd/system/
cp deploy/monday-audit-kontrola.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now monday-audit-kontrola.timer
```

**Kontrola:**

```bash
systemctl start monday-audit-kontrola.service   # jednorazowo, na żądanie
journalctl -u monday-audit-kontrola -n 20 --no-pager
systemctl list-timers monday-audit-kontrola.timer
```

Skrypt sprawdza **trzy rzeczy, nie jedną**:

| co | co wykrywa |
|---|---|
| `/health` po pętli zwrotnej | martwa usługa, baza która się nie otwiera |
| numer migracji vs liczba plików `.sql` w kodzie | baza w tyle za kodem — awaria cicha, bo panel wstaje, a zapytanie wywala się w trakcie audytu |
| `/health` pod publicznym adresem | **całą drogę**: DNS, certyfikat, proxy operatora, nginx |

Trzeci punkt jest osobny celowo. 2026-09-02 zdarzyło się dokładnie to, co on
wykrywa: aplikacja odpowiadała lokalnie, a z zewnątrz przychodziło 404, bo host
przestał być podpięty u operatora. Kontrola pytająca tylko lokalnie pokazałaby
wtedy „wszystko w porządku".

### Czego ta kontrola NIE potrafi — i co z tym zrobić

Działa **na tej samej maszynie** co panel, więc **nie wykryje śmierci maszyny
ani zerwanej sieci** — nie ma jej wtedy kto uruchomić. Monitor, który milczy
razem z tym, co monitoruje, jest wart tyle, co jego brak.

Domyka to **czuwak** (dead man's switch): serwer pinguje **na zewnątrz** po
każdej udanej kontroli, a usługa zewnętrzna krzyczy, gdy pingi ustaną.
Odwrócenie kierunku jest tu całą sztuczką — nie wymaga otwartych portów i łapie
awarię, której lokalny monitor z definicji nie zgłosi.

```
URL_CZUWAKA=https://…        # w /etc/monday-audit.env, NIE w jednostce
```

W jednostce nie, bo `systemctl show` pokazuje `Environment=` każdemu na
maszynie, a URL czuwaka jest sekretem: kto go zna, może pingować za serwer
i udawać, że wszystko żyje.

**Dopóki `URL_CZUWAKA` jest pusty, Z4 nie jest domknięte.** Wynik kontroli
trafia wyłącznie do journala i do `systemctl --failed` — czyli tam, gdzie
trzeba zajrzeć z własnej woli. Skrypt mówi to przy każdym uruchomieniu i ma
mówić, dopóki czuwak nie zostanie skonfigurowany.

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

**Dwa przygotowania jako root.** Pierwsze to prawa, drugie to plik, którego
`audyt` nie może utworzyć sam:

```bash
# 700, bo w kopiach są dane osobowe pracowników klienta
install -d -m 700 -o audyt -g audyt /var/backups/monday-audit

# /var/log jest zapisywalny tylko dla grupy `syslog`, więc `audyt` NIE utworzy
# tam pliku. Przekierowanie `>>` w cronie zawodzi PRZED uruchomieniem skryptu,
# czyli kopia nie powstaje i nie ma o tym śladu. Sprawdzone 2026-09-02.
install -m 640 -o audyt -g audyt /dev/null /var/log/monday-audit-backup.log
```

```bash
crontab -u audyt -e
```

```cron
# Kopia bazy audytu, codziennie 03:15.
15 3 * * * /opt/monday-audit/deploy/backup.sh >> /var/log/monday-audit-backup.log 2>&1
```

**`CEL_ZDALNY` świadomie pusty — stan na 2026-09-02.** Nie ma maszyny
docelowej poza Mikrusem, więc kopia zostaje na serwerze. To **nie jest kopia
zapasowa w pełnym sensie**: chroni przed złą migracją, przypadkowym `DELETE`
i pomyłką człowieka, ale awaria dysku zabierze oryginał i kopię razem. Skrypt
wypisuje to ostrzeżenie przy każdym uruchomieniu i **ma je wypisywać** — dzień,
w którym przestanie, to dzień, w którym ktoś podał `CEL_ZDALNY`.

Ściąganie na maszynę deweloperską jest po stronie człowieka:

```bash
scp mikrus:/var/backups/monday-audit/'monday_audit_*.db.gz' ~/kopie-audytu/
```

Docelowo `CEL_ZDALNY` w linii crona. Do obcego magazynu obiektowego
**nie wysyłaj tego bez szyfrowania** — `backup.sh` go nie ma, kopia jest
w środku zwykłą bazą z nazwiskami.

**Test odtworzenia — RAZ, RĘCZNIE, ale NIE PRZED pierwszym runem:**

```bash
gunzip -k /var/backups/monday-audit/monday_audit_*.db.gz
./deploy/backup.sh --sprawdz /var/backups/monday-audit/monday_audit_*.db
```

Sprawdza integralność **i zawartość**. Sam `integrity_check` przepuszcza pustą,
poprawną bazę — a to jest gorsze niż brak kopii, bo wygląda na kopię.

> **Kolejność ma znaczenie i jest nieoczywista.** Test wymaga niezerowych
> `snapshots`, `runy` i `_migracje`. Na świeżym wdrożeniu `snapshots` i `runy`
> są puste, więc test kończy się **kodem 1 i komunikatem `BŁĄD: snapshots jest
> pusta`** — sprawdzone 2026-09-02 na prawdziwej kopii z tego serwera. To skrypt
> działający poprawnie, nie awaria: pusta baza NIE jest kopią, którą warto mieć.
> Test odtworzenia wykonaj więc **po** pierwszym runie produkcyjnym (krok 8),
> nie przed.

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
