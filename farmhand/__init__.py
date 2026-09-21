"""Farmhand - Distributed Blender rendering on Modal."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("farmhand-bpy")
except PackageNotFoundError:  # running from a source tree
    __version__ = "0.0.0"

__all__ = ["Config", "FarmhandError", "RenderJob", "RenderResult", "combine_frames", "submit_render"]

_LAZY = {
    "Config": ("farmhand.config", "Config"),
    "FarmhandError": ("farmhand.config", "FarmhandError"),
    "RenderJob": ("farmhand.render", "RenderJob"),
    "RenderResult": ("farmhand.render", "RenderResult"),
    "submit_render": ("farmhand.render", "submit_render"),
    "combine_frames": ("farmhand.video", "combine_frames"),
}


def __getattr__(name):
    # Everything is imported lazily. The render containers import this package with
    # only bpy installed, and Modal reads its profile from the environment at import
    # time, so nothing may pull in rich or modal before config has been applied.
    if name in _LAZY:
        import importlib

        module, attr = _LAZY[name]
        return getattr(importlib.import_module(module), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted([*__all__, "__version__"])
