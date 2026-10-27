"""Replay Task15 against local real services; create and delete only our own template."""

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("D:/MemOS"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--other-project", required=True)
    parser.add_argument("--cube", required=True)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.root / "setup"))
    from verify_task01 import authenticate_local_backend
    from verify_task06 import run_ps

    base = "http://127.0.0.1:5281/api/dashboard/profileTemplate/"
    native = "http://127.0.0.1:8000/product/"
    run = uuid.uuid4().hex[:12]
    output = args.root / "dashboard/.data/task15" / (run + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    proof = {"run_id": run, "passed": False, "checks": []}
    identifier = None

    def record(name, **details):
        proof["checks"].append({"name": name, "passed": True, **details})

    def envelope(response, code=0):
        assert response.status_code == 200, ("HTTP", response.status_code)
        body = response.json()
        assert body["code"] == code, ("envelope", body["code"])
        return body["data"]

    with httpx.Client(trust_env=False, timeout=45) as web:
        try:
            assert web.post(base + "list", json={"projectId": args.project}).status_code == 401
            record("anonymous_denied")
            authenticate_local_backend(web)
            body = {
                "projectId": args.project,
                "templateName": "TASK15_" + run,
                "Work": {
                    "role": {"value": "engineer", "algorithm_updatable": False},
                    "location": {"value": "test-only", "algorithm_updatable": True},
                },
            }
            identifier = envelope(web.post(base + "create", json=body))["templateId"]
            params = {"projectId": args.project, "templateId": identifier}
            record("create_real_upstream", template_id=identifier)
            upstream = envelope(
                web.post(
                    native + "get_profile_template",
                    json={"mem_cube_id": args.cube, "profile_template_id": identifier},
                )
            )
            detail = envelope(web.get(base + "detail", params=params))
            assert upstream["profile_template_id"] == detail["templateId"] == identifier
            assert upstream["metadata"] == {"Work": detail["Work"]}
            assert detail["bindingCount"] == upstream["bound_count"] == 0
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", detail["updatedAt"])
            record(
                "same_id_rest_and_tree",
                tree_sha256=hashlib.sha256(
                    json.dumps(detail["Work"], sort_keys=True).encode()
                ).hexdigest(),
            )
            page = 1
            found = False
            while not found:
                listing = envelope(
                    web.post(
                        base + "list",
                        json={"projectId": args.project, "pageNum": page, "pageSize": 2},
                    )
                )
                assert listing["current"] == page and listing["size"] == 2
                assert listing["pages"] == (listing["total"] + 1) // 2
                found = any(row["templateId"] == identifier for row in listing["list"])
                assert found or page < min(listing["pages"], 100)
                page += 1
            record("paged_list_contains_same_id")
            foreign = {**params, "projectId": args.other_project}
            for operation in ["detail", "update", "delete"]:
                response = (
                    web.get(base + operation, params=foreign)
                    if operation == "detail"
                    else web.post(
                        base + operation,
                        json=(foreign if operation == "delete" else {**body, **foreign}),
                    )
                )
                assert response.status_code == 404
            record("foreign_detail_update_delete_denied")
            # Remove a field to validate the native retained/deprecated-field semantics.
            updated = {
                **body,
                **params,
                "templateName": "TASK15_UPDATED_" + run,
                "Work": {"role": {"value": "reviewer", "algorithm_updatable": False}},
            }
            envelope(web.post(base + "update", json=updated))
            detail = envelope(web.get(base + "detail", params=params))
            assert detail["Work"] == updated["Work"]
            upstream = envelope(
                web.post(
                    native + "get_profile_template",
                    json={"mem_cube_id": args.cube, "profile_template_id": identifier},
                )
            )
            assert "Work.location" in upstream["deprecated_paths"]
            record("update_active_tree_and_native_deprecation")
            if args.restart:
                for service in ["Backend", "Upstream"]:
                    result = run_ps(args.root / "setup" / ("Restart-" + service + ".ps1"))
                    assert result.returncode == 0, "Owned service restart failed: " + service
                deadline = time.monotonic() + 45
                while True:
                    ready = web.get("http://127.0.0.1:5281/api/meta")
                    if ready.status_code == 200 and ready.json().get("code") == 0:
                        break
                    assert time.monotonic() < deadline, "Upstream readiness timeout"
                    time.sleep(0.5)
                authenticate_local_backend(web)
                detail = envelope(web.get(base + "detail", params=params))
                assert detail["templateName"] == updated["templateName"]
                assert detail["Work"] == updated["Work"]
                record("backend_and_upstream_restart_persistence")
            envelope(web.post(base + "delete", json=params))
            assert web.get(base + "detail", params=params).status_code == 404
            assert (
                web.post(
                    native + "get_profile_template",
                    json={"mem_cube_id": args.cube, "profile_template_id": identifier},
                ).json()["code"]
                == 404
            )
            record("delete_native_and_bff_missing")
            identifier = None
            proof["passed"] = True
        except Exception as error:
            proof["error_type"] = type(error).__name__
            raise
        finally:
            if identifier is not None:
                try:
                    authenticate_local_backend(web)
                    cleaned = web.post(
                        base + "delete", json={"projectId": args.project, "templateId": identifier}
                    )
                    proof["cleanup_status"] = cleaned.status_code
                except Exception as error:
                    proof["cleanup_error_type"] = type(error).__name__
            serialized = json.dumps(proof, indent=2)
            assert "sk-" not in serialized
            output.write_text(serialized, encoding="utf-8")
            print(
                json.dumps(
                    {
                        "passed": proof["passed"],
                        "checks": len(proof["checks"]),
                        "evidence": str(output),
                    }
                )
            )


if __name__ == "__main__":
    main()
