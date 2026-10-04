"""Tests for gated Google Analytics embed."""

from __future__ import annotations

import os
from unittest.mock import patch

from core.config import get_google_analytics_script


def test_google_analytics_script_empty_without_tracking_id() -> None:
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("GA_TRACKING_ID", None)
        assert get_google_analytics_script() == ""


def test_google_analytics_script_gates_production_hostname() -> None:
    with patch.dict(os.environ, {"GA_TRACKING_ID": "G-TEST12345"}):
        script = get_google_analytics_script()
    assert "rugbyunionmap.uk" in script
    assert "host !== 'rugbyunionmap.uk'" in script


def test_google_analytics_script_skips_common_bot_user_agents() -> None:
    with patch.dict(os.environ, {"GA_TRACKING_ID": "G-TEST12345"}):
        script = get_google_analytics_script()
    assert "headless" in script
    assert "crawler" in script


def test_google_analytics_script_defers_load_until_engagement() -> None:
    with patch.dict(os.environ, {"GA_TRACKING_ID": "G-TEST12345"}):
        script = get_google_analytics_script()
    assert "setTimeout(loadGa, 5000)" in script
    assert "'scroll'" in script
    assert "'click'" in script
