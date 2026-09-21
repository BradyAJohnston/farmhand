"""Configuration: CLI args > farmhand.yml > [tool.farmhand] in pyproject.toml > defaults."""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

CONFIG_FILENAMES = ("farmhand.yml", "farmhand.yaml", "pyproject.toml")


@dataclass
class Config:
    # Modal infrastructure
    environment: str | None = None  # None -> Modal profile's default environment
    app_name: str = "farmhand"
    volume: str | None = None  # None -> "<app_name>-data"
    gpu: str = "L40S"
    max_containers: int = 50
    timeout: int = 7200
    bpy_version: str = "5.1.0"
    # Render defaults
    output_dir: str = "render"
    frames_per_container: int = 1
    fps: int = 30
    codec: str = "libx265"
    crf: int = 20
    # Where these values came from (not a setting)
    source: Path | None = None

    def __post_init__(self):
        if self.volume is None:
            self.volume = f"{self.app_name}-data"


_KEYS = {f.name for f in fields(Config)} - {"source"}


def _read_pyproject(path: Path) -> dict | None:
    with path.open("rb") as f:
        return tomllib.load(f).get("tool", {}).get("farmhand")


def _read(path: Path) -> dict:
    if path.suffix in (".yml", ".yaml"):
        import yaml

        return yaml.safe_load(path.read_text()) or {}
    return _read_pyproject(path) or {}


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for farmhand.yml or a pyproject.toml with [tool.farmhand]."""
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

    unknown = set(data) - _KEYS
    if unknown:
        raise ValueError(f"Unknown keys in {cfg_path}: {', '.join(sorted(unknown))}")

    data.update({k: v for k, v in overrides.items() if v is not None})
    return Config(source=cfg_path, **data)


_active: Config | None = None


def set_active(cfg: Config) -> None:
    """Set the config that modal_app will use when it is imported."""
    global _active
    _active = cfg


def active() -> Config:
    global _active
    if _active is None:
        _active = load()
    return _active
