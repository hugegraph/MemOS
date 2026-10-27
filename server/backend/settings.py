"""Small explicit configuration surface; never load model credentials."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    upstream_url: str = "http://127.0.0.1:8000/"
    upstream_timeout_seconds: float = Field(default=3.0, gt=0, le=60, allow_inf_nan=False)
    projects_seed: tuple[tuple[str, str], ...] = ()
    default_project_id: str | None = None
    data_dir: Path = Path(".data")
    audit_gateway_dir: Path | None = None
    audit_console_dir: Path | None = None
    auth_enabled: bool = True
    session_ttl_seconds: int = Field(86400, ge=60, le=2592000)
    model_timeout_seconds: float = Field(120, gt=0, le=180, allow_inf_nan=False)
    gateway_keys_file: Path | None = None
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5274",
        "http://127.0.0.1:5276",
        "http://127.0.0.1:5281",
        "http://localhost:5274",
        "http://localhost:5276",
        "http://localhost:5281",
        "http://[::1]:5274",
        "http://[::1]:5276",
        "http://[::1]:5281",
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def origins(cls, value):
        if isinstance(value, str):
            value = tuple(part.strip() for part in value.split(";") if part.strip())
        if not value:
            raise ValueError("Set at least one explicit console origin")
        for origin in value:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path
            ):
                raise ValueError(
                    "Origins must be explicit HTTP(S) origins without paths or credentials"
                )
            _ = parsed.port
        return value

    @field_validator("projects_seed", mode="before")
    @classmethod
    def parse_projects(cls, value):
        if isinstance(value, str):
            if not value:
                return ()
            value = tuple(tuple(part.split(":")) for part in value.split(";"))
        for pair in value:
            if len(pair) != 2 or any(
                not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", x)
                for x in pair
            ):
                raise ValueError("Projects must be projectId:cubeId pairs of valid identifiers")
        if len({x[0] for x in value}) != len(value) or len({x[1] for x in value}) != len(value):
            raise ValueError("Project IDs and cube IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_default(self) -> "Settings":
        ids = {pair[0] for pair in self.projects_seed}
        if self.default_project_id is not None and self.default_project_id not in ids:
            raise ValueError("Default project must be in configured projects")
        if ids and self.default_project_id is None:
            raise ValueError("Set DASHBOARD_DEFAULT_PROJECT_ID when enabling project reads")
        return self

    @field_validator("upstream_url")
    @classmethod
    def validate_upstream_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTP(S) URL without credentials, query, or fragment")
        # Accessing port also validates malformed and out-of-range ports.
        _ = parsed.port
        return value.rstrip("/") + "/"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            upstream_url=os.environ.get("DASHBOARD_UPSTREAM_URL", "http://127.0.0.1:8000/"),
            upstream_timeout_seconds=os.environ.get("DASHBOARD_UPSTREAM_TIMEOUT_SECONDS", "3"),
            projects_seed=os.environ.get("DASHBOARD_PROJECTS", ""),
            default_project_id=os.environ.get("DASHBOARD_DEFAULT_PROJECT_ID") or None,
            data_dir=Path(os.environ.get("DASHBOARD_DATA_DIR", ".data")),
            audit_gateway_dir=os.environ.get("DASHBOARD_AUDIT_GATEWAY_DIR") or None,
            audit_console_dir=os.environ.get("DASHBOARD_AUDIT_CONSOLE_DIR") or None,
            auth_enabled=os.environ.get("DASHBOARD_AUTH_ENABLED", "true"),
            session_ttl_seconds=os.environ.get("DASHBOARD_SESSION_TTL_SECONDS", "86400"),
            model_timeout_seconds=os.environ.get("DASHBOARD_MODEL_TIMEOUT_SECONDS", "120"),
            gateway_keys_file=os.environ.get("DASHBOARD_GATEWAY_KEYS_FILE") or None,
            allowed_origins=os.environ.get("DASHBOARD_ALLOWED_ORIGINS")
            or cls.model_fields["allowed_origins"].default,
        )
