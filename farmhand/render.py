"""Public API for submitting Blender render jobs to Modal."""

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import modal
from rich.table import Table

from farmhand import config, log
from farmhand.config import Config


def _lookup(fn_name: str, cfg: Config) -> modal.Function:
    return modal.Function.from_name(cfg.app_name, fn_name, environment_name=cfg.environment)


@dataclass
class RenderJob:
    """Configuration for a Blender render job. None fields fall back to the loaded Config."""

    blend_file: str | Path
    output_dir: str | Path | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    frame_step: int | None = None
    resolution_x: int | None = None
    resolution_y: int | None = None
    resolution_percentage: int | None = None
    samples: int | None = None
    engine: str | None = None
    frames_per_container: int | None = None
    # Video options
    video: str | None = None
    fps: int | None = None
    codec: str | None = None
    crf: int | None = None
    video_only: bool = False
    ephemeral: bool = False


def _get_functions(job: RenderJob, cfg: Config):
    """Get Modal function handles, either from deployed app or direct import for ephemeral mode."""
    if job.ephemeral:
        config.set_active(cfg)
        from farmhand.modal_app import combine, render, upload_blend

        return upload_blend, render, combine
    else:
        return (
            _lookup("upload_blend", cfg),
            _lookup("render", cfg),
            _lookup("combine", cfg),
        )


def submit_render(job: RenderJob, cfg: Config | None = None) -> Iterator[Path]:
    """Submit a render job to Modal and yield frame paths as they complete."""
    log.init()
    if cfg is None:
        cfg = config.load()

    # Fill unset job fields from config
    for name in ("output_dir", "frames_per_container", "fps", "codec", "crf"):
        if getattr(job, name) is None:
            setattr(job, name, getattr(cfg, name))

    blend_path = Path(job.blend_file).expanduser().resolve()
    output_directory = Path(job.output_dir).expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    blend_bytes = blend_path.read_bytes()
    job_id = uuid.uuid4().hex[:12]

    # Print job config table
    table = Table(title=cfg.app_name, show_header=False, border_style="dim")
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job ID", job_id)
    table.add_row("Blend file", f"{blend_path.name} ({len(blend_bytes) / 1024 / 1024:.1f} MB)")
    table.add_row("Output", str(output_directory))
    table.add_row("Environment", cfg.environment or "(modal default)")
    if job.ephemeral:
        table.add_row("Mode", "ephemeral")
    if job.resolution_x or job.resolution_y:
        table.add_row("Resolution", f"{job.resolution_x}x{job.resolution_y}")
    if job.resolution_percentage:
        table.add_row("Resolution %", f"{job.resolution_percentage}%")
    if job.samples:
        table.add_row("Samples", str(job.samples))
    if job.engine:
        table.add_row("Engine", job.engine)
    if job.video:
        table.add_row("Video", f"{job.video} (fps={job.fps}, codec={job.codec}, crf={job.crf})")
        if job.video_only:
            table.add_row("Mode", "video-only (frames not saved locally)")
    log.console.print(table)

    upload_blend, render, combine_fn = _get_functions(job, cfg)

    def _run_render():
        nonlocal upload_blend, render, combine_fn

        log.log("Uploading blend file to shared volume...")
        info = upload_blend.remote(blend_bytes, job_id)
        log.success(f"Upload complete. Blend file frames: {info['start']}-{info['end']} (step {info['step']})")

        start = job.frame_start if job.frame_start is not None else info["start"]
        end = job.frame_end if job.frame_end is not None else info["end"]
        step = job.frame_step if job.frame_step is not None else info["step"]

        total_frames = len(range(start, end + 1, step))
        all_frames = list(range(start, end + 1, step))
        chunks = [
            all_frames[i : i + job.frames_per_container]
            for i in range(0, len(all_frames), job.frames_per_container)
        ]

        log.log(f"Rendering [bold]{total_frames}[/bold] frames ({start}-{end}, step {step})")
        log.log(f"Dispatching [bold]{len(chunks)}[/bold] jobs ({job.frames_per_container} frame(s) each)")

        args = [
            (
                job_id,
                chunk[0],
                chunk[-1],
                step,
                job.resolution_x,
                job.resolution_y,
                job.resolution_percentage,
                job.samples,
                job.engine,
            )
            for chunk in chunks
        ]

        # Ensure output dir exists (retry for cloud-mounted filesystems)
        output_directory.mkdir(parents=True, exist_ok=True)
        if not output_directory.exists():
            raise FileNotFoundError(f"Could not create output directory: {output_directory}")

        completed = 0
        results = []
        for i, frame_results in enumerate(render.starmap(args)):
            log.log(f"Job {i + 1}/{len(chunks)} returned {len(frame_results)} frames")
            for frame_number, image in frame_results:
                completed += 1
                if not job.video_only:
                    frame_path = output_directory / f"frame_{frame_number:05}.png"
                    frame_path.parent.mkdir(parents=True, exist_ok=True)
                    frame_path.write_bytes(image)
                    log.success(f"[{completed}/{total_frames}] Frame {frame_number} \u2192 {frame_path.name}")
                    results.append(frame_path)
                else:
                    log.log(f"[{completed}/{total_frames}] Frame {frame_number} rendered")

        if job.video:
            log.log(f"Combining {total_frames} frames into video on server...")
            video_bytes = combine_fn.remote(
                job_id,
                fps=job.fps,
                codec=job.codec,
                crf=job.crf,
            )
            video_path = output_directory / job.video
            video_path.write_bytes(video_bytes)
            log.success(f"Video saved \u2192 {video_path} ({len(video_bytes) / 1024 / 1024:.1f} MB)")
            results.append(video_path)

        log.success(f"Done! {completed} frames rendered to {output_directory}")
        return results

    if job.ephemeral:
        from farmhand.modal_app import app

        modal.enable_output()
        with app.run(environment_name=cfg.environment):
            yield from _run_render()
    else:
        yield from _run_render()
