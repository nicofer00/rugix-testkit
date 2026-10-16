"""File locks for coordinating test processes."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive ``flock`` on *path*, across processes and threads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        yield
