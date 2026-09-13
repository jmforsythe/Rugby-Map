"""Tests for fixture scrape anti-bot backoff wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.http import AntiBotBackoffBudget, AntiBotDetectedError
from rugby import fixtures as fx


def _response(status: int, body: bytes = b"") -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body
    return resp


def test_scrape_fixtures_from_league_recovers_after_anti_bot() -> None:
    """A transient 202 is retried inside make_request rather than aborting."""
    session = MagicMock()
    session.get.side_effect = [_response(202), _response(200, b"<html></html>")]
    budget = AntiBotBackoffBudget(max_seconds=120.0)

    with (
        patch("core.http.get_session", return_value=session),
        patch("core.http._curl_fallback", side_effect=lambda url, referer, timeout: _response(202)),
        patch("core.http.time.sleep"),
    ):
        fixtures, heading = fx.scrape_fixtures_from_league(
            "https://www.englandrugby.com/x#tables", "League", antibot_budget=budget
        )

    assert fixtures == []
    assert heading is None
    assert session.get.call_count == 2
    assert budget.used_seconds == 5.0


def test_scrape_fixtures_from_league_raises_once_budget_is_spent() -> None:
    """Persistent 202s stop at the budget instead of retrying all attempts."""
    session = MagicMock()
    session.get.return_value = _response(202)
    budget = AntiBotBackoffBudget(max_seconds=8.0)

    with (
        patch("core.http.get_session", return_value=session),
        patch("core.http._curl_fallback", side_effect=lambda url, referer, timeout: _response(202)),
        patch("core.http.time.sleep"),
        pytest.raises(AntiBotDetectedError, match="budget exhausted"),
    ):
        fx.scrape_fixtures_from_league(
            "https://www.englandrugby.com/x#tables", "League", antibot_budget=budget
        )

    assert budget.exhausted


def test_antibot_backoff_budget_covers_a_long_lockout() -> None:
    """One league can wait out a 20+ minute lockout within the run budget."""
    waits = [min(5.0 * 2**attempt, 600.0) for attempt in range(fx._ANTIBOT_MAX_ATTEMPTS - 1)]
    assert sum(waits) >= 20 * 60
    assert sum(waits) <= fx._ANTIBOT_BACKOFF_BUDGET_SECONDS
