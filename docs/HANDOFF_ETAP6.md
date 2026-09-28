# Handoff: stan wdrożenia i co zostało w etapie 6

**Dla kogo:** kolejna sesja pracująca nad tym repo (albo ja za trzy miesiące).
**Stan na:** 2026-09-28, `main` i produkcja na `2bd93e2`.
**Po co:** żeby dało się podjąć pracę bez odtwarzania kontekstu z rozmów.

Ten dokument **nie powtarza** `docs/etapy/05-WYKONANE.md` (przebieg wdrożenia
i dziesięć usterek) ani `deploy/README.md` (pełna instrukcja krok po kroku).
Jest wejściem: co stoi, jak to wdrażać, czego brakuje.

---

## 1. Co stoi na produkcji

| element | stan |
|---|---|
| panel | `https://audyt.cxlabs.digital`, certyfikat `CN=audyt.cxlabs.digital` od Google Trust Services |
| kod | `2bd93e2` — fazy 1–7 i podział repo na pakiety; migracja **14** |
| usługa | `monday-audit.service` — `active`, `enabled`, nasłuch **wyłącznie** na `127.0.0.1:8000`, uruchamia `monday_audit.stary_panel.cli_web` |
| nowe audyty z panelu | **wstrzymane** (`AUDYTY_WSTRZYMANE = True` w `stary_panel/web/api.py`, faza 5c) — dotychczasowe da się przeglądać |
| kontrola zdrowia | `monday-audit-kontrola.timer` — co 5 min, trzy sprawdzenia, ostatnio „bez zastrzeżeń" |
| kopie zapasowe | **brak** — 14 kopii z `/var/backups/monday-audit` usunięte, cron kopii w crontabie `audyt` **wyłączony** (zakomentowany), decyzja Kuby 2026-09-25 |
| baza | 5 kont panelu, 6 runów, 3 snapshoty, 101 wierszy `osoby_mapowanie`, 47 findingów — dane starej ścieżki, zostają do decyzji |

**Czym jest ten panel dziś.** To **stary panel** z etapów 3–5 (snapshot trwały,
pętla per hipoteza, findingi z wagami). Docelowo zastąpi go portal (decyzja
Kuby 2026-09-25) i wtedy idzie do usunięcia w całości. Nowa ścieżka
(`monday_audit.usluga`, `monday_audit.cli.analiza`) leży na serwerze, ale
nikt jej tam nie uruchamia: analizy idą z maszyny operatora, a `MONDAY_TOKEN`
na serwerze jest pusty. Pod dostępy powstanie nowa baza; logowanie później.

**Serwer:** Mikrus, kontener LXC dzielony z sześcioma cudzymi vhostami, dwiema
aplikacjami PM2 i n8n w Dockerze (**D19**). Nazwa hosta, port SSH, IP i adres
IPv6 **świadomie nie są w repo** — żyją w panelu Mikrusa i w `~/.ssh/config`
(wpis `Host mikrus`).

**Droga żądania:** przeglądarka → Cloudflare (terminuje TLS) → Cytrus
(`backend.strony.me`, proxy Mikrusa) → nginx na serwerze → `127.0.0.1:8000`.
Strefa DNS jest w **OVH**, nie w Cloudflare.

---

## 2. Jak wdrożyć

```bash
ssh mikrus 'cd /opt/monday-audit && sudo -u audyt ./deploy/wdroz.sh < /dev/null'
```

Skrypt: stan repo → kolejka zadań → `git pull --ff-only` → `uv sync --frozen
--no-dev` → kolejka drugi raz → porównanie konfiguracji poza kodem → restart →
czekanie na `/health`. Przerywa przy pierwszym niepowodzeniu.

### Cztery rzeczy, o które się potykaliśmy

**`< /dev/null` nie jest ozdobą.** Bez tego `ssh` zjada resztę polecenia ze
standardowego wejścia i kolejne kroki po cichu się nie wykonują.

