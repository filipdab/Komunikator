# Komunikator 0.5

Prosty graficzny komunikator tekstowy w Pythonie. Jeden serwer, dowolnie wielu klientów;
każda wiadomość trafia do wszystkich podłączonych osób (jeden wspólny pokój).

Wymagania: Python 3.8+ (tylko biblioteka standardowa). Klient potrzebuje modułu `tkinter`
(na Windows i macOS jest w standardowej instalacji; na Linuksie: `sudo apt install python3-tk`).

## Pliki

- `server.py` – serwer, rozsyła wiadomości i listę użytkowników
- `webserver.py` – strona czatu dla przeglądarek i połączenie WebSocket (część serwera)
- `web/` – strona czatu (HTML, CSS, JavaScript)
- `komunikator.service` – przykładowa usługa systemd do uruchamiania serwera na Raspberry Pi
- `client.py` – okno czatu (tkinter)
- `voice.py` – rozmowa głosowa (mikrofon, miksowanie, odtwarzanie)
- `protocol.py` – wspólny format wiadomości (JSON, jedna wiadomość na linię)

## Uruchomienie

1. Na komputerze, który będzie serwerem:

   ```
   python server.py
   ```

   Domyślnie nasłuchuje na wszystkich interfejsach: port 5050 dla programu `client.py`
   i port 8080 dla przeglądarek (`--port` i `--http-port`, by zmienić; `--http-port 0` wyłącza stronę).

2. Na każdym komputerze uczestnika (pliki `client.py` i `protocol.py` w jednym folderze):

   ```
   python client.py
   ```

   Wpisz nick, adres serwera i port, kliknij **Połącz**. Wiadomość wysyłasz Enterem lub przyciskiem **Wyślij**,
   a Shift+Enter dodaje nową linię. Nick, adres i port są zapamiętywane w pliku `~/.komunikator.json`.

   Na macOS uruchamiaj klienta Pythonem z python.org albo z Homebrew (`brew install python-tk`).
   Systemowy `/usr/bin/python3` ma stary Tk 8.5, który wyświetla puste okno.

## Czat w przeglądarce (iPhone, Android, dowolny komputer)

Serwer od razu udostępnia stronę czatu. W przeglądarce otwórz `http://adres-serwera:8080/`,
wpisz nick i kliknij **Połącz**. Osoby z przeglądarki i z programu `client.py` są w tym samym czacie
i w tej samej rozmowie głosowej. Nick jest zapamiętywany w przeglądarce. Na iPhonie stronę można dodać
do ekranu początkowego (Udostępnij → Do ekranu początkowego), wtedy otwiera się jak aplikacja.

Czat tekstowy działa po zwykłym `http://`. **Rozmowa głosowa w przeglądarce wymaga HTTPS**: Safari, Chrome
i inne przeglądarki dają stronie dostęp do mikrofonu tylko pod adresem `https://` (wyjątkiem jest
`http://localhost` na tym samym komputerze). Opis włączenia HTTPS jest niżej.

## Raspberry Pi: serwer z HTTPS

Przykład dla adresu `czat.dabrowski.in` i projektu w `/home/filip/Komunikator` (zmień na swoje).

1. **Adres.** W DNS domeny dodaj rekord `A` dla `czat.dabrowski.in` wskazujący publiczny adres IP Twojego
   internetu. Jeśli ten adres się zmienia, użyj usługi dynamicznego DNS. Na routerze przekieruj:
   port TCP 80 → Raspberry Pi port 80 (tylko dla odnawiania certyfikatu), TCP 443 → Raspberry Pi port 8080
   (strona czatu) i TCP 5050 → Raspberry Pi port 5050 (program `client.py`).

