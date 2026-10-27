import json

import pytest


def test_legacy_missing_fields_and_subject_is_not_tenant():
    from server.backend.audit_normalize import normalize

    row = normalize({"tenant": "cube-a", "tool": "get_user_profile"}, "gateway")
    assert row["event"] == "getMemory"
    assert row["status"] == "UNKNOWN" and row["duration"] is None
    assert row["time"] == "" and row["ts"] == 0
    assert row["users"] == row["agents"] == []
    assert row["request_tokens"] == row["response_tokens"] == 0
    assert json.loads(row["request_body"]) is None


@pytest.mark.parametrize(
    "tool,event",
    [
        ("add_message", "addMessage"),
        ("search_memory", "searchMemory"),
        ("get_user_profile", "getMemory"),
        ("delete_memory", "deleteMemory"),
        ("add_feedback", "addFeedback"),
        ("unknown_tool", "unknown_tool"),
    ],
)
def test_tool_mapping_keeps_unknown_events(tool, event):
    from server.backend.audit_normalize import normalize

    assert normalize({"tenant": "a", "tool": tool, "ok": True}, "gateway")["event"] == event


def test_body_json_strings_preserved_credentials_redacted_and_business_failure_wins():
    from server.backend.audit_normalize import normalize

    encoded = '{ "query" : "unique-marker" }'
    row = normalize(
        {
            "ts": "2026-10-26T10:00:00+0800",
            "tool": "search_memory",
            "tenant": "a",
            "ok": True,
            "input": encoded,
            "output": '{"code":200,"data":{"success":false}}',
            "ms": 12,
        },
        "gateway",
    )
    assert row["request_body"] == encoded
    assert row["status"] == "FAILURE" and row["time"] == "2026-10-26T02:00:00+00:00"
    redacted = normalize(
        {
            "tenant": "a",
            "tool": "search_memory",
            "input": {
                "api_key": "private-test-secret",
                "nested": {"Authorization": "Bearer private-test-token"},
            },
            "output": {"usage": {"prompt_tokens": 11, "completion_tokens": 7}},
        },
        "gateway",
    )
    assert "private-test" not in redacted["request_body"]
    assert redacted["request_tokens"] == 11 and redacted["response_tokens"] == 7


def test_async_request_duration_is_unknown_and_terminal_aliases():
    from server.backend.audit_normalize import normalize

    row = normalize(
        {
            "tenant": "a",
            "tool": "add_message",
            "callId": "call-1",
            "phase": "request",
            "ok": True,
            "ms": 4,
            "input": {"async_mode": "async", "user_id": "u", "agent_id": "agent"},
            "output": {"status": "running"},
        },
        "gateway",
    )
    assert row["status"] == "PENDING" and row["duration"] is None
    assert row["users"] == ["u"] and row["agents"] == ["agent"]
    assert row["call_id"] == "call-1" and row["phase"] == "request"
    done = normalize(
        {"tenant": "a", "call_id": "call-1", "phase": "completed", "ok": True, "ms": 300}, "gateway"
    )
    assert done["phase"] == "terminal" and done["duration"] == 300


def test_explicit_request_phase_is_pending_without_async_mode_hint():
    from server.backend.audit_normalize import normalize

    row = normalize(
        {
            "tenant": "a",
            "tool": "add_message",
            "callId": "x",
            "phase": "request",
            "ok": True,
            "ms": 5,
        },
        "gateway",
    )
    assert row["status"] == "PENDING" and row["duration"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"responseCode": 200, "output": {"code": 500}},
        {"status": "success", "output": {"data": {"status": "failed"}}},
        {"callId": "x", "phase": "failed"},
    ],
)
def test_any_explicit_business_failure_overrides_outer_success(extra):
    from server.backend.audit_normalize import normalize

    assert normalize({"tenant": "a", "ok": True, **extra}, "gateway")["status"] == "FAILURE"


def test_camelcase_credentials_are_redacted():
    from server.backend.audit_normalize import normalize

    row = normalize(
        {
            "tenant": "a",
            "input": {
                "accessToken": "private-access",
                "refreshToken": "private-refresh",
                "clientSecret": "private-secret",
            },
        },
        "gateway",
    )
    assert "private-" not in row["request_body"]


def test_tenant_missing_is_quarantined_not_assigned_from_user():
    from server.backend.audit_normalize import UnscopedLine, normalize

    with pytest.raises(UnscopedLine):
        normalize({"tool": "search_memory", "input": {"user_id": "cube-a"}}, "gateway")


def test_console_source_comes_from_config_not_payload():
    from server.backend.audit_normalize import normalize

    row = normalize(
        {"tenant": "a", "source": "gateway", "eventType": "console.memory.list", "ok": True},
        "console",
    )
    assert row["source"] == "console" and row["event"] == "console.memory.list"


def test_query_validation_date_semantics_and_subject_type_clearing():
    from pydantic import ValidationError
    from server.backend.audit_models import LogListRequest

    body = LogListRequest(projectId="a", subjectType="user", subjectId="", scope="all")
    assert body.subjectType is None
    for extra in (
        {"pageNum": 0},
        {"pageSize": 101},
        {"eventType": "not-a-filter"},
        {"startTime": "2026-01-02", "endTime": "2026-01-01"},
        {"endTime": "9999-12-31"},
    ):
        with pytest.raises(ValidationError):
            LogListRequest(projectId="a", **extra)
