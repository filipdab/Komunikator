"""Serwer komunikatora: przyjmuje klientów TCP i rozsyła wiadomości do wszystkich.

Uruchomienie:  python server.py [--host 0.0.0.0] [--port 5050]
"""

import argparse
import socket
import threading
import time

from protocol import DEFAULT_PORT, LineReader, encode

MAX_NICK = 24
MAX_TEXT = 2000
MAX_AUDIO = 4000  # maks. długość ramki dźwięku w base64 (20 ms PCM to ok. 860 znaków)


class ChatServer:
    def __init__(self, host="0.0.0.0", port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.clients = {}  # gniazdo -> nick
        self.send_locks = {}  # gniazdo -> blokada, żeby wątki nie przeplatały danych w jednym gnieździe
        self.voice = set()  # gniazda osób w rozmowie głosowej
        self.lock = threading.Lock()
        self.sock = None
        self.running = False

    def log(self, text):
        print(time.strftime("[%H:%M:%S] ") + text, flush=True)

    def start(self):
        """Otwiera gniazdo nasłuchujące. Zwraca rzeczywisty port."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.log(f"Serwer nasłuchuje na {self.host}:{self.port}")
        return self.port

    def serve_forever(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        with self.lock:
            conns = list(self.clients)
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)  # budzi wątek klienta czekający na recv()
            except OSError:
                pass

    def send(self, conn, obj):
        self._send_raw(conn, encode(obj))

    def _send_raw(self, conn, data):
        with self.lock:
            lock = self.send_locks.get(conn)
        try:
            if lock is None:
                conn.sendall(data)
            else:
                with lock:
                    conn.sendall(data)
        except OSError:
            pass

    def broadcast(self, obj, skip=None, only=None):
        data = encode(obj)
        with self.lock:
            pool = self.clients if only is None else only
            targets = [c for c in pool if c is not skip]
        for conn in targets:
            self._send_raw(conn, data)

    def voice_users(self):
        with self.lock:
            return sorted((self.clients[c] for c in self.voice if c in self.clients), key=str.lower)

    def broadcast_voice_users(self):
        self.broadcast({"type": "voice_users", "users": self.voice_users()})

    def handle_voice(self, conn, nick, msg):
        kind = msg.get("type")
        if kind == "voice_join":
            with self.lock:
                self.voice.add(conn)
            self.log(f"{nick} dołączył do rozmowy głosowej")
            self.broadcast_voice_users()
        elif kind == "voice_leave":
            with self.lock:
                was_in = conn in self.voice
                self.voice.discard(conn)
            if was_in:
                self.log(f"{nick} opuścił rozmowę głosową")
                self.broadcast_voice_users()
        elif kind == "audio":
            data = msg.get("data")
            with self.lock:
                in_voice = conn in self.voice
                listeners = set(self.voice)
            if in_voice and isinstance(data, str) and len(data) <= MAX_AUDIO:
                self.broadcast({"type": "audio", "nick": nick, "data": data}, skip=conn, only=listeners)

    def broadcast_users(self):
        with self.lock:
            users = sorted(self.clients.values(), key=str.lower)
        self.broadcast({"type": "users", "users": users})

    def handle_client(self, conn, addr):
        reader = LineReader(conn)
        nick = None
        try:
            hello = reader.read()
            if not hello or hello.get("type") != "join":
                return
            nick = str(hello.get("nick", "")).strip()[:MAX_NICK]
            if not nick:
                self.send(conn, {"type": "error", "text": "Nick nie może być pusty."})
                nick = None
                return
            with self.lock:
                taken = nick.lower() in (n.lower() for n in self.clients.values())
                if not taken:
                    self.clients[conn] = nick
                    self.send_locks[conn] = threading.Lock()
            if taken:
                self.send(conn, {"type": "error", "text": f"Nick „{nick}” jest już zajęty."})
                nick = None
                return

            self.log(f"{nick} dołączył ({addr[0]}:{addr[1]})")
            self.send(conn, {"type": "welcome", "nick": nick})
            self.broadcast({"type": "system", "text": f"{nick} dołączył do czatu.", "time": time.time()})
            self.broadcast_users()
            self.send(conn, {"type": "voice_users", "users": self.voice_users()})

            while True:
                msg = reader.read()
                if msg is None:
                    break
                if msg.get("type") in ("voice_join", "voice_leave", "audio"):
                    self.handle_voice(conn, nick, msg)
                elif msg.get("type") == "typing":
                    self.broadcast({"type": "typing", "nick": nick, "active": bool(msg.get("active"))}, skip=conn)
                elif msg.get("type") == "msg":
                    text = str(msg.get("text", "")).strip()[:MAX_TEXT]
                    if text:
                        self.broadcast({"type": "msg", "nick": nick, "text": text, "time": time.time()})
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.clients.pop(conn, None)
                self.send_locks.pop(conn, None)
                was_in_voice = conn in self.voice
                self.voice.discard(conn)
            conn.close()
            if nick:
                self.log(f"{nick} wyszedł")
                self.broadcast({"type": "system", "text": f"{nick} opuścił czat.", "time": time.time()})
                self.broadcast_users()
                if was_in_voice:
                    self.broadcast_voice_users()


def main():
    parser = argparse.ArgumentParser(description="Serwer komunikatora")
    parser.add_argument("--host", default="0.0.0.0", help="adres nasłuchu (domyślnie wszystkie interfejsy)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (domyślnie {DEFAULT_PORT})")
    args = parser.parse_args()

    server = ChatServer(args.host, args.port)
    server.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.log("Zatrzymywanie serwera...")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
