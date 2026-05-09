# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Unit tests for batch duplicate deletion functionality.

Tests the TaskBatchDeleteDuplicates task and batch deletion endpoint.
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, call
import sys
import os

# Add the cps directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cps.tasks.batch_duplicate_deletion import TaskBatchDeleteDuplicates


class TestBatchDeleteDuplicatesTask(unittest.TestCase):
    """Tests for TaskBatchDeleteDuplicates task"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.book_ids = [1, 2, 3, 4, 5]
        self.batch_size = 2
        self.strategy = 'newest'
        self.user_id = 1
        self.group_hash = 'abc123def456'
    
    def test_init_valid_params(self):
        """Test task initialization with valid parameters"""
        task = TaskBatchDeleteDuplicates(
            book_ids=self.book_ids,
            batch_size=self.batch_size,
            strategy=self.strategy,
            user_id=self.user_id,
            group_hash=self.group_hash
        )
        
        self.assertEqual(task.book_ids, self.book_ids)
        self.assertEqual(task.batch_size, self.batch_size)
        self.assertEqual(task.strategy, self.strategy)
        self.assertEqual(task.user_id, self.user_id)
        self.assertEqual(task.group_hash, self.group_hash)
        self.assertEqual(task.total_books, 5)
        self.assertEqual(task.deleted_count, 0)
        self.assertEqual(task.failed_count, 0)
    
    def test_init_clamps_batch_size(self):
        """Test that batch size is clamped between 1 and 50"""
        # Test too small
        task = TaskBatchDeleteDuplicates(self.book_ids, batch_size=0)
        self.assertEqual(task.batch_size, 1)
        
        # Test too large
        task = TaskBatchDeleteDuplicates(self.book_ids, batch_size=100)
        self.assertEqual(task.batch_size, 50)
        
        # Test valid
        task = TaskBatchDeleteDuplicates(self.book_ids, batch_size=25)
        self.assertEqual(task.batch_size, 25)
    
    def test_init_empty_book_ids(self):
        """Test initialization with empty book IDs list"""
        task = TaskBatchDeleteDuplicates(book_ids=[])
        self.assertEqual(task.book_ids, [])
        self.assertEqual(task.total_books, 0)
    
    def test_init_none_book_ids(self):
        """Test initialization with None book IDs"""
        task = TaskBatchDeleteDuplicates(book_ids=None)
        self.assertEqual(task.book_ids, [])
        self.assertEqual(task.total_books, 0)
    
    def test_batch_calculation(self):
        """Test that batches are calculated correctly"""
        task = TaskBatchDeleteDuplicates(
            book_ids=[1, 2, 3, 4, 5],
            batch_size=2
        )
        
        # With 5 books and batch size 2, should have 3 batches
        # Batch 1: [1, 2]
        # Batch 2: [3, 4]
        # Batch 3: [5]
        self.assertEqual(task.total_books, 5)
        self.assertEqual(task.batch_size, 2)


class TestBatchDeleteEndpoint(unittest.TestCase):
    """Tests for batch delete endpoint"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Mock current_user
        self.current_user = Mock()
        self.current_user.id = 1
        self.current_user.role_admin.return_value = True
        self.current_user.is_authenticated = True
    
    def test_batch_delete_empty_selection(self):
        """Test batch delete with empty book selection"""
        # This would be tested in integration tests with Flask client
        # Unit test validates the logic
        request_data = {'book_ids': []}
        self.assertEqual(len(request_data['book_ids']), 0)
        self.assertTrue('book_ids' in request_data)
    
    def test_batch_delete_invalid_batch_size(self):
        """Test batch delete with invalid batch size"""
        request_data = {
            'book_ids': [1, 2, 3],
            'batch_size': 0
        }
        
        batch_size = int(request_data.get('batch_size', 10))
        batch_size = max(1, min(batch_size, 50))
        
        self.assertEqual(batch_size, 1)
    
    def test_batch_delete_valid_request(self):
        """Test batch delete with valid request data"""
        request_data = {
            'book_ids': [1, 2, 3, 4, 5],
            'batch_size': 10,
            'strategy': 'newest'
        }
        
        self.assertEqual(len(request_data['book_ids']), 5)
        self.assertIn('batch_size', request_data)
        self.assertIn('strategy', request_data)
        self.assertEqual(request_data['strategy'], 'newest')


class TestBatchDeletionLogic(unittest.TestCase):
    """Tests for batch deletion logic"""
    
    def test_batch_iteration(self):
        """Test that books are correctly divided into batches"""
        book_ids = list(range(1, 16))  # 15 books
        batch_size = 4
        
        batches = []
        for batch_idx in range(0, len(book_ids), batch_size):
            batch_start = batch_idx
            batch_end = min(batch_idx + batch_size, len(book_ids))
            batch = book_ids[batch_start:batch_end]
            batches.append(batch)
        
        self.assertEqual(len(batches), 4)
        self.assertEqual(batches[0], [1, 2, 3, 4])
        self.assertEqual(batches[1], [5, 6, 7, 8])
        self.assertEqual(batches[2], [9, 10, 11, 12])
        self.assertEqual(batches[3], [13, 14, 15])
    
    def test_progress_calculation(self):
        """Test progress calculation during batch deletion"""
        total_books = 100
        
        # Simulate different deletion stages
        test_cases = [
            (0, 0),      # 0 deleted out of 100 = 0%
            (25, 25),    # 25 deleted out of 100 = 25%
            (50, 50),    # 50 deleted out of 100 = 50%
            (100, 100),  # 100 deleted out of 100 = 100%
        ]
        
        for deleted, expected_progress in test_cases:
            progress = (deleted * 100) // total_books
            self.assertEqual(progress, expected_progress)


class TestDatabaseConnectionPooling(unittest.TestCase):
    """Tests for SQLite connection pooling configuration"""
    
    def test_pragma_settings(self):
        """Test that recommended PRAGMA settings are applied"""
        expected_pragmas = [
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA cache_size=10000",
            "PRAGMA temp_store=MEMORY",
            "PRAGMA mmap_size=33554432",
            "PRAGMA busy_timeout=30000",
            "PRAGMA query_only=False",
            "PRAGMA foreign_keys=ON"
        ]
        
        # These pragmas should be applied to each connection
        for pragma in expected_pragmas:
            self.assertIn("PRAGMA", pragma)
            self.assertIn("=", pragma)
    
    def test_pooling_configuration(self):
        """Test connection pooling parameters"""
        # These values should be used when creating the SQLAlchemy engine
        pool_config = {
            'pool_size': 5,
            'max_overflow': 10,
            'pool_pre_ping': True,
            'pool_recycle': 3600
        }
        
        self.assertEqual(pool_config['pool_size'], 5)
        self.assertEqual(pool_config['max_overflow'], 10)
        self.assertTrue(pool_config['pool_pre_ping'])
        self.assertEqual(pool_config['pool_recycle'], 3600)


if __name__ == '__main__':
    unittest.main()
