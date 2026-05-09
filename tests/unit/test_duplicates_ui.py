# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for duplicates.js UI behaviour.

Verifies source-level guarantees for the batch delete / selection fix:
- .book-item click handler exists (clicking a card toggles the checkbox)
- The handler guards against double-firing when the checkbox itself is clicked
- updateSelectionCount enables both delete buttons when count > 0
- batch_delete_selected click handler checks the disabled class before acting
"""

import os
import pytest

_JS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'cps', 'static', 'js', 'duplicates.js')
)


def _read_js():
    with open(_JS_PATH) as f:
        return f.read()


class TestBookItemClickHandler:

    def test_book_item_click_handler_exists(self):
        """Clicking a book card must toggle its checkbox — handler must exist."""
        src = _read_js()
        assert "on('click', '.book-item'" in src, (
            "No .book-item click handler found. Without it, users must click the tiny "
            "checkbox exactly — clicking the card does nothing and the batch delete "
            "button stays disabled."
        )

    def test_book_item_click_guards_against_links_and_buttons(self):
        """Click handler must bail out if the click landed on a link, button, or checkbox."""
        src = _read_js()
        start = src.index("on('click', '.book-item'")
        end = src.index('\n    });', start) + 7
        snippet = src[start:end]
        assert "closest('a, button, .book-checkbox')" in snippet or \
               "closest('a," in snippet, (
            "book-item click handler must ignore clicks on <a>, <button>, and "
            ".book-checkbox so Edit/Archive/Merge links still work."
        )

    def test_card_click_directly_updates_selectedbooks(self):
        """Handler must update selectedBooks directly, not rely on trigger('change')."""
        src = _read_js()
        start = src.index("on('click', '.book-item'")
        end = src.index('\n    });', start) + 7
        snippet = src[start:end]
        assert 'selectedBooks.push' in snippet, (
            "book-item click handler must push to selectedBooks directly — "
            "trigger('change') can silently fail in some browser/jQuery combinations."
        )
        assert 'updateSelectionCount()' in snippet


class TestSelectionCountEnablesButtons:

    def test_batch_delete_enabled_when_one_book_selected(self):
        """batch_delete_selected must be enabled when exactly 1 book is selected."""
        src = _read_js()
        # Find the count === 1 branch of updateSelectionCount
        start = src.index('count === 1')
        snippet = src[start:start + 300]
        assert "batch_delete_selected" in snippet
        assert "removeClass('disabled')" in snippet

    def test_batch_delete_enabled_when_multiple_books_selected(self):
        """batch_delete_selected must be enabled in the count > 1 branch."""
        src = _read_js()
        # Find the updateSelectionCount function and check both enable branches exist
        start = src.index('function updateSelectionCount')
        end = src.index('\n    }', start) + 6  # end of the closing brace
        snippet = src[start:end + 200]
        enabled_count = snippet.count("batch_delete_selected').removeClass('disabled')")
        assert enabled_count >= 2, (
            f"Expected batch_delete_selected to be enabled in at least 2 branches "
            f"(count==1 and count>1), found {enabled_count}"
        )

    def test_batch_delete_click_checks_disabled_class(self):
        """Click handler must no-op when the button still has the disabled class."""
        src = _read_js()
        start = src.index("$('#batch_delete_selected').click")
        snippet = src[start:start + 200]
        assert "hasClass('disabled')" in snippet
