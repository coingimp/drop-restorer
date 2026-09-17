"""Process-level ownership of the web server, including scheduled starts."""
from contextlib import contextmanager
import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Another process owns this workspace's server on this port."""


@contextmanager
def server_lock(workspace: Path, port: int):
    """An OS lock is released on process death; a stale file never blocks restart."""
    root = workspace / 'var' / 'drop-restorer'
    root.mkdir(parents=True, exist_ok=True)
    with (root / f'web-server-{port}.lock').open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise AlreadyRunning(f'DropRestorer is already running on port {port}.') from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
