import argparse
import json

import httpx
import pytest
from server.backend.acceptance.smoke import origin, run_checks


def test_read_only_smoke_has_no_private_setup_or_fixture_dependency():
    calls = []

    def transport(request):
        path = request.url.path
        calls.append(path)
        if path == "/api/meta":
            data = {"mode": "live"}
        elif path.endswith("/memory/list"):
            data = {"memListItemResponseList": [], "total": 0}
        elif path.endswith("/graph/memories"):
            data = {"nodes": [], "stats": {"truncated": False}}
        elif path.endswith(("/models", "/log/list")):
            data = {"total": 0}
        else:
            data = {}
        return httpx.Response(200, json={"code": 0, "data": data, "message": "success"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as web:
        proof = run_checks(web, "http://test", "demo")
    assert proof["passed"] and len(proof["pages"]) == 6
    assert "memoryContent" not in json.dumps(proof)
    assert all(not path.endswith(("/create", "/delete", "/save", "/revoke")) for path in calls)


@pytest.mark.parametrize(
    "url", ["file:///tmp/test", "https://user:secret@example.com", "https://example.com?key=x"]
)
def test_service_url_rejects_embedded_credentials_and_non_http(url):
    with pytest.raises(argparse.ArgumentTypeError):
        origin(url)
