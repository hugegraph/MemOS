import json

import pytest


def line(number=1, **extra):
    return {
        "tenant": "a",
        "tool": "search_memory",
        "ts": f"2026-10-26T02:00:{number:02d}+00:00",
        "ok": True,
        "ms": 12,
        "input": {"query": f"marker-{number}", "user_id": "u"},
        "output": {"code": 200, "data": {}},
        **extra,
    }


def write(path, *rows, mode="a"):
    with path.open(mode, encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")


def index_for(tmp_path, *, console=False):
    from server.backend.audit_index import AuditIndex

    directory = tmp_path / "gateway"
    directory.mkdir(exist_ok=True)
    sources = {"gateway": directory}
    if console:
        folder = tmp_path / "console"
        folder.mkdir(exist_ok=True)
        sources["console"] = folder
    return AuditIndex(tmp_path / "audit.db", sources), directory / "calls-20261026.jsonl"


def page(index, cube="a", **extra):
    from server.backend.audit_models import LogListRequest

    return index.page(cube, LogListRequest(projectId="p", **extra))


def test_initial_append_restart_and_half_lines_are_incremental(tmp_path):
    index, path = index_for(tmp_path)
    write(path, line())
    assert index.sync()["imported"] == 1
    first = page(index)["logs"][0]
    assert index.sync()["imported"] == 0
    restarted, _ = index_for(tmp_path)
    assert restarted.sync()["imported"] == 0
    with path.open("ab") as output:
        encoded = json.dumps(line(2)).encode()
        output.write(encoded[:17])
    assert restarted.sync()["imported"] == 0
    with path.open("ab") as output:
        output.write(encoded[17:] + b"\n")
    assert restarted.sync()["imported"] == 1
    assert page(restarted)["total"] == 2
    assert index.detail("a", first["id"])["requestBody"] == '{"query":"marker-1","user_id":"u"}'
    assert index.detail("b", first["id"]) is None


def test_rotate_same_file_and_replace_same_path_without_duplicates(tmp_path):
    index, path = index_for(tmp_path)
    write(path, line())
    index.sync()
    first = page(index)["logs"][0]["id"]
    rotated = path.with_name(path.name + ".1")
    path.rename(rotated)
    write(path, line(2))
    assert index.sync()["imported"] == 1
    assert page(index)["total"] == 2
    assert index.detail("a", first)
    replacement = path.parent / "replacement.tmp"
    write(replacement, line(3))
    replacement.replace(path)
    assert index.sync()["imported"] == 1
    assert page(index)["total"] == 3


def test_truncate_regrow_past_checkpoint_and_bad_json_quarantine(tmp_path):
    index, path = index_for(tmp_path)
    write(path, line())
    index.sync()
    write(path, line(2), line(3), mode="w")
    with path.open("a", encoding="utf-8") as output:
        output.write("{broken\n[]\n" + json.dumps({"tool": "search_memory"}) + "\n")
    write(path, line(4))
    result = index.sync()
    assert result["imported"] == 3
    assert result["invalid"] == 2 and result["unscoped"] == 1
    assert page(index)["total"] == 4
    assert index.sync()["invalid"] == 0


def test_failed_import_rolls_back_cursor_and_rows_together(tmp_path, monkeypatch):
    index, path = index_for(tmp_path)
    write(path, line(), line(2))
    original = index._store_line
    count = 0

    def interrupt(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("simulated importer crash")
        return original(*args, **kwargs)

    monkeypatch.setattr(index, "_store_line", interrupt)
    with pytest.raises(RuntimeError):
        index.sync()
    assert page(index)["total"] == 0
    monkeypatch.setattr(index, "_store_line", original)
    assert index.sync()["imported"] == 2
    assert page(index)["total"] == 2


def test_async_terminal_first_repeated_and_cross_scope_ids(tmp_path):
    index, path = index_for(tmp_path, console=True)
    terminal = line(
        3,
        callId="same-call",
        phase="completed",
        ms=300,
        output={"code": 200, "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
    )
    write(path, terminal)
    index.sync()
    identifier = page(index)["logs"][0]["id"]
    write(
        path,
        line(
            1,
            callId="same-call",
            phase="request",
            ms=3,
            input={"async_mode": "async", "user_id": "u"},
            output={"status": "running"},
        ),
        terminal,
        line(2, tenant="b", callId="same-call", phase="completed"),
    )
    write(
        tmp_path / "console/console-20261026.jsonl", line(4, callId="same-call", phase="completed")
    )
    index.sync()
    result = page(index)
    assert result["total"] == 2
    gateway = page(index, scope="gateway")["logs"][0]
    assert (
        gateway["id"] == identifier
        and gateway["duration"] == 300
        and gateway["status"] == "SUCCESS"
    )
    assert gateway["createTime"] == "2026-10-26 10:00:01" and gateway["requestTokens"] == 12
    assert index.detail("a", identifier)["callId"] == "same-call"
    assert page(index, cube="b")["total"] == 1


def test_combined_filters_pagination_and_json_string_detail(tmp_path):
    index, path = index_for(tmp_path)
    write(path, line(1), line(2, ok=False), line(3, tool="add_message"), line(4, tenant="b"))
    index.sync()
    assert (
        page(
            index,
            eventType="searchMemory",
            subjectId="u",
            subjectType="user",
            status="success",
            query="marker-1",
            startTime="2026-10-26",
            endTime="2026-10-26",
        )["total"]
        == 1
    )
    assert page(index, subjectId="u", subjectType="agent")["total"] == 0
    assert page(index, query="%")["total"] == 0
    first = page(index, pageSize=1)
    second = page(index, pageSize=1, pageNum=2)
    assert first["total"] == second["total"] == 3
    assert first["logs"][0]["id"] != second["logs"][0]["id"]
    assert page(index, pageNum=10)["logs"] == []


def test_missing_configured_source_fails_explicitly(tmp_path):
    from server.backend.audit_index import AuditIndex
    from server.backend.errors import ApiError

    index = AuditIndex(tmp_path / "audit.db", {"gateway": tmp_path / "missing"})
    with pytest.raises(ApiError) as exc:
        index.sync()
    assert exc.value.status_code == 503


def test_oversized_partial_and_malformed_fields_do_not_hide_following_rows(tmp_path):
    from server.backend.audit_index import MAX_LINE_BYTES

    index, path = index_for(tmp_path)
    with path.open("wb") as file:
        file.write(b"x" * (MAX_LINE_BYTES + 100))
    assert index.sync()["invalid"] == 0
    with path.open("ab") as file:
        file.write(b"\n")
        file.write(b'{"tenant":"a","input":{"user_ids":[{},null]},"output":[1,2],"ms":"bad"}\n')
    write(path, line(1))
    result = index.sync()
    assert result["invalid"] == 1 and result["imported"] == 2 and page(index)["total"] == 2
    assert index.sync()["invalid"] == 0


def test_concurrent_imports_do_not_duplicate_and_conflicting_call_event_is_quarantined(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    index, path = index_for(tmp_path)
    write(
        path, line(callId="x", phase="completed"), line(2, callId="x", tool="add_message"), line(3)
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: index.sync(), range(4)))
    assert sum(item["imported"] for item in results) == 2
    assert sum(item["invalid"] for item in results) == 1
    assert page(index)["total"] == 2


def test_sparse_late_terminal_does_not_erase_known_outcome_or_measurements(tmp_path):
    index, path = index_for(tmp_path)
    write(
        path,
        line(
            callId="x",
            phase="completed",
            ms=300,
            output={"code": 200, "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
        ),
    )
    index.sync()
    first = page(index)["logs"][0]
    write(path, {"tenant": "a", "callId": "x", "phase": "completed", "ts": "2026-10-26T03:00:00Z"})
    index.sync()
    row = page(index)["logs"][0]
    assert row["id"] == first["id"] and row["status"] == "SUCCESS" and row["duration"] == 300
    assert row["requestTokens"] == 12 and row["responseTokens"] == 8
    assert index.detail("a", row["id"])["responseBody"] != "null"


def test_rejected_line_diagnostics_are_durable_without_raw_payloads(tmp_path):
    index, path = index_for(tmp_path)
    path.write_text("{broken-secret\n{}\n", encoding="utf-8")
    assert index.sync() == {"imported": 0, "invalid": 1, "unscoped": 1}
    with index.connection() as db:
        rows = db.execute(
            "SELECT reason,count(*) FROM audit_rejects GROUP BY reason ORDER BY reason"
        ).fetchall()
        assert [tuple(row) for row in rows] == [("invalid", 1), ("unscoped", 1)]
    assert b"broken-secret" not in index.path.read_bytes()


def test_backup_and_temporary_files_are_not_business_call_sources(tmp_path):
    index, path = index_for(tmp_path)
    write(path, line())
    for suffix in (".bak", ".tmp", ".backup", ".gz", ".zip"):
        write(path.with_name(path.name + suffix), line())
    assert index.sync()["imported"] == 1
    assert page(index)["total"] == 1


def test_conflicting_genuine_terminal_uses_latest_evidence_and_keeps_explicit_zero(tmp_path):
    index, path = index_for(tmp_path)
    write(
        path,
        line(callId="x", phase="completed", output={"code": 200, "usage": {"prompt_tokens": 12}}),
    )
    index.sync()
    write(
        path,
        line(
            2,
            callId="x",
            phase="failed",
            ok=False,
            output={"code": 500, "usage": {"prompt_tokens": 0}},
        ),
    )
    index.sync()
    row = page(index)["logs"][0]
    assert row["status"] == "FAILURE" and row["requestTokens"] == 0
    write(path, line(callId="x", phase="completed"))
    index.sync()
    assert page(index)["logs"][0]["status"] == "FAILURE"
