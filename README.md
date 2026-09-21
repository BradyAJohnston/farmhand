# farmhand

Distributed Blender rendering on [Modal](https://modal.com). farmhand uploads a `.blend` file, splits its frame range across GPU containers, streams the finished frames back to your machine, and can stitch them into a video on the server. It was built for rendering [Molecular Nodes](https://github.com/BradyAJohnston/MolecularNodes) animations, which need nothing installed on the server beyond bpy because the node groups and materials travel inside the `.blend`.

## Requirements

- Python 3.11 or newer.
- A [Modal](https://modal.com) account. Modal bills per second of GPU time; an L40S is about $2 per hour.
- `ffmpeg` on your machine, only if you want to combine already-downloaded frames locally.

## Install

```sh
uv tool install farmhand-bpy    # or: pip install farmhand-bpy
```

The distribution is `farmhand-bpy`; the command and the import name are `farmhand`.

## Quickstart

```sh
farmhand setup                                   # log in to Modal (opens a browser)
farmhand deploy                                  # build the render image and deploy the app (minutes, first time)
farmhand render scene.blend --video scene.mp4    # render every frame, stitch the video on the server
```

Frames land in `render/<job_id>/frame_00001.png` and so on, and the video in `render/<job_id>/scene.mp4`. The job ID is printed in the table at the start of the render and by `farmhand jobs`.

Two things to know before the first real render:

- **The `.blend` must be self-contained.** Only that one file is uploaded. Pack external data first (File > External Data > Pack Resources) or linked libraries and textures will be missing.
- **`bpy_version` must match the Blender that saved the file.** The default is 5.1.0. Set it in your config to the version you use, then `farmhand deploy`. A file saved by a newer Blender can lose node groups or materials when opened by an older bpy without any error, giving blank frames after paying for GPU time.

## Commands

```sh
farmhand config                                  # resolved settings, which file they came from, Modal auth status
farmhand setup                                   # Modal login; creates the configured environment if it is missing
farmhand deploy                                  # deploy; rerun after changing gpu, bpy_version, timeout, max_containers or volume

farmhand render scene.blend                      # all frames from the file's range, into render/<job_id>/
farmhand render scene.blend --frame-start 1 --frame-end 48 --samples 64 --resolution-percentage 50
farmhand render scene.blend --video out.mp4      # also stitch an mp4 server-side, no local ffmpeg needed
farmhand render scene.blend --ephemeral          # no deploy needed; image built on first use and cached

farmhand jobs                                    # jobs still on the Modal volume
farmhand download <job_id>                       # fetch a job's frames again, into render/<job_id>/
farmhand combine render/<job_id> -o out.mp4      # local frames -> video with your ffmpeg
farmhand combine <job_id> -o out.mp4             # frames still on the volume -> video, server-side
farmhand cleanup <job_id> [--yes]                # delete a job's blend file and frames from the volume
```

Every job's input file and frames stay on the Modal volume, and count towards its storage, until you run `cleanup`.

The global options `--config`, `--environment` and `--app-name` go before the subcommand:

```sh
farmhand --environment prod render scene.blend
```

## Configuration

farmhand reads one config file: the nearest `farmhand.yml`, or `pyproject.toml` with a `[tool.farmhand]` table, searching upward from the current directory. Within a directory `farmhand.yml` wins. Files are not merged. Command-line flags override the file, and anything unset uses the built-in default. `farmhand config` shows the result.

```toml
# pyproject.toml, in the project you render from
[tool.farmhand]
environment = "render"      # Modal environment; omit for your profile's default
bpy_version = "5.2.2"       # the Blender version that saved your .blend files
gpu = "L40S"                # or a fallback list: ["RTX-PRO-6000", "L40S"]
frames_per_container = 4
```

| Key | Default | When it is read |
|---|---|---|
| `profile` | active Modal profile | every command |
| `environment` | profile default | every command |
| `app_name` | `farmhand` | every command |
| `volume` | `<app_name>-data` | deploy |
| `gpu` | `L40S` | deploy |
| `max_containers` | `50` | deploy, capped by your Modal plan |
| `timeout` | `7200` | deploy, seconds per container |
| `bpy_version` | `5.1.0` | deploy |
| `output_dir` | `render` | render, download |
| `frames_per_container` | `1` | render |
| `fps` | `30` | render, combine |
| `codec` | `libx265` | render, combine |
| `crf` | `20` | render, combine |

Keys read at deploy are baked into the deployed app; change them and run `farmhand deploy` again. The rest are per-run defaults that the matching flags override. Modal credentials come from `farmhand setup`, or from the `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` environment variables in CI.

Each container opens the file once and renders `frames_per_container` frames in sequence, so a higher value amortises the container start-up and scene evaluation over more frames. Keep `frames_per_container` times the per-frame render time under `timeout`.

## Choosing a GPU

See [docs/gpus.md](https://github.com/BradyAJohnston/farmhand/blob/master/docs/gpus.md). Short version: Cycles wants RT cores and FP32 throughput, so the visualisation cards (`RTX-PRO-6000`, `L40S`, `L4`) are both faster and several times cheaper per frame than the AI cards (`A100`, `H100`, `H200`, `B200`). The default `L40S` is a good balance.

## Python API

```python
from farmhand import RenderJob, combine_frames, config, submit_render

cfg = config.load()  # same lookup as the CLI; or Config(environment="render", bpy_version="5.2.2")
config.apply_env(cfg)  # exports cfg.profile for Modal; call before importing farmhand.render

job = RenderJob("scene.blend", frame_end=48, samples=64, video="out.mp4")
result = submit_render(job, cfg)  # blocks until done
result.frames  # list[Path] under result.output_dir, which is <output_dir>/<job_id>
result.video  # Path or None

combine_frames("render/abc123def456", "out.mp4", fps=30)  # local ffmpeg
```

`RenderJob` fields left as `None` take the config default, or, for the frame range, the values saved in the `.blend`.

## Example

[`examples/6n2y-spin`](https://github.com/BradyAJohnston/farmhand/tree/master/examples/6n2y-spin) builds a Molecular Nodes scene of PDB 6n2y turning on the spot and renders it with farmhand.

## License

MIT. See [LICENSE](https://github.com/BradyAJohnston/farmhand/blob/master/LICENSE).
