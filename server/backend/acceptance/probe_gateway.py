"""Direct loopback MCP probes. Credentials remain in memory, never evidence."""

import json
import os
from pathlib import Path

import httpx

ACCEPT = "application/json, text/event-stream"


def gateway_client(**kwargs):
    return httpx.Client(trust_env=False, timeout=120, **kwargs)


def old_token(root: Path, project="issue3-project-a"):
    for line in (root / ".local/gateway.env").read_text(encoding="utf-8-sig").splitlines():
        if not line.strip().startswith("MEMOS_TENANTS="):
            continue
        raw = line.split("=", 1)[1].strip()
        if raw.startswith("{"):
            for token, spec in json.loads(raw).items():
                uid = spec if isinstance(spec, str) else spec.get("user_id")
                if uid == project:
                    return token
        else:
            for pair in raw.split(","):
                token, _, uid = pair.rpartition(":")
                if uid.strip() == project:
                    return token.strip()
    raise ValueError("Existing regression credential not configured")


def result(response):
    for frame in [response.text] + [
        s[5:] for s in response.text.splitlines() if s.startswith("data:")
    ]:
        try:
            data = json.loads(frame)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            return data["result"]
    return {}


def rpc(client, gateway, token, method, params, number=1):
    return client.post(
        gateway + "/mcp",
        headers={"Authorization": "Bearer " + token, "Accept": ACCEPT},
        json={"jsonrpc": "2.0", "id": number, "method": method, "params": params},
    )


def probe(client, gateway, token):
    response = rpc(
        client,
        gateway,
        token,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "task14-isolated", "version": "2"},
        },
    )
    if response.status_code != 200:
        return {"http_status": response.status_code, "tools": []}
    response = rpc(client, gateway, token, "tools/list", {}, 2)
    tools = result(response).get("tools", [])
    return {
        "http_status": response.status_code,
        "tools": [
            item["name"]
            for item in tools
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ],
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["old", "malicious", "env"], nargs="?", default="env")
    parser.add_argument("--root", type=Path, default=Path("D:/MemOS"))
    parser.add_argument("--gateway", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    token = (
        old_token(args.root)
        if args.mode == "old"
        else "sk-" + "0" * 48
        if args.mode == "malicious"
        else os.environ.get("PROBE_TOKEN", "")
    )
    with gateway_client() as client:
        data = probe(client, args.gateway, token)
    safe = json.dumps({"step": args.mode, **data})
    if "sk-" in safe:
        raise ValueError("Unsafe probe output rejected")
    print(safe)
    return 0 if data["http_status"] == (401 if args.mode == "malicious" else 200) else 1


if __name__ == "__main__":
    raise SystemExit(main())
