"""Shared browser Origin validation for control-plane writes."""

import logging

from .errors import ApiError


def check_origin(request):
    origin = request.headers.get("origin")
    if origin is None:
        return  # Trusted local CLI callers do not send browser Origin.
    valid = origin in request.app.state.settings.allowed_origins
    if not valid:
        logging.getLogger(__name__).warning("Rejected browser Origin (opaque=%s)", origin == "null")
        raise ApiError(40003, "不允许该来源执行写操作", status_code=403)


async def browser_write_receive(request, receive):
    """Allow JSON or genuinely empty browser writes; never trust Content-Length alone."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        return receive
    if content_type:
        raise ApiError(40003, "写操作仅接受JSON或空请求体", status_code=403)
    message = await receive()
    if message["type"] != "http.request" or message.get("body") or message.get("more_body", False):
        raise ApiError(40003, "写操作仅接受JSON或空请求体", status_code=403)

    original_receive = receive

    async def replay():
        nonlocal message
        if message is not None:
            first, message = message, None
            return first
        return await original_receive()

    return replay
