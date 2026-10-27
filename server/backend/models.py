"""Public responses and deliberately limited upstream health projection."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt

SERVICE_NAME = "memos-dashboard-backend"
T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    code: int = 0
    data: T
    message: str = "success"


class UpstreamHealth(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    status: Literal["healthy"]
    service: Literal["memos"]
    version: str = Field(min_length=1, max_length=64)
    contract_version: StrictInt = Field(ge=2, le=2)


class ToolInfo(BaseModel):
    name: str
    title: str


# Gateway protocol descriptors, not a claim that Dashboard business routes exist.
GATEWAY_TOOLS = (
    ToolInfo(name="add_message", title="写入记忆"),
    ToolInfo(name="search_memory", title="检索记忆"),
    ToolInfo(name="get_user_profile", title="读取画像"),
    ToolInfo(name="delete_memory", title="删除记忆"),
    ToolInfo(name="add_feedback", title="提交反馈"),
)


class Meta(BaseModel):
    mode: Literal["live"] = "live"
    service: str = SERVICE_NAME
    health: UpstreamHealth
    keyPrefix: str = "sk-"
    tools: tuple[ToolInfo, ...] = GATEWAY_TOOLS
    authEnabled: bool = False