2. **Certyfikat** (darmowy, Let's Encrypt):

   ```
   sudo apt install certbot
   sudo certbot certonly --standalone -d czat.dabrowski.in
   ```

   Pliki trafiają do `/etc/letsencrypt/live/czat.dabrowski.in/`. Są czytelne tylko dla roota, więc
   skopiuj je do katalogu programu po każdym odnowieniu:

   ```
   sudo tee /etc/letsencrypt/renewal-hooks/deploy/komunikator.sh <<'EOS'
   #!/bin/sh
   install -o filip -m 600 /etc/letsencrypt/live/czat.dabrowski.in/fullchain.pem /home/filip/Komunikator/cert.pem
   install -o filip -m 600 /etc/letsencrypt/live/czat.dabrowski.in/privkey.pem /home/filip/Komunikator/key.pem
   systemctl restart komunikator
   EOS
   sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/komunikator.sh
   sudo /etc/letsencrypt/renewal-hooks/deploy/komunikator.sh
   ```

   (Ostatnie polecenie zgłosi błąd restartu, dopóki usługa z kroku 3 nie istnieje. To nic nie szkodzi.)
   Certbot sam odnawia certyfikat co ok. 2 miesiące.

3. **Uruchamianie przy starcie systemu.** W pliku `komunikator.service` popraw nazwę użytkownika i ścieżkę, potem:

   ```
   sudo cp komunikator.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now komunikator
   journalctl -u komunikator -f      # podgląd logu serwera
   ```

   Serwer działa wtedy z `--cert cert.pem --key key.pem`, a czat jest pod adresem `https://czat.dabrowski.in/`.
   Program `client.py` łączy się jak dotąd na `czat.dabrowski.in`, port 5050.

**Bez domeny, tylko w sieci domowej:** można zrobić certyfikat samopodpisany:

```
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 3650 -subj /CN=komunikator
python3 server.py --cert cert.pem --key key.pem
```

Przeglądarka pokaże ostrzeżenie o niezaufanym certyfikacie, które trzeba zaakceptować
(w Safari: „Pokaż szczegóły” → „odwiedź tę witrynę”). Głos zadziała, ale ostrzeżenie wraca na każdym urządzeniu.

## Rozmowa głosowa

Rozmowa głosowa wymaga biblioteki `sounddevice` po stronie klienta (serwer jej nie potrzebuje):

```
python3 -m pip install sounddevice
```

Na macOS z Pythonem z Homebrew pip odmawia instalacji do systemu, więc najprościej użyć osobnego środowiska:

```
/opt/homebrew/bin/python3 -m venv ~/komunikator-venv
~/komunikator-venv/bin/pip install sounddevice
~/komunikator-venv/bin/python client.py
```

Na Linuksie potrzebna jest też biblioteka PortAudio: `sudo apt install libportaudio2`.

W przeglądarce nic nie trzeba instalować (patrz wyżej: potrzebne HTTPS).

W oknie klienta kliknij **Dołącz do rozmowy** (panel po prawej). Wszyscy, którzy dołączą, słyszą się nawzajem.
Przycisk **Wycisz mikrofon** wyłącza wysyłanie Twojego głosu. Przy pierwszym użyciu macOS zapyta o dostęp
do mikrofonu dla Terminala, na co trzeba się zgodzić. Używaj słuchawek, bo bez nich rozmówcy usłyszą echo.

Dźwięk idzie przez ten sam serwer i port co czat (PCM 16 kHz, ok. 45 kB/s na każdą mówiącą osobę),
więc nie trzeba niczego dodatkowo przekierowywać na routerze.

## Łączenie przez internet

- W tej samej sieci domowej: wpisz lokalny adres serwera (np. `192.168.1.20`).
- Przez internet: na routerze serwera przekieruj port TCP 5050 na komputer z serwerem
  i podaj klientom publiczny adres IP. Zezwól też na ten port w zaporze systemu.
- Alternatywa bez konfiguracji routera: VPN typu Tailscale/ZeroTier i adres z tej sieci.

Uwaga: ruch programu `client.py` nie jest szyfrowany, a czat nie ma haseł – każdy, kto zna adres, może dołączyć.
Strona z HTTPS jest szyfrowana między przeglądarką a serwerem.

## Co działa

- wiadomości do wszystkich z godziną i nickiem (pełne UTF-8, polskie znaki, emoji)
- lista osób online, komunikaty o dołączeniu i wyjściu
- unikalne nicki (bez względu na wielkość liter)
- zapamiętywanie nicku, adresu i portu serwera (0.2)
- większe, wielowierszowe pole wpisywania, zawsze widoczne (0.2)
- sygnalizacja „… pisze…” pod oknem czatu (0.2)
- rozmowa głosowa dla wielu osób z wyciszaniem mikrofonu (0.3)
- wskaźnik poziomu mikrofonu i podpowiedź, gdy mikrofon daje ciszę (0.4)
- wybór emotek przyciskiem 😊 obok pola wpisywania (0.4)
- klikalne linki w oknie czatu (http://…, https://…, www.…) (0.4)
- okno „O programie” z wersją i danymi autora (0.4)
- czat w przeglądarce, także na iPhonie i Androidzie, z rozmową głosową przez HTTPS (0.5)
- obsługa HTTPS w serwerze i opis instalacji na Raspberry Pi (0.5)
- poprawne rozłączenie po zamknięciu okna lub zatrzymaniu serwera

## Autor

Filip Dąbrowski · [filip@dabrowski.in](mailto:filip@dabrowski.in) · [www.dabrowski.in](https://www.dabrowski.in)
