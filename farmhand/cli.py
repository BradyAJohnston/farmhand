"""CLI entrypoint for farmhand."""

import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import click

from farmhand import config, log
from farmhand.config import Config, FarmhandError, check_job_id

# `modal` is imported inside each command, after the config has been loaded and
# exported to the environment, because Modal reads its profile at import time.

CONFIG_HELP = "Config file (default: nearest farmhand.yml, or pyproject.toml with [tool.farmhand])."


def _job_id_arg(ctx, param, value):
    return check_job_id(value)


@click.group()
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), default=None, help=CONFIG_HELP)
@click.option("--environment", default=None, help="Modal environment (default: Modal profile default).")
@click.option("--app-name", default=None, help="Modal app name (default: farmhand).")
@click.pass_context
def main(ctx, config_path, environment, app_name):
    """Distributed Blender rendering on Modal.

    These options are global and go before the subcommand:
    farmhand --environment prod render scene.blend
    """
    ctx.obj = {"config_path": config_path, "environment": environment, "app_name": app_name}


def _load(ctx, **overrides) -> Config:
    opts = ctx.obj
    cfg = config.load(opts["config_path"], environment=opts["environment"], app_name=opts["app_name"], **overrides)
    config.apply_env(cfg)
    return cfg


def _remote(fn_name: str, cfg: Config):
    from farmhand.render import _lookup

    return _lookup(fn_name, cfg)


def _modal_cli(cfg: Config, *args: str, capture: bool = False) -> str:
    """Run Modal's own CLI in a fresh process so it sees credentials written moments ago."""
    cmd = [sys.executable, "-m", "modal", *args]
    if cfg.profile:
        cmd += ["--profile", cfg.profile]
    result = subprocess.run(cmd, check=True, capture_output=capture, text=True)
    return result.stdout if capture else ""


def _authenticated() -> bool:
    import modal.config

    return bool(modal.config.config.get("token_id"))


@main.command("config")
@click.pass_context
def config_cmd(ctx):
    """Show the resolved configuration and Modal auth status."""
    cfg = _load(ctx)
    log.console.print(f"[dim]source:[/dim] {cfg.source or '(built-in defaults)'}")
    for f in fields(cfg):
        if f.name != "source":
            log.console.print(f"  [bold]{f.name}[/bold] = {getattr(cfg, f.name)!r}")
    import modal.config

    status = "[green]token found[/green]" if _authenticated() else "[yellow]no token, run `farmhand setup`[/yellow]"
    log.console.print(f"[dim]modal:[/dim] profile [bold]{modal.config._profile}[/bold], {status}")


@main.command("setup")
@click.pass_context
def setup_cmd(ctx):
    """Log in to Modal (opens a browser) and create the configured environment if it is missing."""
    cfg = _load(ctx)
    log.init()

    if _authenticated():
        log.success("Modal credentials found.")
    else:
        log.log("No Modal credentials found. Opening a browser to create a token...")
        _modal_cli(cfg, "token", "new")
        log.success("Token saved.")

    if cfg.environment:
        envs = json.loads(_modal_cli(cfg, "environment", "list", "--json", capture=True))
        if cfg.environment in {e["name"] for e in envs}:
            log.success(f"Environment [bold]{cfg.environment}[/bold] exists.")
        else:
            log.log(f"Creating environment [bold]{cfg.environment}[/bold]...")
            _modal_cli(cfg, "environment", "create", cfg.environment)
            log.success(f"Environment [bold]{cfg.environment}[/bold] created.")
    else:
        log.log("No environment configured; using the Modal profile default.")

    log.success("Ready. Next: [bold]farmhand deploy[/bold]")


@main.command("deploy")
@click.option("--gpu", default=None, help="GPU type, or comma-separated fallback list (see docs/gpus.md).")
@click.option("--max-containers", type=int, default=None, help="Max concurrent render containers.")
@click.option("--timeout", type=int, default=None, help="Per-container timeout in seconds.")
@click.option("--bpy-version", default=None, help="bpy version to install; match the Blender that saved your files.")
@click.option("--volume", default=None, help="Modal volume name (default: <app_name>-data).")
@click.pass_context
def deploy_cmd(ctx, gpu, max_containers, timeout, bpy_version, volume):
    """Build the render image and deploy the app. Rerun after changing gpu, bpy_version, timeout, etc."""
    import modal

    cfg = _load(ctx, gpu=gpu, max_containers=max_containers, timeout=timeout, bpy_version=bpy_version, volume=volume)
    config.set_active(cfg)
    from farmhand.modal_app import app

    log.init()
    env = cfg.environment or "(modal default)"
    log.log(f"Deploying [bold]{cfg.app_name}[/bold] to environment [bold]{env}[/bold] (gpu={cfg.gpu!r})...")
    with modal.enable_output():
        app.deploy(environment_name=cfg.environment)
    log.success("Deployed! The app will stay running and accept render jobs.")


