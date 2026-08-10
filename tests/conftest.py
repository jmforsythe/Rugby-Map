"""Configure test environment."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(REPO_ROOT))

_GUARDED_DIRS = ("core", "rugby", "football", "scotland", "tests")

_snapshot: dict[Path, tuple[int, int, bytes]] = {}


def pytest_sessionstart(session: pytest.Session) -> None:
    """Record every source file so a stray test write can be caught and undone.

    Tests that patch a module's ``REPO_ROOT`` have previously leaked writes onto the
    real tree, truncating modules to placeholder stubs.
    """
    for name in _GUARDED_DIRS:
        for path in (REPO_ROOT / name).rglob("*.py"):
            stat = path.stat()
            _snapshot[path] = (stat.st_size, stat.st_mtime_ns, path.read_bytes())


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """Fail the test that modified a source file, restoring it when it was truncated."""
    damaged: list[str] = []
    for path, (size, mtime_ns, original) in list(_snapshot.items()):
        try:
            stat = path.stat()
        except FileNotFoundError:
            path.write_bytes(original)
            damaged.append(f"{path} (deleted, restored)")
            continue
        if (stat.st_size, stat.st_mtime_ns) == (size, mtime_ns):
            continue
        current = path.read_bytes()
        if current == original:
            _snapshot[path] = (stat.st_size, stat.st_mtime_ns, original)
            continue
        # A shrunken file is corruption, not a concurrent edit, so it is safe to undo.
        if len(current) * 2 < len(original):
            path.write_bytes(original)
            damaged.append(f"{path} ({len(original)} → {len(current)} bytes, restored)")
        else:
            _snapshot[path] = (stat.st_size, stat.st_mtime_ns, current)

    if damaged:
        listing = "\n  ".join(damaged)
        pytest.fail(
            f"{item.nodeid} wrote to repository source files:\n  {listing}\n"
            "Tests must write only inside tmp_path.",
            pytrace=False,
        )
