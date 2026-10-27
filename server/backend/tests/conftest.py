"""Never let default relative control/audit paths reach the developer's live state."""

import pytest


@pytest.fixture(autouse=True)
def isolate_relative_data_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
