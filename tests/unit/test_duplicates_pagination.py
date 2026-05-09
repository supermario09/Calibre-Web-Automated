# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for duplicates page pagination.

Tests the slicing logic and page-clamping in show_duplicates without
importing the full Flask app (which needs DB + config).
"""

import math
import os
import pytest

_CPS_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')


# ---------------------------------------------------------------------------
# Inline recreation of the pagination logic from show_duplicates()
# ---------------------------------------------------------------------------

_PER_PAGE_DEFAULT = 25
_PER_PAGE_MIN = 5
_PER_PAGE_MAX = 100


def _paginate(all_groups, page_num, per_page):
    """Mirror of the pagination logic in show_duplicates."""
    per_page = max(_PER_PAGE_MIN, min(_PER_PAGE_MAX, per_page))
    total_groups = len(all_groups)
    total_pages = max(1, math.ceil(total_groups / per_page))
    page_num = max(1, min(page_num, total_pages))
    offset = (page_num - 1) * per_page
    page_groups = all_groups[offset:offset + per_page]
    return page_groups, page_num, total_pages, total_groups


class TestPaginationLogic:

    def _groups(self, n):
        return [{"group_hash": str(i), "title": f"Book {i}"} for i in range(n)]

    def test_first_page_returns_first_n_groups(self):
        groups = self._groups(60)
        page, _, _, _ = _paginate(groups, 1, 25)
        assert len(page) == 25
        assert page[0]['title'] == 'Book 0'

    def test_second_page_returns_next_n_groups(self):
        groups = self._groups(60)
        page, _, _, _ = _paginate(groups, 2, 25)
        assert len(page) == 25
        assert page[0]['title'] == 'Book 25'

    def test_last_page_returns_remainder(self):
        groups = self._groups(60)
        page, _, total_pages, _ = _paginate(groups, 3, 25)
        assert total_pages == 3
        assert len(page) == 10  # 60 - 50 = 10
        assert page[0]['title'] == 'Book 50'

    def test_total_pages_calculated_correctly(self):
        assert _paginate(self._groups(25), 1, 25)[2] == 1
        assert _paginate(self._groups(26), 1, 25)[2] == 2
        assert _paginate(self._groups(100), 1, 25)[2] == 4
        assert _paginate(self._groups(101), 1, 25)[2] == 5

    def test_empty_library_returns_one_page(self):
        page, page_num, total_pages, total_groups = _paginate([], 1, 25)
        assert page == []
        assert total_pages == 1
        assert total_groups == 0

    def test_page_num_clamped_to_valid_range(self):
        groups = self._groups(30)
        _, p, _, _ = _paginate(groups, 0, 25)   # below 1
        assert p == 1
        _, p, _, _ = _paginate(groups, 99, 25)  # beyond last page
        assert p == 2  # only 2 pages for 30 groups at 25/page

    def test_per_page_clamped_to_valid_range(self):
        groups = self._groups(10)
        page, _, total_pages, _ = _paginate(groups, 1, 1)    # below min -> clamped to 5
        assert total_pages == 2   # ceil(10/5) = 2
        assert len(page) == 5     # first page of 5
        page, _, total_pages, _ = _paginate(groups, 1, 9999) # above max -> clamped to 100
        assert total_pages == 1   # all 10 fit on one page
        assert len(page) == 10

    def test_single_group_single_page(self):
        groups = self._groups(1)
        page, page_num, total_pages, total = _paginate(groups, 1, 25)
        assert len(page) == 1
        assert total_pages == 1
        assert total == 1


class TestPaginationSourceGuarantees:
    """Verify the source of duplicates.py has the pagination variables."""

    def _read(self):
        with open(os.path.join(_CPS_ROOT, 'cps', 'duplicates.py')) as f:
            return f.read()

    def test_page_num_param_in_route(self):
        src = self._read()
        assert "request.args.get('page'" in src

    def test_per_page_param_in_route(self):
        src = self._read()
        assert "request.args.get('per_page'" in src

    def test_total_pages_passed_to_template(self):
        src = self._read()
        assert 'total_pages=total_pages' in src

    def test_page_groups_slicing_present(self):
        src = self._read()
        assert 'duplicate_groups[offset:offset + per_page]' in src

    def test_per_page_clamped_in_source(self):
        src = self._read()
        assert '_DUPLICATES_PER_PAGE_MIN' in src
        assert '_DUPLICATES_PER_PAGE_MAX' in src


class TestTemplateSourceGuarantees:
    """Verify the template has pagination controls."""

    def _read(self):
        with open(os.path.join(_CPS_ROOT, 'cps', 'templates', 'duplicates.html')) as f:
            return f.read()

    def test_pagination_controls_present(self):
        src = self._read()
        assert 'total_pages' in src
        assert 'page_num' in src

    def test_per_page_selector_present(self):
        src = self._read()
        assert 'per_page_select' in src

    def test_prev_next_links_present(self):
        src = self._read()
        assert 'page_num-1' in src
        assert 'page_num+1' in src

    def test_select_all_label_scoped_to_page(self):
        src = self._read()
        assert 'this page' in src or "Select Duplicates (this page)" in src
