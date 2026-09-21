import json

import pytest

from farmhand import config
from farmhand.config import FarmhandError


def test_defaults_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = config.load()
    assert cfg.source is None
    assert cfg.environment is None
    assert cfg.volume == "farmhand-data"


def test_pyproject_found_from_subdirectory(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[tool.farmhand]\nenvironment = "lab"\napp_name = "x"\n')
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    cfg = config.load()
    assert cfg.source == tmp_path / "pyproject.toml"
    assert cfg.environment == "lab"
    assert cfg.volume == "x-data"


def test_pyproject_without_tool_table_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\n')
    monkeypatch.chdir(tmp_path)
    assert config.load().source is None


def test_yaml_beats_pyproject(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[tool.farmhand]\ngpu = "L4"\n')
    (tmp_path / "farmhand.yml").write_text("gpu: T4\n")
    monkeypatch.chdir(tmp_path)
    cfg = config.load()
    assert cfg.source == tmp_path / "farmhand.yml"
    assert cfg.gpu == "T4"


def test_overrides_beat_files_and_none_is_ignored(tmp_path, monkeypatch):
    (tmp_path / "farmhand.yml").write_text("gpu: T4\nfps: 24\n")
    monkeypatch.chdir(tmp_path)
    cfg = config.load(gpu="L4", fps=None)
    assert cfg.gpu == "L4"
    assert cfg.fps == 24


def test_explicit_path(tmp_path):
    path = tmp_path / "custom.yml"
    path.write_text("crf: 12\n")
    assert config.load(path).crf == 12


def test_unknown_key_rejected(tmp_path):
    path = tmp_path / "farmhand.yml"
    path.write_text("gpus: L4\n")
    with pytest.raises(FarmhandError, match="gpus"):
        config.load(path)


def test_wrong_types_rejected(tmp_path):
    path = tmp_path / "farmhand.yml"
    path.write_text("gpu: 5\n")
    with pytest.raises(FarmhandError, match="gpu"):
        config.load(path)
    path.write_text("max_containers: '50'\n")
    with pytest.raises(FarmhandError, match="max_containers"):
        config.load(path)
    path.write_text("fps: true\n")
    with pytest.raises(FarmhandError, match="fps"):
        config.load(path)
    with pytest.raises(FarmhandError, match="frames_per_container"):
        config.load(frames_per_container="4")


def test_non_mapping_yaml_rejected(tmp_path):
    path = tmp_path / "farmhand.yml"
    path.write_text("- a\n- b\n")
    with pytest.raises(FarmhandError, match="mapping"):
        config.load(path)


def test_broken_pyproject_in_parent_gives_clean_error(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.farmhand\nthis is not toml")
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    with pytest.raises(FarmhandError, match="pyproject.toml"):
        config.load()


def test_broken_yaml_gives_clean_error(tmp_path):
    path = tmp_path / "farmhand.yml"
    path.write_text("gpu: [unclosed\n")
    with pytest.raises(FarmhandError, match="farmhand.yml"):
        config.load(path)


def test_volume_follows_app_name_unless_set(tmp_path):
    assert config.load(app_name="x").volume == "x-data"
    assert config.load(app_name="x", volume="v").volume == "v"


@pytest.mark.parametrize("bad", ["", "..", ".", "a/../..", "ABCDEF123456", "abc", "0123456789abcdef"])
def test_check_job_id_rejects(bad):
    with pytest.raises(FarmhandError):
        config.check_job_id(bad)


def test_check_job_id_accepts_uuid_prefix():
    assert config.check_job_id("0123456789ab") == "0123456789ab"


def test_gpu_list_from_comma_string_and_toml(tmp_path):
    assert config.load(gpu="RTX-PRO-6000, L40S").gpu == ["RTX-PRO-6000", "L40S"]
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.farmhand]\ngpu = ["A", "B"]\n')
    assert config.load(path).gpu == ["A", "B"]


def test_container_env_round_trip(monkeypatch):
    cfg = config.load(gpu="A,B", volume="custom", environment="lab")
    env = config.to_container_env(cfg)
    assert "source" not in json.loads(env[config.CONTAINER_ENV])
    monkeypatch.setenv(config.CONTAINER_ENV, env[config.CONTAINER_ENV])
    config.set_active(None)
    restored = config.active()
    assert restored.gpu == ["A", "B"]
    assert restored.volume == "custom"
    assert restored.environment == "lab"
    assert restored.source is None
    config.set_active(None)


def test_apply_env_sets_profile(monkeypatch):
    monkeypatch.delenv("MODAL_PROFILE", raising=False)
    config.apply_env(config.load(profile="lab"))
    assert __import__("os").environ["MODAL_PROFILE"] == "lab"
