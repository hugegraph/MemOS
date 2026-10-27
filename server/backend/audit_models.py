"""Call log query contracts; output unknown history is kept explicit."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .memory_models import time_boundary

EVENT_TYPES = frozenset(
    (
        "addMessage",
        "searchMemory",
        "getMemory",
        "deleteMemory",
        "addFeedback",
        "manualAddMemory",
        "updateMemory",
        "bindProfileTemplate",
        "editProfile",
        "deleteProfile",
        "getMessage",
        "chat",
        "extractMemory",
        "rerank",
    )
)


class LogListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str = Field(min_length=1, max_length=128)
    pageNum: int = Field(1, ge=1, le=2_147_483_647, strict=True)
    pageSize: int = Field(50, ge=1, le=100, strict=True)
    scope: Literal["all", "gateway", "console"] = "all"
    subjectType: Literal["all", "user", "agent"] | None = None
    subjectId: str | None = Field(None, max_length=256)
    eventType: str | None = Field(None, max_length=256)
    query: str | None = Field(None, max_length=1000)
    status: Literal["SUCCESS", "FAILURE", "PENDING", "UNKNOWN"] | None = None
    startTime: str | None = Field(None, max_length=64)
    endTime: str | None = Field(None, max_length=64)

    @field_validator(
        "subjectType",
        "subjectId",
        "eventType",
        "query",
        "status",
        "startTime",
        "endTime",
        mode="before",
    )
    @classmethod
    def empty_is_missing(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("status", mode="before")
    @classmethod
    def status_alias(cls, value):
        if not isinstance(value, str) or not value.strip():
            return None if value == "" else value
        return {"FAILED": "FAILURE", "FAIL": "FAILURE"}.get(value.upper(), value.upper())

    @field_validator("eventType")
    @classmethod
    def event_filter(cls, value):
        value = "extractMemory" if value == "extractMemoryDirect" else value
        if value is not None and value not in EVENT_TYPES:
            raise ValueError("Unsupported event type filter")
        return value

    @model_validator(mode="after")
    def validate_filters(self):
        if self.subjectId is None:
            self.subjectType = None
        try:
            start, _ = time_boundary(self.startTime)
            end, exclusive = time_boundary(self.endTime, end=True)
        except OverflowError as exc:
            raise ValueError("UTC date boundary out of range") from exc
        if start and end and (start > end or (exclusive and start == end)):
            raise ValueError("Invalid date range")
        return self


class LogDetailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logId: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
