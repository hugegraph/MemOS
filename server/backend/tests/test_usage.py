from datetime import date

import pytest
from fastapi.testclient import TestClient
from server.backend.tests.test_audit_api import append, application, validate_contract

ROUTE = "/api/dashboard/usage/query"


def query(client, **extra):
    return client.post(
        ROUTE, json={"projectId": "p", "startDate": "2026-10-25", "endDate": "2026-10-27", **extra}
    )


def test_daily_boundaries_zero_days_tokens_source_and_status(tmp_path):
    app, folder = application(tmp_path)
    append(
        folder,
        ts="2026-10-24T16:00:00Z",
        tool="add_message",
        usage={"prompt_tokens": 3, "completion_tokens": 2},
    )
    append(folder, ts="2026-10-25T15:59:59.999999Z", ok=False)
    append(folder, ts="2026-10-25T16:00:00Z", tool="add_message", status="PENDING")
    append(folder, ts="2026-10-27T15:59:59.999999Z", tool="search_memory")
    append(folder, ts="2026-10-27T16:00:00Z")
    append(folder, ts=None)
    append(folder, tool="unrecognized_tool")
    append(folder, tool="chat", usage={"input_tokens": 7, "output_tokens": 4})
    append(folder, tenant="b")
    with TestClient(app) as client:
        console = app.state.settings.data_dir / "audit"
        console.mkdir(parents=True, exist_ok=True)
        # The trusted source directory wins over an entry source label.
        import json

        (console / "console-20261026.jsonl").write_text(
            json.dumps(
                {
                    "tenant": "a",
                    "tool": "add_message",
                    "ts": "2026-10-26T00:00:00Z",
                    "source": "gateway",
                }
            )
            + "\n"
        )
        response = query(client)
        assert response.status_code == 200
        data = response.json()["data"]
        validate_contract(response.json(), "UsageQuery")
        days = data["dailyStats"]
        assert [d["date"] for d in days] == ["2026-10-25", "2026-10-26", "2026-10-27"]
        assert [d["addMessage"]["calls"] for d in days] == [1, 1, 0]
        assert [d["searchMemory"]["calls"] for d in days] == [1, 0, 1]
        assert days[0]["addMessage"]["totalTokens"] == 5
        assert days[0]["searchMemory"]["totalTokens"] == 0
        assert days[1]["chat"] == {"requestTokens": 7, "responseTokens": 4, "totalTokens": 11}
        assert data["summary"]["totalCalls"] == 4
        assert data["summary"]["totalTokens"] == 16
        assert response.headers["cache-control"] == "no-store"
        assert data["timezone"] == "Asia/Shanghai"
        no_days = query(client, includeDailyDetails=False).json()["data"]
        assert no_days["dailyStats"] == [] and no_days["summary"] == data["summary"]


def test_async_one_call_same_index_updates_tokens_and_request_date(tmp_path):
    app, folder = application(tmp_path)
    append(
        folder,
        tool="add_message",
        ts="2026-10-25T15:59:59Z",
        callId="async-one",
        phase="request",
        status="PENDING",
    )
    with TestClient(app) as client:
        before = query(client).json()["data"]
        assert before["summary"]["totalCalls"] == 1
        index = app.state.audit.get()
        append(
            folder,
            tool="add_message",
            ts="2026-10-25T16:00:02Z",
            callId="async-one",
            phase="terminal",
            usage={"prompt_tokens": 11, "completion_tokens": 9},
        )
        after = query(client).json()["data"]
        assert app.state.audit.get() is index
        assert after["summary"]["totalCalls"] == 1
        assert after["dailyStats"][0]["addMessage"] == {"calls": 1, "totalTokens": 20}
        assert after["dailyStats"][1]["addMessage"]["calls"] == 0
        assert (
            client.post(
                "/api/dashboard/log/list", json={"projectId": "p", "scope": "gateway"}
            ).json()["data"]["total"]
            == 1
        )


@pytest.mark.parametrize("project", [None, "", "all"])
def test_all_projects_includes_current_allowlist_only(tmp_path, project):
    app, folder = application(tmp_path)
    append(folder)
    append(folder, tenant="b")
    append(folder, tenant="not-allowed")
    with TestClient(app) as client:
        request = {"startDate": "2026-10-26", "endDate": "2026-10-26"}
        if project is not None:
            request["projectId"] = project
        data = client.post(ROUTE, json=request).json()["data"]
        assert data["summary"]["totalCalls"] == 2
    # Existing database contains project q, but the new process no longer allows it.
    config = app.state.settings.model_copy(update={"projects_seed": (("p", "a"),)})
    from server.backend.app import create_app

    with TestClient(create_app(config)) as client:
        assert query(client, projectId="all").json()["data"]["summary"]["totalCalls"] == 1
        assert query(client, projectId="q").status_code == 404


