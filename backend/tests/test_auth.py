from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from gametheory.auth import authenticate
from gametheory.config import Settings


@pytest.fixture
def identity(monkeypatch):
    tenant, oid = str(uuid4()), str(uuid4())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        "gametheory.auth.jwks_client",
        lambda *_: SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
        ),
    )
    settings = Settings(
        _env_file=None,
        tenant_id=tenant,
        spa_client_id="spa",
        api_audience="api",
        api_scope="api://api/access_as_user",
    )
    claims = {
        "tid": tenant,
        "oid": oid,
        "sub": oid,
        "aud": "api",
        "iss": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "scp": "access_as_user",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        "nbf": datetime.now(UTC) - timedelta(seconds=1),
    }
    return settings, claims, key


def test_valid_tenant_bound_access_token(identity):
    settings, claims, key = identity
    token = jwt.encode(claims, key, algorithm="RS256")
    actor = authenticate(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), settings)
    assert actor.object_id == claims["oid"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("tid", str(uuid4())),
        ("aud", "different-api"),
        ("iss", "https://untrusted.test"),
        ("scp", "User.Read"),
        ("exp", datetime.now(UTC) - timedelta(minutes=1)),
    ],
)
def test_invalid_claims_rejected(identity, field, value):
    settings, claims, key = identity
    claims[field] = value
    token = jwt.encode(claims, key, algorithm="RS256")
    with pytest.raises(HTTPException) as caught:
        authenticate(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), settings)
    assert caught.value.status_code == 401


def test_no_auth_bypass(identity):
    settings, _, _ = identity
    with pytest.raises(HTTPException) as caught:
        authenticate(None, settings)
    assert caught.value.status_code == 401
