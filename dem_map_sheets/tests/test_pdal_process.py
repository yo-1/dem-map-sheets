"""PDAL process failure/cancellation regressions using a fake PDAL executable.

These are mocked tests: no real PDAL is involved. They pin down how
engine.point_inputs.run_process / executable behave when PDAL exits
nonzero, is missing, or the user cancels mid-run. Real-PDAL behaviour
is a separate verification item (PDAL-equipped environment / Windows QA).
"""
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from dem_map_sheets.engine.point_inputs import executable, run_process


class _Feedback:
    """Minimal stand-in for QgsProcessingFeedback (isCanceled only)."""

    def __init__(self, cancel_after=None):
        self.calls = 0
        self.cancel_after = cancel_after

    def isCanceled(self):
        self.calls += 1
        return self.cancel_after is not None and self.calls > self.cancel_after


def _script(directory, name, body):
    path = Path(directory) / name
    path.write_text(body, encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@unittest.skipIf(os.name == 'nt', 'fake PDAL executables are POSIX shell scripts')
class PdalProcessTests(unittest.TestCase):
    def test_nonzero_exit_raises_with_stderr(self):
        with tempfile.TemporaryDirectory() as td:
            exe = _script(td, 'pdal_fail', '#!/bin/sh\necho "mock pdal boom" >&2\nexit 3\n')
            with self.assertRaises(RuntimeError) as ctx:
                run_process(exe, ['info', 'x.las'], str(Path(td) / 'log.txt'))
            self.assertIn('PDAL failed (3)', str(ctx.exception))
            self.assertIn('mock pdal boom', str(ctx.exception))

    def test_missing_executable_raises(self):
        with self.assertRaises(ValueError):
            executable({'pdal_path': '/nonexistent/dir/pdal'})
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                run_process(str(Path(td) / 'no_such_pdal'), ['--version'],
                            str(Path(td) / 'log.txt'))

    def test_cancel_terminates_running_process(self):
        with tempfile.TemporaryDirectory() as td:
            exe = _script(td, 'pdal_sleep', '#!/bin/sh\nsleep 60\n')
            start = time.monotonic()
            with self.assertRaises(RuntimeError):
                run_process(exe, ['pipeline', 'x.json'], str(Path(td) / 'log.txt'),
                            _Feedback(cancel_after=3))
            self.assertLess(time.monotonic() - start, 20)

    def test_precancelled_feedback_prevents_start(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / 'started.txt'
            exe = _script(td, 'pdal_mark', f'#!/bin/sh\ntouch {marker}\n')
            with self.assertRaises(RuntimeError):
                run_process(exe, ['--version'], str(Path(td) / 'log.txt'),
                            _Feedback(cancel_after=0))
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
