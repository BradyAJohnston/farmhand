import pytest

from farmhand.config import FarmhandError
from farmhand.render import chunk_frames


def test_chunk_frames_even_split():
    assert chunk_frames(1, 8, 1, 4) == [[1, 2, 3, 4], [5, 6, 7, 8]]


def test_chunk_frames_uneven_split_and_step():
    assert chunk_frames(1, 10, 3, 2) == [[1, 4], [7, 10]]
    assert chunk_frames(1, 10, 4, 2) == [[1, 5], [9]]


def test_chunk_frames_more_per_container_than_frames():
    assert chunk_frames(1, 3, 1, 10) == [[1, 2, 3]]


def test_chunk_frames_single_frame():
    assert chunk_frames(5, 5, 1, 4) == [[5]]


@pytest.mark.parametrize("args", [(1, 10, 0, 1), (1, 10, -1, 1), (1, 10, 1, 0), (10, 1, 1, 1)])
def test_chunk_frames_rejects_bad_ranges(args):
    with pytest.raises(FarmhandError):
        chunk_frames(*args)
