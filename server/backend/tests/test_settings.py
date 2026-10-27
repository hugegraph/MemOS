import math

import pytest
from pydantic import ValidationError
from server.backend.settings import Settings


def test_defaults_are_local_and_bounded(monkeypatch):
    monkeypatch.delenv("DASHBOARD_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_UPSTREAM_TIMEOUT_SECONDS", raising=False)
    settings = Settings.from_env()
    assert settings.upstream_url == "http://127.0.0.1:8000/"
    assert settings.upstream_timeout_seconds == 3.0


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("DASHBOARD_UPSTREAM_URL", "https://memos.test/prefix")
    monkeypatch.setenv("DASHBOARD_UPSTREAM_TIMEOUT_SECONDS", "1.5")
    settings = Settings.from_env()
    assert settings.upstream_url == "https://memos.test/prefix/"
    assert settings.upstream_timeout_seconds == 1.5


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/memos",
        "ftp://memos.test",
        "http://",
        "http://user:password@memos.test",
        "http://memos.test/?token=private",
        "http://memos.test/#fragment",
    ],
)
def test_invalid_or_credential_bearing_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        Settings(auth_enabled=False, upstream_url=url)


@pytest.mark.parametrize("timeout", [0, -1, math.inf, math.nan, 61])
def test_timeout_must_be_finite_positive_and_bounded(timeout):
    with pytest.raises(ValidationError):
        Settings(auth_enabled=False, upstream_timeout_seconds=timeout)
