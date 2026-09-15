"""Bound expensive subprocesses and reap their descendants, including Chrome."""

import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
import psutil
from denzo.runtime_limits import check_cancelled


def descendants(pid):
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def kill_descendants(processes):
    for process in reversed(list(processes)):
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass


@contextmanager
def browser_slot(timeout=30):
    """One Chrome audit per shared SQLite installation, including public audits."""
    import fcntl
    from denzo.db import DB_PATH

    folder = Path(DB_PATH).resolve().parent / ".locks"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "lighthouse.lock").open("a") as handle:
        deadline = time.monotonic() + timeout
        while True:
            check_cancelled()
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another browser audit is still running")
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_bounded(cmd, timeout=180, max_output_bytes=8 * 1024 * 1024):
    """Keep output on disk and reap known children on success, error or timeout."""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(cmd, stdout=output, stderr=errors)
        children = {}
        deadline = time.monotonic() + timeout
        try:
            while True:
                check_cancelled()
                for child in descendants(process.pid):
                    children[(child.pid, child.create_time())] = child
                if (
                    os.fstat(output.fileno()).st_size
                    + os.fstat(errors.fileno()).st_size
                    > max_output_bytes
                ):
                    raise ValueError("Subprocess output limit reached")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                try:
                    process.wait(timeout=min(0.1, remaining))
                    if (
                        os.fstat(output.fileno()).st_size
                        + os.fstat(errors.fileno()).st_size
                        > max_output_bytes
                    ):
                        raise ValueError("Subprocess output limit reached")
                    break
                except subprocess.TimeoutExpired:
                    pass
            output.seek(0)
            errors.seek(0)
            return subprocess.CompletedProcess(
                cmd,
                process.returncode,
                output.read(max_output_bytes).decode("utf-8", "replace"),
                errors.read(max_output_bytes).decode("utf-8", "replace"),
            )
        finally:
            kill_descendants([*children.values(), *descendants(process.pid)])
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
