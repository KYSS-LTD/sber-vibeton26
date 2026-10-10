import base64
import hashlib
import json
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from .config import secret


def digest(value: str | bytes) -> str:
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def encrypt(value: str) -> str:
    return Fernet(secret("encryption_key").encode()).encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return Fernet(secret("encryption_key").encode()).decrypt(value.encode()).decode()


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_device(jwk, challenge, signature):
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or "d" in jwk:
        raise ValueError("Invalid public key")
    public = ec.EllipticCurvePublicNumbers(
        int.from_bytes(b64decode(jwk["x"]), "big"),
        int.from_bytes(b64decode(jwk["y"]), "big"),
        ec.SECP256R1(),
    ).public_key()
    raw = b64decode(signature)
    if len(raw) != 64:
        raise ValueError("Expected WebCrypto P1363 signature")
    der = encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    public.verify(der, challenge.encode(), ec.ECDSA(hashes.SHA256()))


def canonical_challenge(**values):
    return json.dumps(values, sort_keys=True, separators=(",", ":"))
