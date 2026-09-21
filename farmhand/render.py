"""Public API for submitting Blender render jobs to Modal."""

import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

import modal
from rich.table import Table

from farmhand import config, log
from farmhand.config import Config, FarmhandError


def _lookup(fn_name: str, cfg: Config) -> modal.Function:
    return modal.Function.from_name(cfg.app_name, fn_name, environment_name=cfg.environment)


@dataclass
class RenderJob:
    """A render job. None means "use the config default" or, for frame settings, "as saved in the .blend"."""

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


@dataclass
class RenderResult:
    job_id: str
    output_dir: Path  # <output_dir>/<job_id>
    frames: list[Path] = field(default_factory=list)
    video: Path | None = None


def chunk_frames(start: int, end: int, step: int, per_container: int) -> list[list[int]]:
    """Split the frame range into consecutive chunks, one per container."""
    if step <= 0:
        raise FarmhandError(f"frame_step must be positive, got {step}")
    if per_container <= 0:
        raise FarmhandError(f"frames_per_container must be positive, got {per_container}")
    if end < start:
        raise FarmhandError(f"frame_end ({end}) is before frame_start ({start})")
    frames = list(range(start, end + 1, step))
    return [frames[i : i + per_container] for i in range(0, len(frames), per_container)]


def _get_functions(job: RenderJob, cfg: Config):
    """Get Modal function handles, either from deployed app or direct import for ephemeral mode."""
    if job.ephemeral:
        config.set_active(cfg)
        from farmhand.modal_app import combine, render, upload_blend

        return upload_blend, render, combine
    return (
        _lookup("upload_blend", cfg),
        _lookup("render", cfg),
        _lookup("combine", cfg),
    )


def submit_render(job: RenderJob, cfg: Config | None = None) -> RenderResult:
    """Run a render job on Modal and block until it is done.

    Frames are written to `<output_dir>/<job_id>/` as their containers finish, and the
    video (if requested) is written there too. Progress is logged to the console.
    """
    log.init()
    if cfg is None:
        cfg = config.load()
        config.apply_env(cfg)

    job = replace(
        job,
        output_dir=cfg.output_dir if job.output_dir is None else job.output_dir,
        frames_per_container=cfg.frames_per_container if job.frames_per_container is None else job.frames_per_container,
        fps=cfg.fps if job.fps is None else job.fps,
        codec=cfg.codec if job.codec is None else job.codec,
        crf=cfg.crf if job.crf is None else job.crf,
    )
    assert job.output_dir is not None and job.frames_per_container is not None
    per_container: int = job.frames_per_container
    if job.video_only and not job.video:
        raise FarmhandError("--video-only requires --video")

    blend_path = Path(job.blend_file).expanduser().resolve()
    blend_bytes = blend_path.read_bytes()
    job_id = uuid.uuid4().hex[:12]
    output_directory = Path(job.output_dir).expanduser().resolve() / job_id
    output_directory.mkdir(parents=True, exist_ok=True)
    result = RenderResult(job_id=job_id, output_dir=output_directory)

    table = Table(title=cfg.app_name, show_header=False, border_style="dim")
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job ID", job_id)
    table.add_row("Blend file", f"{blend_path.name} ({len(blend_bytes) / 1024 / 1024:.1f} MB)")
    table.add_row("Output", str(output_directory))
    table.add_row("Environment", cfg.environment or "(modal default)")
    if job.ephemeral:
        table.add_row("Mode", "ephemeral")
    if job.resolution_x is not None or job.resolution_y is not None:
        table.add_row("Resolution", f"{job.resolution_x or 'file'} x {job.resolution_y or 'file'}")
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

    def _run() -> None:
        log.log("Uploading blend file to shared volume...")
        info = upload_blend.remote(blend_bytes, job_id)
        log.success(f"Upload complete. Blend file frames: {info['start']}-{info['end']} (step {info['step']})")

        start = info["start"] if job.frame_start is None else job.frame_start
        end = info["end"] if job.frame_end is None else job.frame_end
        step = info["step"] if job.frame_step is None else job.frame_step
        chunks = chunk_frames(start, end, step, per_container)
        total_frames = sum(len(c) for c in chunks)

        log.log(f"Rendering [bold]{total_frames}[/bold] frames ({start}-{end}, step {step})")
        log.log(f"Dispatching [bold]{len(chunks)}[/bold] jobs ({per_container} frame(s) each)")

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

        completed = 0
        for i, frame_results in enumerate(render.starmap(args)):
            log.log(f"Job {i + 1}/{len(chunks)} returned {len(frame_results)} frames")
            for frame_number, ext, image in frame_results:
                completed += 1
                if job.video_only:
                    log.log(f"[{completed}/{total_frames}] Frame {frame_number} rendered")
                    continue
                frame_path = output_directory / f"frame_{frame_number:05d}{ext}"
                frame_path.write_bytes(image)
                log.success(f"[{completed}/{total_frames}] Frame {frame_number} → {frame_path.name}")
                result.frames.append(frame_path)

        if job.video:
            log.log(f"Combining {total_frames} frames into video on server...")
            video_bytes = combine_fn.remote(job_id, fps=job.fps, codec=job.codec, crf=job.crf)
            video_path = output_directory / job.video
            video_path.write_bytes(video_bytes)
            log.success(f"Video saved → {video_path} ({len(video_bytes) / 1024 / 1024:.1f} MB)")
            result.video = video_path

        log.success(f"Done! {completed} frames rendered to {output_directory}")

    if job.ephemeral:
        from farmhand.modal_app import app

        with modal.enable_output(), app.run(environment_name=cfg.environment):
            _run()
    else:
        _run()
    return result
