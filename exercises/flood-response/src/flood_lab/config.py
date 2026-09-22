from __future__ import annotations

import re
from uuid import UUID

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class SetupRequired(Exception):
    """Configuration is deliberately incomplete; never fall back to another app."""


def database_target(url: str, expected_name: str) -> URL:
    if not re.fullmatch(r"flood_lab_[a-zA-Z0-9_]{1,80}", expected_name):
        raise SetupRequired("A dedicated flood_lab_ database name is required.")
    try:
        parsed = make_url(url)
    except Exception:
        raise SetupRequired("A dedicated MSSQL URL is required.") from None
    if parsed.drivername != "mssql+pyodbc" or parsed.database != expected_name:
        raise SetupRequired("Database driver or dedicated database name does not match.")
    if "odbc_connect" in parsed.query:
        raise SetupRequired("Opaque ODBC connection strings are not accepted.")
    if parsed.query.get("driver") != "ODBC Driver 18 for SQL Server":
        raise SetupRequired("ODBC Driver 18 for SQL Server is required.")
    if str(parsed.query.get("Encrypt", "")).lower() not in {"yes", "true", "mandatory"}:
        raise SetupRequired("Encrypted SQL transport is required.")
    return parsed


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLOOD_LAB_", extra="ignore")

    database_url: str = ""
    database_name: str = ""
    operator_database_url: str = ""
    entra_tenant_id: str = ""
    entra_audience: str = ""
    entra_scope: str = "FloodLab.Access"
    entra_service_role: str = "FloodLab.Service"
    allowed_origins: list[str] = []

    @field_validator("allowed_origins")
    @classmethod
    def origins_are_explicit(cls, value: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for origin in value:
            parsed = urlsplit(origin)
            local = parsed.hostname in {"localhost", "127.0.0.1", "[::1]", "::1"}
            if (
                not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
            ):
                raise ValueError("Origins must be explicit HTTPS origins or loopback HTTP.")
        return [origin.rstrip("/") for origin in value]

    def require_auth(self) -> None:
        try:
            UUID(self.entra_tenant_id)
        except ValueError:
            raise SetupRequired("Configure the lab Entra tenant, audience and scope.") from None
        if not self.entra_audience or not self.entra_scope or not self.entra_service_role:
            raise SetupRequired("Configure the lab Entra tenant, audience and scope.")

    def database(self, *, operator: bool = False) -> URL:
        url = self.operator_database_url if operator else self.database_url
        return database_target(url, self.database_name)
