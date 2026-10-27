import os
import subprocess
import sys
from pathlib import Path


def test_module_has_standalone_cli():
    completed = subprocess.run(
        [sys.executable, "-m", "server.backend", "--help"],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--host" in completed.stdout
    assert "--port" in completed.stdout


def test_invalid_startup_configuration_does_not_print_credentials():
    env = dict(os.environ, DASHBOARD_UPSTREAM_URL="http://user:private-password@localhost/")
    completed = subprocess.run(
        [sys.executable, "-m", "server.backend", "--port", "0"],
        cwd=Path(__file__).resolve().parents[3],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 2
    assert "private-password" not in completed.stdout + completed.stderr
    assert "Traceback" not in completed.stderr
