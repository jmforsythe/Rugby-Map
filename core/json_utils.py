"""Compact JSON writer for dist/ sidecar files (boundaries, territories, match-day data).

These sidecars are fetched by the browser at runtime, never hand-edited, so
there's no reason to pay for indentation whitespace or ``\\uXXXX``-escaped
non-ASCII characters (a handful of team/place names contain characters like
the U+2019 right single quote). ``write_compact_json`` centralises the
``separators=(",", ":"), ensure_ascii=False`` convention so every sidecar
writer gets the smallest correct encoding without repeating it inline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_compact_json(path: Path, data: Any) -> None:
    """Write *data* as compact (no whitespace, UTF-8) JSON to *path*.

    Creates parent directories as needed. Not for human-edited config/cache
    files (those should stay ``indent=2`` for readable diffs) — this is only
    for machine-generated, machine-consumed dist/ output.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
