"""Modal functions for remote Blender rendering."""

from pathlib import Path

import modal

from farmhand import config

cfg = config.active()

app = modal.App(cfg.app_name)

rendering_image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("xorg", "libxkbcommon0")
    .uv_pip_install(f"bpy=={cfg.bpy_version}")
)

vol = modal.Volume.from_name(cfg.volume, create_if_missing=True)


VOLUME_MOUNT = "/data"


@app.function(image=rendering_image, volumes={VOLUME_MOUNT: vol})
def upload_blend(blend_file: bytes, job_id: str) -> dict:
    """Uploads blend file to shared volume and returns frame info."""
    import bpy

    blend_path = f"{VOLUME_MOUNT}/{job_id}/input.blend"
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
    volumes={VOLUME_MOUNT: vol},
)
def render(
    job_id: str,
    frame_start: int = 0,
    frame_end: int = 0,
    frame_step: int = 1,
    resolution_x: int | None = None,
    resolution_y: int | None = None,
    resolution_percentage: int | None = None,
    samples: int | None = None,
    engine: str | None = None,
) -> list[tuple[int, bytes]]:
    """Renders a range of frames from a Blender file, returning (frame_number, png_bytes) pairs."""
    import bpy

    print(f"[render] Starting: frames {frame_start}-{frame_end} step {frame_step}")
    print(
        f"[render] Overrides: resolution={resolution_x}x{resolution_y} pct={resolution_percentage} samples={samples} engine={engine}"
    )

    blend_path = f"{VOLUME_MOUNT}/{job_id}/input.blend"
    vol.reload()
    print(f"[render] Reading blend file from {blend_path}")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    print(f"[render] Opened blend file")

    configure_rendering(
        bpy.context,
        resolution_x=resolution_x,
        resolution_y=resolution_y,
        resolution_percentage=resolution_percentage,
        samples=samples,
        engine=engine,
    )
    print(
        f"[render] Rendering configured: engine={bpy.context.scene.render.engine} res={bpy.context.scene.render.resolution_x}x{bpy.context.scene.render.resolution_y} samples={bpy.context.scene.cycles.samples}"
    )

    # Map Blender file format to file extension
    format_to_ext = {
        "PNG": ".png",
        "JPEG": ".jpg",
        "OPEN_EXR": ".exr",
        "OPEN_EXR_MULTILAYER": ".exr",
        "TIFF": ".tiff",
        "BMP": ".bmp",
        "HDR": ".hdr",
        "TARGA": ".tga",
        "TARGA_RAW": ".tga",
    }
    file_format = bpy.context.scene.render.image_settings.file_format
    ext = format_to_ext.get(file_format, ".png")
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
        vol_frame_path = f"{VOLUME_MOUNT}/{job_id}/frame_{frame:05d}{ext}"
        Path(vol_frame_path).parent.mkdir(parents=True, exist_ok=True)
        Path(vol_frame_path).write_bytes(frame_bytes)

        results.append((frame, frame_bytes))

    vol.commit()
    print(f"[render] Complete: {len(results)} frames rendered")
    return results


combination_image = modal.Image.debian_slim(python_version="3.13").apt_install("ffmpeg")


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
    import subprocess

    vol.reload()

    job_dir = Path(f"{VOLUME_MOUNT}/{job_id}")
    frame_dir = job_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    # Find frames with any image extension
    extensions = ("*.png", "*.jpg", "*.jpeg", "*.exr", "*.tiff", "*.tif", "*.bmp")
    frame_files = []
    for ext in extensions:
        frame_files.extend(job_dir.glob(f"frame_{ext}"))
    frame_files = sorted(frame_files)

    if not frame_files:
        raise FileNotFoundError(f"No frame files found in {job_dir}")

    img_ext = frame_files[0].suffix
    print(f"[combine] Found {len(frame_files)} frames ({img_ext})")

    # Symlink into sequential naming for ffmpeg
    for i, src in enumerate(frame_files):
        dst = frame_dir / f"frame_{i:05d}{img_ext}"
        if not dst.exists():
            dst.symlink_to(src)

    out_path = "/tmp/output.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        f"{frame_dir}/frame_%05d{img_ext}",
        "-vcodec",
        codec,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        out_path,
    ]
    print(f"[combine] Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    video_bytes = Path(out_path).read_bytes()
    print(f"[combine] Video complete: {len(video_bytes)} bytes")
    return video_bytes


@app.function(
    image=combination_image,
    volumes={VOLUME_MOUNT: vol},
)
def list_jobs() -> list[dict]:
    """List all jobs stored on the volume."""
    import os

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
                "total_size": sum(
                    f.stat().st_size for f in entry.rglob("*") if f.is_file()
                ),
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
    job_dir = Path(f"{VOLUME_MOUNT}/{job_id}")
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
    job_dir = Path(f"{VOLUME_MOUNT}/{job_id}")
    if not job_dir.exists():
        raise FileNotFoundError(f"Job {job_id} not found on volume")

    count = sum(1 for _ in job_dir.rglob("*") if _.is_file())
    shutil.rmtree(job_dir)
    vol.commit()
    print(f"[cleanup] Deleted {count} files for job {job_id}")
    return count


def enable_gpus(device_type, use_cpus=False):
    import bpy

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

    enable_optimal_gpu()
