# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for conversion resource limits.

Covers:
- process_open limit_memory flag wires preexec_fn on Linux
- _set_conversion_limits applies RLIMIT_AS and survives errors
- _CONVERSION_TIMEOUT / _CONVERSION_MEMORY_BYTES defaults and env override
- _kill_after watchdog timer kills a running process
- convert_library._kill_after cancels cleanly when process finishes first
- TaskConvert deadline loop kills process on timeout

NOTE: These tests are designed to run inside the Docker container where all
CWA dependencies are available. The cps package imports require Flask, tabulate,
and other deps present in the container but not on the host.
"""

import os
import sys
import time
import importlib
import importlib.util
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

_CPS_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
_SCRIPTS_ROOT = os.path.join(_CPS_ROOT, 'scripts')


def _load_module_directly(rel_path, name):
    """Load a .py file directly without triggering package __init__ chains."""
    abs_path = os.path.normpath(os.path.join(_CPS_ROOT, rel_path))
    spec = importlib.util.spec_from_file_location(name, abs_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_convert_library():
    """Load convert_library.py with module-level side-effect deps mocked out."""
    mocks = {
        'cwa_db': MagicMock(),
        'kindle_epub_fixer': MagicMock(),
        'tabulate': MagicMock(),
    }
    saved = {}
    for key, mock in mocks.items():
        saved[key] = sys.modules.get(key)
        sys.modules[key] = mock

    try:
        with patch('pwd.getpwnam', return_value=MagicMock(pw_uid=1000)):
            with patch('grp.getgrnam', return_value=MagicMock(gr_gid=1000)):
                with patch('logging.FileHandler', MagicMock()):
                    with patch('builtins.open', MagicMock()):
                        with patch('os.scandir', return_value=[]):
                            cl = _load_module_directly('scripts/convert_library.py', 'convert_library_test')
    finally:
        for key, original in saved.items():
            if original is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = original
    return cl


# ---------------------------------------------------------------------------
# subproc_wrapper — loaded directly to bypass cps/__init__.py chain
# ---------------------------------------------------------------------------

class TestProcessOpenMemoryLimit:
    """Tests for the limit_memory parameter added to process_open."""

    @pytest.fixture
    def sw(self):
        return _load_module_directly('cps/subproc_wrapper.py', 'subproc_wrapper')

    def test_limit_memory_false_no_preexec(self, sw):
        """Default call must not set preexec_fn (preserves existing behaviour)."""
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value = MagicMock()
            sw.process_open(['echo', 'hi'], limit_memory=False)
            _, kwargs = mock_popen.call_args
            assert kwargs.get('preexec_fn') is None

    @pytest.mark.skipif(os.name == 'nt', reason="preexec_fn is Linux-only")
    def test_limit_memory_true_sets_preexec_on_linux(self, sw):
        """limit_memory=True must attach _set_conversion_limits as preexec_fn."""
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value = MagicMock()
            sw.process_open(['echo', 'hi'], limit_memory=True)
            _, kwargs = mock_popen.call_args
            assert kwargs.get('preexec_fn') is sw._set_conversion_limits

    def test_limit_memory_true_on_windows_no_preexec(self, sw):
        """On Windows, preexec_fn must stay None even with limit_memory=True."""
        with patch.object(sw.os, 'name', 'nt'):
            with patch('subprocess.Popen') as mock_popen:
                mock_popen.return_value = MagicMock()
                sw.process_open(['echo', 'hi'], limit_memory=True)
                _, kwargs = mock_popen.call_args
                assert kwargs.get('preexec_fn') is None


class TestSetConversionLimits:
    """Tests for _set_conversion_limits preexec hook."""

    @pytest.fixture
    def sw(self):
        return _load_module_directly('cps/subproc_wrapper.py', 'subproc_wrapper')

    @pytest.mark.skipif(os.name == 'nt', reason="resource module is Linux-only")
    def test_calls_setrlimit_with_rlimit_as(self, sw):
        """Must call resource.setrlimit with RLIMIT_AS and a positive limit."""
        import resource
        with patch('resource.setrlimit') as mock_setrlimit:
            sw._set_conversion_limits()
            mock_setrlimit.assert_called_once()
            args = mock_setrlimit.call_args[0]
            assert args[0] == resource.RLIMIT_AS
            assert args[1][0] > 0

    @pytest.mark.skipif(os.name == 'nt', reason="resource module is Linux-only")
    def test_survives_setrlimit_failure(self, sw):
        """A failing setrlimit must be swallowed — hook must never raise."""
        with patch('resource.setrlimit', side_effect=ValueError("test")):
            sw._set_conversion_limits()  # must not raise


class TestConversionLimitDefaults:
    """Tests for default and env-overridden timeout/memory values."""

    def test_default_memory_limit_is_1500mb(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CWA_CONVERSION_MEMORY_MB', None)
            sw = _load_module_directly('cps/subproc_wrapper.py', 'subproc_wrapper_fresh')
            assert sw._CONVERSION_MEMORY_BYTES == 1500 * 1024 * 1024

    def test_memory_limit_env_override(self):
        with patch.dict(os.environ, {'CWA_CONVERSION_MEMORY_MB': '2000'}):
            sw = _load_module_directly('cps/subproc_wrapper.py', 'subproc_wrapper_2000')
            assert sw._CONVERSION_MEMORY_BYTES == 2000 * 1024 * 1024

    def test_default_conversion_timeout_in_convert_library(self):
        cl = _load_convert_library()
        assert cl._CONVERSION_TIMEOUT == 600

    def test_conversion_timeout_env_override_in_convert_library(self):
        with patch.dict(os.environ, {'CWA_CONVERSION_TIMEOUT': '120'}):
            cl = _load_convert_library()
            assert cl._CONVERSION_TIMEOUT == 120


# ---------------------------------------------------------------------------
# convert_library watchdog timer
# ---------------------------------------------------------------------------

class TestKillAfterWatchdog:
    """Tests for the _kill_after watchdog helper in convert_library."""

    @pytest.fixture
    def kill_after(self):
        return _load_convert_library()._kill_after

    def test_cancels_when_process_finishes_before_timeout(self, kill_after):
        """Timer must be cancellable with no kill if process already exited."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # already finished

        timer = kill_after(mock_process, timeout=10)
        timer.cancel()

        mock_process.kill.assert_not_called()

    def test_kills_overrunning_process(self, kill_after):
        """Timer must call process.kill() when the process is still running at expiry."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # still running

        kill_after(mock_process, timeout=0.05)
        time.sleep(0.15)  # let timer fire

        mock_process.kill.assert_called_once()

    def test_watchdog_is_daemon_thread(self, kill_after):
        """Watchdog timer must be a daemon so it doesn't block process exit."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0

        timer = kill_after(mock_process, timeout=60)
        assert timer.daemon is True
        timer.cancel()


# ---------------------------------------------------------------------------
# Verify source-level presence of limits (works without full dep chain)
# ---------------------------------------------------------------------------

class TestSourceLevelGuarantees:
    """Lightweight checks on the source files themselves."""

    def _read(self, rel_path):
        with open(os.path.normpath(os.path.join(_CPS_ROOT, rel_path))) as f:
            return f.read()

    def test_subproc_wrapper_has_limit_memory_param(self):
        src = self._read('cps/subproc_wrapper.py')
        assert 'limit_memory' in src
        assert '_set_conversion_limits' in src
        assert 'RLIMIT_AS' in src

    def test_convert_task_has_timeout_deadline(self):
        src = self._read('cps/tasks/convert.py')
        assert '_CONVERSION_TIMEOUT' in src
        assert 'deadline' in src
        assert 'p.kill()' in src
        assert 'limit_memory=True' in src

    def test_convert_library_has_watchdog_and_gc(self):
        src = self._read('scripts/convert_library.py')
        assert '_kill_after' in src
        assert '_set_conversion_limits' in src
        assert 'gc.collect()' in src
        assert 'preexec_fn' in src
