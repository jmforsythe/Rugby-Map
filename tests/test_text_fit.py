"""Tests for single-line text fitting on SVG graphics.

Uses the estimated-width fallback (no font download) so results are deterministic.
"""

import pytest

from rugby import text_fit
from rugby.text_fit import ELLIPSIS, MIN_SCALE, fit_line, fit_pair, text_width


@pytest.fixture(autouse=True)
def _estimated_widths(monkeypatch):
    monkeypatch.setattr(text_fit, "_font", lambda kind, weight: None)


def _width(text, size):
    return text_width(text, kind="heading", weight=600, size=size)


def test_fitting_text_is_untouched():
    assert fit_line("Bath", kind="heading", weight=600, size=32, max_width=500) == ("Bath", 32)


def test_slightly_long_text_shrinks():
    text = "A" * 20
    max_width = _width(text, 32) * 0.9
    fitted, size = fit_line(text, kind="heading", weight=600, size=32, max_width=max_width)
    assert fitted == text
    assert size == pytest.approx(32 * 0.9)


def test_very_long_text_truncates_at_minimum_size():
    text = "Letchworth Garden City Rugby Union Football Club"
    max_width = _width(text, 32) * 0.5
    fitted, size = fit_line(text, kind="heading", weight=600, size=32, max_width=max_width)
    assert size == pytest.approx(32 * MIN_SCALE)
    assert fitted.endswith(ELLIPSIS)
    assert _width(fitted, size) <= max_width


def test_pair_truncates_the_longer_name_and_keeps_both():
    first, second = "Letchworth Garden City", "Bath"
    max_width = _width(f"{first} v {second}", 32) * 0.6
    fitted, size = fit_pair(first, second, kind="heading", weight=600, size=32, max_width=max_width)
    assert fitted.endswith(" v Bath")
    assert fitted.startswith("Letchworth")
    assert ELLIPSIS in fitted
    assert _width(fitted, size) <= max_width
