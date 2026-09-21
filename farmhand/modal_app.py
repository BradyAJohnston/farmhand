"""Modal functions for remote Blender rendering."""

from pathlib import Path

import modal

from farmhand import config

cfg = config.active()

app = modal.App(cfg.app_name)

# The containers get the deploy-time config through an env var and this package
# as source, so they never need a config file, rich, pyyaml or a farmhand install.
container_env = config.to_container_env(cfg)

rendering_image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("xorg", "libxkbcommon0")
    .uv_pip_install(f"bpy=={cfg.bpy_version}")
    .env(container_env)
    .add_local_python_source("farmhand")
)

vol = modal.Volume.from_name(cfg.volume, create_if_missing=True)


VOLUME_MOUNT = "/data"


def _job_dir(job_id: str) -> Path:
    """The job's directory on the volume. Validates the ID so it cannot escape the mount."""
    return Path(VOLUME_MOUNT) / config.check_job_id(job_id)


@app.function(image=rendering_image, volumes={VOLUME_MOUNT: vol})
def upload_blend(blend_file: bytes, job_id: str) -> dict:
    """Uploads blend file to shared volume and returns frame info."""
    import bpy  # ty: ignore[unresolved-import]

    blend_path = str(_job_dir(job_id) / "input.blend")
    Path(blend_path).parent.mkdir(parents=True, exist_ok=True)
    Path(blend_path).write_bytes(blend_file)
    vol.commit()
    print(f"[upload] Wrote {len(blend_file)} bytes to {blend_path}")

    bpy.ops.wm.open_mainfile(filepath=blend_path)
    scene = bpy.context.scene
    info = {
        "start": scene.frame_start,
        "end": scene.frame_end,
        "step": scene.frame_step,
    }
    print(f"[upload] Frame info: {info}")
    return info


@app.function(
    gpu=cfg.gpu,
    max_containers=cfg.max_containers,
    image=rendering_image,
    timeout=cfg.timeout,
    retries=modal.Retries(max_retries=2, initial_delay=5.0, backoff_coefficient=1.0),
    volumes={VOLUME_MOUNT: vol},
)
def render(
    job_id: str,
    frame_start: int,
    frame_end: int,
    frame_step: int = 1,
    resolution_x: int | None = None,
    resolution_y: int | None = None,
    resolution_percentage: int | None = None,
    samples: int | None = None,
    engine: str | None = None,
) -> list[tuple[int, str, bytes]]:
    """Renders a range of frames, returning (frame_number, file_extension, image_bytes) triples."""
    import bpy  # ty: ignore[unresolved-import]

    print(f"[render] Starting: frames {frame_start}-{frame_end} step {frame_step}")
    print(
        f"[render] Overrides: resolution={resolution_x}x{resolution_y} pct={resolution_percentage} "
        f"samples={samples} engine={engine}"
    )

    job_dir = _job_dir(job_id)
    blend_path = str(job_dir / "input.blend")
    vol.reload()
    print(f"[render] Reading blend file from {blend_path}")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    print("[render] Opened blend file")

    configure_rendering(
        bpy.context,
        resolution_x=resolution_x,
        resolution_y=resolution_y,
        resolution_percentage=resolution_percentage,
        samples=samples,
        engine=engine,
    )
    scene = bpy.context.scene
    print(
        f"[render] Rendering configured: engine={scene.render.engine} "
        f"res={scene.render.resolution_x}x{scene.render.resolution_y} samples={scene.cycles.samples}"
    )

    # Frames are single images. A movie output format (FFMPEG) cannot write stills, so fall back to PNG.
    render_settings = bpy.context.scene.render
    if render_settings.is_movie_format:
        render_settings.image_settings.file_format = "PNG"
    file_format = render_settings.image_settings.file_format
    ext = render_settings.file_extension
    print(f"[render] Output format: {file_format} ({ext})")

    results = []
    total = len(range(frame_start, frame_end + 1, frame_step))
    for i, frame in enumerate(range(frame_start, frame_end + 1, frame_step)):
        print(f"[render] Rendering frame {frame} ({i + 1}/{total})...")
        output_path = f"/tmp/output-{frame}{ext}"
        bpy.context.scene.frame_set(frame)
        bpy.context.scene.render.filepath = output_path
        bpy.ops.render.render(write_still=True)
        frame_bytes = Path(output_path).read_bytes()
        print(f"[render] Frame {frame} done, {len(frame_bytes)} bytes")
        # Also save to volume for server-side video combine
        vol_frame_path = job_dir / f"frame_{frame:05d}{ext}"
        vol_frame_path.parent.mkdir(parents=True, exist_ok=True)
        vol_frame_path.write_bytes(frame_bytes)

        results.append((frame, ext, frame_bytes))

    vol.commit()
    print(f"[render] Complete: {len(results)} frames rendered")
    return results


