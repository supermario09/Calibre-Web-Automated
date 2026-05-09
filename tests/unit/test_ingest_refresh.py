# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for ingest_processor.refresh_cwa_session retry logic.

ingest_processor.py is a script (acquires locks, calls sys.exit at module level)
so it cannot be cleanly imported. Tests use two strategies:
  1. Source-level checks — verify the literal code has the right properties
  2. Behavioural tests — inline a minimal recreation of refresh_cwa_session to
     test the retry/timeout contract without touching the heavy module import chain.
"""

import os
import sys
import time
import pytest
import requests
from unittest.mock import MagicMock, patch, call

_CPS_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
_INGEST_PATH = os.path.normpath(os.path.join(_CPS_ROOT, 'scripts/ingest_processor.py'))


def _read_ingest_source():
    with open(_INGEST_PATH) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Source-level guarantees (no import required)
# ---------------------------------------------------------------------------

class TestRefreshSourceGuarantees:
    """Verify the source of refresh_cwa_session has the expected properties."""

    def test_timeout_is_30_not_5_in_all_internal_calls(self):
        """All three internal API calls must use timeout=30, not the old 5s."""
        src = _read_ingest_source()
        methods = [
            'def refresh_cwa_session',
            'def invalidate_duplicate_cache',
            'def schedule_debounced_duplicate_scan',
        ]
        for method in methods:
            start = src.index(method)
            snippet = src[start:start + 1500]
            assert 'timeout=30' in snippet, (
                f"{method} must use timeout=30 — 5s is too short when "
                "Flask is saturated during heavy ingest."
            )
            assert 'timeout=5' not in snippet, (
                f"{method} still has timeout=5 — must be 30"
            )

    def test_retry_loop_present(self):
        src = _read_ingest_source()
        start = src.index('def refresh_cwa_session')
        snippet = src[start:start + 1500]
        assert 'for attempt' in snippet or 'delays' in snippet, (
            "refresh_cwa_session must have a retry loop"
        )

    def test_sleep_between_retries(self):
        src = _read_ingest_source()
        start = src.index('def refresh_cwa_session')
        snippet = src[start:start + 1500]
        assert 'time.sleep' in snippet, (
            "refresh_cwa_session must sleep between retry attempts"
        )

    def test_no_bare_raise(self):
        """Method must never propagate — always warn and continue."""
        src = _read_ingest_source()
        start = src.index('def refresh_cwa_session')
        snippet = src[start:start + 1500]
        # 'raise' must not appear outside an except re-raise pattern
        lines = [l.strip() for l in snippet.splitlines() if l.strip().startswith('raise')]
        assert len(lines) == 0, "refresh_cwa_session must not raise — warn and continue"


# ---------------------------------------------------------------------------
# Behavioural tests — minimal inline recreation of the retry contract
# ---------------------------------------------------------------------------

def _refresh_cwa_session_impl(requests_mod, time_mod, get_url, get_headers):
    """
    Inline recreation of the refresh_cwa_session retry logic.
    Mirrors the actual implementation so behavioural tests stay in sync.
    """
    url = get_url("/cwa-internal/reconnect-db")
    delays = [2, 5, 10]
    for attempt, delay in enumerate(delays, start=1):
        try:
            resp = requests_mod.post(
                url,
                headers=get_headers(),
                timeout=30,
                verify=False,
            )
            if resp.status_code == 200:
                return
            # non-200: warn but don't retry
            return
        except Exception as e:
            if attempt < len(delays):
                time_mod.sleep(delay)
            # on final attempt: warn and continue (no raise)


class TestRefreshRetryBehaviour:
    """Behavioural tests against the inline recreation of the retry logic."""

    def _run(self, post_side_effect):
        mock_requests = MagicMock()
        mock_requests.post.side_effect = post_side_effect
        mock_time = MagicMock()
        _refresh_cwa_session_impl(
            mock_requests, mock_time,
            lambda path: f"http://127.0.0.1:8083{path}",
            lambda: {"X-Forwarded-For": "127.0.0.1"},
        )
        return mock_requests, mock_time

    def test_succeeds_on_first_attempt_no_sleep(self):
        resp = MagicMock(); resp.status_code = 200
        mock_req, mock_time = self._run([resp])
        mock_req.post.assert_called_once()
        mock_time.sleep.assert_not_called()

    def test_timeout_per_call_is_30s(self):
        resp = MagicMock(); resp.status_code = 200
        mock_req, _ = self._run([resp])
        _, kwargs = mock_req.post.call_args
        assert kwargs['timeout'] == 30

    def test_retries_on_read_timeout_succeeds_second_attempt(self):
        resp = MagicMock(); resp.status_code = 200
        mock_req, mock_time = self._run([
            requests.exceptions.ReadTimeout("t/o"),
            resp,
        ])
        assert mock_req.post.call_count == 2
        mock_time.sleep.assert_called_once()

    def test_exhausts_three_attempts_without_raising(self):
        mock_req, mock_time = self._run([
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.ConnectionError("refused"),
        ])
        assert mock_req.post.call_count == 3
        assert mock_time.sleep.call_count == 2  # sleep between attempts 1→2 and 2→3

    def test_non_200_returns_without_retry(self):
        resp = MagicMock(); resp.status_code = 503
        mock_req, mock_time = self._run([resp])
        mock_req.post.assert_called_once()
        mock_time.sleep.assert_not_called()
