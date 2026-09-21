# farmhand

Distributed Blender rendering on [Modal](https://modal.com). Splits a `.blend` file's frame range across GPU containers, stores the frames on a Modal volume, and optionally stitches them into a video server-side.

## Setup

```sh
uv sync
uv run farmhand deploy            # once, deploys the Modal app
```

## Usage

```sh
uv run farmhand render scene.blend                      # render all frames
uv run farmhand render scene.blend --frame-start 1 --frame-end 48 --samples 64
uv run farmhand render scene.blend --video out.mp4      # also stitch an mp4
uv run farmhand render scene.blend --ephemeral          # no deploy needed
uv run farmhand jobs                                    # list jobs on the volume
uv run farmhand download <job_id>
uv run farmhand cleanup <job_id>
```

## Configuration

Settings are resolved in this order: CLI flags, then `farmhand.yml`, then `[tool.farmhand]` in `pyproject.toml`, then built-in fallbacks. Config files are found by walking up from the current directory, so put one in the project you render from. Use `--config PATH` to point at a specific file, and `farmhand config` to print the resolved values.

```toml
# pyproject.toml
[tool.farmhand]
environment = "my-modal-env"   # omit to use your Modal profile's default
gpu = "L40S"
```

See `farmhand.example.yml` for every key. Infrastructure keys (`gpu`, `max_containers`, `timeout`, `bpy_version`, `volume`) are baked in at `farmhand deploy`; `environment` and `app_name` are used by every command.
