"use strict";

// Szyfrowanie end-to-end wspólnym hasłem pokoju – ten sam format i parametry co e2e.py.
// PBKDF2-HMAC-SHA256 (600 000 iteracji) -> klucz AES-256-GCM; AAD = rodzaj danych i nick nadawcy.
// Na łączu: base64(nonce 12 B || szyfrogram || znacznik 16 B). Web Crypto działa tylko pod https://
// (albo http://localhost).

const E2E = (() => {
  const PROTOCOL = 1;
  const SALT = new TextEncoder().encode("komunikator-e2e-v1");
  const ITERATIONS = 600000;
  const NONCE_SIZE = 12;
  const enc = new TextEncoder();

  function available() {
    return !!(window.isSecureContext && window.crypto && crypto.subtle);
  }

  function toBase64(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }

  function fromBase64(data) {
    const s = atob(data);
    const bytes = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i);
    return bytes;
  }

  function toHex(bytes) {
    return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  function fromHex(hex) {
    if (typeof hex !== "string" || !/^[0-9a-f]{64}$/i.test(hex)) return null;
    return new Uint8Array(hex.match(/../g).map((h) => parseInt(h, 16)));
  }

  // hasło -> 32 bajty klucza
  async function deriveKey(passphrase) {
    const base = await crypto.subtle.importKey("raw", enc.encode(passphrase.normalize("NFC")), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: SALT, iterations: ITERATIONS }, base, 256);
    return new Uint8Array(bits);
  }

  async function fingerprint(raw) {
    const prefix = enc.encode("komunikator-fp|");
    const data = new Uint8Array(prefix.length + raw.length);
    data.set(prefix);
    data.set(raw, prefix.length);
    const h = toHex(new Uint8Array(await crypto.subtle.digest("SHA-256", data))).toUpperCase();
    return `${h.slice(0, 4)}-${h.slice(4, 8)}-${h.slice(8, 12)}`;
  }

  const aad = (kind, nick) => enc.encode(`komunikator-e2e-v1|${kind}|${nick}`);

  // raw: 32 bajty klucza. Zwraca obiekt z encrypt/decrypt i kodem klucza.
  async function roomCipher(raw) {
    const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
    return {
      fingerprint: await fingerprint(raw),
      async encrypt(kind, nick, data) { // data: Uint8Array albo ArrayBuffer -> tekst base64
        const nonce = crypto.getRandomValues(new Uint8Array(NONCE_SIZE));
        const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: aad(kind, nick) }, key, data));
        const out = new Uint8Array(NONCE_SIZE + ct.length);
        out.set(nonce);
        out.set(ct, NONCE_SIZE);
        return toBase64(out);
      },
      async decrypt(kind, nick, blob) { // tekst base64 -> ArrayBuffer; wyjątek, gdy się nie da
        const raw = fromBase64(blob);
        if (raw.length < NONCE_SIZE + 16) throw new Error("Za krótkie dane");
        return crypto.subtle.decrypt({ name: "AES-GCM", iv: raw.subarray(0, NONCE_SIZE), additionalData: aad(kind, nick) },
          key, raw.subarray(NONCE_SIZE));
      },
    };
  }

  return { PROTOCOL, available, deriveKey, roomCipher, toHex, fromHex };
})();
