import base64
from datetime import datetime, timezone
from uuid import uuid4
import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.exceptions import InvalidSignature
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from app.security import verify_device, canonical_challenge, digest
from app.db import Actor, require_roles
from app.llm import parse_object, facts_from_results
from app.main import GeoInput, period, app
from app import auth


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def test_device_signature_binds_every_passage_field():
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": b64(numbers.x.to_bytes(32, "big")),
        "y": b64(numbers.y.to_bytes(32, "big")),
    }
    challenge = canonical_challenge(
        attempt_id=str(uuid4()),
        site_id=str(uuid4()),
        direction="entry",
        nonce="new-nonce",
    )
    r, s = decode_dss_signature(
        private.sign(challenge.encode(), ec.ECDSA(hashes.SHA256()))
    )
    signature = b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    verify_device(jwk, challenge, signature)
    with pytest.raises(InvalidSignature):
        verify_device(jwk, challenge.replace("entry", "exit"), signature)
    with pytest.raises(InvalidSignature):
        verify_device(jwk, challenge.replace("new-nonce", "old-nonce"), signature)
    with pytest.raises(ValueError):
        verify_device(jwk | {"d": "private"}, challenge, signature)


def test_employee_cannot_select_controller_or_manager_actions():
    actor = Actor(uuid4(), frozenset({"employee"}))
    for role in ["admin", "manager", "controller"]:
        with pytest.raises(HTTPException) as exc:
            require_roles(actor, role)
        assert exc.value.status_code == 403


def test_qr_hash_is_not_credential_plaintext():
    assert digest("daily-secret") != "daily-secret"
    assert digest("daily-secret") != digest("another-day")


def test_llm_numbers_are_rendered_from_db_facts():
    facts = facts_from_results(
        {"period_summary": {"employees": 2, "shifts": 3, "hours": 12.75}}
    )
    assert "12.75" in facts[0]["text"] and "3" in facts[0]["text"]
    assert parse_object('<think>ignore me</think>{"tools":["onsite_now"]}') == {
        "tools": ["onsite_now"]
    }
    with pytest.raises(ValueError):
        parse_object('["DROP TABLE shifts"]')


def test_api_rejects_client_supplied_authentication_decisions():
    with pytest.raises(ValueError):
        GeoInput(
            shift_id=uuid4(),
            sample_id=uuid4(),
            measured_at=datetime.now(timezone.utc),
            latitude=55,
            longitude=37,
            accuracy_m=10,
            site_id=uuid4(),
        )
    with pytest.raises(HTTPException):
        period(datetime.now(), datetime.now())
    paths = app.openapi()["paths"]
    assert "/api/v1/passages/{attempt_id}/verify" in paths


def test_jwt_rejects_wrong_audience_issuer_and_unsigned_tokens(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True) | {
        "kid": "test-key"
    }
    monkeypatch.setattr(auth, "_jwks", jwt.PyJWKSet.from_dict({"keys": [jwk]}))
    monkeypatch.setattr(auth, "_until", float("inf"))
    claims = {
        "sub": str(uuid4()),
        "iat": int(datetime.now().timestamp()),
        "exp": int(datetime.now().timestamp()) + 60,
        "iss": "https://attacker.invalid",
        "aud": "wrong",
    }
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
    assert exc.value.status_code == 401
    token = jwt.encode(claims, key="", algorithm="none", headers={"kid": "test-key"})
    with pytest.raises(HTTPException):
        auth.authenticate(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
