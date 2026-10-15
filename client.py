"""Klient komunikatora z interfejsem graficznym (tkinter).

Uruchomienie:  python client.py
"""

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

APP_VERSION = "0.1"


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

    def __init__(self, root):
        self.root = root
        self.conn = None
        self.root.title(f"Komunikator {APP_VERSION}")
        self.root.geometry("720x480")
        self.root.minsize(480, 320)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Pasek połączenia
        top = ttk.Frame(root, padding=6)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Nick:").pack(side=tk.LEFT)
        self.nick_var = tk.StringVar(value="")
        self.nick_entry = ttk.Entry(top, textvariable=self.nick_var, width=14)
        self.nick_entry.pack(side=tk.LEFT, padx=(2, 10))
        ttk.Label(top, text="Serwer:").pack(side=tk.LEFT)
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.host_entry = ttk.Entry(top, textvariable=self.host_var, width=18)
        self.host_entry.pack(side=tk.LEFT, padx=2)
        ttk.Label(top, text=":").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.port_entry = ttk.Entry(top, textvariable=self.port_var, width=6)
        self.port_entry.pack(side=tk.LEFT, padx=(2, 10))
        self.connect_btn = ttk.Button(top, text="Połącz", width=10, command=self.toggle_connection)
        self.connect_btn.pack(side=tk.LEFT)
        self.nick_entry.bind("<Return>", lambda e: self.toggle_connection())

        # Środek: okno czatu i lista użytkowników
        middle = ttk.Frame(root, padding=(6, 0))
        middle.pack(fill=tk.BOTH, expand=True)
        self.chat = scrolledtext.ScrolledText(middle, state=tk.DISABLED, wrap=tk.WORD, font=("TkDefaultFont", 11),
                                              bg="white", fg="black", relief=tk.SOLID, borderwidth=1)
        self.chat.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.chat.tag_config("time", foreground="#888888")
        self.chat.tag_config("nick", foreground="#1a5fb4", font=("TkDefaultFont", 11, "bold"))
        self.chat.tag_config("me", foreground="#26a269", font=("TkDefaultFont", 11, "bold"))
        self.chat.tag_config("system", foreground="#888888", font=("TkDefaultFont", 10, "italic"))

        side = ttk.Frame(middle, padding=(6, 0, 0, 0))
        side.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Label(side, text="Użytkownicy").pack(anchor=tk.W)
        self.users = tk.Listbox(side, width=18, bg="white", fg="black", relief=tk.SOLID, borderwidth=1)
        self.users.pack(fill=tk.Y, expand=True)

        # Pole wpisywania
        bottom = ttk.Frame(root, padding=6)
        bottom.pack(fill=tk.X)
        self.input_var = tk.StringVar()
        self.input = ttk.Entry(bottom, textvariable=self.input_var)
        self.input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input.bind("<Return>", lambda e: self.send())
        self.send_btn = ttk.Button(bottom, text="Wyślij", width=10, command=self.send)
        self.send_btn.pack(side=tk.LEFT, padx=(6, 0))

        self.my_nick = None
        self.set_connected_ui(False)
        self.nick_entry.focus_set()
        self.root.after(100, self.poll_events)

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
        self.input.config(state=tk.NORMAL if connected else tk.DISABLED)
        self.send_btn.config(state=tk.NORMAL if connected else tk.DISABLED)
        if connected:
            self.input.focus_set()
        else:
            self.users.delete(0, tk.END)

    def send(self):
        text = self.input_var.get().strip()
        if not text or not (self.conn and self.conn.connected):
            return
        try:
            self.conn.send_message(text)
            self.input_var.set("")
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
            self.add_message(event.get("nick", "?"), event.get("text", ""), event.get("time"))
        elif kind == "system":
            self.add_system(event.get("text", ""), event.get("time"))
        elif kind == "users":
            self.users.delete(0, tk.END)
            for name in event.get("users", []):
                self.users.insert(tk.END, name)
        elif kind == "disconnected":
            self.add_system(event.get("text", "Rozłączono."))
            self.conn = None
            self.set_connected_ui(False)

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
