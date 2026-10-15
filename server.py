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


class ChatServer:
    def __init__(self, host="0.0.0.0", port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.clients = {}  # gniazdo -> nick
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
        try:
            conn.sendall(encode(obj))
        except OSError:
            pass

    def broadcast(self, obj, skip=None):
        data = encode(obj)
        with self.lock:
            targets = [c for c in self.clients if c is not skip]
        for conn in targets:
            try:
                conn.sendall(data)
            except OSError:
                pass

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
            if taken:
                self.send(conn, {"type": "error", "text": f"Nick „{nick}” jest już zajęty."})
                nick = None
                return

            self.log(f"{nick} dołączył ({addr[0]}:{addr[1]})")
            self.send(conn, {"type": "welcome", "nick": nick})
            self.broadcast({"type": "system", "text": f"{nick} dołączył do czatu.", "time": time.time()})
            self.broadcast_users()

            while True:
                msg = reader.read()
                if msg is None:
                    break
                if msg.get("type") == "typing":
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
            conn.close()
            if nick:
                self.log(f"{nick} wyszedł")
                self.broadcast({"type": "system", "text": f"{nick} opuścił czat.", "time": time.time()})
                self.broadcast_users()


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
