"""Configuration: CLI args > nearest farmhand.yml or [tool.farmhand] in pyproject.toml > defaults."""

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CONFIG_FILENAMES = ("farmhand.yml", "farmhand.yaml", "pyproject.toml")
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")


class FarmhandError(Exception):
    """A user-facing error: the CLI prints the message and exits without a traceback."""


@dataclass
class Config:
    # Modal account
    profile: str | None = None  # Modal profile in ~/.modal.toml; None -> active profile
    environment: str | None = None  # None -> Modal profile's default environment
    # Modal infrastructure (baked in at deploy)
    app_name: str = "farmhand"
    volume: str = ""  # "" -> "<app_name>-data"
    gpu: str | list[str] = "L40S"  # a list is a Modal fallback order, e.g. ["RTX-PRO-6000", "L40S"]
    max_containers: int = 50
    timeout: int = 7200
    bpy_version: str = "5.1.0"
    # Render defaults (per run)
    output_dir: str = "render"
    frames_per_container: int = 1
    fps: int = 30
    codec: str = "libx265"
    crf: int = 20
    # Where these values came from (not a setting)
    source: Path | None = None

    def __post_init__(self):
        if not self.volume:
            self.volume = f"{self.app_name}-data"
        if isinstance(self.gpu, str) and "," in self.gpu:
            self.gpu = [g.strip() for g in self.gpu.split(",") if g.strip()]


_KEYS = {f.name for f in fields(Config)} - {"source"}
_STR_KEYS = {"profile", "environment", "app_name", "volume", "bpy_version", "output_dir", "codec"}
_INT_KEYS = {"max_containers", "timeout", "frames_per_container", "fps", "crf"}


def _read_pyproject(path: Path) -> dict | None:
    try:
        with path.open("rb") as f:
            return tomllib.load(f).get("tool", {}).get("farmhand")
    except tomllib.TOMLDecodeError as e:
        raise FarmhandError(f"{path}: {e}") from e


def _read(path: Path) -> dict:
    if path.suffix in (".yml", ".yaml"):
        import yaml

        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            raise FarmhandError(f"{path}: {e}") from e
    else:
        data = _read_pyproject(path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FarmhandError(f"{path}: expected a mapping of settings, got {type(data).__name__}")
    return data


def _validate(data: dict, path: Path | None) -> None:
    where = str(path) if path else "config"
    unknown = set(data) - _KEYS
    if unknown:
        raise FarmhandError(f"{where}: unknown keys: {', '.join(sorted(unknown))}")
    for key, value in data.items():
        if value is None and key in ("profile", "environment"):
            continue
        ok = True
        if key in _STR_KEYS:
            ok = isinstance(value, str)
        elif key in _INT_KEYS:
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif key == "gpu":
            ok = isinstance(value, str) or (
                isinstance(value, list) and value and all(isinstance(g, str) for g in value)
            )
        if not ok:
            raise FarmhandError(f"{where}: {key} = {value!r} has the wrong type")


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk up from `start` to the nearest farmhand.yml or pyproject.toml with [tool.farmhand]."""
    start = (start or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        for name in CONFIG_FILENAMES:
            path = directory / name
            if not path.is_file():
                continue
            if name == "pyproject.toml" and _read_pyproject(path) is None:
                continue
            return path
    return None


def load(path: str | Path | None = None, **overrides) -> Config:
    """Load config from `path` (or the nearest config file), applying non-None overrides on top."""
    cfg_path = Path(path).resolve() if path else find_config_file()
    data = _read(cfg_path) if cfg_path else {}
    _validate(data, cfg_path)
    overrides = {k: v for k, v in overrides.items() if v is not None}
    _validate(overrides, None)
    data.update(overrides)
    return Config(source=cfg_path, **data)


def apply_env(cfg: Config) -> None:
    """Export settings that Modal reads when it is imported. Call before `import modal`."""
    if cfg.profile:
        os.environ["MODAL_PROFILE"] = cfg.profile


CONTAINER_ENV = "FARMHAND_CONFIG"


def to_container_env(cfg: Config) -> dict[str, str]:
    """Serialise the config so render containers use the deploy-time settings, not defaults."""
    data = {k: v for k, v in asdict(cfg).items() if k != "source"}
    return {CONTAINER_ENV: json.dumps(data)}


_active: Config | None = None


def set_active(cfg: Config | None) -> None:
    """Set the config that modal_app will use when it is imported."""
    global _active
    _active = cfg


def active() -> Config:
    global _active
    if _active is None:
        if CONTAINER_ENV in os.environ:
            _active = Config(**json.loads(os.environ[CONTAINER_ENV]))
        else:
            _active = load()
    return _active


def check_job_id(job_id: str) -> str:
    """Job IDs are 12 hex characters. Anything else could escape the job directory."""
    if not JOB_ID_PATTERN.match(job_id):
        raise FarmhandError(f"Invalid job ID {job_id!r}: expected 12 hex characters")
    return job_id
