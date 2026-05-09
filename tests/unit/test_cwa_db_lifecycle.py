# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for CWA_DB connection lifecycle and fd-leak fixes.

Verifies:
- CWA_DB has close(), __del__, __enter__/__exit__
- close() releases the connection
- Context manager protocol works
- render_template.py notification functions accept cwa_settings kwarg
- get_unresolved_duplicate_count uses cache not full scan
- _log_activity helper exists in web.py
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

_CPS_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')


def _read(rel_path):
    with open(os.path.normpath(os.path.join(_CPS_ROOT, rel_path))) as f:
        return f.read()


# ---------------------------------------------------------------------------
# CWA_DB lifecycle
# ---------------------------------------------------------------------------

class TestCwaDbLifecycle:

    def test_close_method_exists(self):
        src = _read('scripts/cwa_db.py')
        assert 'def close(self)' in src

    def test_del_method_exists(self):
        src = _read('scripts/cwa_db.py')
        assert 'def __del__(self)' in src

    def test_context_manager_enter_exit(self):
        src = _read('scripts/cwa_db.py')
        assert 'def __enter__(self)' in src
        assert 'def __exit__(self,' in src

    def test_close_sets_con_to_none(self):
        src = _read('scripts/cwa_db.py')
        fn_start = src.index('def close(self)')
        snippet = src[fn_start:fn_start + 300]
        assert 'self.con = None' in snippet

    def test_del_calls_close(self):
        src = _read('scripts/cwa_db.py')
        fn_start = src.index('def __del__(self)')
        snippet = src[fn_start:fn_start + 100]
        assert 'close()' in snippet

    def test_exit_calls_close(self):
        src = _read('scripts/cwa_db.py')
        fn_start = src.index('def __exit__(self,')
        snippet = src[fn_start:fn_start + 100]
        assert 'close()' in snippet


# ---------------------------------------------------------------------------
# render_template.py — notification functions accept shared settings
# ---------------------------------------------------------------------------

class TestRenderTemplateSharedDb:

    def test_cwa_update_notification_accepts_settings_param(self):
        src = _read('cps/render_template.py')
        assert 'def cwa_update_notification(cwa_settings=None)' in src

    def test_translations_notification_accepts_settings_param(self):
        src = _read('cps/render_template.py')
        assert 'def translations_missing_notification(cwa_settings=None)' in src

    def test_render_title_template_creates_shared_db(self):
        src = _read('cps/render_template.py')
        fn_start = src.index('def render_title_template(')
        snippet = src[fn_start:fn_start + 3000]
        assert '_render_db' in snippet or ('CWA_DB()' in snippet and 'finally' in snippet)

    def test_render_title_template_closes_db(self):
        src = _read('cps/render_template.py')
        fn_start = src.index('def render_title_template(')
        snippet = src[fn_start:fn_start + 3000]
        assert '.close()' in snippet or 'with CWA_DB()' in snippet

    def test_notification_functions_skip_db_when_settings_provided(self):
        """When cwa_settings is passed, no new CWA_DB() should be opened."""
        src = _read('cps/render_template.py')
        fn_start = src.index('def cwa_update_notification(cwa_settings=None)')
        fn_end = src.index('\ndef ', fn_start + 1)
        fn_body = src[fn_start:fn_end]
        assert '_db = None' in fn_body or 'if cwa_settings is None' in fn_body


# ---------------------------------------------------------------------------
# web.py — _log_activity helper
# ---------------------------------------------------------------------------

class TestWebLogActivityHelper:

    def test_log_activity_helper_exists(self):
        src = _read('cps/web.py')
        assert 'def _log_activity(' in src

    def test_log_activity_uses_context_manager(self):
        src = _read('cps/web.py')
        fn_start = src.index('def _log_activity(')
        fn_end = src.index('\n\n', fn_start)
        fn_body = src[fn_start:fn_end]
        assert 'with CWA_DB()' in fn_body

    def test_no_bare_cwa_db_in_activity_log_paths(self):
        """All activity logging must go through _log_activity, not raw CWA_DB()."""
        src = _read('cps/web.py')
        # The only remaining `CWA_DB()` calls should be the helper def itself
        # and `with CWA_DB()` context-managed calls
        lines = src.splitlines()
        violations = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if 'cwa_db = CWA_DB()' in stripped:
                violations.append(f"Line {i}: {stripped}")
        assert violations == [], (
            f"Bare unclosed CWA_DB() still present:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# duplicates.py — count uses cache not full scan
# ---------------------------------------------------------------------------

class TestDuplicateCountFromCache:

    def test_count_function_does_not_call_find_duplicate_books(self):
        src = _read('cps/duplicates.py')
        fn_start = src.index('def get_unresolved_duplicate_count(')
        fn_end = src.index('\ndef ', fn_start + 1)
        fn_body = src[fn_start:fn_end]
        assert 'find_duplicate_books(' not in fn_body, (
            "get_unresolved_duplicate_count must use cache, not a full library scan"
        )

    def test_count_function_uses_get_duplicate_cache(self):
        src = _read('cps/duplicates.py')
        fn_start = src.index('def get_unresolved_duplicate_count(')
        fn_end = src.index('\ndef ', fn_start + 1)
        fn_body = src[fn_start:fn_end]
        assert 'get_duplicate_cache()' in fn_body

    def test_count_function_uses_context_manager(self):
        src = _read('cps/duplicates.py')
        fn_start = src.index('def get_unresolved_duplicate_count(')
        fn_end = src.index('\ndef ', fn_start + 1)
        fn_body = src[fn_start:fn_end]
        assert 'with CWA_DB()' in fn_body
