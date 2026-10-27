"""Explicit isolated Task14 acceptance; issues/revokes only this run's credential."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .probe_gateway import gateway_client, old_token, probe, result, rpc


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Recorder:
    def __init__(self, path, run_id):
        self.path, self.run_id, self.rows = Path(path), run_id, []

    def record(self, step, **data):
        rows = self.rows + [{"run_id": self.run_id, "step": step, **data}]
        encoded = json.dumps(rows, indent=2)
        if "sk-" in encoded:
            raise ValueError("Unsafe credential evidence rejected")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(encoded + "\n", encoding="utf-8")
        self.rows = rows
        print(json.dumps({"step": step, **data}), flush=True)


@dataclass
class RunResources:
    project_id: str | None = None
    key_id: str | None = None
    revoked: bool = False

    def cleanup_ids(self):
        return [self.key_id] if self.key_id and not self.revoked else []


def restart(root, script, name, recorder):
    state = root / ".local/processes.json"
    before = json.loads(state.read_text(encoding="utf-8-sig"))[name]["id"]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "setup" / script),
            ],
            stdout=stdout,
            stderr=stderr,
            timeout=100,
        )
    require(process.returncode == 0, "Owned service restart failed")
    after = json.loads(state.read_text(encoding="utf-8-sig"))[name]["id"]
    require(before != after, "Service PID did not change")
    recorder.record(name + "_restart", passed=True, pid_changed=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("D:/MemOS"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = args.root
    run_id = "task14-" + uuid.uuid4().hex[:12]
    path = args.output or root / ("dashboard/.data/task14-keys/runs/" + run_id + ".json")
    require(not path.exists(), "Fresh output path required; historical evidence is immutable")
    recorder = Recorder(path, run_id)
    resources = RunResources()
    manifest = root / "upstream/deploy/mcp-gateway/tenants.conf"
    base, gateway = "http://127.0.0.1:5281", "http://127.0.0.1:8002"
    start_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    process_state = root / ".local/processes.json"
    gateway_pid = json.loads(process_state.read_text(encoding="utf-8-sig"))["gateway"]["id"]
    failed = False
    with gateway_client() as client:

        def post(route, body):
            response = client.post(base + route, json=body)
            require(response.status_code == 200, "Dashboard operation failed")
            data = response.json()
            require(data.get("code") == 0, "Dashboard business failure")
            return data["data"]

        try:
            account = json.loads((root / ".local/dashboard-admin.json").read_text(encoding="utf-8"))
            page = client.get(base + "/__login")
            csrf = re.search(r'name="csrf" value="([^" ]+)"', page.text)
            require(csrf is not None, "Missing login CSRF")
            login = client.post(
                base + "/__login",
                data={
                    "username": account["username"],
                    "password": account["password"],
                    "csrf": csrf[1],
                },
                follow_redirects=False,
            )
            require(login.status_code == 303, "Login rejected")
            recorder.record("login", passed=True)
            existing = old_token(root)
            require(
                probe(client, gateway, existing)["http_status"] == 200,
                "Existing key rejected before run",
            )
            recorder.record("existing_key_before", status=200)
            require(
                probe(client, gateway, "sk-" + "0" * 48)["http_status"] == 401,
                "Invalid key accepted",
            )
            recorder.record("invalid_key", status=401)
            project = post(
                "/api/dashboard/project/create",
                {"name": run_id, "description": "Isolated Task14 credential acceptance"},
            )
            require(
                project.get("name") == run_id and project["id"].startswith("proj_"),
                "Unexpected project identity",
            )
            resources.project_id = project["id"]
            recorder.record("isolated_project", project_id=resources.project_id)
            created = post(
                "/api/dashboard/keys/create", {"projectId": resources.project_id, "keyName": run_id}
            )
            resources.key_id = created["id"]
            token = created["apiKey"]
            require(
                bool(re.fullmatch(r"sk-[0-9a-f]{48}", token)), "Invalid generated credential shape"
            )
            require(
                token in json.loads(manifest.read_text(encoding="utf-8")),
                "Missing published credential",
            )
            recorder.record("create", passed=True, file_matches=True)
            copied = post("/api/dashboard/keys/copy", {"apiKeyId": resources.key_id})
            require(copied == token, "Copy response mismatch")
            recorder.record("copy", passed=True)
            before_rename = manifest.read_bytes()
            post("/api/dashboard/keys/rename/" + resources.key_id, {"keyName": run_id + "-renamed"})
            require(manifest.read_bytes() == before_rename, "Rename changed gateway bytes")
            recorder.record("rename", file_unchanged=True)
            restart(root, "Restart-Backend.ps1", "backend", recorder)
            persisted = post("/api/dashboard/keys/copy", {"apiKeyId": resources.key_id})
            require(persisted == token, "Credential/session did not persist")
            recorder.record("backend_persistence", passed=True)
            require(
                probe(client, gateway, token)["http_status"] == 200,
                "New key did not become live without gateway restart",
            )
            recorder.record("new_key_without_gateway_restart", status=200)
            live = probe(client, gateway, token)
            require(
                live["http_status"] == 200 and "search_memory" in live["tools"],
                "New credential not usable",
            )
            recorder.record("new_key_live", status=200, has_search=True)
            response = rpc(
                client,
                gateway,
                token,
                "tools/call",
                {"name": "search_memory", "arguments": {"query": run_id}},
                3,
            )
            content = result(response)
            require(
                response.status_code == 200
                and isinstance(content.get("content"), list)
                and not content.get("isError"),
                "Real search failed",
            )
            recorder.record("real_search", status=200, success=True)
            hits = []
            for _ in range(15):
                hits = []
                for audit in (root / ".local/audit").glob("calls-*.jsonl"):
                    for line in audit.read_text(encoding="utf-8").splitlines():
                        if run_id not in line:
                            continue
                        row = json.loads(line)
                        if row.get("tool") == "search_memory" and run_id in json.dumps(
                            row.get("input")
                        ):
                            hits.append(row)
                if hits:
                    break
                time.sleep(0.2)
            require(
                len(hits) == 1 and hits[0].get("tenant") == resources.project_id,
                "Audit tenant/count mismatch",
            )
            recorder.record("audit_attribution", hits=1, tenant_matches=True)
            post("/api/dashboard/keys/revoke/" + resources.key_id, {})
            resources.revoked = True
            require(
                token not in json.loads(manifest.read_text(encoding="utf-8")),
                "Revoked key still in configuration",
            )
            recorder.record("revoke", file_absent=True)
            require(
                probe(client, gateway, token)["http_status"] == 401, "Revoked key still accepted"
            )
            recorder.record("revoked_key_without_gateway_restart", status=401)
            require(
                probe(client, gateway, existing)["http_status"] == 200, "Existing key regressed"
            )
            recorder.record("existing_key_after", status=200)
        except Exception as error:
            failed = True
            recorder.record("failed", passed=False, error_type=type(error).__name__)
        finally:
            try:
                for identifier in resources.cleanup_ids():
                    post("/api/dashboard/keys/revoke/" + identifier, {})
                    resources.revoked = True
                if resources.project_id:
                    post("/api/dashboard/project/revoke/" + resources.project_id, {})
                require(
                    hashlib.sha256(manifest.read_bytes()).hexdigest() == start_hash,
                    "Preexisting manifest bytes changed",
                )
                recorder.record(
                    "cleanup", own_resources_only=True, original_manifest_unchanged=True
                )
                require(
                    json.loads(process_state.read_text(encoding="utf-8-sig"))["gateway"]["id"]
                    == gateway_pid,
                    "Gateway process changed during live credential acceptance",
                )
                recorder.record("gateway_pid_unchanged", pid=gateway_pid)
            except Exception as error:
                failed = True
                recorder.record("cleanup_failed", passed=False, error_type=type(error).__name__)
    recorder.record("complete", passed=not failed, secret_scan_passed=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
