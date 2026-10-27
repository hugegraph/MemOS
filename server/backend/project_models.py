"""Writable fields only: clients cannot assign ownership, cube IDs or archive metadata."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProjectPageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pageNum: int = Field(1, ge=1, le=2147483647)
    pageSize: int = Field(10, ge=1, le=200)


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(max_length=4000)
    isMultiView: int = Field(0, ge=0, le=1)

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127 for c in value):
                raise ValueError("Invalid text")
        return value


class ProjectUpdate(ProjectCreate):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=4000)
    isMultiView: int | None = Field(None, ge=0, le=1)
    status: Literal["ENABLED", "DISABLED"] | None = None

    @model_validator(mode="after")
    def actual_change(self):
        fields = self.model_fields_set - {"id"}
        if not fields or any(getattr(self, key) is None for key in fields):
            raise ValueError("Expected explicit non-null update")
        return self
