import pytest
from pydantic import ValidationError
from server.backend.control import ControlStore
from server.backend.settings import Settings


def configured(tmp_path, seeds=(("a", "cube-a"),), default="a"):
    return Settings(
        auth_enabled=False, projects_seed=seeds, default_project_id=default, data_dir=tmp_path
    )


def test_project_mapping_persists_without_authorizing_retired_configuration(tmp_path):
    store = ControlStore(configured(tmp_path))
    assert store.resolve_cube("a") == "cube-a"
    assert store.resolve_cube("cube-a") is None
    assert ControlStore(configured(tmp_path)).resolve_cube("a") == "cube-a"
    replacement = ControlStore(configured(tmp_path, (("b", "cube-b"),), "b"))
    assert replacement.resolve_cube("a") is None
    assert replacement.resolve_cube("b") == "cube-b"
    assert [x["id"] for x in replacement.dictionary("b")] == ["b"]
    with pytest.raises(ValueError, match="conflicts"):
        ControlStore(configured(tmp_path, (("a", "changed-cube"),), "a"))


@pytest.mark.parametrize(
    "seeds,default",
    [
        ((("a", "cube-a"),), "missing"),
        ((("a", "cube-a"),), None),
        ((("a", "cube-a"), ("a", "cube-b")), "a"),
        ((("a", "cube-a"), ("b", "cube-a")), "a"),
        ("wrong-format", "a"),
        ("a:cube-a;", "a"),
    ],
)
def test_invalid_project_configuration_fails_startup_validation(tmp_path, seeds, default):
    with pytest.raises(ValidationError):
        configured(tmp_path, seeds, default)


def test_project_environment_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PROJECTS", "a:cube-a;b:cube-b")
    monkeypatch.setenv("DASHBOARD_DEFAULT_PROJECT_ID", "a")
    monkeypatch.setenv("DASHBOARD_DATA_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.projects_seed == (("a", "cube-a"), ("b", "cube-b"))
    assert settings.default_project_id == "a"
    assert settings.data_dir == tmp_path
