from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from gametheory.config import Settings, get_settings
from gametheory.domain import Role
from gametheory.persistence import Administrator, Membership, Workspace

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    tenant: str
    object_id: str


@lru_cache
def jwks_client(authority: str, tenant: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{authority}/{tenant}/discovery/v2.0/keys", cache_keys=False, lifespan=300, timeout=10
    )


def authenticate(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if not settings.auth_configured:
        raise HTTPException(503, "Entra authentication is not configured")
    if credentials is None:
        raise HTTPException(401, "Sign in is required", headers={"WWW-Authenticate": "Bearer"})
    try:
        authority = settings.profile.authority.rstrip("/")
        key = jwks_client(authority, settings.tenant_id).get_signing_key_from_jwt(
            credentials.credentials
        )
        claims = jwt.decode(
            credentials.credentials,
            key.key,
            algorithms=["RS256"],
            audience=settings.api_audience,
            issuer=f"{authority}/{settings.tenant_id}/v2.0",
            options={"require": ["exp", "iat", "nbf", "tid", "oid", "sub", "aud", "iss"]},
        )
        if claims["tid"] != settings.tenant_id:
            raise jwt.InvalidTokenError("Wrong tenant")
        required_scope = settings.api_scope.rsplit("/", 1)[-1]
        if required_scope not in str(claims.get("scp", "")).split():
            raise jwt.InvalidTokenError("Required scope missing")
        return Principal(str(UUID(claims["tid"])), str(UUID(claims["oid"])))
    except jwt.PyJWKClientConnectionError as exc:
        raise HTTPException(503, "Identity signing keys are temporarily unavailable") from exc
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise HTTPException(
            401, "Token is invalid or expired", headers={"WWW-Authenticate": "Bearer"}
        ) from exc


def is_admin(db: Session, actor: Principal) -> bool:
    return db.get(Administrator, (actor.tenant, actor.object_id)) is not None


def require_admin(db: Session, actor: Principal) -> None:
    if not is_admin(db, actor):
        raise HTTPException(403, "Organization administrator access is required")


def authorize(db: Session, actor: Principal, workspace_id: str, role: str = "viewer") -> Role:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.organization_id != actor.tenant:
        raise HTTPException(404, "Workspace not found")
    actual = "owner" if is_admin(db, actor) else None
    if actual is None:
        membership = db.get(Membership, (workspace_id, actor.object_id))
        actual = membership.role if membership else None
    levels = {"viewer": 0, "editor": 1, "owner": 2}
    if actual is None:
        raise HTTPException(404, "Workspace not found")
    if levels.get(actual, -1) < levels[role]:
        raise HTTPException(403, f"Workspace {role} access is required")
    if actual == "owner":
        return "owner"
    if actual == "editor":
        return "editor"
    return "viewer"
