# Etap 5 — co zostało wdrożone, jak i dlaczego

> Dokumentacja **wykonania** wdrożenia z 2026-09-01. Specyfikacja etapu jest
> w `05-deploy.md`, instrukcja operacyjna w `deploy/README.md`, decyzje
> w `docs/ARCHITEKTURA.md` (D19, D20), pomiary w `docs/OTWARTE.md` (O6, O25).
> Ten plik odpowiada na „co się faktycznie stało i czego się przy tym
> dowiedzieliśmy".
>
> **Nazwa hosta, port SSH, adres IP i adres IPv6 nie są w tym repo.** Repo jest
> publiczne od 2026-09-01. Wartości żyją w panelu Mikrusa i w `~/.ssh/config`.

## Stan na koniec dnia

| element | stan |
|---|---|
| usługa `monday-audit` | `active`, `enabled`, wstaje po restarcie maszyny |
| nasłuch | **wyłącznie `127.0.0.1:8000`** |
| wejście z zewnątrz | vhost nginx `audyt.cxlabs.digital` → `127.0.0.1:8000` |
| front | serwowany z `front/dist`, identyczny z buildem ze źródeł |
| baza | 12 migracji zastosowanych przy pierwszym starcie |
| `deploy/wdroz.sh` | przechodzi całą drogę: pull → sync → restart → `/health` |
| limit prób startu | 5 prób / 10 min, **potwierdzone przez `systemctl show`** |
| RAM usługi | 63 MB w spoczynku; 1317 MB wolne na maszynie |
| dysk | `.venv` 307 MB + interpreter 108 MB + cache; 15 GB wolne |

> Tabela wyżej to stan z 2026-09-01, gdy panel jeszcze nie był używalny.
> **Domknięcie etapu i wszystkie pomiary z 2026-09-02 są na końcu pliku** —
> adres publiczny, dwa runy produkcyjne, zamknięcie O6 i trzy usterki, które
> wyszły dopiero z prawdziwego runu.

---

## Dopisane 2026-09-02 — adres publiczny i trzy rzeczy, które trzeba rozdzielić

Panel **działał już pod publicznym adresem** i ktoś go otwierał: log nginxa
pokazuje żądania z proxy Mikrusa z refererem `https://audit.cxlabs.digital/`,
w tym `GET /api/ja` → 401 (poprawnie: kont jest zero). Czyli cały łańcuch od
przeglądarki do `127.0.0.1:8000` jest **dowiedziony w praktyce**, nie tylko
skonfigurowany.

Potem adres został przełączony na docelowy i przy tej okazji wyszło, że
„podpięcie domeny" to **trzy niezależne rzeczy**, a ich pomieszanie zmyliło
mnie dwa razy w ciągu jednego dnia:

| co | gdzie | jak sprawdzić |
|---|---|---|
| **rekord DNS** | panel **OVH** (strefa `cxlabs.digital`) | `dig @dns200.anycast.me <host> CNAME +short` |
| **podpięcie hosta u operatora** — wydaje certyfikat dla tej konkretnej nazwy | panel Mikrusa (Cytrus / `backend.strony.me`) | `curl -sv https://<host>` — brak podpięcia daje **zerwane TLS**, nie 404 |
| **vhost** | nasz nginx | `curl -H "Host: <host>" http://127.0.0.1:PORT/health` |

**Dlaczego to nie jest drobiazg.** Każda z tych warstw zawodzi inaczej i objaw
mówi wprost, która to:

- **zerwane uzgadnianie TLS** (`sslv3 alert handshake failure`) — host nie jest
  podpięty u operatora, więc Cloudflare nie ma dla niego certyfikatu. Działający
  `docs.cxlabs.digital` ma `CN=docs.cxlabs.digital` wystawiony przez
  „Cloudflare TLS Issuing ECC CA" — certyfikat jest **per nazwa**, nie dla całej
  domeny
- **404 z gołym `<center>nginx</center>`** — żądanie nie dotarło do nas.
  Rozstrzyga to log: unikalna ścieżka w `curl` z zewnątrz i `grep` w
  `access.log`. Zero trafień = problem jest przed naszym serwerem
- **404 albo 502 z naszej strony** — brak `server_name` albo martwa usługa

