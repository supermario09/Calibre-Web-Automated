# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Batch duplicate deletion task for CWA.

This task allows users to delete large numbers of duplicate books asynchronously,
processing them in batches to avoid memory exhaustion and database lockups.
"""

import sys
import os
from datetime import datetime, timezone
import gc

sys.path.insert(0, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB

from cps.services.worker import CalibreTask
from .. import logger, calibre_db, ub, config, helper
from ..editbooks import delete_whole_book
from ..db import Books

log = logger.create()


class TaskBatchDeleteDuplicates(CalibreTask):
    """
    Asynchronous task for batch deleting duplicate books.
    
    Processes deletions in configurable batch sizes to:
    - Prevent OOM (out of memory) errors
    - Avoid database lockups
    - Provide progress tracking
    - Allow user cancellation
    """
    
    def __init__(self, book_ids, batch_size=10, strategy='newest', user_id=None, group_hash=None):
        """
        Initialize batch deletion task.
        
        Args:
            book_ids: List of book IDs to delete (duplicates)
            batch_size: Number of books to delete per batch (default: 10)
            strategy: Resolution strategy used ('newest', 'oldest', etc.)
            user_id: User who initiated the deletion (for audit)
            group_hash: Hash of the duplicate group (for audit)
        """
        super().__init__(_("Batch Delete Duplicates"))
        self.book_ids = list(book_ids) if book_ids else []
        self.total_books = len(self.book_ids)
        self.batch_size = max(1, min(batch_size, 50))  # Clamp between 1 and 50
        self.strategy = strategy
        self.user_id = user_id
        self.group_hash = group_hash
        
        # Track progress
        self.deleted_count = 0
        self.failed_count = 0
        self.failed_books = []
        
        self.title = _("Batch Delete Duplicates ({} books)").format(self.total_books)
        self.message = _("{} of {} books deleted").format(self.deleted_count, self.total_books)

    @property
    def name(self):
        return str(_("Batch Delete Duplicates"))

    @property
    def is_cancellable(self):
        return True
    
    def run(self, worker_thread=None):
        """Execute batch deletion task"""
        if not self.book_ids:
            self._add_log_entry("No books to delete", level=2)  # Info level
            self.message = _("No books to delete")
            return
        
        log.info(f"Starting batch deletion of {self.total_books} duplicate books in batches of {self.batch_size}")
        self._add_log_entry(
            _("Starting batch deletion of {} books (batch size: {})").format(self.total_books, self.batch_size),
            level=1  # Info
        )
        
        try:
            # Process books in batches
            for batch_idx in range(0, self.total_books, self.batch_size):
                # Check for cancellation
                if self.is_canceled:
                    log.warning("Batch deletion task cancelled by user")
                    self._add_log_entry(_("Batch deletion cancelled by user"), level=2)
                    break
                
                batch_start = batch_idx
                batch_end = min(batch_idx + self.batch_size, self.total_books)
                batch_books = self.book_ids[batch_start:batch_end]
                current_batch_num = (batch_idx // self.batch_size) + 1
                total_batches = (self.total_books + self.batch_size - 1) // self.batch_size
                
                log.info(f"Processing batch {current_batch_num}/{total_batches} (books {batch_start+1}-{batch_end})")
                self._add_log_entry(
                    _("Processing batch {} of {} ({} books)").format(current_batch_num, total_batches, len(batch_books)),
                    level=1
                )
                
                # Delete books in this batch
                self._delete_batch(batch_books)
                
                # Update progress message
                self.message = _("{} of {} books deleted, {} failed").format(
                    self.deleted_count, self.total_books, self.failed_count
                )
                
                # Force garbage collection between batches to free memory
                gc.collect()
                
                # Update task progress (display percentage)
                self.progress = (self.deleted_count * 100) // self.total_books
        
        except Exception as e:
            log.exception(f"Batch deletion task failed: {e}")
            self._add_log_entry(
                _("Batch deletion failed: {}").format(str(e)),
                level=4  # Error level
            )
            self.message = _("Batch deletion failed: {}").format(str(e))
            return
        
        # Completion summary
        summary = _("Batch deletion complete: {} deleted, {} failed").format(
            self.deleted_count, self.failed_count
        )
        log.info(summary)
        self._add_log_entry(summary, level=1)
        
        if self.failed_books:
            failed_list = ", ".join(str(bid) for bid in self.failed_books[:10])
            if len(self.failed_books) > 10:
                failed_list += f", ... and {len(self.failed_books) - 10} more"
            self._add_log_entry(
                _("Failed book IDs: {}").format(failed_list),
                level=3  # Warning
            )
        
        self.message = summary
        self.progress = 100
    
    def _delete_batch(self, batch_book_ids):
        """
        Delete a single batch of books.
        
        Args:
            batch_book_ids: List of book IDs to delete in this batch
        """
        calibre_db.ensure_session()
        
        for book_id in batch_book_ids:
            try:
                book = calibre_db.get_book(book_id)
                if not book:
                    log.warning(f"Book {book_id} not found in database")
                    self.failed_books.append(book_id)
                    self.failed_count += 1
                    continue
                
                # Delete book files
                delete_result, delete_error = helper.delete_book(
                    book,
                    config.get_book_path(),
                    book_format=""
                )
                
                if not delete_result:
                    log.error(f"Failed to delete book files for {book_id}: {delete_error}")
                    self._add_log_entry(
                        _("Failed to delete files for book {}: {}").format(book_id, delete_error),
                        level=3  # Warning
                    )
                    self.failed_books.append(book_id)
                    self.failed_count += 1
                    continue
                
                # Delete from database
                delete_whole_book(book.id, book)
                calibre_db.session.commit()
                
                self.deleted_count += 1
                log.info(f"Successfully deleted duplicate book {book_id}")
                
            except Exception as e:
                log.exception(f"Error deleting book {book_id}: {e}")
                self._add_log_entry(
                    _("Error deleting book {}: {}").format(book_id, str(e)),
                    level=3  # Warning
                )
                self.failed_books.append(book_id)
                self.failed_count += 1
                # Continue with next book instead of failing completely
                continue
    
    def _add_log_entry(self, message, level=1):
        """
        Add entry to task log.
        
        Args:
            message: Log message
            level: 0=debug, 1=info, 2=warning, 3=error, 4=critical
        """
        # Log to application logger
        if level == 0:
            log.debug(message)
        elif level == 1:
            log.info(message)
        elif level == 2:
            log.warning(message)
        elif level == 3:
            log.error(message)
        else:
            log.critical(message)
        
        # Also log to CWA database for audit trail (if available)
        try:
            cwa_db_instance = CWA_DB()
            cwa_db_instance.insert_setting(
                'batch_delete_log',
                f"{datetime.now(timezone.utc).isoformat()} [Level {level}] {message}",
                data_type='json'
            )
        except Exception as e:
            log.debug(f"Could not log to CWA DB: {e}")


# Import translation function (must be after task definition)
from flask_babel import gettext as _
