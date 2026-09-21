"""Shared logging with elapsed time from start."""

import time

from rich.console import Console

console = Console()
_start_time: float | None = None


def init():
    global _start_time
    _start_time = time.monotonic()


def _elapsed() -> str:
    if _start_time is None:
        return "00:00"
    s = time.monotonic() - _start_time
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def log(msg: str, style: str = "dim"):
    console.print(f"[{style}]{_elapsed()}[/{style}]  {msg}")


def success(msg: str):
    console.print(f"[dim]{_elapsed()}[/dim]  [green]{msg}[/green]")


def warn(msg: str):
    console.print(f"[dim]{_elapsed()}[/dim]  [yellow]{msg}[/yellow]")


def error(msg: str):
    console.print(f"[dim]{_elapsed()}[/dim]  [red bold]{msg}[/red bold]")
