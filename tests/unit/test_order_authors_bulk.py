# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for the order_authors() bulk-query rewrite.

Verifies:
- Source-level guarantees: bulk .in_() queries, null guards, no per-author loop queries
- Correctness of the in-memory ordering logic (via inline recreation)
- Crash fix: None values from DB/ORM do not cause AttributeError
- find_duplicate_books_python uses selectinload for all relationships
- Per-book order_authors() call removed from grouping loop
"""

import os
import pytest
from unittest.mock import MagicMock, patch

_CPS_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')


def _read(rel_path):
    with open(os.path.normpath(os.path.join(_CPS_ROOT, rel_path))) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Source-level guarantees
# ---------------------------------------------------------------------------

class TestOrderAuthorsSourceGuarantees:

    def test_bulk_in_query_for_sort_values(self):
        src = _read('cps/db.py')
        fn_start = src.index('def order_authors(')
        snippet = src[fn_start:fn_start + 2500]
        assert 'Authors.sort.in_(' in snippet, \
            "order_authors must use .in_() to bulk-fetch by sort rather than per-author queries"

    def test_bulk_in_query_for_missing_ids(self):
        src = _read('cps/db.py')
        fn_start = src.index('def order_authors(')
        snippet = src[fn_start:fn_start + 2500]
        assert 'Authors.id.in_(' in snippet, \
            "order_authors must use .in_() for the fallback ID lookup"

    def test_null_guard_on_sort_results(self):
        src = _read('cps/db.py')
        fn_start = src.index('def order_authors(')
        snippet = src[fn_start:fn_start + 2500]
        assert 'if a is None' in snippet, \
            "order_authors must guard against None in bulk query results (fixes AttributeError crash)"

    def test_null_guard_on_fallback_result(self):
        src = _read('cps/db.py')
        fn_start = src.index('def order_authors(')
        fn_end = src.index('\n    def ', fn_start + 1)
        fn_body = src[fn_start:fn_end]
        assert 'if result is not None' in fn_body or \
               ('authors_by_id.get(' in fn_body and 'if result is not None' in fn_body), \
            "Fallback ID lookup must not append None to authors_ordered"

    def test_no_per_entry_query_in_loop(self):
        """The inner loop must not call session.query() per entry."""
        src = _read('cps/db.py')
        fn_start = src.index('def order_authors(')
        fn_end = src.index('\n    def ', fn_start + 1)
        fn_body = src[fn_start:fn_end]
        # Phase 3 (in-memory ordering) starts after the bulk queries
        phase3_marker = '# --- Phase 3'
        assert phase3_marker in fn_body
        phase3_start = fn_body.index(phase3_marker)
        phase3_body = fn_body[phase3_start:]
        assert 'session.query(' not in phase3_body, \
            "Phase 3 (per-entry ordering) must not call session.query() — all lookups should be in-memory"

    def test_none_safe_author_sort_access(self):
        src = _read('cps/db.py')
        fn_start = src.index('def order_authors(')
        snippet = src[fn_start:fn_start + 2500]
        assert "author_sort or ''" in snippet, \
            "author_sort must be guarded against None with 'or empty string'"


class TestDuplicatesPythonSelectinload:

    def test_selectinload_authors(self):
        src = _read('cps/duplicates.py')
        assert 'selectinload(db.Books.authors)' in src

    def test_selectinload_languages(self):
        src = _read('cps/duplicates.py')
        assert 'selectinload(db.Books.languages)' in src

    def test_selectinload_series(self):
        src = _read('cps/duplicates.py')
        assert 'selectinload(db.Books.series)' in src

    def test_selectinload_publishers(self):
        src = _read('cps/duplicates.py')
        assert 'selectinload(db.Books.publishers)' in src

    def test_selectinload_data(self):
        src = _read('cps/duplicates.py')
        assert 'selectinload(db.Books.data)' in src

    def test_no_per_book_order_authors_in_grouping_loop(self):
        """The main grouping loop must not call order_authors([book]) per book."""
        src = _read('cps/duplicates.py')
        fn_start = src.index('def find_duplicate_books_python(')
        # The grouping loop ends before the duplicate_groups building section
        grouping_loop_end = src.index('# Filter to only groups with duplicates', fn_start)
        grouping_section = src[fn_start:grouping_loop_end]
        assert 'calibre_db.order_authors([book])' not in grouping_section, \
            "Per-book order_authors call in grouping loop must be removed — use author_sort directly"

    def test_bulk_order_authors_in_display_section(self):
        """Display section must call order_authors on the whole group, not per book."""
        src = _read('cps/duplicates.py')
        fn_start = src.index('def find_duplicate_books_python(')
        display_start = src.index('# Filter to only groups with duplicates', fn_start)
        display_section = src[display_start:display_start + 1000]
        assert 'order_authors(books, list_return=True)' in display_section, \
            "Display section must call order_authors(books, list_return=True) once per group"


class TestDBIndexesInSetupDb:

    def test_index_on_authors_sort(self):
        src = _read('cps/db.py')
        assert 'cwa_idx_authors_sort' in src
        assert 'ON calibre.authors(sort)' in src

    def test_index_on_data_book(self):
        src = _read('cps/db.py')
        assert 'cwa_idx_data_book' in src
        assert 'ON calibre.data(book)' in src

    def test_index_on_books_author_sort(self):
        src = _read('cps/db.py')
        assert 'cwa_idx_books_author_sort' in src
        assert 'ON calibre.books(author_sort)' in src

    def test_indexes_use_if_not_exists(self):
        src = _read('cps/db.py')
        assert src.count('CREATE INDEX IF NOT EXISTS') >= 3


# ---------------------------------------------------------------------------
# Correctness: inline recreation of the bulk ordering logic
# ---------------------------------------------------------------------------

class FakeAuthor:
    def __init__(self, id, sort, name):
        self.id = id
        self.sort = sort
        self.name = name


class FakeBook:
    def __init__(self, author_sort, author_ids, author_objs):
        self.author_sort = author_sort
        self._authors = author_objs
        self.ordered_authors = None

    @property
    def authors(self):
        return self._authors


def _order_authors_bulk(entries):
    """Inline recreation of the bulk order_authors logic for correctness testing."""
    strip_whitespaces = str.strip  # avoid importing cps (needs tabulate + Flask)

    if not entries:
        return entries

    all_sort_values = set()
    all_ids = set()
    for entry in entries:
        sort_str = entry.author_sort or ''
        entry_ids = [a.id for a in entry.authors if a is not None]
        for s in sort_str.split('&'):
            s = s.strip()
            if s:
                all_sort_values.add(s)
        all_ids.update(entry_ids)

    # Simulate what the DB would return
    all_possible = {a.id: a for entry in entries for a in entry.authors if a is not None}
    authors_by_sort = {}
    authors_by_id = {}
    for a in all_possible.values():
        if a.sort in all_sort_values:
            authors_by_id[a.id] = a
            authors_by_sort.setdefault(a.sort, []).append(a)

    missing_ids = all_ids - set(authors_by_id.keys())
    for aid in missing_ids:
        if aid in all_possible:
            authors_by_id[aid] = all_possible[aid]

    for entry in entries:
        sort_authors = (entry.author_sort or '').split('&')
        ids = list(a.id for a in entry.authors if a is not None)
        authors_ordered = []
        for auth in sort_authors:
            auth = auth.strip()
            if not auth:
                continue
            for r in authors_by_sort.get(auth, []):
                if r.id in ids:
                    authors_ordered.append(r)
                    ids.remove(r.id)
        for aid in ids:
            result = authors_by_id.get(aid)
            if result is not None:
                authors_ordered.append(result)
        entry.ordered_authors = authors_ordered

    return entries


class TestOrderAuthorsBulkCorrectness:

    def test_single_author_ordered_correctly(self):
        a = FakeAuthor(1, 'Smith, John', 'John Smith')
        book = FakeBook('Smith, John', [1], [a])
        result = _order_authors_bulk([book])
        assert result[0].ordered_authors == [a]

    def test_multiple_authors_sorted_by_author_sort(self):
        a1 = FakeAuthor(1, 'Aardvark, Anne', 'Anne Aardvark')
        a2 = FakeAuthor(2, 'Zebra, Zack', 'Zack Zebra')
        # author_sort says a2 first, then a1
        book = FakeBook('Zebra, Zack & Aardvark, Anne', [1, 2], [a1, a2])
        result = _order_authors_bulk([book])
        assert result[0].ordered_authors == [a2, a1]

    def test_none_in_authors_does_not_crash(self):
        """Simulate DB returning None — must not raise AttributeError."""
        a = FakeAuthor(1, 'Smith, John', 'John Smith')
        book = FakeBook('Smith, John', [1], [None, a])
        result = _order_authors_bulk([book])
        assert a in result[0].ordered_authors

    def test_missing_sort_falls_back_to_id_lookup(self):
        """Author with no matching sort entry falls back to ID lookup."""
        a = FakeAuthor(99, 'Unknown Sort', 'Mystery Author')
        book = FakeBook('Nonexistent Sort', [99], [a])
        result = _order_authors_bulk([book])
        assert a in result[0].ordered_authors

    def test_empty_entries_returns_empty(self):
        result = _order_authors_bulk([])
        assert result == []

    def test_multiple_books_ordered_independently(self):
        a1 = FakeAuthor(1, 'Alpha, A', 'A Alpha')
        a2 = FakeAuthor(2, 'Beta, B', 'B Beta')
        book1 = FakeBook('Alpha, A', [1], [a1])
        book2 = FakeBook('Beta, B', [2], [a2])
        result = _order_authors_bulk([book1, book2])
        assert result[0].ordered_authors == [a1]
        assert result[1].ordered_authors == [a2]
