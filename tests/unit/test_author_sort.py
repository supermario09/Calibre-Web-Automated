# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for the author sort lookup fix in cps/db.py.

The original code used `break` when an author_sort value wasn't found in the
Authors table, abandoning all remaining authors in the sort loop. This caused:
  - Noisy ERROR log spam for books with magazine-style author_sort values
    (e.g. "The New Yorker   Apr 04, 2005")
  - Incorrect ordering when one of multiple authors couldn't be matched

Fix: use `continue` instead of `break`, and downgrade to `warning`.

These tests verify the source-level change (no full CPS import needed).
"""

import os
import re
import pytest

_CPS_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
_DB_PATH = os.path.normpath(os.path.join(_CPS_ROOT, 'cps', 'db.py'))


def _read_db_source():
    with open(_DB_PATH) as f:
        return f.read()


def _get_author_sort_snippet(src):
    """Extract the relevant section of the author sort loop."""
    start = src.index('not found to display name in right order') if 'not found to display name in right order' in src else None
    if start:
        return None  # old code still present — test will fail

    # Find the new warning message
    marker = 'not in Authors table; skipping sort for this entry'
    if marker not in src:
        return None
    start = src.rindex('results = self.session.query(Authors)', 0, src.index(marker))
    return src[start:start + 400]


class TestAuthorSortLoopFix:

    def test_error_log_replaced_with_warning(self):
        """The error-level log for missing author sort must be gone."""
        src = _read_db_source()
        assert 'not found to display name in right order' not in src, (
            "Old ERROR log message still present — should have been replaced with warning"
        )

    def test_debug_log_present(self):
        """A debug-level log must exist for missing author sort values."""
        src = _read_db_source()
        assert 'not in Authors table; skipping sort for this entry' in src, (
            "Expected debug log for unmatched author sort not found"
        )

    def test_continue_not_break_after_missing_author(self):
        """Must use `continue` not `break` so remaining authors are still processed."""
        src = _read_db_source()
        marker = 'not in Authors table; skipping sort for this entry'
        assert marker in src

        idx = src.index(marker)
        after = src[idx:idx + 200]
        assert 'continue' in after, "Expected `continue` after log — `break` abandons remaining authors"
        assert 'break' not in after, "`break` still present — must be `continue`"

    def test_no_error_or_warning_log_for_author_sort(self):
        """Author sort mismatch is a data quality issue — must be debug only, not error/warn."""
        src = _read_db_source()
        sort_section_start = src.index('sort_authors = ')
        sort_section = src[sort_section_start:sort_section_start + 800]
        assert 'log.error' not in sort_section, (
            "log.error found in author sort section — must be log.debug"
        )
        assert 'log.warning' not in sort_section, (
            "log.warning found in author sort section — must be log.debug (these are permanent "
            "data quality issues that flood production logs)"
        )