combination_image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("ffmpeg")
    .env(container_env)
    .add_local_python_source("farmhand")
)


@app.function(
    image=combination_image,
    volumes={VOLUME_MOUNT: vol},
    timeout=3600,
)
def combine(
    job_id: str,
    fps: int = 30,
    codec: str = "libx265",
    crf: int = 20,
) -> bytes:
    """Combines rendered frames from the volume into a video."""
    from farmhand.video import combine_frames

    vol.reload()
    out_path = combine_frames(_job_dir(job_id), "/tmp/output.mp4", fps=fps, codec=codec, crf=crf)
    video_bytes = out_path.read_bytes()
    print(f"[combine] Video complete: {len(video_bytes)} bytes")
    return video_bytes


@app.function(
    image=combination_image,
    volumes={VOLUME_MOUNT: vol},
)
def list_jobs() -> list[dict]:
    """List all jobs stored on the volume."""
    vol.reload()
    data_dir = Path(VOLUME_MOUNT)
    jobs = []
    if not data_dir.exists():
        return jobs

    for entry in sorted(data_dir.iterdir()):
        if not entry.is_dir():
            continue
        frames = sorted(entry.glob("frame_*.*"))
        has_blend = (entry / "input.blend").exists()
        blend_size = (entry / "input.blend").stat().st_size if has_blend else 0
        jobs.append(
            {
                "job_id": entry.name,
                "frame_count": len(frames),
                "has_blend": has_blend,
                "blend_size": blend_size,
                "total_size": sum(f.stat().st_size for f in entry.rglob("*") if f.is_file()),
            }
        )
    return jobs


@app.function(
    image=combination_image,
    volumes={VOLUME_MOUNT: vol},
)
def download_job_frames(job_id: str) -> list[tuple[str, bytes]]:
    """Download all frames for a job from the volume."""
    vol.reload()
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        raise FileNotFoundError(f"Job {job_id} not found on volume")

    files = []
    for f in sorted(job_dir.glob("frame_*.*")):
        files.append((f.name, f.read_bytes()))
    print(f"[download] Returning {len(files)} files for job {job_id}")
    return files


@app.function(
    image=combination_image,
    volumes={VOLUME_MOUNT: vol},
)
def delete_job(job_id: str) -> int:
    """Delete a job and its files from the volume."""
    import shutil

    vol.reload()
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        raise FileNotFoundError(f"Job {job_id} not found on volume")

    count = sum(1 for _ in job_dir.rglob("*") if _.is_file())
    shutil.rmtree(job_dir)
    vol.commit()
    print(f"[cleanup] Deleted {count} files for job {job_id}")
    return count


def enable_gpus(device_type, use_cpus=False):
    import bpy  # ty: ignore[unresolved-import]

    preferences = bpy.context.preferences
    cycles_preferences = preferences.addons["cycles"].preferences
    cycles_preferences.refresh_devices()
    devices = cycles_preferences.devices

    if not devices:
        raise RuntimeError("Unsupported device type")

    activated_gpus = []
    for device in devices:
        if device.type == "CPU":
            device.use = use_cpus
        else:
            device.use = True
            activated_gpus.append(device.name)
            print("activated gpu", device.name)

    bpy.context.scene.cycles.device = "GPU"
    cycles_preferences.compute_device_type = device_type

    return activated_gpus


def enable_optimal_gpu():
    options = ["OPTIX", "CUDA", "METAL", "HIP", "ONEAPI"]
    for backend in options:
        try:
            enable_gpus(backend)
            return
        except TypeError:
            continue

    raise TypeError("Failed to enable GPU backend")


def configure_rendering(
    ctx,
    resolution_x: int | None = None,
    resolution_y: int | None = None,
    resolution_percentage: int | None = None,
    samples: int | None = None,
    engine: str | None = None,
):
    if engine is not None:
        ctx.scene.render.engine = engine

    if resolution_x is not None:
        ctx.scene.render.resolution_x = resolution_x
    if resolution_y is not None:
        ctx.scene.render.resolution_y = resolution_y
    if resolution_percentage is not None:
        ctx.scene.render.resolution_percentage = resolution_percentage
    if samples is not None:
        ctx.scene.cycles.samples = samples

    if ctx.scene.render.engine == "CYCLES":
        enable_optimal_gpu()
