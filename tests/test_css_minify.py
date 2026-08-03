"""Tests for rugby.seo's dist/styles.css minifier."""

from __future__ import annotations

from pathlib import Path

from core.config import REPO_ROOT
from rugby.seo import minify_css, minify_styles_css


def test_minify_css_strips_comments() -> None:
    css = "/* a comment */\n.foo { color: red; }\n/* another */\n"
    out = minify_css(css)
    assert "comment" not in out
    assert "another" not in out
    assert out == ".foo{color:red}"


def test_minify_css_collapses_whitespace_between_rules() -> None:
    css = """
    .a {
        color: red;
        margin:  0   auto;
    }

    .b,   .c {
        display: flex;
    }
    """
    out = minify_css(css)
    assert "\n" not in out
    assert ".a{color:red;margin:0 auto}" in out
    assert ".b,.c{display:flex}" in out


def test_minify_css_preserves_space_inside_quoted_content_value() -> None:
    """content: "\\25B6 " has a significant trailing space inside the string --
    the minifier must not touch whitespace inside quoted values."""
    css = '.x::before { content: "\\25B6 "; }'
    out = minify_css(css)
    assert '"\\25B6 "' in out


def test_minify_css_drops_trailing_semicolon_before_brace() -> None:
    css = ".a { color: red; }"
    out = minify_css(css)
    assert out == ".a{color:red}"


def test_minify_css_is_valid_shorter_output_for_real_stylesheet() -> None:
    """Sanity check against the real, hand-maintained dist/styles.css."""
    real_path = REPO_ROOT / "dist" / "styles.css"
    if not real_path.is_file():
        return  # dist/ not built in this environment; skip.
    original = real_path.read_text(encoding="utf-8")
    minified = minify_css(original)
    assert len(minified) < len(original)
    # Every declaration block must stay balanced.
    assert minified.count("{") == minified.count("}")


def test_minify_styles_css_writes_smaller_file_in_place(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    styles_path = dist_dir / "styles.css"
    styles_path.write_text(
        "/* header */\n.a {\n  color: red;\n}\n",
        encoding="utf-8",
    )

    minify_styles_css(dist_dir)

    minified = styles_path.read_text(encoding="utf-8")
    assert minified == ".a{color:red}"


def test_minify_styles_css_noop_when_file_missing(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    # Should not raise even though styles.css doesn't exist.
    minify_styles_css(dist_dir)
