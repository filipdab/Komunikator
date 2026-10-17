"""Strona czatu dla przeglądarek i most WebSocket do serwera czatu.

Pod adresem http(s)://serwer:8080/ jest strona z katalogu web/, a pod /ws połączenie
WebSocket, które mówi tym samym protokołem JSON co client.py. Tylko biblioteka standardowa.
"""

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 64 * 1024


class WebSocketPeer:
    """Klient z przeglądarki: wiadomości JSON w ramkach tekstowych WebSocket (RFC 6455)."""

    def __init__(self, sock, addr, rfile):
        self.sock = sock
        self.addr = addr
        self.rfile = rfile  # strumień, z którego serwer HTTP czytał nagłówki (może mieć już dane w buforze)
        self.send_lock = threading.Lock()

    def _read_exact(self, n):
        data = self.rfile.read(n)
        if data is None or len(data) < n:
            raise ConnectionError("Połączenie zamknięte")
        return data

    def _read_frame(self):
        b1, b2 = self._read_exact(2)
        fin, opcode = b1 & 0x80, b1 & 0x0F
        masked, length = b2 & 0x80, b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if length > MAX_MESSAGE:
            raise ValueError("Wiadomość jest za długa")
        if not masked:
            raise ValueError("Ramka od klienta musi być zamaskowana")
        mask = self._read_exact(4)
        payload = self._read_exact(length)
        if length:  # odmaskowanie całej ramki naraz, szybciej niż bajt po bajcie
            key = (mask * (length // 4 + 1))[:length]
            payload = (int.from_bytes(payload, "big") ^ int.from_bytes(key, "big")).to_bytes(length, "big")
        return fin, opcode, payload

    def read(self):
        """Zwraca kolejny słownik albo None, gdy połączenie zostało zamknięte."""
        message = b""
        try:
            while True:
                fin, opcode, payload = self._read_frame()
                if opcode == 0x8:  # zamknięcie
                    self._send_frame(0x8, payload[:2])
                    return None
                if opcode == 0x9:  # ping
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:  # pong
                    continue
                message += payload
                if len(message) > MAX_MESSAGE:
                    raise ValueError("Wiadomość jest za długa")
                if fin:
                    break
        except ConnectionError:
            return None
        try:
            obj = json.loads(message.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Niepoprawna wiadomość")
        if not isinstance(obj, dict):
            raise ValueError("Niepoprawna wiadomość")
        return obj

    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([n])
        elif n < 65536:
            header += bytes([126]) + struct.pack("!H", n)
        else:
            header += bytes([127]) + struct.pack("!Q", n)
        with self.send_lock:
            self.sock.sendall(header + payload)

    def send_text(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def shutdown(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Handler(SimpleHTTPRequestHandler):
    chat = None  # ChatServer, ustawiany w WebServer

    def log_message(self, format, *args):
        pass  # bez logu każdego zapytania o plik

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/ws":
            self.handle_websocket()
        else:
            super().do_GET()

    def handle_websocket(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            self.send_error(400, "Oczekiwano połączenia WebSocket")
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response_only(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        super().end_headers()
        self.wfile.flush()
        self.close_connection = True
        peer = WebSocketPeer(self.connection, self.client_address, self.rfile)
        self.chat.handle_peer(peer)  # działa do rozłączenia, w wątku tego zapytania


class ChatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    ssl_context = None

    def finish_request(self, request, client_address):
        # Uzgadnianie TLS w wątku zapytania, żeby jeden wolny klient nie blokował przyjmowania innych.
        if self.ssl_context is not None:
            request.settimeout(10)
            try:
                request = self.ssl_context.wrap_socket(request, server_side=True)
            except (OSError, ssl.SSLError):
                request.close()
                return
            request.settimeout(None)
        super().finish_request(request, client_address)


class WebServer:
    def __init__(self, chat, host, port, cert=None, key=None):
        self.chat = chat
        self.host = host
        self.port = port
        self.cert = cert
        self.key = key
        self.httpd = None

    def start(self):
        handler = type("ChatHandler", (Handler,), {"chat": self.chat})
        self.httpd = ChatHTTPServer((self.host, self.port), partial(handler, directory=WEB_DIR))
        scheme = "http"
        if self.cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cert, self.key)
            self.httpd.ssl_context = context
            scheme = "https"
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.chat.log(f"Strona czatu: {scheme}://{self.host}:{self.port}/")
        return self.port

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
