"""Tests for core.json_utils."""

from __future__ import annotations

import json
from pathlib import Path

from core.json_utils import write_compact_json


def test_write_compact_json_no_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    write_compact_json(path, {"a": 1, "b": [1, 2, 3]})

    text = path.read_text(encoding="utf-8")
    assert text == '{"a":1,"b":[1,2,3]}'
    assert json.loads(text) == {"a": 1, "b": [1, 2, 3]}


def test_write_compact_json_preserves_non_ascii(tmp_path: Path) -> None:
    """Non-ASCII team/place names should round-trip as UTF-8, not \\uXXXX escapes."""
    path = tmp_path / "out.json"
    write_compact_json(path, {"name": "CCS Women\u2019s Rugby"})

    text = path.read_text(encoding="utf-8")
    assert "\\u2019" not in text
    assert "\u2019" in text
    assert json.loads(text) == {"name": "CCS Women\u2019s Rugby"}


def test_write_compact_json_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "out.json"
    write_compact_json(path, {"ok": True})

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
