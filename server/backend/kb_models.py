"""Strict dashboard knowledge requests, with explicit project selection support."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = str


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Scoped(Strict):
    projectId: str | None = Field(None, min_length=1, max_length=128)


class Page(Scoped):
    pageNum: int = Field(1, ge=1, le=2147483647)
    pageSize: int = Field(10, ge=1, le=200)


class KBList(Page):
    name: str = Field("", max_length=128)


class KBCreate(Scoped):
    knowledgebaseName: str = Field(min_length=1, max_length=128)
    knowledgebaseDescription: str = Field("", max_length=4000)
    projectIds: list[str] | None = Field(None, max_length=100)

    @field_validator("knowledgebaseName")
    @classmethod
    def name(cls, value):
        if not isinstance(value, str):
            raise ValueError("Expected text")
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid name")
        return value

    @field_validator("projectIds")
    @classmethod
    def unique(cls, value):
        if value is not None and (
            len(value) != len(set(value)) or any(not x or len(x) > 128 for x in value)
        ):
            raise ValueError("Invalid project IDs")
        return value


class KBUpdate(KBCreate):
    id: str = Field(min_length=1, max_length=128)
    knowledgebaseName: str | None = Field(None, min_length=1, max_length=128)
    knowledgebaseDescription: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def changes(self):
        fields = self.model_fields_set - {"id", "projectId"}
        if not fields or any(getattr(self, k) is None for k in fields):
            raise ValueError("Expected non-null update")
        return self


class KBDetail(Scoped):
    knowledgeBaseId: str = Field(min_length=1, max_length=128)


class KBDelete(KBDetail):
    projectId: str = Field(min_length=1, max_length=128)


class Associate(Strict):
    projectId: str = Field(min_length=1, max_length=128)
    knowledgeBaseIds: list[str] = Field(max_length=100)

    @field_validator("knowledgeBaseIds")
    @classmethod
    def ids(cls, value):
        if len(set(value)) != len(value) or any(not x or len(x) > 128 for x in value):
            raise ValueError("Invalid IDs")
        return value


class Remove(KBDelete):
    deleteAll: bool = False
