"""Serwer komunikatora: przyjmuje klientów i rozsyła wiadomości do wszystkich.

Obsługuje jednocześnie:
- klientów okienkowych (client.py) przez TCP, domyślnie port 5050,
- przeglądarki: strona czatu i WebSocket, domyślnie port 8080 (patrz webserver.py).

Uruchomienie:  python server.py [--host 0.0.0.0] [--port 5050] [--http-port 8080] [--cert plik --key plik]
"""

import argparse
import socket
import threading
import time

from protocol import DEFAULT_PORT, LineReader, encode

DEFAULT_HTTP_PORT = 8080
MAX_NICK = 24
MAX_TEXT = 2000
MAX_AUDIO = 4000  # maks. długość ramki dźwięku w base64 (20 ms PCM to ok. 860 znaków)


class TcpPeer:
    """Klient okienkowy: wiadomości JSON rozdzielone znakiem nowej linii."""

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.reader = LineReader(sock)
        self.send_lock = threading.Lock()

    def read(self):
        return self.reader.read()

    def send_text(self, text):
        data = (text + "\n").encode("utf-8")
        with self.send_lock:
            self.sock.sendall(data)

    def shutdown(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # budzi wątek klienta czekający na recv()
        except OSError:
            pass

    def close(self):
        self.sock.close()


class ChatServer:
    def __init__(self, host="0.0.0.0", port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.clients = {}  # klient (TcpPeer/WebSocketPeer) -> nick
        self.voice = set()  # klienci w rozmowie głosowej
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
            threading.Thread(target=self.handle_peer, args=(TcpPeer(conn, addr),), daemon=True).start()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        with self.lock:
            peers = list(self.clients)
        for peer in peers:
            peer.shutdown()

    def send(self, peer, obj):
        self._send_text(peer, encode(obj).decode("utf-8").rstrip("\n"))

    @staticmethod
    def _send_text(peer, text):
        try:
            peer.send_text(text)
        except OSError:
            pass

    def broadcast(self, obj, skip=None, only=None):
        text = encode(obj).decode("utf-8").rstrip("\n")
        with self.lock:
            pool = self.clients if only is None else only
            targets = [p for p in pool if p is not skip and p in self.clients]
        for peer in targets:
            self._send_text(peer, text)

    def voice_users(self):
        with self.lock:
            return sorted((self.clients[p] for p in self.voice if p in self.clients), key=str.lower)

    def broadcast_voice_users(self):
        self.broadcast({"type": "voice_users", "users": self.voice_users()})

    def handle_voice(self, peer, nick, msg):
        kind = msg.get("type")
        if kind == "voice_join":
            with self.lock:
                self.voice.add(peer)
            self.log(f"{nick} dołączył do rozmowy głosowej")
            self.broadcast_voice_users()
        elif kind == "voice_leave":
            with self.lock:
                was_in = peer in self.voice
                self.voice.discard(peer)
            if was_in:
                self.log(f"{nick} opuścił rozmowę głosową")
                self.broadcast_voice_users()
        elif kind == "audio":
            data = msg.get("data")
            with self.lock:
                in_voice = peer in self.voice
                listeners = set(self.voice)
            if in_voice and isinstance(data, str) and len(data) <= MAX_AUDIO:
                self.broadcast({"type": "audio", "nick": nick, "data": data}, skip=peer, only=listeners)

    def broadcast_users(self):
        with self.lock:
            users = sorted(self.clients.values(), key=str.lower)
        self.broadcast({"type": "users", "users": users})

    def handle_peer(self, peer):
        """Obsługuje jednego klienta od powitania do rozłączenia (w osobnym wątku)."""
        nick = None
        try:
            hello = peer.read()
            if not hello or hello.get("type") != "join":
                return
            nick = str(hello.get("nick", "")).strip()[:MAX_NICK]
            if not nick:
                self.send(peer, {"type": "error", "text": "Nick nie może być pusty."})
                nick = None
                return
            with self.lock:
                taken = nick.lower() in (n.lower() for n in self.clients.values())
                if not taken:
                    self.clients[peer] = nick
            if taken:
                self.send(peer, {"type": "error", "text": f"Nick „{nick}” jest już zajęty."})
                nick = None
                return

            self.log(f"{nick} dołączył ({peer.addr[0]}:{peer.addr[1]})")
            self.send(peer, {"type": "welcome", "nick": nick})
            self.broadcast({"type": "system", "text": f"{nick} dołączył do czatu.", "time": time.time()})
            self.broadcast_users()
            self.send(peer, {"type": "voice_users", "users": self.voice_users()})

            while True:
                msg = peer.read()
                if msg is None:
                    break
                if msg.get("type") in ("voice_join", "voice_leave", "audio"):
                    self.handle_voice(peer, nick, msg)
                elif msg.get("type") == "typing":
                    self.broadcast({"type": "typing", "nick": nick, "active": bool(msg.get("active"))}, skip=peer)
                elif msg.get("type") == "msg":
                    text = str(msg.get("text", "")).strip()[:MAX_TEXT]
                    if text:
                        self.broadcast({"type": "msg", "nick": nick, "text": text, "time": time.time()})
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.clients.pop(peer, None)
                was_in_voice = peer in self.voice
                self.voice.discard(peer)
            peer.close()
            if nick:
                self.log(f"{nick} wyszedł")
                self.broadcast({"type": "system", "text": f"{nick} opuścił czat.", "time": time.time()})
                self.broadcast_users()
                if was_in_voice:
                    self.broadcast_voice_users()


def main():
    parser = argparse.ArgumentParser(description="Serwer komunikatora")
    parser.add_argument("--host", default="0.0.0.0", help="adres nasłuchu (domyślnie wszystkie interfejsy)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port dla client.py (domyślnie {DEFAULT_PORT})")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT,
                        help=f"port strony WWW (domyślnie {DEFAULT_HTTP_PORT}, 0 wyłącza stronę)")
    parser.add_argument("--cert", help="plik certyfikatu (PEM) – włącza HTTPS, potrzebne do rozmów głosowych")
    parser.add_argument("--key", help="plik klucza prywatnego (PEM) do certyfikatu")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("--cert i --key trzeba podać razem")

    server = ChatServer(args.host, args.port)
    server.start()
    web = None
    if args.http_port:
        from webserver import WebServer
        web = WebServer(server, args.host, args.http_port, args.cert, args.key)
        web.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.log("Zatrzymywanie serwera...")
    finally:
        if web:
            web.stop()
        server.stop()


if __name__ == "__main__":
    main()
