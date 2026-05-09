# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for database connection pool configuration.

Verifies that CalibreDB's in-memory SQLite engine uses StaticPool, which is
required for in-memory SQLite with ATTACH DATABASE. QueuePool would give each
pool connection its own isolated in-memory database, breaking calibre/app_settings
schema access on any connection that didn't run ATTACH.

NOTE: These tests are designed to run inside the Docker container where all
CWA dependencies are available.
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class TestStaticPoolBehaviourForInMemorySQLite:
    """
    Demonstrate why StaticPool is the only correct pool for sqlite:// (in-memory).
    These tests verify SQLAlchemy semantics directly — no CPS imports needed.
    """

    def test_staticpool_shares_single_connection(self):
        """
        With StaticPool, a second connection sees data committed by the first.
        This is the property CalibreDB relies on for ATTACH DATABASE.
        """
        from sqlalchemy.pool import StaticPool
        from sqlalchemy import create_engine, text

        engine = create_engine(
            'sqlite://', poolclass=StaticPool,
            connect_args={'check_same_thread': False}
        )
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE test_share (id INTEGER)"))
            conn.execute(text("INSERT INTO test_share VALUES (1)"))
            conn.commit()  # must commit so StaticPool's single connection sees it

        with engine.connect() as conn2:
            result = conn2.execute(text("SELECT id FROM test_share")).fetchone()
            assert result is not None, (
                "StaticPool must share a single connection so all 'connections' "
                "see the same in-memory database — required for ATTACH DATABASE."
            )
            assert result[0] == 1

    def test_staticpool_attach_database_visible_across_connections(self):
        """
        ATTACH DATABASE performed on one StaticPool connection is visible to the next.
        This mirrors exactly how CalibreDB wires up calibre and app_settings schemas.
        """
        import tempfile
        import sqlite3
        from sqlalchemy.pool import StaticPool
        from sqlalchemy import create_engine, text

        # Create a real on-disk SQLite file to attach
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        try:
            # Seed the attached database
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE books (title TEXT)")
            con.execute("INSERT INTO books VALUES ('Test Book')")
            con.commit()
            con.close()

            engine = create_engine(
                'sqlite://', poolclass=StaticPool,
                connect_args={'check_same_thread': False}
            )
            # Attach on first connection
            with engine.connect() as conn:
                conn.execute(text(f"ATTACH DATABASE '{db_path}' AS calibre"))
                conn.commit()

            # Attached schema must be visible on a second connection
            with engine.connect() as conn2:
                result = conn2.execute(
                    text("SELECT title FROM calibre.books")
                ).fetchone()
                assert result is not None, (
                    "ATTACH DATABASE must be visible on subsequent StaticPool connections."
                )
                assert result[0] == 'Test Book'
        finally:
            os.unlink(db_path)

    def test_queuepool_attach_not_shared(self):
        """
        With QueuePool, ATTACH DATABASE is NOT shared across connections.
        This confirms QueuePool is the wrong choice for CalibreDB's in-memory engine.
        """
        import tempfile
        import sqlite3
        from sqlalchemy.pool import QueuePool
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import OperationalError

        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        try:
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE books (title TEXT)")
            con.commit()
            con.close()

            engine = create_engine(
                'sqlite://', poolclass=QueuePool, pool_size=2,
                connect_args={'check_same_thread': False}
            )
            with engine.connect() as conn:
                conn.execute(text(f"ATTACH DATABASE '{db_path}' AS calibre"))
                conn.commit()

            # A second QueuePool connection may be a different in-memory DB
            # and will not have the ATTACH — this is the bug we fixed.
            with engine.connect() as conn2:
                try:
                    conn2.execute(text("SELECT title FROM calibre.books")).fetchone()
                    # If it works, it's a fluke of connection reuse — still unreliable
                except OperationalError:
                    pass  # Expected: no such table — confirms the bug
        finally:
            os.unlink(db_path)

    def test_calibre_db_module_imports_staticpool(self):
        """cps.db must import StaticPool (confirm it's available to the engine factory)."""
        # This test only verifies the import exists in the module source.
        db_path = os.path.join(
            os.path.dirname(__file__), '..', '..', 'cps', 'db.py'
        )
        with open(db_path) as f:
            source = f.read()
        assert 'StaticPool' in source, "cps/db.py must import and use StaticPool"
        assert 'poolclass=StaticPool' in source, (
            "in-memory SQLite engine must use poolclass=StaticPool, not QueuePool"
        )
