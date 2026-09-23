from functools import cached_property, lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authority: str
    storage_scope: str
    model_scope: str
    scheduler_scope: str
    scheduler_supported: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GT_", env_file=".env", extra="ignore")
    cloud: Literal["commercial", "government", "custom"] = Field(
        default="commercial", validation_alias="AZURE_CLOUD"
    )
    cloud_profile: str = Field(default="", validation_alias="AZURE_CLOUD_PROFILE")
    tenant_id: str = ""
    spa_client_id: str = ""
    api_audience: str = ""
    api_scope: str = ""
    sql_url: str = ""
    blob_url: str = ""
    blob_container: str = "assets"
    blob_connection_string: str = ""
    scheduler_endpoint: str = ""
    scheduler_taskhub: str = "gametheory"
    scheduler_emulator: bool = False
    foundry_project_endpoint: str = ""
    model_deployment: str = ""
    planning_enabled: bool = False
    execution_enabled: bool = False
    execution_taskhub: str = "gametheory-exercises"
    execution_bindings_file: str = ""
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    model_timeout_seconds: int = Field(default=120, ge=10, le=600)
    planner_max_output_tokens: int = Field(default=6000, ge=256, le=16000)
    web_dist: str = "apps/web/dist"

    @cached_property
    def profile(self) -> CloudProfile:
        if self.cloud_profile:
            return CloudProfile.model_validate_json(Path(self.cloud_profile).read_text())
        if self.cloud == "commercial":
            return CloudProfile(
                authority="https://login.microsoftonline.com",
                storage_scope="https://storage.azure.com/.default",
                model_scope="https://ai.azure.com/.default",
                scheduler_scope="https://durabletask.io/.default",
                scheduler_supported=True,
            )
        if self.cloud == "government":
            return CloudProfile(
                authority="https://login.microsoftonline.us",
                storage_scope="",
                model_scope="",
                scheduler_scope="",
            )
        raise ValueError("AZURE_CLOUD_PROFILE is required for a custom cloud")

    @model_validator(mode="after")
    def validate_cloud(self) -> Self:
        profile = self.profile
        if urlparse(profile.authority).scheme != "https":
            raise ValueError("Identity authority must use HTTPS")
        if self.planning_enabled:
            if self.cloud != "commercial" or not profile.scheduler_supported:
                raise ValueError("Planning execution is not validated for this cloud")
            if (
                profile.authority.rstrip("/") != "https://login.microsoftonline.com"
                or profile.model_scope != "https://ai.azure.com/.default"
                or profile.scheduler_scope != "https://durabletask.io/.default"
            ):
                raise ValueError(
                    "The current planning SDK integration requires the commercial authority and audiences"
                )
            if not all(
                [
                    self.sql_url,
                    self.scheduler_endpoint,
                    self.foundry_project_endpoint,
                    self.model_deployment,
                    self.tenant_id,
                ]
            ):
                raise ValueError(
                    "Planning requires SQL, scheduler, Foundry, model, and tenant settings"
                )
            if urlparse(self.foundry_project_endpoint).scheme != "https":
                raise ValueError("Foundry must use HTTPS")
        if self.scheduler_endpoint:
            endpoint = urlparse(self.scheduler_endpoint)
            if self.scheduler_emulator:
                host = endpoint.hostname or ""
                try:
                    local = ip_address(host).is_loopback
                except ValueError:
                    local = host in {"localhost", "scheduler"}
                if not local or endpoint.scheme != "http":
                    raise ValueError("Unauthenticated scheduler is limited to the local emulator")
            elif endpoint.scheme != "https":
                raise ValueError("Managed scheduler must use HTTPS")
        if self.execution_enabled:
            if self.cloud != "commercial" or not profile.scheduler_supported:
                raise ValueError("Exercise execution requires the commercial Scheduler runtime")
            if (
                profile.authority.rstrip("/") != "https://login.microsoftonline.com"
                or profile.scheduler_scope != "https://durabletask.io/.default"
            ):
                raise ValueError(
                    "Exercise execution requires the commercial authority and Scheduler audience"
                )
            if not all(
                [
                    self.sql_url,
                    self.tenant_id,
                    self.scheduler_endpoint,
                    self.execution_bindings_file,
                ]
            ):
                raise ValueError(
                    "Execution requires SQL, tenant, Scheduler, and operator target bindings"
                )
            if not self.execution_taskhub or self.execution_taskhub == self.scheduler_taskhub:
                raise ValueError("Execution requires a separate task hub from planning")
        return self

    @property
    def auth_configured(self) -> bool:
        return all([self.tenant_id, self.spa_client_id, self.api_audience, self.api_scope])


@lru_cache
def get_settings() -> Settings:
    return Settings()