def test_authorize_before_scanning_and_no_configuration_error(tmp_path):
    app, _ = application(tmp_path, gateway=False)
    with TestClient(app) as client:
        assert query(client, projectId="not-allowed").status_code == 404
        assert app.state.audit.index is None
        assert query(client).status_code == 503
    from server.backend.app import create_app
    from server.backend.settings import Settings

    with TestClient(
        create_app(Settings(auth_enabled=False, data_dir=tmp_path / "empty"))
    ) as client:
        assert client.post(ROUTE, json={}).status_code == 503


@pytest.mark.parametrize(
    "extra",
    [
        {"startDate": "2026-02-30"},
        {"startDate": "2026-1-01"},
        {"startDate": "2026-10-25T00:00:00Z"},
        {"endDate": "2026-10-24"},
        {"startDate": "2025-01-01", "endDate": "2026-10-27"},
        {"apiKeyId": "key-1"},
        {"projectId": 123},
        {"includeDailyDetails": "false"},
        {"includeDailyDetails": None},
        {"other": "x"},
        {"endDate": "9999-12-31"},
    ],
)
def test_invalid_requests_fail_explicitly(tmp_path, extra):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        result = query(client, **extra)
        assert result.status_code == 400 and result.json()["code"] != 0


def test_defaults_current_month_and_empty_source_days(tmp_path, monkeypatch):
    from server.backend import usage_models

    monkeypatch.setattr(usage_models, "today", lambda: date(2026, 3, 1))
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        data = client.post(ROUTE, json={"projectId": "p", "apiKeyId": ""}).json()["data"]
        assert data["startDate"] == data["endDate"] == "2026-03-01"
        assert len(data["dailyStats"]) == 1
        assert data["summary"]["totalCalls"] == 0


def test_blank_dates_are_defaults_and_midnight_cannot_change_validated_range(tmp_path, monkeypatch):
    from server.backend import usage_models

    calls = iter((date(2026, 10, 31), date(2026, 11, 1)))
    monkeypatch.setattr(usage_models, "today", lambda: next(calls))
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        response = client.post(ROUTE, json={"projectId": "p", "endDate": "2026-10-31"})
        assert response.status_code == 200
        assert response.json()["data"]["startDate"] == "2026-10-01"
    monkeypatch.setattr(usage_models, "today", lambda: date(2026, 10, 31))
    with TestClient(app) as client:
        response = client.post(ROUTE, json={"projectId": "p", "startDate": "", "endDate": " "})
        assert response.status_code == 200
        assert response.json()["data"]["endDate"] == "2026-10-31"


def test_leap_year_full_366_days_and_maximum_range(tmp_path):
    app, folder = application(tmp_path)
    append(folder, ts="2024-02-28T16:00:00Z")
    with TestClient(app) as client:
        response = query(client, startDate="2024-01-01", endDate="2024-12-31")
        assert response.status_code == 200
        days = response.json()["data"]["dailyStats"]
        assert len(days) == 366
        assert next(d for d in days if d["date"] == "2024-02-29")["searchMemory"]["calls"] == 1
        assert query(client, startDate="2024-01-01", endDate="2025-01-01").status_code == 400


def test_usage_date_schema_matches_actual_blank_null_and_date_acceptance():
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator, FormatChecker

    spec = json.loads(
        (Path(__file__).resolve().parents[3] / "openapi.json").read_text(encoding="utf-8")
    )
    schema = spec["paths"][ROUTE]["post"]["requestBody"]["content"]["application/json"]["schema"]
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for raw in ("", " ", "\t\n", None, "2026-10-26"):
        validator.validate({"startDate": raw, "endDate": raw})
    for raw in ("2026-02-30", "2026-1-01", " 2026-10-26", "2026-10-26 "):
        assert list(validator.iter_errors({"startDate": raw}))


@pytest.mark.parametrize("field", ["startDate", "endDate"])
@pytest.mark.parametrize("raw", [" 2026-10-26", "2026-10-26 "])
def test_nonblank_dates_with_surrounding_whitespace_are_rejected(tmp_path, field, raw):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        assert query(client, **{field: raw}).status_code == 400
