"""`GitLock`: re-entrant within a thread (also across instances on the same file), exclusive across threads and
across processes."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

from wynd.controller.git import GitLock


def test_lock_creates_its_file_and_is_reentrant(tmp_path: Path) -> None:
    path = tmp_path / ".wynd" / "locks" / "git.lock"
    lock = GitLock(path)
    with lock:
        assert path.is_file()
        with lock:
            with GitLock(path):                  # another instance on the same file: same holder, no deadlock
                pass
    with lock:                                    # fully released and usable again
        pass


def test_threads_are_serialised(tmp_path: Path) -> None:
    path = tmp_path / "git.lock"
    inside = 0
    overlaps = []
    counter_guard = threading.Lock()

    def work() -> None:
        nonlocal inside
        for _ in range(20):
            with GitLock(path):
                with counter_guard:
                    inside += 1
                    overlaps.append(inside)
                time.sleep(0.001)
                with counter_guard:
                    inside -= 1

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(overlaps) == 80
    assert max(overlaps) == 1


def test_waits_for_another_thread(tmp_path: Path) -> None:
    lock = GitLock(tmp_path / "git.lock")
    acquired = threading.Event()

    def take() -> None:
        with lock:
            acquired.set()

    with lock:
        t = threading.Thread(target=take)
        t.start()
        assert not acquired.wait(0.2)
    assert acquired.wait(5)
    t.join()


def test_excludes_another_process(tmp_path: Path) -> None:
    path = tmp_path / "git.lock"
    held, release = tmp_path / "held", tmp_path / "release"
    script = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from wynd.controller.git import GitLock
        with GitLock(Path({str(path)!r})):
            Path({str(held)!r}).touch()
            deadline = time.monotonic() + 30
            while not Path({str(release)!r}).exists() and time.monotonic() < deadline:
                time.sleep(0.01)
    """)
    child = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 30
        while not held.exists():
            assert time.monotonic() < deadline and child.poll() is None, "the child never took the lock"
            time.sleep(0.01)
        acquired = threading.Event()

        def take() -> None:
            with GitLock(path):
                acquired.set()

        t = threading.Thread(target=take)
        t.start()
        assert not acquired.wait(0.3), "the lock was taken while another process held it"
        release.touch()
        assert acquired.wait(30)
        t.join()
    finally:
        release.touch()
        child.wait(30)
    assert child.returncode == 0
