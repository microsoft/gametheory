from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from flood_lab.auth import EntraVerifier
from flood_lab.config import Settings

TENANT = str(uuid4())
AUDIENCE = "api://independent-lab-test-only"


@pytest.fixture
def verifier():
    return EntraVerifier(
        Settings(
            entra_tenant_id=TENANT,
            entra_audience=AUDIENCE,
            database_url="",
            database_name="",
            allowed_origins=[],
        )
    )


def claims(**changes):
    now = datetime.now(UTC)
    return {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": AUDIENCE,
        "tid": TENANT,
        "oid": str(uuid4()),
        "sub": "TEST-ONLY-SIGNED-TOKEN",
        "ver": "2.0",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "scp": "FloodLab.Access",
        **changes,
    }


def test_real_signature_validation_without_network(verifier, monkeypatch):
    # TEST ONLY: supply a test RSA public key, not a production verification bypass.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        verifier.jwks,
        "get_signing_key_from_jwt",
        lambda token: SimpleNamespace(key=key.public_key()),
    )
    actor = verifier.verify(jwt.encode(claims(), key, algorithm="RS256"))
    assert actor.kind == "user"
    for change in (
        {"aud": "another-api"},
        {"iss": "https://invalid.example/v2.0"},
        {"tid": str(uuid4())},
        {"scp": "Some.OtherScope"},
        {"exp": datetime.now(UTC) - timedelta(minutes=2)},
        {"nbf": datetime.now(UTC) + timedelta(minutes=2)},
        {"ver": "1.0"},
        {"oid": "not-an-object-id"},
    ):
        with pytest.raises((jwt.PyJWTError, ValueError)):
            verifier.verify(jwt.encode(claims(**change), key, algorithm="RS256"))
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.InvalidSignatureError):
        verifier.verify(jwt.encode(claims(), other_key, algorithm="RS256"))


def test_service_scope_is_not_participant_authority(verifier):
    service = verifier.claims_to_actor(claims(scp="", roles=["FloodLab.Service"], idtyp="app"))
    assert service.kind == "service"
    for changes in (
        {"scp": "", "roles": []},
        {"scp": "", "roles": ["FloodLab.Service"]},
        {"idtyp": "app"},
        {"scp": "", "roles": ["OtherRole"], "idtyp": "app"},
        {"scp": ["FloodLab.Access"]},
    ):
        with pytest.raises(ValueError):
            verifier.claims_to_actor(claims(**changes))