**Zmiana w samym `wdroz.sh` wymaga DWÓCH przebiegów.** Pierwszy używa jeszcze
starej wersji skryptu i dopiero pobiera nową. Zdarzyło się to dwa razy tego
samego dnia.

**`wdroz.sh` wdraża KOD, nie konfigurację.** Jednostki systemd, vhost nginxa
i reguła sudo leżą poza repo i skrypt ich **nie kopiuje** — porównuje je i mówi
o rozjeździe, ale instalacja jest ręczna:

```bash
cp deploy/monday-audit*.service deploy/monday-audit*.timer /etc/systemd/system/
systemctl daemon-reload && systemctl restart monday-audit-kontrola.timer
# vhost: podstaw port (deploy/README.md krok 2b), potem nginx -t && systemctl reload nginx
```

**Zmiana jednostki usługi idzie PRZED restartem, nie po.** Gdy jednostka
wskazuje moduł, który w nowym kodzie zmienił ścieżkę, `wdroz.sh` zrestartuje
usługę ze STARĄ jednostką i panel nie wstanie. Tak było przy podziale repo
(2026-09-25: `monday_audit.cli_web` → `monday_audit.stary_panel.cli_web`).
Kolejność, która zadziałała:

```bash
sudo -u audyt git pull --ff-only                 # nowy kod i nowa jednostka w repo
diff /etc/systemd/system/monday-audit.service deploy/monday-audit.service
cp deploy/monday-audit.service /etc/systemd/system/ && systemctl daemon-reload
sudo -u audyt ./deploy/wdroz.sh < /dev/null      # pull bez zmian, sync, restart, /health
```

Poprzednia wersja jednostki leży w `/root/monday-audit.service.przed-podzialem`.

Komunikat „reguła sudo — nieczytelny dla audyt, NIE sprawdziłem" jest
**poprawny**: plik ma prawa `440 root:root`, a skrypt biegnie jako `audyt`.
Mówi, że nie sprawdził, zamiast udawać, że sprawdził.

### Kod idzie po SSH, nie po HTTPS

Repo jest publiczne, ale anonimowy `git-upload-pack` **z tego serwera** dostaje
od GitHuba 401 (pierwsze żądanie 200, drugie 401 — `curl` na ten sam adres
dostaje 200, a anonimowy `ls-remote` z innego IP działa). Najpewniej limit dla
współdzielonego IPv4 Mikrusa. Remote stoi na SSH z kluczem wdrożeniowym
**read-only**; klucz hosta GitHuba przypięty po porównaniu odcisku
z `api.github.com/meta`.

---

## 3. Co zostało zrobione

Nie przepisuję — wskazuję, gdzie to jest.

| gdzie | co znajdziesz |
|---|---|
| `docs/etapy/05-WYKONANE.md` | przebieg wdrożenia, **dziesięć usterek** z mechanizmami i dowodami, pomiary |
| `deploy/README.md` | pełna instrukcja od pustej maszyny, z uzasadnieniem każdego nieoczywistego kroku |
| `docs/ARCHITEKTURA.md` **D19, D20** | serwer współdzielony i HTTPS przez cudzy nginx; brak Dockera jako decyzja |
| `docs/OTWARTE.md` **O6, O25** | pomiary RAM (szczyt 452 MB pod obciążeniem) i zrzuty pamięci |
| `docs/etapy/06-operate.md` | kolejka zerowa Z1–Z5, czyli to, co niżej |
| `docs/plan.md` | fazy przebudowy 1–7 (zamknięte) i 5c (otwarta na decyzjach o danych) |

**Jedna liczba warta zapamiętania:** pomiar RAM z macOS-a (280 MB) był zaniżony
o 60% wobec Linuksa (452 MB). Na planie Mikrus 1.0 ten run by się nie zmieścił —
decyzja o 2.1 była słuszna **przypadkiem**, nie z dobrego powodu.

---

## 4. Co zostało do zrobienia

