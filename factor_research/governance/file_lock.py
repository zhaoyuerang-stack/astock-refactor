"""Small cross-process lock for canonical file read-modify-write transactions."""
from __future__ import annotations

import fcntl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_PROCESS_LOCK = threading.RLock()


@contextmanager
def exclusive_file_lock(target: Path) -> Iterator[None]:
    """Serialize threads and processes that mutate ``target``."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    with _PROCESS_LOCK:
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
