"""CLI entrypoint for farmhand."""

from pathlib import Path

import click
import modal

from farmhand import config, log
from farmhand.render import RenderJob, _lookup, submit_render


@click.group()
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Config file (default: nearest farmhand.yml or pyproject.toml [tool.farmhand]).")
@click.option("--environment", default=None, help="Modal environment (default: Modal profile default).")
@click.option("--app-name", default=None, help="Modal app name.")
@click.pass_context
def main(ctx, config_path, environment, app_name):
    """Distributed Blender rendering on Modal."""
    ctx.obj = {"config_path": config_path, "environment": environment, "app_name": app_name}


def _load(ctx, **overrides) -> config.Config:
    opts = ctx.obj
    return config.load(opts["config_path"], environment=opts["environment"], app_name=opts["app_name"], **overrides)


@main.command("config")
@click.pass_context
def config_cmd(ctx):
    """Show the resolved configuration."""
    from dataclasses import fields

    cfg = _load(ctx)
    log.console.print(f"[dim]source:[/dim] {cfg.source or '(built-in defaults)'}")
    for f in fields(cfg):
        if f.name != "source":
            log.console.print(f"  [bold]{f.name}[/bold] = {getattr(cfg, f.name)!r}")


@main.command("deploy")
@click.option("--gpu", default=None, help="GPU type for render containers.")
@click.option("--max-containers", type=int, default=None, help="Max concurrent render containers.")
@click.option("--timeout", type=int, default=None, help="Render container timeout (seconds).")
@click.option("--bpy-version", default=None, help="bpy package version to install.")
@click.option("--volume", default=None, help="Modal volume name.")
@click.pass_context
def deploy_cmd(ctx, gpu, max_containers, timeout, bpy_version, volume):
    """Deploy the farmhand app to Modal (run this once)."""
    cfg = _load(ctx, gpu=gpu, max_containers=max_containers, timeout=timeout, bpy_version=bpy_version, volume=volume)
    config.set_active(cfg)
    from farmhand.modal_app import app

    log.init()
    env = cfg.environment or "(modal default)"
    log.log(f"Deploying [bold]{cfg.app_name}[/bold] to environment [bold]{env}[/bold] (gpu={cfg.gpu})...")
    modal.enable_output()
    app.deploy(environment_name=cfg.environment)
    log.success("Deployed! The app will stay running and accept render jobs.")


@main.command("render")
@click.argument("blend_file", type=click.Path(exists=True))
@click.option("-o", "--output-dir", default=None, help="Output directory.")
# Frame range
@click.option("--frame-start", type=int, default=None, help="Start frame (default: from blend file).")
@click.option("--frame-end", type=int, default=None, help="End frame (default: from blend file).")
@click.option("--frame-step", type=int, default=None, help="Frame step (default: from blend file).")
# Render settings
@click.option("--resolution-x", type=int, default=None, help="Override render width.")
@click.option("--resolution-y", type=int, default=None, help="Override render height.")
@click.option("--resolution-percentage", type=int, default=None, help="Override resolution scale %.")
@click.option("--samples", type=int, default=None, help="Override Cycles sample count.")
@click.option("--engine", default=None, help="Render engine (default: from blend file).")
# Video output
@click.option("--video", default=None, metavar="FILENAME", help="Combine frames into video on server (e.g. output.mp4).")
@click.option("--fps", type=int, default=None, help="Video framerate.")
@click.option("--codec", default=None, help="Video codec.")
@click.option("--crf", type=int, default=None, help="Video quality, lower=better.")
@click.option("--video-only", is_flag=True, help="Only return the video, skip individual frames.")
# Infrastructure
@click.option("--frames-per-container", type=int, default=None, help="Frames each container renders.")
@click.option("--ephemeral", is_flag=True, help="Run as ephemeral app (builds fresh image, no deploy needed).")
@click.pass_context
def render_cmd(
    ctx,
    blend_file,
    output_dir,
    frame_start,
    frame_end,
    frame_step,
    resolution_x,
    resolution_y,
    resolution_percentage,
    samples,
    engine,
    video,
    fps,
    codec,
    crf,
    video_only,
    frames_per_container,
    ephemeral,
):
    """Render BLEND_FILE frames in parallel on Modal GPU containers."""
    cfg = _load(ctx)
    job = RenderJob(
        blend_file=blend_file,
        output_dir=output_dir,
        frame_start=frame_start,
        frame_end=frame_end,
        frame_step=frame_step,
        resolution_x=resolution_x,
        resolution_y=resolution_y,
        resolution_percentage=resolution_percentage,
        samples=samples,
        engine=engine,
        frames_per_container=frames_per_container,
        ephemeral=ephemeral,
        video=video,
        fps=fps,
        codec=codec,
        crf=crf,
        video_only=video_only,
    )

    for _ in submit_render(job, cfg):
        pass


@main.command("jobs")
@click.pass_context
def jobs_cmd(ctx):
    """List render jobs stored on the Modal volume."""
    from rich.table import Table

    cfg = _load(ctx)
    log.init()
    log.log("Fetching jobs from volume...")

    list_jobs = _lookup("list_jobs", cfg)
    jobs = list_jobs.remote()

    if not jobs:
        log.warn("No jobs found on volume.")
        return

    table = Table(title="Render Jobs")
    table.add_column("Job ID", style="bold")
    table.add_column("Frames", justify="right")
    table.add_column("Blend", justify="center")
    table.add_column("Total Size", justify="right")

    for j in jobs:
        size_mb = j["total_size"] / 1024 / 1024
        table.add_row(
            j["job_id"],
            str(j["frame_count"]),
            "✓" if j["has_blend"] else "✗",
            f"{size_mb:.1f} MB",
        )

    log.console.print(table)


@main.command("download")
@click.argument("job_id")
@click.option("-o", "--output-dir", default=None, help="Output directory.")
@click.pass_context
def download_cmd(ctx, job_id, output_dir):
    """Download rendered frames for JOB_ID from the Modal volume."""
    cfg = _load(ctx)
    log.init()
    output_directory = Path(output_dir or cfg.output_dir).expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    log.log(f"Downloading frames for job [bold]{job_id}[/bold]...")

    download_job_frames = _lookup("download_job_frames", cfg)
    files = download_job_frames.remote(job_id)

    for filename, data in files:
        frame_path = output_directory / filename
        frame_path.write_bytes(data)

    log.success(f"Downloaded {len(files)} frames to {output_directory}")


@main.command("cleanup")
@click.argument("job_id")
@click.confirmation_option(prompt="Are you sure you want to delete this job?")
@click.pass_context
def cleanup_cmd(ctx, job_id):
    """Delete a render job and its files from the Modal volume."""
    cfg = _load(ctx)
    log.init()
    log.log(f"Deleting job [bold]{job_id}[/bold]...")

    delete_job = _lookup("delete_job", cfg)
    count = delete_job.remote(job_id)

    log.success(f"Deleted {count} files for job {job_id}")


if __name__ == "__main__":
    main()