**Sprostowanie: strefa DNS jest w OVH, nie w Cloudflare.** Zapisałem
„Cloudflare", bo tak mówiły nagłówki `cf-ray` i `server: cloudflare`. One opisują
**ścieżkę żądania**; strefę trzyma OVH (`dns200.anycast.me`, SOA `tech.ovh.net`).
Cloudflare stoi przed `backend.strony.me` i nie jest niczym, co konfigurujemy —
w panelu OVH nie ma żadnej „pomarańczowej chmurki" do włączenia. Poprawione
w D18, D19 i `deploy/README.md`.

**Adres kanoniczny: `audyt.cxlabs.digital`** (pisownia polska). Przez kilka
godzin panel stał pod `audit` (angielska) — stąd obie pisownie w historii repo.
Zmiana adresu to trzy miejsca: `server_name`, `ADRES_PUBLICZNY` i restart
usługi, żeby to drugie zadziałało.

---

## Dlaczego to nie było „wykonanie README"

Instrukcja z `deploy/README.md` opisywała świeżego Mikrusa i osiem kroków do
odklikania. Maszyna docelowa jest współdzielona z sześcioma cudzymi vhostami,
dwiema aplikacjami PM2 i n8n w Dockerze (**D19**), a sama instrukcja zawierała
usterki, które zatrzymywały wdrożenie w trzech różnych miejscach.

**Dziewięć usterek: trzy z czytania, sześć z kontaktu z maszyną.** Ten podział
jest wart zapisania, bo mówi, czego nie da się wyłapać recenzją.

---

## Część 1 — trzy usterki znalezione z czytania

Wyszły przy odpowiadaniu na pytanie „co jest potrzebne poza kluczem SSH", przed
pierwszym logowaniem. Commit `63ecac5`.

### 1. `front/dist` nigdy nie trafiał do repo

Reguła `dist/` spod nagłówka „Python-generated files" w `.gitignore` łapała
także zbudowany panel. `deploy/README.md` i `wdroz.sh` zakładały, że gotowe
pliki przychodzą z repo, bo na serwerze nie ma Node'a — czyli po `git clone`
FastAPI oddawałoby samo API, bez interfejsu, z ostrzeżeniem w logu.

Naprawione zakotwiczeniem reguły do korzenia (`/dist/`), nie negacją: negacja
pod wykluczonym katalogiem działa nieoczywiście, a tu wystarczył ukośnik.
Sprawdzone w obie strony — `front/dist/index.html` już nie jest ignorowany,
`dist/foo.whl` w korzeniu nadal jest.

### 2. `uv` lądował gdzie indziej, niż szuka go systemd

README instalował `uv` bez `UV_INSTALL_DIR`, czyli do `~/.local/bin` roota,
a jednostka woła `/usr/local/bin/uv` ścieżką bezwzględną — `ExecStart` nie
przeszukuje PATH. Objaw byłby `status=203/EXEC`, komunikat wyglądający na
awarię aplikacji.

### 3. Kontrola po kroku 1 sprawdzała nie to, co trzeba

`uv --version` potwierdzał obecność `uv` w PATH roota, czyli dokładnie to, co
nie ma znaczenia dla usługi startującej jako `audyt`. Teraz sprawdza ścieżkę.

---

## Część 2 — sześć usterek, które wyszły tylko z maszyny

### 4. `uv run` dociągał zależności deweloperskie przy każdym starcie

`--no-dev` przy `uv sync` nie wystarcza. `uv run` **synchronizuje środowisko
przed uruchomieniem komendy** i domyślnie bierze grupę `dev`. Zmierzone
w tej kolejności:

```
uv sync --frozen --no-dev   ->  298 MB, zero narzędzi dev
uv run --frozen             ->  +24 pakiety (ruff, mypy, pytest), 405 MB
uv sync --frozen --no-dev   ->  z powrotem 298 MB
```

`ExecStart` miał wariant bez flagi, więc każdy `systemctl start` i każdy
`Restart=on-failure` sięgałby do sieci po rzeczy, których produkcja nie używa,
i przepisywał środowisko **pod działającą usługą**. Przy niedostępnym indeksie
start mógłby się nie udać z powodu niezwiązanego z aplikacją.

Commit `bb90650`. Poprawione też trzy polecenia kontrolne w README i wywołanie
bramy promocji.

### 5. Limit prób startu nie obowiązywał

`systemd-analyze verify`: *„Unknown key name 'StartLimitIntervalSec' in section
'Service', ignoring."* Oba klucze należą do `[Unit]`, nie `[Service]`.

Zamierzone „po pięciu nieudanych startach przestań próbować" nie działało —
zepsuta konfiguracja dawałaby pętlę restartów co 5 s i zapchany journal, na
maszynie dzielonej z cudzą produkcją.

