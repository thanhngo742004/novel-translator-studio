from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from nts_storage.workspace import Workspace


ProviderType = Literal[
    "mock",
    "openai_responses",
    "openai_chat_compatible",
    "anthropic_messages",
    "google_gemini",
]


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: ProviderType
    base_url: str | None = None
    api_key_env: str | None = None
    api_key_optional: bool = False
    enabled: bool = True

    @field_validator("api_key_env")
    @classmethod
    def api_key_env_must_be_name(cls, value: str | None) -> str | None:
        if value and any(marker in value for marker in ("sk-", " ", "\n", "\r")):
            raise ValueError("api_key_env must be an environment variable name, not a raw key")
        return value


class ProvidersFile(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_provider(self) -> "ProvidersFile":
        if not self.providers:
            raise ValueError("providers must contain at least one provider")
        return self


class TaskPrimary(BaseModel):
    provider: str
    model_class: str


class TaskPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    structured_output: bool | None = None
    max_cost_usd: float | None = None
    prefer_different_provider_from: str | None = None


class TaskRoute(BaseModel):
    primary: TaskPrimary
    policy: TaskPolicy = Field(default_factory=TaskPolicy)


class RoutingFile(BaseModel):
    tasks: dict[str, TaskRoute] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_tasks(self) -> "RoutingFile":
        if not self.tasks:
            raise ValueError("tasks must contain at least one task route")
        return self


NlpProviderKind = Literal["ltp_server", "fallback_simple"]


class LtpServerConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    base_url: str = "http://127.0.0.1:3003"
    working_dir: str | None = "C:/Users/Admin/tools/ltp-server"
    start_command: str = "cargo run --release"
    executable: str | None = None
    startup_timeout_seconds: int = 30
    request_timeout_seconds: int = 15
    max_sentences_per_request: int = 512

    @field_validator("startup_timeout_seconds", "request_timeout_seconds", "max_sentences_per_request")
    @classmethod
    def positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value


class NlpFallbackConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class NlpSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    provider: NlpProviderKind = "ltp_server"
    auto_start: bool = True
    ltp_server: LtpServerConfig = Field(default_factory=LtpServerConfig)
    fallback: NlpFallbackConfig = Field(default_factory=NlpFallbackConfig)


class NlpConfigFile(BaseModel):
    nlp: NlpSettings = Field(default_factory=NlpSettings)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file must be a YAML mapping: {path}")
    return loaded


def load_providers(path: Path) -> ProvidersFile:
    try:
        return ProvidersFile.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def load_routing(path: Path) -> RoutingFile:
    try:
        return RoutingFile.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def load_nlp_config(path: Path | None = None, workspace: Workspace | None = None) -> NlpSettings:
    if path is None and workspace is not None:
        path = workspace.config_dir / "nlp.yaml"
    if path is not None and path.exists():
        raw = _read_yaml(path)
    else:
        raw = {"nlp": NlpSettings().model_dump()}
    try:
        return NlpConfigFile.model_validate(raw).nlp
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def default_config_paths(workspace: Workspace | None) -> tuple[Path, Path]:
    if workspace:
        return workspace.config_dir / "providers.yaml", workspace.config_dir / "routing.yaml"
    repo_root = Path.cwd()
    return repo_root / "config" / "providers.example.yaml", repo_root / "config" / "task-routing.example.yaml"


def validate_config_files(
    *,
    providers: Path | None = None,
    routing: Path | None = None,
    workspace: Workspace | None = None,
) -> dict[str, Any]:
    default_providers, default_routing = default_config_paths(workspace)
    providers_path = providers or default_providers
    routing_path = routing or default_routing
    providers_file = load_providers(providers_path)
    routing_file = load_routing(routing_path)
    nlp_path = workspace.config_dir / "nlp.yaml" if workspace else None
    nlp_file = load_nlp_config(nlp_path, workspace=workspace)

    provider_keys = set(providers_file.providers)
    missing = sorted(
        {
            route.primary.provider
            for route in routing_file.tasks.values()
            if route.primary.provider not in provider_keys
        }
    )
    if missing:
        raise ValueError(f"Routing references unknown provider(s): {', '.join(missing)}")

    return {
        "providers_path": str(providers_path),
        "routing_path": str(routing_path),
        "providers": sorted(provider_keys),
        "tasks": sorted(routing_file.tasks),
        "nlp": {
            "enabled": nlp_file.enabled,
            "provider": nlp_file.provider,
            "auto_start": nlp_file.auto_start,
            "fallback_enabled": nlp_file.fallback.enabled,
        },
        "valid": True,
    }
