"""Klient komunikatora z interfejsem graficznym (tkinter).

Uruchomienie:  python client.py
"""

import json
import os
import queue
import socket
import threading
import time

from protocol import DEFAULT_PORT, LineReader, encode

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk
except ImportError:  # pozwala używać ChatConnection bez tkintera (np. w testach)
    tk = None

APP_VERSION = "0.2"
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".komunikator.json")


def load_settings():
    """Wczytuje zapamiętany nick, adres i port serwera."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def typing_text(nicks):
    """Tekst pod oknem czatu, np. „Ania pisze…”."""
    if not nicks:
        return ""
    if len(nicks) == 1:
        return f"{nicks[0]} pisze…"
    if len(nicks) <= 3:
        return ", ".join(nicks[:-1]) + f" i {nicks[-1]} piszą…"
    return "Kilka osób pisze…"


class ChatConnection:
    """Połączenie z serwerem. Odebrane zdarzenia trafiają do kolejki `events`.

    Każde zdarzenie to słownik z protokołu, a dodatkowo
    {"type": "disconnected", "text": ...} po zerwaniu połączenia.
    """

    def __init__(self):
        self.sock = None
        self.events = queue.Queue()
        self.connected = False

    def connect(self, host, port, nick, timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(None)
        self.sock.sendall(encode({"type": "join", "nick": nick}))
        self.connected = True
        threading.Thread(target=self._receive_loop, daemon=True).start()

    def _receive_loop(self):
        reader = LineReader(self.sock)
        reason = "Połączenie z serwerem zostało zamknięte."
        try:
            while True:
                msg = reader.read()
                if msg is None:
                    break
                if msg.get("type") == "error":
                    reason = msg.get("text", reason)
                self.events.put(msg)
        except (OSError, ValueError):
            pass
        self.connected = False
        self.events.put({"type": "disconnected", "text": reason})

    def send_message(self, text):
        if self.connected:
            self.sock.sendall(encode({"type": "msg", "text": text}))

    def send_typing(self, active):
        if self.connected:
            try:
                self.sock.sendall(encode({"type": "typing", "active": bool(active)}))
            except OSError:
                pass

    def close(self):
        self.connected = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.sock = None


class ChatApp:
    """Okno czatu."""

    TYPING_SEND_EVERY = 2.0   # co ile sekund ponawiać sygnał „piszę”
    TYPING_IDLE = 3.0         # po ilu sekundach bez klawisza uznać, że przestałem pisać
    TYPING_EXPIRE = 6.0       # po ilu sekundach zapomnieć cudze „pisze”, gdy nie przyszło odwołanie

    def __init__(self, root):
        self.root = root
        self.conn = None
        self.my_nick = None
        self.typing_users = {}        # nick -> czas ostatniego sygnału
        self.last_typing_sent = 0.0
        self.typing_idle_job = None
        settings = load_settings()

        self.root.title(f"Komunikator {APP_VERSION}")
        self.root.geometry("760x540")
        self.root.minsize(640, 380)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Pasek połączenia (góra)
        top = ttk.Frame(root, padding=6)
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text="Nick:").pack(side=tk.LEFT)
        self.nick_var = tk.StringVar(value=settings.get("nick", ""))
        self.nick_entry = ttk.Entry(top, textvariable=self.nick_var, width=14)
        self.nick_entry.pack(side=tk.LEFT, padx=(2, 10))
        ttk.Label(top, text="Serwer:").pack(side=tk.LEFT)
        self.host_var = tk.StringVar(value=settings.get("host", "127.0.0.1"))
        self.host_entry = ttk.Entry(top, textvariable=self.host_var, width=18)
        self.host_entry.pack(side=tk.LEFT, padx=2)
        ttk.Label(top, text=":").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(settings.get("port", DEFAULT_PORT)))
        self.port_entry = ttk.Entry(top, textvariable=self.port_var, width=6)
        self.port_entry.pack(side=tk.LEFT, padx=(2, 10))
        self.connect_btn = ttk.Button(top, text="Połącz", width=10, command=self.toggle_connection)
        self.connect_btn.pack(side=tk.LEFT)
        for entry in (self.nick_entry, self.host_entry, self.port_entry):
            entry.bind("<Return>", lambda e: self.toggle_connection())

        # Pole wpisywania (dół). Pakowane przed środkiem, żeby zawsze było widoczne.
        bottom = ttk.Frame(root, padding=6)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.input = tk.Text(bottom, width=1, height=3, wrap=tk.WORD, font=("TkDefaultFont", 12),
                             bg="white", fg="black", insertbackground="black",
                             relief=tk.SOLID, borderwidth=1, undo=True)
        self.input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input.bind("<Return>", self.on_enter)
        self.input.bind("<KeyRelease>", self.on_key)
        self.send_btn = ttk.Button(bottom, text="Wyślij", width=10, command=self.send)
        self.send_btn.pack(side=tk.LEFT, padx=(6, 0), fill=tk.Y)

        # Linia „ktoś pisze…”
        self.typing_var = tk.StringVar()
        ttk.Label(root, textvariable=self.typing_var, foreground="#888888",
                  padding=(8, 0)).pack(side=tk.BOTTOM, fill=tk.X)

        # Środek: okno czatu i lista użytkowników
        middle = ttk.Frame(root, padding=(6, 0))
        middle.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        side = ttk.Frame(middle, padding=(6, 0, 0, 0))
        side.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Label(side, text="Użytkownicy").pack(anchor=tk.W)
        self.users = tk.Listbox(side, width=18, height=5, bg="white", fg="black", relief=tk.SOLID, borderwidth=1)
        self.users.pack(fill=tk.Y, expand=True)
        # height=5 to tylko minimum: okno czatu i tak rozciąga się na całe wolne miejsce
        self.chat = scrolledtext.ScrolledText(middle, state=tk.DISABLED, wrap=tk.WORD, height=5,
                                              font=("TkDefaultFont", 12), bg="white", fg="black",
                                              relief=tk.SOLID, borderwidth=1)
        self.chat.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.chat.tag_config("time", foreground="#888888")
        self.chat.tag_config("nick", foreground="#1a5fb4", font=("TkDefaultFont", 12, "bold"))
        self.chat.tag_config("me", foreground="#26a269", font=("TkDefaultFont", 12, "bold"))
        self.chat.tag_config("system", foreground="#888888", font=("TkDefaultFont", 11, "italic"))

        self.set_connected_ui(False)
        self.nick_entry.focus_set()
        self.root.after(100, self.poll_events)
        self.root.after(1000, self.expire_typing)

    # --- połączenie ---

    def toggle_connection(self):
        if self.conn and self.conn.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        nick = self.nick_var.get().strip()
        host = self.host_var.get().strip()
        if not nick:
            messagebox.showwarning("Brak nicku", "Wpisz swój nick.")
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showwarning("Zły port", "Port musi być liczbą.")
            return
        save_settings({"nick": nick, "host": host, "port": port})
        self.conn = ChatConnection()
        try:
            self.conn.connect(host, port, nick)
        except OSError as e:
            self.conn = None
            messagebox.showerror("Błąd połączenia", f"Nie udało się połączyć z {host}:{port}\n{e}")
            return
        self.set_connected_ui(True)
        self.add_system(f"Łączenie z {host}:{port}...")

    def disconnect(self):
        if self.conn:
            self.conn.close()

    def set_connected_ui(self, connected):
        entry_state = tk.DISABLED if connected else tk.NORMAL
        for w in (self.nick_entry, self.host_entry, self.port_entry):
            w.config(state=entry_state)
        self.connect_btn.config(text="Rozłącz" if connected else "Połącz")
        self.input.config(state=tk.NORMAL if connected else tk.DISABLED,
                          bg="white" if connected else "#f0f0f0")
        self.send_btn.config(state=tk.NORMAL if connected else tk.DISABLED)
        if connected:
            self.input.focus_set()
        else:
            self.users.delete(0, tk.END)
            self.typing_users.clear()
            self.update_typing_label()

    # --- wysyłanie i sygnał pisania ---

    def on_enter(self, event):
        if event.state & 0x0001:  # Shift+Enter: nowa linia
            return None
        self.send()
        return "break"

    def on_key(self, event):
        if not (self.conn and self.conn.connected):
            return
        if not self.input.get("1.0", "end-1c").strip():
            self.stop_typing()
            return
        now = time.time()
        if now - self.last_typing_sent > self.TYPING_SEND_EVERY:
            self.last_typing_sent = now
            self.conn.send_typing(True)
        if self.typing_idle_job:
            self.root.after_cancel(self.typing_idle_job)
        self.typing_idle_job = self.root.after(int(self.TYPING_IDLE * 1000), self.stop_typing)

    def stop_typing(self):
        if self.typing_idle_job:
            self.root.after_cancel(self.typing_idle_job)
            self.typing_idle_job = None
        if self.last_typing_sent and self.conn and self.conn.connected:
            self.conn.send_typing(False)
        self.last_typing_sent = 0.0

    def send(self):
        text = self.input.get("1.0", "end-1c").strip()
        if not text or not (self.conn and self.conn.connected):
            return
        try:
            self.conn.send_message(text)
            self.input.delete("1.0", tk.END)
            self.stop_typing()
        except OSError as e:
            self.add_system(f"Nie udało się wysłać: {e}")

    # --- zdarzenia z serwera ---

    def poll_events(self):
        if self.conn:
            while True:
                try:
                    event = self.conn.events.get_nowait()
                except queue.Empty:
                    break
                self.handle_event(event)
        self.root.after(100, self.poll_events)

    def handle_event(self, event):
        kind = event.get("type")
        if kind == "welcome":
            self.my_nick = event.get("nick")
            self.add_system(f"Połączono jako {self.my_nick}.")
        elif kind == "msg":
            nick = event.get("nick", "?")
            self.typing_users.pop(nick, None)
            self.update_typing_label()
            self.add_message(nick, event.get("text", ""), event.get("time"))
        elif kind == "typing":
            nick = event.get("nick")
            if nick and nick != self.my_nick:
                if event.get("active"):
                    self.typing_users[nick] = time.time()
                else:
                    self.typing_users.pop(nick, None)
                self.update_typing_label()
        elif kind == "system":
            self.add_system(event.get("text", ""), event.get("time"))
        elif kind == "users":
            self.users.delete(0, tk.END)
            names = event.get("users", [])
            for name in names:
                self.users.insert(tk.END, name)
            for nick in list(self.typing_users):
                if nick not in names:
                    del self.typing_users[nick]
            self.update_typing_label()
        elif kind == "disconnected":
            self.add_system(event.get("text", "Rozłączono."))
            self.conn = None
            self.set_connected_ui(False)

    def expire_typing(self):
        now = time.time()
        stale = [n for n, t in self.typing_users.items() if now - t > self.TYPING_EXPIRE]
        for nick in stale:
            del self.typing_users[nick]
        if stale:
            self.update_typing_label()
        self.root.after(1000, self.expire_typing)

    def update_typing_label(self):
        self.typing_var.set(typing_text(sorted(self.typing_users)))

    # --- wyświetlanie ---

    def _append(self, parts):
        self.chat.config(state=tk.NORMAL)
        for text, tag in parts:
            self.chat.insert(tk.END, text, tag)
        self.chat.insert(tk.END, "\n")
        self.chat.config(state=tk.DISABLED)
        self.chat.see(tk.END)

    @staticmethod
    def _clock(ts):
        return time.strftime("%H:%M", time.localtime(ts or time.time()))

    def add_message(self, nick, text, ts=None):
        tag = "me" if nick == self.my_nick else "nick"
        self._append([(f"[{self._clock(ts)}] ", "time"), (f"{nick}: ", tag), (text, None)])

    def add_system(self, text, ts=None):
        self._append([(f"[{self._clock(ts)}] ", "time"), (f"* {text}", "system")])

    def on_close(self):
        self.disconnect()
        self.root.destroy()


def main():
    if tk is None:
        raise SystemExit("Brak modułu tkinter. Zainstaluj go (np. sudo apt install python3-tk).")
    if tk.TkVersion < 8.6:
        print(f"Uwaga: stara wersja Tk ({tk.TkVersion}). Jeśli okno wyświetla się źle, "
              "zainstaluj Pythona z python.org albo 'brew install python-tk'.")
    root = tk.Tk()
    ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
