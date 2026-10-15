# Komunikator 0.2

Prosty graficzny komunikator tekstowy w Pythonie. Jeden serwer, dowolnie wielu klientów;
każda wiadomość trafia do wszystkich podłączonych osób (jeden wspólny pokój).

Wymagania: Python 3.8+ (tylko biblioteka standardowa). Klient potrzebuje modułu `tkinter`
(na Windows i macOS jest w standardowej instalacji; na Linuksie: `sudo apt install python3-tk`).

## Pliki

- `server.py` – serwer TCP, rozsyła wiadomości i listę użytkowników
- `client.py` – okno czatu (tkinter)
- `protocol.py` – wspólny format wiadomości (JSON, jedna wiadomość na linię)

## Uruchomienie

1. Na komputerze, który będzie serwerem:

   ```
   python server.py
   ```

   Domyślnie nasłuchuje na wszystkich interfejsach, port 5050 (`--port 6000`, by zmienić).

2. Na każdym komputerze uczestnika (pliki `client.py` i `protocol.py` w jednym folderze):

   ```
   python client.py
   ```

   Wpisz nick, adres serwera i port, kliknij **Połącz**. Wiadomość wysyłasz Enterem lub przyciskiem **Wyślij**,
   a Shift+Enter dodaje nową linię. Nick, adres i port są zapamiętywane w pliku `~/.komunikator.json`.

   Na macOS uruchamiaj klienta Pythonem z python.org albo z Homebrew (`brew install python-tk`).
   Systemowy `/usr/bin/python3` ma stary Tk 8.5, który wyświetla puste okno.

## Łączenie przez internet

- W tej samej sieci domowej: wpisz lokalny adres serwera (np. `192.168.1.20`).
- Przez internet: na routerze serwera przekieruj port TCP 5050 na komputer z serwerem
  i podaj klientom publiczny adres IP. Zezwól też na ten port w zaporze systemu.
- Alternatywa bez konfiguracji routera: VPN typu Tailscale/ZeroTier i adres z tej sieci.

Uwaga: w tej wersji ruch nie jest szyfrowany ani uwierzytelniany – nie wysyłaj nim haseł.

## Co działa

- wiadomości do wszystkich z godziną i nickiem (pełne UTF-8, polskie znaki, emoji)
- lista osób online, komunikaty o dołączeniu i wyjściu
- unikalne nicki (bez względu na wielkość liter)
- zapamiętywanie nicku, adresu i portu serwera (0.2)
- większe, wielowierszowe pole wpisywania, zawsze widoczne (0.2)
- sygnalizacja „… pisze…” pod oknem czatu (0.2)
- poprawne rozłączenie po zamknięciu okna lub zatrzymaniu serwera
