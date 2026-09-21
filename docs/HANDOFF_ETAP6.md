# Handoff: stan wdrożenia i co zostało w etapie 6

**Dla kogo:** kolejna sesja pracująca nad tym repo (albo ja za trzy miesiące).
**Stan na:** 2026-09-21, `main` na `9332643`.
**Po co:** żeby dało się podjąć pracę bez odtwarzania kontekstu z rozmów.

Ten dokument **nie powtarza** `docs/etapy/05-WYKONANE.md` (przebieg wdrożenia
i dziesięć usterek) ani `deploy/README.md` (pełna instrukcja krok po kroku).
Jest wejściem: co stoi, jak to wdrażać, czego brakuje.

---

## 1. Co stoi na produkcji

| element | stan |
|---|---|
| panel | `https://audyt.cxlabs.digital`, certyfikat `CN=audyt.cxlabs.digital` od Google Trust Services |
| usługa | `monday-audit.service` — `active`, `enabled`, nasłuch **wyłącznie** na `127.0.0.1:8000` |
| kontrola zdrowia | `monday-audit-kontrola.timer` — co 5 min, trzy sprawdzenia |
| kopie zapasowe | cron 03:15, lokalnie w `/var/backups/monday-audit`, retencja 14 |
| konta zespołu | 2 |
| runy produkcyjne | 2 (12 i 18 znalezisk; 1,54 i 2,29 USD) |

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

### Trzy rzeczy, o które się potykaliśmy

**`< /dev/null` nie jest ozdobą.** Bez tego `ssh` zjada resztę polecenia ze
standardowego wejścia i kolejne kroki po cichu się nie wykonują.

**Zmiana w samym `wdroz.sh` wymaga DWÓCH przebiegów.** Pierwszy używa jeszcze
starej wersji skryptu i dopiero pobiera nową. Zdarzyło się to dwa razy tego
samego dnia.

**`wdroz.sh` wdraża KOD, nie konfigurację.** Jednostki systemd, vhost nginxa
i reguła sudo leżą poza repo i skrypt ich **nie kopiuje** — od 2026-09-21
porównuje je i mówi o rozjeździe, ale instalacja jest ręczna:

```bash
cp deploy/monday-audit*.service deploy/monday-audit*.timer /etc/systemd/system/
systemctl daemon-reload && systemctl restart monday-audit-kontrola.timer
# vhost: podstaw port (deploy/README.md krok 2b), potem nginx -t && systemctl reload nginx
```

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

**Jedna liczba warta zapamiętania:** pomiar RAM z macOS-a (280 MB) był zaniżony
o 60% wobec Linuksa (452 MB). Na planie Mikrus 1.0 ten run by się nie zmieścił —
decyzja o 2.1 była słuszna **przypadkiem**, nie z dobrego powodu.

---

## 4. Co zostało do zrobienia

Kolejka zerowa z `06-operate.md`. **Trzy z pięciu wymagają decyzji Kuby i nie da
się ich obejść kodem.**

| # | co | kto | stan |
|---|---|---|---|
| **Z1** | **O23** — cztery pytania o dane osobowe pod URL-em: wygasanie dostępu, kasowanie konta po relacji, logi wejść, nazwiska w adresie | Kuba | otwarte. Dopóki trwa, panel jest **tylko dla zespołu**; `--dodaj-klienta` działa i nikt go nie zablokował |
| **Z2** | **baseline bramy promocji** — złoty zestaw jest dla snapshotu `acme`, nie dla runów CXLABS | Kuba, potem kod | brama istnieje i przeszła na prawdziwym runie, ale **nie ma dziś czego przepuścić**. Warunek „uruchom przed czymkolwiek dla klienta" jest niewykonalny, a nie spełniony |
| **Z3** | **kopia poza serwerem** | Kuba (maszyna albo magazyn) | dziś kopia leży na tym samym dysku co oryginał. Do obcego magazynu potrzebne szyfrowanie, którego `backup.sh` nie ma |
| **Z4** | **obserwator `/health`** | kod — **prawie gotowe** | timer działa i jest uczciwy. Brakuje **jednego pola**: `URL_CZUWAKA` |
| **Z5** | **`nftables` z `policy accept` i zero reguł** | właściciel maszyny | panel słucha na pętli zwrotnej, więc nas nie dotyka — ale dotyczy maszyny |

### Z4 — dlaczego „prawie"

Kontrola działa **na tej samej maszynie** co panel, więc nie wykryje śmierci
maszyny ani zerwanej sieci: nie ma jej wtedy kto uruchomić. Domyka to czuwak —
serwer pinguje **na zewnątrz** po udanej kontroli, a usługa zewnętrzna krzyczy,
gdy pingi ustaną. Odwrócenie kierunku jest całą sztuczką: nie wymaga otwartych
portów.

Potrzebny jeden URL z darmowego konta (healthchecks.io albo podobne), wpisany do
`/etc/monday-audit.env` jako `URL_CZUWAKA=`. **Nie do jednostki systemd** —
`systemctl show` pokazuje `Environment=` każdemu na maszynie, a kto zna ten URL,
może pingować za serwer i udawać, że wszystko żyje.

---

## 5. Konfiguracja ręczna, której nie ma w repo

Nie jest pusta i nie może być — to jest ta część, która ginie między sesjami.

| co | gdzie | uwaga |
|---|---|---|
| `MONDAY_TOKEN` | `/etc/monday-audit.env` | **puste i tak ma być** — czytane tylko przez `cli.py` i `cli_agent.py`; panel bierze klucz z przeglądarki |
| `SOL_PSEUDONIMIZACJI` | `/etc/monday-audit.env` | ustawione, wygenerowane na serwerze wewnątrz procesu — nigdy nie przeszło przez argv ani przez rozmowę |
| `SMTP_*` | `/etc/monday-audit.env` | puste świadomie: konta zakłada CLI, który wypisuje hasło. Potrzebne, gdy reset hasła ma nie wymagać SSH (O29) |
| `URL_CZUWAKA` | `/etc/monday-audit.env` | **brakuje** — to jest Z4 |
| rekord DNS | panel OVH | CNAME `audyt` → `backend.strony.me.` |
| podpięcie hosta | panel Mikrusa | **osobna rzecz od DNS** — to ono wydaje certyfikat. Brak podpięcia daje **zerwane TLS**, nie 404 |
| klucz wdrożeniowy | GitHub → Settings repo → Deploy keys | read-only, bez `Allow write access` |

---

## 6. Jak sprawdzić, że wszystko stoi

```bash
# z dowolnej maszyny
curl -s https://audyt.cxlabs.digital/health        # {"status":"ok","migracja":12}

# na serwerze
systemctl is-active monday-audit monday-audit-kontrola.timer
systemctl show monday-audit -p StartLimitBurst -p UMask
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
dotyczy jednak tego dokumentu wprost: **jeśli audyt wejdzie do repo portalu,
`deploy/` przestaje być ścieżką produkcyjną.** Jednostki systemd, `wdroz.sh`
i kontrola zdrowia opisują wdrożenie **tej** makiety na Mikrusie i nie są
instrukcją dla portalu.

`STATUS.md` należy do człowieka. Projekt jest w **etapie 6 (Operate)**; ten
dokument niczego nie przesuwa.
