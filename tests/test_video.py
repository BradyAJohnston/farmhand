import shutil

import pytest

from farmhand.video import combine_frames, find_frames


def _png(width: int = 2, height: int = 2) -> bytes:
    """A minimal RGB PNG, built by hand so the tests need no imaging library."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PNG = _png()


def test_find_frames_sorted_and_filtered(tmp_path):
    for name in ("frame_00010.png", "frame_00002.png", "frame_00001.txt", "other.png"):
        (tmp_path / name).write_bytes(PNG)
    assert [p.name for p in find_frames(tmp_path)] == ["frame_00002.png", "frame_00010.png"]


def test_combine_missing_frames_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        combine_frames(tmp_path, tmp_path / "out.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_combine_frames_produces_video_and_cleans_up(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    for i in (1, 2, 3):
        (frames / f"frame_{i:05d}.png").write_bytes(PNG)
    out = combine_frames(frames, tmp_path / "out" / "video.mp4", fps=1, codec="libx264", crf=30)
    assert out.exists() and out.stat().st_size > 0
    assert not (frames / ".ffmpeg_seq").exists()
