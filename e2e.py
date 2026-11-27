"""Szyfrowanie end-to-end czatu i rozmowy głosowej wspólnym hasłem pokoju.

Klucz powstaje z hasła (PBKDF2-HMAC-SHA256) u każdego uczestnika i nigdy nie trafia na serwer.
Każda wiadomość i ramka dźwięku jest szyfrowana AES-256-GCM z losowym nonce. Dane uwierzytelniane
(AAD) zawierają rodzaj danych i nick nadawcy, więc serwer nie może przypisać wiadomości innej osobie
ani podać ramki dźwięku jako wiadomości.

Format na łączu: base64(nonce 12 B || szyfrogram || znacznik 16 B).
Ten sam format i te same parametry ma web/e2e.js (przeglądarka).

Wymaga biblioteki `cryptography` (pip install cryptography; na Raspberry Pi OS: python3-cryptography).
"""

import base64
import hashlib
import os
import unicodedata

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    IMPORT_ERROR = None
except Exception as e:  # brak biblioteki
    AESGCM = None
    InvalidTag = ValueError
    IMPORT_ERROR = e

PROTOCOL = 1                       # wersja szyfrowania, wysyłana serwerowi w powitaniu
SALT = b"komunikator-e2e-v1"
ITERATIONS = 600_000
NONCE_SIZE = 12
TAG_SIZE = 16


def available():
    return AESGCM is not None


def derive_key(passphrase):
    """Zamienia hasło pokoju na 32-bajtowy klucz (trwa ułamek sekundy)."""
    data = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", data, SALT, ITERATIONS, 32)


def fingerprint(key):
    """Krótki kod klucza do porównania między uczestnikami, np. 3F9A-12C7-0B44."""
    h = hashlib.sha256(b"komunikator-fp|" + key).hexdigest().upper()
    return f"{h[0:4]}-{h[4:8]}-{h[8:12]}"


def _aad(kind, nick):
    return f"komunikator-e2e-v1|{kind}|{nick}".encode("utf-8")


class RoomCipher:
    """Szyfruje i odszyfrowuje dane jednego pokoju."""

    def __init__(self, key):
        if AESGCM is None:
            raise RuntimeError(f"Brak biblioteki cryptography: {IMPORT_ERROR}")
        if len(key) != 32:
            raise ValueError("Klucz musi mieć 32 bajty")
        self.key = key
        self.fingerprint = fingerprint(key)
        self._aead = AESGCM(key)

    def encrypt(self, kind, nick, data):
        """bytes -> tekst base64 gotowy do wysłania."""
        nonce = os.urandom(NONCE_SIZE)
        return base64.b64encode(nonce + self._aead.encrypt(nonce, data, _aad(kind, nick))).decode("ascii")

    def decrypt(self, kind, nick, blob):
        """Tekst base64 -> bytes. Rzuca ValueError, gdy dane są uszkodzone albo klucz jest inny."""
        try:
            raw = base64.b64decode(blob, validate=True)
        except (ValueError, TypeError):
            raise ValueError("Niepoprawne dane")
        if len(raw) < NONCE_SIZE + TAG_SIZE:
            raise ValueError("Za krótkie dane")
        try:
            return self._aead.decrypt(raw[:NONCE_SIZE], raw[NONCE_SIZE:], _aad(kind, str(nick)))
        except InvalidTag:
            raise ValueError("Nie można odszyfrować (inne hasło pokoju?)")
