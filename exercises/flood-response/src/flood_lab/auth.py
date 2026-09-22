from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from flood_lab.config import Settings


@dataclass(frozen=True)
class Actor:
    tenant_id: UUID
    object_id: UUID
    kind: Literal["user", "service"]

    @property
    def key(self) -> str:
        return f"{self.tenant_id}:{self.object_id}:{self.kind}"


class EntraVerifier:
    def __init__(self, settings: Settings):
        settings.require_auth()
        self.settings = settings
        self.issuer = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"
        self.jwks = PyJWKClient(
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}/discovery/v2.0/keys",
            cache_keys=True,
            lifespan=300,
            timeout=10,
        )

    def claims_to_actor(self, claims: dict[str, Any]) -> Actor:
        if claims.get("tid") != self.settings.entra_tenant_id or claims.get("ver") != "2.0":
            raise ValueError("Invalid tenant or token version")
        scopes = claims.get("scp", "")
        if not isinstance(scopes, str):
            raise ValueError("Invalid scope claim")
        if scopes:
            if self.settings.entra_scope not in scopes.split() or claims.get("idtyp") == "app":
                raise ValueError("Required delegated scope is absent")
            kind: Literal["user", "service"] = "user"
        else:
            roles = claims.get("roles", [])
            if (
                claims.get("idtyp") != "app"
                or not isinstance(roles, list)
                or self.settings.entra_service_role not in roles
            ):
                raise ValueError("Required application role is absent")
            kind = "service"
        return Actor(UUID(claims["tid"]), UUID(claims["oid"]), kind)

    def verify(self, token: str) -> Actor:
        key = self.jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=self.settings.entra_audience,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid", "sub"]},
            leeway=15,
        )
        return self.claims_to_actor(claims)


bearer = HTTPBearer(
    auto_error=False,
    scheme_name="LabEntraBearer",
    description=(
        "Entra v2 token for the configured lab audience and delegated scope/application role."
    ),
)


def current_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> Actor:
    settings: Settings = request.app.state.settings
    settings.require_auth()
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 16384:
        raise HTTPException(401, "authentication_required", headers={"WWW-Authenticate": "Bearer"})
    if request.app.state.verifier is None:
        request.app.state.verifier = EntraVerifier(settings)
    try:
        return request.app.state.verifier.verify(token)
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise HTTPException(
            401, "invalid_access_token", headers={"WWW-Authenticate": "Bearer"}
        ) from None
