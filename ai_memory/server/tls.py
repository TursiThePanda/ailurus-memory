"""HTTPS without a reverse proxy: a generated self-signed certificate, or your own.

The files live in the config directory (`tls/`), the key readable by the owner only.
uvicorn reads them at start, so a new certificate takes effect on the next restart --
the admin web says so rather than pretending otherwise.

Self-signed validity is 397 days: browsers refuse longer-lived leaf certificates even
when you have chosen to trust them, and a certificate that silently stops working on
one browser is worse than one that has to be regenerated once a year.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from ai_memory.core import paths
VALID_DAYS = 397


def tls_dir() -> Path:
    return paths.config_dir() / "tls"


def cert_path() -> Path:
    return tls_dir() / "cert.pem"


def key_path() -> Path:
    return tls_dir() / "key.pem"


def _write(path: Path, data: bytes, private: bool) -> None:
    paths.ensure(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _sans(names: list[str]) -> list[x509.GeneralName]:
    out: list[x509.GeneralName] = []
    seen = set()
    for n in ["localhost", "127.0.0.1", "::1", *names]:
        n = n.strip().lower()
        if not n or n in seen or n in ("0.0.0.0", "::"):
            continue
        seen.add(n)
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            out.append(x509.DNSName(n))
    return out


def generate_self_signed(names: list[str]) -> dict:
    """New key + certificate for the given host names / IPs (plus localhost)."""
    key = ec.generate_private_key(ec.SECP256R1())
    cn = next((n for n in names if n and not n[0].isdigit()), "ai-memory")
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ailurus Memory")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(_sans(names)), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    _write(key_path(), key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()), private=True)
    _write(cert_path(), cert.public_bytes(serialization.Encoding.PEM), private=False)
    return info()


def install_custom(cert_pem: bytes, key_pem: bytes) -> dict:
    """Validate and install an uploaded certificate (chain allowed) and its key."""
    try:
        certs = x509.load_pem_x509_certificates(cert_pem)
    except ValueError:
        raise ValueError("The certificate is not a PEM certificate.") from None
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except (ValueError, TypeError):
        raise ValueError("The key is not an unencrypted PEM private key.") from None
    leaf = certs[0]
    pub = lambda k: k.public_bytes(serialization.Encoding.DER,  # noqa: E731
                                   serialization.PublicFormat.SubjectPublicKeyInfo)
    if pub(leaf.public_key()) != pub(key.public_key()):
        raise ValueError("The key does not belong to the certificate.")
    if leaf.not_valid_after_utc < dt.datetime.now(dt.timezone.utc):
        raise ValueError("The certificate has expired.")
    _write(key_path(), key_pem, private=True)
    _write(cert_path(), cert_pem, private=False)
    return info()


def info() -> dict | None:
    """What is installed: subject, names, expiry, self-signed or not. None if nothing."""
    try:
        cert = x509.load_pem_x509_certificates(cert_path().read_bytes())[0]
    except (OSError, ValueError, IndexError):
        return None
    try:
        names = [str(n.value) for n in
                 cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value]
    except x509.ExtensionNotFound:
        names = []
    return {"subject": cert.subject.rfc4514_string(), "names": names,
            "not_after": cert.not_valid_after_utc.date().isoformat(),
            "self_signed": cert.issuer == cert.subject,
            "expired": cert.not_valid_after_utc < dt.datetime.now(dt.timezone.utc)}


def ensure(mode: str, names: list[str]) -> tuple[str, str] | None:
    """(certfile, keyfile) for uvicorn, or None when TLS is off. Self-signed mode
    generates the pair on first use and renews it when it has expired."""
    if mode == "off":
        return None
    if mode == "self-signed":
        i = info()
        if i is None or i["expired"] or not key_path().exists():
            generate_self_signed(names)
    elif not (cert_path().exists() and key_path().exists()):
        raise RuntimeError(f"TLS is set to 'custom' but {cert_path()} / {key_path()} "
                           "are missing -- upload them in the admin web or switch TLS off "
                           "with `ai-memory network reset`.")
    return str(cert_path()), str(key_path())