Kolejka zerowa z `06-operate.md`. **Każda pozycja czeka na człowieka** — kod
po naszej stronie jest gotowy albo nie jest potrzebny.

| # | co | kto | stan |
|---|---|---|---|
| **Z1** | **O23** — cztery pytania o dane osobowe pod URL-em: wygasanie dostępu, kasowanie konta po relacji, logi wejść, nazwiska w adresie | Kuba | otwarte. Panel jest **tylko dla zespołu**, a nowe audyty są wstrzymane — pytania wrócą przy portalu |
| **Z2** | **baseline bramy promocji** — złoty zestaw jest dla snapshotu `acme`, nie dla runów CXLABS | Kuba, potem kod | brama istnieje, ale **nie ma dziś czego przepuścić**. Dotyczy starej ścieżki; nowa ma inny kształt wyniku (uwagi bez wag) |
| **Z3** | **kopia poza serwerem** | — | **nieaktualne w obecnym kształcie**: kopii nie ma wcale (decyzja 2026-09-25). Wraca przy nowej bazie dostępów |
| **Z4** | **czuwak `/health`** | Kuba (konto), potem jedna linia w pliku | **kod gotowy**, brakuje `URL_CZUWAKA` — w `/etc/monday-audit.env` nie ma nawet tej linii. Kroki niżej |
| **Z5** | **`nftables` z `policy accept` i zero reguł** | właściciel maszyny | panel słucha na pętli zwrotnej, więc nas nie dotyka — ale dotyczy maszyny |

### Z4 — co dokładnie zostało

Kontrola działa **na tej samej maszynie** co panel, więc nie wykryje śmierci
maszyny ani zerwanej sieci: nie ma jej wtedy kto uruchomić. Domyka to czuwak —
serwer pinguje **na zewnątrz** po udanej kontroli, a usługa zewnętrzna krzyczy,
gdy pingi ustaną. Odwrócenie kierunku jest całą sztuczką: nie wymaga otwartych
portów.

Po stronie kodu wszystko stoi (`deploy/kontrola-zdrowia.sh`): skrypt czyta
`URL_CZUWAKA` z `/etc/monday-audit.env`, pinguje **tylko** przy pełnym sukcesie
i tylko wtedy, gdy sprawdził też adres publiczny (`ADRES_PUBLICZNY` jest
ustawiony). Brakuje:

1. **Konto i sprawdzenie w usłudze czuwaka** (np. healthchecks.io, plan
   darmowy): okres **5 min** (tyle co timer), zapas **~10 min** — kontrola przy
   problemie powtarza się po pauzie, a wdrożenie potrafi trafić w okno
   restartu. Powiadomienie na maila albo Slacka zespołu. Konto zakłada
   człowiek.
2. **Wpis na serwerze** — URL czuwaka jest sekretem (kto go zna, może pingować
   za serwer), więc **nie do jednostki systemd, nie do repo, nie do rozmowy**:

   ```bash
   ssh mikrus
   sudoedit /etc/monday-audit.env      # dopisz: URL_CZUWAKA=https://hc-ping.com/…
   sudo -u audyt /opt/monday-audit/deploy/kontrola-zdrowia.sh
   ```

   Oczekiwany wynik: znika ostrzeżenie „URL_CZUWAKA nieustawiony", jest
   „kontrola zdrowia: bez zastrzeżeń", a w usłudze czuwaka pierwszy ping.
   Restart usługi niepotrzebny — skrypt czyta plik przy każdym uruchomieniu.

---

## 5. Konfiguracja ręczna, której nie ma w repo

Nie jest pusta i nie może być — to jest ta część, która ginie między sesjami.

