"""Cryptographic foundation for ai-memory-mcp.

- Master key = scrypt(passphrase, salt). The passphrase is entered at startup; the key
  lives in RAM only.
- Two subkeys are derived from the master key (HMAC labels): 'enc' (AES-GCM content
  encryption) and 'idx' (a deterministic blind index for looking up a record without
  revealing its name).
- Content is stored as AES-GCM (nonce, ciphertext) over a JSON payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 128 * 1024 * 1024
VERIFIER = b"ai-memory-verifier-v1"


def derive_kek(passphrase: str, salt: bytes,
               n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> bytes:
    """Key-encryption key from the passphrase (scrypt). Wraps/unwraps the data key (DEK)."""
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p,
                          dklen=32, maxmem=SCRYPT_MAXMEM)


def wrap_dek(kek: bytes, dek: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    return nonce, AESGCM(kek).encrypt(nonce, dek, None)


def unwrap_dek(kek: bytes, nonce: bytes, ct: bytes) -> bytes:
    """Unwrap the DEK. With a wrong passphrase (wrong KEK) it raises (InvalidTag)."""
    return AESGCM(kek).decrypt(bytes(nonce), bytes(ct), None)


class Cipher:
    def __init__(self, master_key: bytes) -> None:
        self.enc_key = hmac.new(master_key, b"enc", hashlib.sha256).digest()
        self.idx_key = hmac.new(master_key, b"idx", hashlib.sha256).digest()
        self._aes = AESGCM(self.enc_key)

    def encrypt(self, obj) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        data = json.dumps(obj, ensure_ascii=False).encode()
        return nonce, self._aes.encrypt(nonce, data, None)

    def decrypt(self, nonce: bytes, ct: bytes):
        return json.loads(self._aes.decrypt(bytes(nonce), bytes(ct), None).decode())

    def encrypt_bytes(self, data: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return nonce, self._aes.encrypt(nonce, data, None)

    def decrypt_bytes(self, nonce: bytes, ct: bytes) -> bytes:
        return self._aes.decrypt(bytes(nonce), bytes(ct), None)

    def blind_index(self, text: str) -> str:
        return hmac.new(self.idx_key, text.encode(), hashlib.sha256).hexdigest()
