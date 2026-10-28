"""Private management routes. BFF validates configured workspace bindings; token stays server-side."""
import hmac
import os
from fastapi import APIRouter, Body, Depends, Header, HTTPException
from . import runtime
from .store import identity


def install(app, reader):
    if not runtime.enabled():
        return
    if len(os.getenv("DASHBOARD_PROMPT_TOKEN", "")) < 32:
        raise RuntimeError("Prompt management requires a strong private token")
    if reader.__class__.__name__ != "MultiModalStructMemReader":
        raise RuntimeError("Prompt extension requires the tested multimodal reader")
    if reader.llm.__class__.__name__ != "OpenAILLM" or not hasattr(reader.llm.client, "with_options"):
        raise RuntimeError("Prompt extension requires the tested bounded OpenAI client")
    runtime.store()

    def authorize(x_dashboard_prompt_token: str = Header(default="")):
        if not hmac.compare_digest(x_dashboard_prompt_token, os.environ["DASHBOARD_PROMPT_TOKEN"]):
            raise HTTPException(403, "Prompt management forbidden")

    router = APIRouter(prefix="/product/dashboard-prompt", dependencies=[Depends(authorize)])

    def run(action, body):
        try:
            user, cube = identity(body.get("user_id")), identity(body.get("mem_cube_id"))
            repository = runtime.store()
            if action == "save":
                value = repository.save(user, cube, body.get("prompt"))
                value = {"ok": True, **value}
            elif action == "reset-default":
                value = repository.save(user, cube, "")
            else:
                snapshot = repository.get(user, cube)
                if action == "detail":
                    value = snapshot
                elif action == "generate":
                    value = runtime.generate(reader, body.get("description"), snapshot)
                else:
                    types = body.get("extractionTypes", ["text_mem"])
                    if types != ["text_mem"]:
                        raise ValueError("Only text_mem chat extraction is supported")
                    data, _ = runtime.extract(reader, body.get("messages"), snapshot)
                    value = {"extractionTypes": ["text_mem"], "results": [
                        {"type": "text_mem", "content": row["value"]} for row in data["memory list"]],
                        "revision": snapshot["revision"]}
            return {"code": 200, "data": value, "message": "success"}
        except ValueError:
            raise HTTPException(400, "Invalid prompt request") from None
        except Exception:
            raise HTTPException(503, "Prompt operation unavailable; no fallback result") from None

    def endpoint(action):
        def handler(body: dict = Body(...)):
            return run(action, body)
        return handler

    for action in ("detail", "save", "generate", "reset-default", "debug/extract"):
        router.add_api_route("/" + action, endpoint(action), methods=["POST"])
    app.include_router(router)
