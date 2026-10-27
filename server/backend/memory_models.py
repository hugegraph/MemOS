"""Dashboard request contracts, source mappings and unambiguous UTC date ranges."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryTab = Literal["text_mem", "pref_mem", "event_mem", "profile_mem", "skill_mem", "tool_mem"]
TAB_TYPES = {
    "text_mem": ("WorkingMemory", "LongTermMemory", "UserMemory", "OuterMemory"),
    "pref_mem": ("PreferenceMemory",),
    "event_mem": ("EventMemory", "Context", "InsightMemory"),
    "profile_mem": ("ProfileMemory",),
    "skill_mem": ("SkillMemory",),
    "tool_mem": ("ToolSchemaMemory", "ToolTrajectoryMemory"),
}
TAB_VIEWS = {
    "text_mem": "detail_factual",
    "pref_mem": "preference",
    "event_mem": "event",
    "skill_mem": "skill",
    "tool_mem": "tool_memory",
}


def time_boundary(value: str | None, *, end: bool = False) -> tuple[datetime | None, bool]:
    if value is None:
        return None, False
    if len(value) == 10:
        parsed = datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
        return (parsed + timedelta(days=1) if end else parsed), end
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp must include timezone offset")
    return parsed.astimezone(timezone.utc), False


class MemoryListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str = Field(min_length=1, max_length=128)
    memoryDetailType: MemoryTab
    pageNum: int = Field(1, ge=1, strict=True)
    pageSize: int = Field(50, ge=1, le=100, strict=True)
    knowledgeBaseId: str | None = Field(None, max_length=256)
    startTime: str | None = Field(None, max_length=64)
    endTime: str | None = Field(None, max_length=64)
    externalUserId: str | None = Field(None, max_length=256)
    subjectType: Literal["user", "agent", "public"] | None = None
    memSearchContent: str | None = Field(None, max_length=1000)

    @field_validator(
        "knowledgeBaseId",
        "startTime",
        "endTime",
        "externalUserId",
        "memSearchContent",
        mode="before",
    )
    @classmethod
    def empty_is_missing(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_times(self) -> "MemoryListRequest":
        try:
            start, _ = time_boundary(self.startTime)
            end, exclusive = time_boundary(self.endTime, end=True)
        except OverflowError as exc:
            raise ValueError("Time boundary is outside the supported UTC range") from exc
        if start and end and (start > end or (exclusive and start == end)):
            raise ValueError("Invalid time range")
        return self


class MemoryDetailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memoryId: str = Field(min_length=1, max_length=1024, pattern=r"^[^\x00-\x1f\x7f]+$")
    memType: MemoryTab


class MemoryItem(BaseModel):
    memoryId: str
    memoryContent: str
    createTime: str
    memoryType: str
    subjectType: Literal["user", "agent", "public"] | None = None
    externalUserId: str | None = None
    skillName: str | None = None
    skillUrl: str | None = None
    profileTemplateId: str | None = None
    profileInstanceId: str | None = None


class MemoryPage(BaseModel):
    memListItemResponseList: list[MemoryItem]
    total: int
    current: int


def build_filter(request: MemoryListRequest) -> dict | None:
    conditions = []
    if request.memSearchContent:
        conditions.append(
            {
                "or": [
                    {"memory": {"like": request.memSearchContent}},
                    {"name": {"like": request.memSearchContent}},
                ]
            }
        )
    if request.subjectType:
        conditions.append({"owner_type": request.subjectType})
    if request.externalUserId:
        conditions.append(
            {"or": [{"user_id": request.externalUserId}, {"owner_id": request.externalUserId}]}
        )
    if request.knowledgeBaseId:
        conditions.append(
            {
                "or": [
                    {"knowledgebase_id": request.knowledgeBaseId},
                    {"knowledge_base_id": request.knowledgeBaseId},
                    {"knowledgebase_ids": {"contains": request.knowledgeBaseId}},
                ]
            }
        )
    start, _ = time_boundary(request.startTime)
    end, exclusive = time_boundary(request.endTime, end=True)
    if start:
        conditions.append({"created_at": {"gte": start.isoformat()}})
    if end:
        conditions.append({"created_at": {"lt" if exclusive else "lte": end.isoformat()}})
    return {"and": conditions} if conditions else None
