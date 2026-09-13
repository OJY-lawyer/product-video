"""Non-blocking process locks on Windows and POSIX, held by an open file."""
from contextlib import contextmanager
import os
from pathlib import Path

from .common import VideoError


@contextmanager
def file_lock(path, message):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never truncate or unlink a lock file: another process may have it open.
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise VideoError(message) from None
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise VideoError(message) from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