| co | gdzie | uwaga |
|---|---|---|
| `MONDAY_TOKEN` | `/etc/monday-audit.env` | **puste i tak ma być** — panel bierze klucz z przeglądarki, a analizy nowej ścieżki idą z maszyny operatora |
| `SOL_PSEUDONIMIZACJI` | `/etc/monday-audit.env` | ustawione, wygenerowane na serwerze wewnątrz procesu — nigdy nie przeszło przez argv ani przez rozmowę |
| `SMTP_*` | `/etc/monday-audit.env` | puste świadomie: konta zakłada CLI, który wypisuje hasło (O29) |
| `ADRES_PUBLICZNY` | `/etc/monday-audit.env` | ustawione — bez niego czuwak nie dostałby pingu |
| `URL_CZUWAKA` | `/etc/monday-audit.env` | **brakuje** — to jest Z4 |
| jednostki systemd | `/etc/systemd/system/monday-audit*.{service,timer}` | kopie z `deploy/`, instalowane ręcznie (sekcja 2); zgodne z repo na `2bd93e2` |
| crontab `audyt` | `crontab -u audyt -l` | linia `backup.sh` **zakomentowana** 2026-09-25 — kopii nie ma świadomie |
| rekord DNS | panel OVH | CNAME `audyt` → `backend.strony.me.` |
| podpięcie hosta | panel Mikrusa | **osobna rzecz od DNS** — to ono wydaje certyfikat. Brak podpięcia daje **zerwane TLS**, nie 404 |
| klucz wdrożeniowy | GitHub → Settings repo → Deploy keys | read-only, bez `Allow write access` |

---

## 6. Jak sprawdzić, że wszystko stoi

```bash
# z dowolnej maszyny
curl -s https://audyt.cxlabs.digital/health        # {"status":"ok","migracja":14}

# na serwerze
systemctl is-active monday-audit monday-audit-kontrola.timer
systemctl show monday-audit -p ExecStart | grep -o 'monday_audit[a-z_.]*'   # monday_audit.stary_panel.cli_web
ss -tlnp | grep 8000                               # MUSI być tylko 127.0.0.1
readlink -f /opt/monday-audit/.venv/bin/python     # NIE może wskazywać do /home
sudo -u audyt /opt/monday-audit/deploy/kontrola-zdrowia.sh
```

**Pytaj o skutek, nie o wykonanie kroku.** `systemctl show <klucz>` zamiast
zajrzenia do pliku jednostki — tak wyszło, że `StartLimitBurst` stał w złej
sekcji i że `Persistent=true` było ignorowane. Dwa razy ta sama klasa błędu.

---

## 7. Wzorzec, który powtórzył się najczęściej

**Narzędzie nie protestuje.** `systemctl start` pomija klucze, których nie
rozumie. `uv run` cicho instaluje grupę `dev`. `wdroz.sh` mówił „wdrożone",
zanim usługa spróbowała wstać. `curl -w '%{http_code}'` sam wypisuje `000`, więc
dopisane `|| echo "000"` dawało `000000` i cała gałąź obsługi awarii była
martwym kodem.

Stąd zasada, którą warto utrzymać: **kontrola ma pytać o skutek**, a gdy nie
może czegoś sprawdzić — **powiedzieć to głośno**, zamiast milczeć. Cicha utrata
kontroli jest gorsza niż jej brak, bo wygląda identycznie jak sukces.

---

## 8. Czego ten dokument świadomie nie rozstrzyga

Portal (tryby abonamentowe, czat, magazyn klucza, harmonogram) opisują
`docs/HANDOFF_PORTAL.md` i `docs/NOTATKA_PORTAL_DECYZJE.md`. Jedna rzecz stamtąd
dotyczy jednak tego dokumentu wprost: **gdy portal zastąpi stary panel,
`deploy/` przestaje być ścieżką produkcyjną.** Jednostki systemd, `wdroz.sh`
i kontrola zdrowia opisują wdrożenie **tej** makiety na Mikrusie i nie są
instrukcją dla portalu.

Otwarte decyzje o danych na serwerze (żywa baza starej ścieżki) są w
`docs/plan.md`, faza 5c.

`STATUS.md` należy do człowieka. Projekt jest w **etapie 6 (Operate)**; ten
dokument niczego nie przesuwa.
