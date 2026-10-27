import json

import pytest
from server.backend.acceptance.probe_gateway import gateway_client
from server.backend.acceptance.task14_driver import Recorder, RunResources, require


def test_failed_gate_raises_and_secret_record_never_reaches_disk(tmp_path):
    with pytest.raises(AssertionError):
        require(False, "expected gate failure")
    path = tmp_path / "proof.json"
    recorder = Recorder(path, "run-one")
    with pytest.raises(ValueError):
        recorder.record("unsafe", value="sk-" + "x" * 48)
    assert not path.exists()
    recorder.record("safe", status=401)
    assert json.loads(path.read_text())[0]["run_id"] == "run-one"


def test_cleanup_targets_only_explicitly_owned_key():
    resources = RunResources()
    assert resources.cleanup_ids() == []
    resources.key_id = "key-this-run"
    assert resources.cleanup_ids() == ["key-this-run"]


def test_gateway_probe_disables_system_proxy(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "server.backend.acceptance.probe_gateway.httpx.Client", lambda **kw: seen.update(kw)
    )
    gateway_client()
    assert seen["trust_env"] is False