**Tego nie widać ani w `systemctl start`, ani w `status`.** Dlatego
`systemd-analyze verify` wszedł do README jako krok kontrolny. Commit `611df86`.

### 6. `ProtectHome=true` odcinał `uv` od interpretera

Pierwszy `systemctl start` padł na `Failed to initialize cache at
/home/audyt/.cache/uv: Permission denied`, `status=2/INVALIDARGUMENT`, pętla
restartów.

Komentarz w jednostce twierdził, że „`ProtectHome` nie przeszkadza, bo repo
jest w /opt". Nieprawda podwójnie — `uv` trzyma w katalogu domowym:

| co | rozmiar |
|---|---|
| `~/.local/share/uv/python/…` — **sam interpreter**, `.venv/bin/python` to dowiązanie tam | 108 MB |
| `~/.cache/uv` — cache pobrań | 401 MB |

Naprawienie samego cache'u przesunęłoby awarię o krok dalej, na interpreter.
Zamiast rozluźniać izolację na współdzielonej maszynie, obie ścieżki poszły
tam, gdzie jednostka sięga: `UV_CACHE_DIR` do `/var/cache/monday-audit` przez
`CacheDirectory=` (systemd sam tworzy katalog z właścicielem i dopisuje go do
zapisywalnych), `UV_PYTHON_INSTALL_DIR` do `.uv-python` w katalogu aplikacji.
Commit `6a2ec5c`.

### 7. Panel był wystawiony na globalnym IPv6, z pominięciem Cloudflare i nginxa

**Najpoważniejsze znalezisko dnia.** Jednostka miała `--host ::` z komentarzem
„nasłuchuje na IPv6 ORAZ IPv4 (dual-stack)". Oba twierdzenia były nieprawdziwe.

