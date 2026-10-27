"""Fixed responses permitted by Issue3 section 2(3), not real quota or billing.

No upstream lookup, knowledge-base access or URL fetching takes place here.
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from .models import Envelope

router = APIRouter()


class SubscriptionRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    cycleId: str = ""
    includeAllCycles: bool = True


class SkillRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    oss_url: str


@router.get("/api/user/quota/summary", response_model=Envelope[dict])
def quota(response: Response, knowledgebaseId: str | None = None):
    response.headers["Cache-Control"] = "no-store"
    return Envelope(
        data={
            "memoryCount": 0,
            "knowledgebaseCount": 0,
            "knowledgebaseCapacity": 1000000,
            "allowedKnowledgebaseCount": 10,
        }
    )


@router.post("/api/user/subscription/details", response_model=Envelope[dict])
def subscription(body: SubscriptionRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data={"cycles": []})


@router.post("/api/dashboard/skill/skillMD", response_model=Envelope[str])
def skill(body: SkillRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data="")
