"""Combine rendered frames into a video with ffmpeg. Stdlib only: runs locally and in containers."""

import shutil
import subprocess
import tempfile
from pathlib import Path

FRAME_EXTENSIONS = (".png", ".jpg", ".jpeg", ".exr", ".tiff", ".tif", ".bmp", ".webp")


def find_frames(directory: str | Path) -> list[Path]:
    """Sorted frame_*.<image ext> files in `directory`."""
    directory = Path(directory)
    return sorted(p for p in directory.glob("frame_*.*") if p.suffix.lower() in FRAME_EXTENSIONS)


def combine_frames(
    directory: str | Path,
    output: str | Path,
    fps: int = 30,
    codec: str = "libx265",
    crf: int = 20,
) -> Path:
    """Encode the frame_* images in `directory` into `output`. Returns the output path.

    Frames are listed for ffmpeg's concat demuxer, so numbering gaps are fine and
    nothing is written next to the frames.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    frames = find_frames(directory)
    if not frames:
        raise FileNotFoundError(f"No frame_* image files found in {directory}")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "frames.txt"
        lines = ["ffconcat version 1.0"]
        for frame in frames:
            escaped = str(frame.resolve()).replace("'", r"'\''")
            lines += [f"file '{escaped}'", f"duration {1 / fps}"]
        listing.write_text("\n".join(lines) + "\n")

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-fps_mode", "cfr", "-r", str(fps),
            "-vcodec", codec, "-crf", str(crf), "-pix_fmt", "yuv420p",
            str(output),
        ]  # fmt: skip
        subprocess.run(cmd, check=True)
    return output