**To nie był dual-stack.** Python ustawia `IPV6_V6ONLY=1` w
`socket.create_server`, niezależnie od `net.ipv6.bindv6only=0` w systemie
(sprawdzone w źródle modułu). Skutek: `[::1]:8000/health` odpowiadał,
`127.0.0.1:8000/health` nie. A po `127.0.0.1` pytają trzy rzeczy —
`ExecStartPost`, `deploy/wdroz.sh` (przerwałby **każde** wdrożenie
komunikatem „usługa NIE jest zdrowa") oraz `proxy_pass` w nginksie, dostając
502.

**`::` to nie tylko pętla zwrotna.** Panel odpowiadał na globalnym adresie IPv6
kontenera, po zwykłym HTTP. `nftables` ma `policy accept` i zero reguł, więc nic
tego nie zasłaniało. Panel niesie imiona, nazwiska i e-maile pracowników
klienta (O23) — to była realna ekspozycja, nie teoria.

Zmienione na `--host 127.0.0.1`: wejściem z zewnątrz jest nginx (D19), więc
pętla zwrotna wystarcza i usuwa pytanie o ekspozycję w całości. Potwierdzone po
restarcie: globalny adres IPv6 odmawia połączenia. Commit `0737af0`.

Cena: droga zapasowa 2d z README (subdomena `mikrus.cloud`) wymaga nasłuchu na
IPv6 kontenera. Gdyby była potrzebna, wraca `--host ::` — ale wtedy świadomie
i z zaporą.

### 8. `wdroz.sh` nie mógł zrestartować usługi

README każe `sudo -u audyt ./deploy/wdroz.sh`. Skrypt przechodził `git pull`
i `uv sync`, po czym przerywał na `sudo: a password is required`.

`audyt` nie ma i nie powinien mieć ogólnego sudo — to maszyna dzielona
z sześcioma cudzymi aplikacjami, a `audyt ALL=(ALL) NOPASSWD: ALL` dałby
aplikacji roota na cudzej produkcji za restart jednej usługi.

Dodany `deploy/sudoers-monday-audit`: **dwie pełne komendy, nie samo
`systemctl`**. Bez wymienienia argumentów `sudo systemctl restart nginx` też by
przeszedł, a `systemctl` z odpowiednimi argumentami potrafi uruchomić dowolny
program jako root. Sprawdzone, że reguła jest wąska: restart nginxa przez
`audyt` **odmawia**. Commit `4dc9fb8`.

Skrypt zostaje pod `audyt`, bo repo należy do `audyt` — `git pull` robiony
rootem zostawiłby pliki, których `audyt` nie nadpisze.

### 9. `wdroz.sh` mógł przenieść interpreter do `/home` i tym zabić usługę

Skrypt wołał `uv sync` bez `UV_PYTHON_INSTALL_DIR` i `UV_CACHE_DIR`. Działa
**poza** sandboksem jednostki, więc zapis do `/home/audyt` by mu się udał — i to
jest pułapka: wdrożenie zakończyłoby się słowem „wdrożone", a usługa po
restarcie nie wstałaby, bo `ProtectHome=true` nie widzi `/home`.

Nie zdarzyłoby się przy każdym wdrożeniu, tylko wtedy, gdy `uv` musi coś
zainstalować — podbity Python w `.python-version` albo nowa zależność
w `uv.lock`. **Awaria oderwana w czasie od zmiany, która ją spowodowała.**
Commit `edd7458`.

---

## Wzorzec, który powtórzył się w większości z nich

**Narzędzie nie protestowało.** `systemctl start` nie zgłasza kluczy, których
nie rozumie. `uv run` cicho instaluje grupę `dev`. `wdroz.sh` mówi „wdrożone",
zanim usługa spróbuje wstać. `--host ::` wygląda szerzej i bezpieczniej niż
`127.0.0.1`, a znaczy węziej po IPv4 i szerzej po IPv6.

Stąd wniosek do `06-operate.md`: **kontrola musi pytać o skutek, nie o wykonanie
kroku.** `systemctl show StartLimitBurst` zamiast „skopiowałem jednostkę",
`ss -tlnp` zamiast „ustawiłem host", `readlink -f .venv/bin/python` zamiast
„zrobiłem sync".

---

## Moje własne dwa błędy w trakcie

Zapisane, bo obie mogą się powtórzyć.

1. **Pliki roota w `.venv` należącym do `audyt`.** Backticki w moim skrypcie
   przez SSH spowodowały, że powłoka wykonała `uv run` jako root, zanim doszło
   do właściwego polecenia. `uv sync` przestał móc usunąć własne pliki.
   Naprawione przez `rm -rf .venv` i odtworzenie jako `audyt`. To ta sama
   klasa błędu, o którą chroni reguła „skrypt wdrożeniowy działa jako `audyt`".
2. **Odczytałem 404 z nginxa jako brak dopasowania vhosta.** Faktycznie to
   najpewniej stare workery jeszcze obsługujące żądanie w trakcie łagodnego
   przeładowania — kilkanaście sekund później ten sam adres oddawał 200.
   Wniosek: po `systemctl reload nginx` kontrola ma być ponawiana, nie
   jednorazowa.

---

## Czego nie zrobiłem i dlaczego

| co | dlaczego nie ja |
|---|---|
| **CNAME `audyt` w Cloudflare** | nie mam dostępu do konta Cloudflare |
| **`MONDAY_TOKEN` w `/etc/monday-audit.env`** | CLAUDE.md zabrania tokena w kontekście modelu. Pole zostało puste, usługa wstaje bez niego (audyty z panelu biorą klucz z przeglądarki), ale collector bez niego nie pojedzie |
| **Pierwsze konto zespołu** | `--dodaj-osobe` wypisuje hasło **raz**; nie ma powodu, żeby przechodziło przez transkrypt. W bazie jest 0 kont, więc bez tego nie ma jak się zalogować |
| **SMTP (O29)** | poświadczenia są człowieka. Dopóki puste, link resetu hasła trafia tylko do journala |
| **Kopie zapasowe** | `CEL_ZDALNY` musi wskazywać maszynę **poza** Mikrusem, a takiej nie ma. Kopia zawiera `osoby_mapowanie` bez szyfrowania, więc cel musi być prywatny |

**Sól pseudonimizacji** została wygenerowana na serwerze, wewnątrz procesu
Pythona — nie przez argv (widoczne w `ps`) i nie przez kontekst modelu. Plik
`/etc/monday-audit.env` powstał z prawami 600 **przed** wpisaniem czegokolwiek.

---

## Jak sprawdzić, że to nadal stoi

```bash
systemctl is-active monday-audit
systemctl show monday-audit -p StartLimitBurst -p StartLimitIntervalUSec
ss -tlnp | grep 8000                      # MUSI być tylko 127.0.0.1
readlink -f /opt/monday-audit/.venv/bin/python   # NIE może wskazywać do /home
curl -s http://127.0.0.1:8000/health
curl -s -o /dev/null -w '%{http_code}\n' -H "Host: audyt.cxlabs.digital" \
     http://127.0.0.1:PORT/                # PORT przekierowany, z panelu Mikrusa
nginx -t                                   # przed każdym reloadem
```

---

## Domknięcie 2026-09-02 — dwa runy produkcyjne i koniec etapu

### Adres publiczny działa

`https://audyt.cxlabs.digital`, certyfikat `CN=audyt.cxlabs.digital` od Google
Trust Services. Panel, statyki i `/api/ja` (401 bez sesji) sprawdzone z zewnątrz.
Dwa konta zespołu, `audit` usunięty z `server_name` razem z rekordem DNS.

### Kopie zapasowe: lokalne, świadomie niepełne

Katalog `700`, kopia `600` po gzipie, cron 03:15, retencja 14 sztuk.
Uruchomione **dokładnie tak, jak zrobi to cron** — i to wyciągnęło usterkę:
linia z README przekierowywała log do `/var/log/`, zapisywalnego tylko dla grupy
`syslog`. Przekierowanie `>>` zawodzi PRZED uruchomieniem skryptu, czyli kopia
nie powstaje i nie ma o tym żadnego śladu.

`CEL_ZDALNY` pusty, bo nie ma maszyny docelowej. Kopia chroni przed złą migracją
i przypadkowym `DELETE`, **nie** przed utratą dysku. Skrypt mówi to przy każdym
uruchomieniu.

**Test odtworzenia przechodzi** (kod 0) — ale dopiero po pierwszym runie.
Wymaga niezerowych `snapshots` i `runy`, więc na świeżym wdrożeniu MUSI zawieść,
a README kazał go zrobić „przed pierwszym audytem klienta".

### Dwa runy: 12 i 18 znalezisk

| | run 1 | run 2 |
|---|---|---|
| znaleziska | 12 | 18 |
| hipotezy odrzucone | 11 | 19 |
| sygnały | 24 | 38 |
| koszt | 1,54 USD | 2,29 USD |
| czas agenta | 684 s | 1063 s |

Oba `klucz_klienta`, oba bez tracebacków. Różnica liczby znalezisk na tym samym
koncie **dotyka O34** (powtarzalność 0,797 przy progu 0,8) — z dwóch runów
o różnym zakresie sygnałów nie wyciągamy o niej wniosku, ale to ten obszar.

### O6 ZAMKNIĘTE — i pomiar z macOS-a był zaniżony o 60%

201 próbek co 3 s przez cały run drugi:

| | wartość | odniesienie |
|---|---|---|
| **szczyt cgroupy usługi** | **452 MB** | budżet projektowy ~720 MB ✅ |
| minimum wolnej pamięci | **1130 MB** | próg z O6: 800 MB ✅ |
| swap użyty | **0 MB** | — |

**Wniosek ważniejszy niż same liczby:** macOS pokazywał szczyt ~280 MB, Linux
452 MB. Na planie 1.0 (384 MB RAM) ten run **by się nie zmieścił**, a decyzja
o 2.1 stała wyłącznie na pomiarze z macOS-a — była więc słuszna przypadkiem,
nie z dobrego powodu. `systemd` 249 nie ma `MemoryPeak`, więc szczytu nie da się
odczytać po fakcie; trzeba próbkować w trakcie.

Zerowy swap zdejmuje praktyczną ostrość z punktu 2 w O25: strona z kluczem
klienta mogła trafić na dysk tylko przy swapowaniu, a przy 1130 MB rezerwy
maszyna nie swapowała.

### Usterka dziesiąta: `prompt_hash` na ścieżce panelu

Weryfikacja DoD na pierwszym runie pokazała `runy.prompt_hash` puste.
`cli_agent.py` wstawia dziewięć kolumn, `web/run.py` wstawiał siedem — bez tej.
Ścieżką produkcyjną jest panel.

**Trzecia kopia tej samej usterki.** `agent.py` opisuje: „do 2026-08-05
`runy.prompt_hash` był NULL we WSZYSTKICH runach, bo nic go nie ustawiało" —
naprawionej wtedy tylko w CLI, bo panel nie dochodził do zapisu. Obok, w tym
samym pliku, stoi komentarz o dwóch innych usterkach, które przeżyły z dokładnie
tego samego powodu.

Naprawione razem z drugim źródłem prawdy: `model` brany ze stałej `agent.MODEL`
zamiast z literału. Test regresyjny sprawdza **skutek w bazie, nie treść SQL-a**,
i został zweryfikowany w obie strony. Potwierdzone na runie drugim:
`prompt_hash = ca3cb58cccb02d0b`, zgodny z `hash_promptu()` z repo.

**Czego NIE zrobiłem:** nie wpisałem hasha wstecz do runu pierwszego. Run
policzony promptem, którego hasha nie zapisano, a potem uzupełniony „tym, co jest
dziś w pliku", wyglądałby na przypięty, nie będąc nim. NULL uczciwie mówi
„nie wiadomo".

**Pomyłka po mojej stronie:** zgłosiłem też `cennik_ver` jako bug. Nie jest —
`web/run.py:401` ustawia je warunkowo, a w bazie jest zero stawek, więc puste
jest zachowaniem WYMAGANYM przez specyfikację („run bez kwot zostaje z NULL,
żeby nie pinować cudzej daty"). Brak stawek to O28.

### Serwer nie mógł pobrać kodu, choć repo jest publiczne

Wdrożenie poprawki przerwało się na `could not read Username for
'https://github.com'`. Prześledzone: pierwsze żądanie 200, drugie **401**
z `www-authenticate: Basic realm="GitHub"`. `curl` na ten sam adres z tego
samego serwera dostaje 200, a anonimowy `ls-remote` z innego IP działa.

Czyli GitHub odmawia anonimowego `git-upload-pack` z adresu tej maszyny —
najpewniej limit dla współdzielonego IPv4 Mikrusa. Mechanizmu nie potwierdzimy
bez logów GitHuba i **nie ma to znaczenia**: anonimowy dostęp zależy od cudzego
ruchu z tego samego adresu, więc wdrożenie nie może na nim stać. Remote
przełączony na SSH z kluczem wdrożeniowym read-only; klucz hosta przypięty po
porównaniu odcisku z `api.github.com/meta`, nie na ślepo z `ssh-keyscan`.

Objaw jest mylący i dlatego opisany w README osobno: wygląda na brak uprawnień
do repo, a repo jest publiczne.

### `wdroz.sh` restartował usługę bez patrzenia na kolejkę

Ostrzeżenie „restart w trakcie analizy niszczy audyt — sprawdź `zadania`" stało
w README, w sekcji „co zostaje otwarte". Czyli pilnowanie tego było zadaniem
człowieka, który właśnie uruchomił automat, żeby nie musieć niczego pilnować.
Skrypt sprawdza teraz stany `w_kolejce`/`zbieram`/`analizuje` i przerywa,
wypisując zadania, które by zginęły.

### Baza była czytelna dla wszystkich

`monday_audit.db` miał `-rw-r--r--`, bo SQLite tworzy plik z domyślną umask.
W środku `osoby_mapowanie`, hasze haseł i tokeny sesji, na maszynie dzielonej
z sześcioma cudzymi aplikacjami. Naprawione przez `UMask=0077` w jednostce,
nie samym `chmod` — umask obejmuje też przyszłe pliki i `raporty/`.

---

## Co zostaje otwarte po tym etapie

| pozycja | stan |
|---|---|
| **O6** | **ZAMKNIĘTE** — szczyt 452 MB pod obciążeniem, rezerwa 1130 MB |
| **O23** | nietknięte — do rozstrzygnięcia **brak kont klientów** |
| **O25** | punkt 1 zamknięty (`LimitCORE=0`, brak `systemd-coredump`, `core_pattern=core`). Punkt 2: swap jest, ale przy dwóch runach nie użyto ani 1 MB |
| **O28** | zero stawek w bazie, więc findingi wyceniane kwotą wychodzą bez kwoty, a `cennik_ver` zostaje NULL |
| **O29** | SMTP puste — świadomie: konta zakłada CLI, który wypisuje hasło |
| **O34** | dwa runy na tym samym koncie: 12 i 18 znalezisk. Różne zakresy sygnałów, więc nie jest to pomiar powtarzalności — ale to ten obszar |
| kopie poza serwer | `CEL_ZDALNY` pusty, brak maszyny docelowej. Do obcego magazynu potrzebne szyfrowanie, którego `backup.sh` nie ma |
| monitoring `/health` | **nikt go nie pyta.** Endpoint jest dobry, brakuje obserwatora — etap 6 |
| zapora | `nftables` z `policy accept` i zero reguł. Przy nasłuchu na pętli zwrotnej nas to nie dotyczy, ale dotyczy maszyny |
| brama promocji | złoty zestaw jest dla snapshotu `acme`, nie dla runów CXLABS — do przemyślenia przed pierwszym klientem |
| podgląd przed runem | `[WARNING] podgląd dla cxlabs nie wyszedł: ZapytanieError` — audyt przeszedł, podgląd nie |
