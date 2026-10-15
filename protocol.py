"""Wspólny protokół komunikatora: jedna wiadomość JSON na linię (UTF-8)."""

import json

DEFAULT_PORT = 5050
MAX_LINE = 64 * 1024  # maksymalna długość jednej wiadomości w bajtach


def encode(obj):
    """Zamienia słownik na bajty gotowe do wysłania."""
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


class LineReader:
    """Czyta z gniazda kolejne wiadomości JSON rozdzielone znakiem nowej linii."""

    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def read(self):
        """Zwraca kolejny słownik albo None, gdy połączenie zostało zamknięte."""
        while b"\n" not in self.buffer:
            if len(self.buffer) > MAX_LINE:
                raise ValueError("Wiadomość jest za długa")
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Niepoprawna wiadomość")
        if not isinstance(obj, dict):
            raise ValueError("Niepoprawna wiadomość")
        return obj