@main.command("render")
@click.argument("blend_file", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--output-dir", default=None, help="Frames go to <output-dir>/<job_id>/ (default: render).")
# Frame range
@click.option("--frame-start", type=int, default=None, help="Start frame (default: from blend file).")
@click.option("--frame-end", type=int, default=None, help="End frame (default: from blend file).")
@click.option("--frame-step", type=int, default=None, help="Frame step (default: from blend file).")
# Render settings
@click.option("--resolution-x", type=int, default=None, help="Override render width.")
@click.option("--resolution-y", type=int, default=None, help="Override render height.")
@click.option("--resolution-percentage", type=int, default=None, help="Override resolution scale %.")
@click.option("--samples", type=int, default=None, help="Override Cycles sample count.")
@click.option("--engine", default=None, help="Render engine, e.g. CYCLES or BLENDER_EEVEE (default: from blend file).")
# Video output
@click.option("--video", default=None, metavar="FILENAME", help="Also stitch frames into a video on the server.")
@click.option("--fps", type=int, default=None, help="Video framerate.")
@click.option("--codec", default=None, help="Video codec.")
@click.option("--crf", type=int, default=None, help="Video quality, lower=better.")
@click.option(
    "--video-only",
    is_flag=True,
    help="Do not save frames locally, only the video (frames stay on the volume).",
)
# Infrastructure
@click.option("--frames-per-container", type=int, default=None, help="Frames each container renders in sequence.")
@click.option(
    "--ephemeral",
    is_flag=True,
    help="Run without a deployed app; the image is built on first use and cached.",
)
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
    from farmhand.render import RenderJob, submit_render

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
    submit_render(job, cfg)


@main.command("jobs")
@click.pass_context
def jobs_cmd(ctx):
    """List render jobs still stored on the Modal volume."""
    from rich.table import Table

    cfg = _load(ctx)
    log.init()
    log.log("Fetching jobs from volume...")
    jobs = _remote("list_jobs", cfg).remote()

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
        table.add_row(j["job_id"], str(j["frame_count"]), "✓" if j["has_blend"] else "✗", f"{size_mb:.1f} MB")
    log.console.print(table)


@main.command("download")
@click.argument("job_id", callback=_job_id_arg)
@click.option("-o", "--output-dir", default=None, help="Frames go to <output-dir>/<job_id>/ (default: render).")
@click.pass_context
def download_cmd(ctx, job_id, output_dir):
    """Download the frames of JOB_ID from the Modal volume."""
    cfg = _load(ctx)
    log.init()
    output_directory = Path(output_dir or cfg.output_dir).expanduser().resolve() / job_id
    output_directory.mkdir(parents=True, exist_ok=True)

    log.log(f"Downloading frames for job [bold]{job_id}[/bold]...")
    files = _remote("download_job_frames", cfg).remote(job_id)
    for filename, data in files:
        (output_directory / filename).write_bytes(data)
    log.success(f"Downloaded {len(files)} frames to {output_directory}")


@main.command("combine")
@click.argument("target")
@click.option(
    "-o",
    "--output",
    default=None,
    help="Output video (default: <dir>.mp4 next to the directory, or <job_id>.mp4).",
)
@click.option("--fps", type=int, default=None, help="Video framerate.")
@click.option("--codec", default=None, help="Video codec.")
@click.option("--crf", type=int, default=None, help="Video quality, lower=better.")
@click.pass_context
def combine_cmd(ctx, target, output, fps, codec, crf):
    """Combine frames into a video. TARGET is a local directory of frames, or a job ID still on the volume."""
    cfg = _load(ctx)
    log.init()
    fps = cfg.fps if fps is None else fps
    codec = cfg.codec if codec is None else codec
    crf = cfg.crf if crf is None else crf
    local_dir = Path(target).expanduser()

    if local_dir.is_dir():
        from farmhand.video import combine_frames, find_frames

        local_dir = local_dir.resolve()
        out_path = Path(output) if output else local_dir.parent / f"{local_dir.name}.mp4"
        log.log(f"Combining {len(find_frames(local_dir))} frames from {local_dir} with local ffmpeg...")
        combine_frames(local_dir, out_path, fps=fps, codec=codec, crf=crf)
    else:
        job_id = check_job_id(target)
        out_path = Path(output) if output else Path(f"{job_id}.mp4")
        log.log(f"Combining frames for job [bold]{job_id}[/bold] on the server...")
        video_bytes = _remote("combine", cfg).remote(job_id, fps=fps, codec=codec, crf=crf)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(video_bytes)

    log.success(f"Video saved → {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")


@main.command("cleanup")
@click.argument("job_id", callback=_job_id_arg)
@click.confirmation_option(prompt="Delete this job's blend file and frames from the volume?")
@click.pass_context
def cleanup_cmd(ctx, job_id):
    """Delete JOB_ID's blend file and frames from the Modal volume."""
    cfg = _load(ctx)
    log.init()
    log.log(f"Deleting job [bold]{job_id}[/bold]...")
    count = _remote("delete_job", cfg).remote(job_id)
    log.success(f"Deleted {count} files for job {job_id}")


def entry():
    try:
        main()
    except FarmhandError as e:
        log.error(str(e))
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        log.error(f"`{' '.join(e.cmd)}` failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        from modal.exception import AuthError, NotFoundError

        if isinstance(e, AuthError):
            log.error("Not logged in to Modal. Run [bold]farmhand setup[/bold] to create a token.")
        elif isinstance(e, NotFoundError):
            log.error(
                f"{e}\nThe app is not deployed here. Run [bold]farmhand deploy[/bold], or render with --ephemeral."
            )
        else:
            raise
        sys.exit(1)


if __name__ == "__main__":
    entry()
