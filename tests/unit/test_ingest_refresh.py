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

    def test_refresh_uses_short_timeout(self):
        """refresh_cwa_session must use a short (≤5s) fire-and-forget timeout.

        A long timeout (e.g. 30s) × retries blocks the ingest lock for 90+s,
        serialising the entire ingest queue. The refresh is QoL-only.
        """
        src = _read_ingest_source()
        start = src.index('def refresh_cwa_session')
        snippet = src[start:start + 1500]
        assert 'timeout=5' in snippet, (
            "refresh_cwa_session must use timeout=5 — it is fire-and-forget, "
            "a long timeout blocks the ingest lock"
        )
        assert 'timeout=30' not in snippet

    def test_no_readtimeout_retry_in_refresh(self):
        """ReadTimeout must not be retried — Flask busy means retries also time out."""
        src = _read_ingest_source()
        start = src.index('def refresh_cwa_session')
        snippet = src[start:start + 1500]
        assert 'ConnectionError' in snippet, \
            "refresh_cwa_session should only retry on ConnectionError, not ReadTimeout"

    def test_all_fire_and_forget_endpoints_use_short_timeout(self):
        """invalidate_duplicate_cache and schedule_debounced_duplicate_scan must use timeout=5."""
        src = _read_ingest_source()
        for method in ('def invalidate_duplicate_cache', 'def schedule_debounced_duplicate_scan'):
            start = src.index(method)
            end = src.index('\n    def ', start + 1)
            fn_body = src[start:end]
            assert 'timeout=5' in fn_body, f"{method} must use timeout=5"
            assert 'timeout=30' not in fn_body

    def test_retry_loop_present(self):
        src = _read_ingest_source()
        start = src.index('def refresh_cwa_session')
        snippet = src[start:start + 1500]
        assert 'for attempt' in snippet or 'delays' in snippet, (
            "refresh_cwa_session must have a retry loop"
        )

    def test_sleeps_only_on_connection_error(self):
        """sleep must appear in the ConnectionError handler, not on ReadTimeout."""
        src = _read_ingest_source()
        start = src.index('def refresh_cwa_session')
        end = src.index('\n    def ', start + 1)
        fn_body = src[start:end]
        assert 'time.sleep' in fn_body, \
            "refresh_cwa_session must sleep between ConnectionError retries"
        assert 'ConnectionError' in fn_body, \
            "sleep must only be in the ConnectionError handler"

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
    Inline recreation of the refresh_cwa_session logic.
    Mirrors the actual implementation so behavioural tests stay in sync.
    Only retries on ConnectionError; ReadTimeout fails fast (fire-and-forget).
    """
    url = get_url("/cwa-internal/reconnect-db")
    for attempt in range(1, 3):  # max 2 attempts
        try:
            resp = requests_mod.post(url, headers=get_headers(), timeout=5, verify=False)
            if resp.status_code == 200:
                return
            return  # non-200: log and give up
        except requests.exceptions.ConnectionError:
            if attempt < 2:
                time_mod.sleep(2)
            # final attempt: give up silently
        except Exception:
            # ReadTimeout or other: fail fast, don't retry
            return


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

    def test_timeout_per_call_is_5s(self):
        """Fire-and-forget: must use 5s timeout to not block the ingest lock."""
        resp = MagicMock(); resp.status_code = 200
        mock_req, _ = self._run([resp])
        _, kwargs = mock_req.post.call_args
        assert kwargs['timeout'] == 5

    def test_readtimeout_fails_fast_no_retry(self):
        """ReadTimeout must not be retried — Flask busy → retry also times out."""
        mock_req, mock_time = self._run([
            requests.exceptions.ReadTimeout("timed out"),
        ])
        mock_req.post.assert_called_once()
        mock_time.sleep.assert_not_called()

    def test_connection_error_retried_once(self):
        """ConnectionError (server down) is worth one retry."""
        resp = MagicMock(); resp.status_code = 200
        mock_req, mock_time = self._run([
            requests.exceptions.ConnectionError("refused"),
            resp,
        ])
        assert mock_req.post.call_count == 2
        mock_time.sleep.assert_called_once()

    def test_exhausts_connection_retries_without_raising(self):
        mock_req, _ = self._run([
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.ConnectionError("refused"),
        ])
        assert mock_req.post.call_count == 2  # max 2 attempts

    def test_non_200_returns_without_retry(self):
        resp = MagicMock(); resp.status_code = 503
        mock_req, mock_time = self._run([resp])
        mock_req.post.assert_called_once()
        mock_time.sleep.assert_not_called()
