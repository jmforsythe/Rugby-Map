"""Tests for core.http request retry behaviour."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.http import AntiBotBackoffBudget, AntiBotDetectedError, make_request


def _response(status: int) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b""
    return resp


def test_make_request_retries_anti_bot_with_exponential_backoff() -> None:
    session = MagicMock()
    session.get.side_effect = [_response(202), _response(202), _response(200)]

    with (
        patch("core.http.get_session", return_value=session),
        patch("core.http._curl_fallback", side_effect=lambda url, referer, timeout: _response(202)),
        patch("core.http.time.sleep") as sleep_mock,
    ):
        response = make_request("https://example.com", max_retries=3, delay_seconds=0)

    assert response.status_code == 200
    assert session.get.call_count == 3
    assert sleep_mock.call_args_list == [((5.0,),), ((10.0,),)]


def test_antibot_backoff_budget_caps_total_sleep() -> None:
    budget = AntiBotBackoffBudget(max_seconds=12.0)

    with patch("core.http.time.sleep"):
        budget.sleep(8.0)
        budget.sleep(3.0)

    assert budget.used_seconds == 11.0
    assert budget.remaining == 1.0

    with (
        patch("core.http.time.sleep"),
        pytest.raises(AntiBotDetectedError, match="budget exhausted"),
    ):
        budget.sleep(5.0)


def test_make_request_respects_antibot_backoff_budget() -> None:
    session = MagicMock()
    session.get.return_value = _response(202)
    budget = AntiBotBackoffBudget(max_seconds=6.0)

    with (
        patch("core.http.get_session", return_value=session),
        patch("core.http._curl_fallback", side_effect=lambda url, referer, timeout: _response(202)),
        patch("core.http.time.sleep") as sleep_mock,
        pytest.raises(AntiBotDetectedError, match="budget exhausted"),
    ):
        make_request(
            "https://example.com",
            max_retries=5,
            delay_seconds=0,
            antibot_budget=budget,
        )

    assert budget.used_seconds == 6.0
    assert sleep_mock.call_args_list == [((5.0,),), ((1.0,),)]


def test_make_request_raises_after_anti_bot_retries_exhausted() -> None:
    session = MagicMock()
    session.get.return_value = _response(202)

    with (
        patch("core.http.get_session", return_value=session),
        patch("core.http._curl_fallback", side_effect=lambda url, referer, timeout: _response(403)),
        patch("core.http.time.sleep"),
        pytest.raises(AntiBotDetectedError, match="403 code"),
    ):
        make_request("https://example.com", max_retries=2, delay_seconds=0)
