"""Deterministic tests for transient SMB/network failures."""
import errno
import unittest
from unittest.mock import patch
from dem_map_sheets.engine.input_sources import _network_call


class NetworkRetryTests(unittest.TestCase):
    def test_retry_transient_network_error_then_succeed(self):
        attempts = []
        def operation():
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError(errno.ENETUNREACH, 'temporary outage')
            return 'ok'
        with patch('dem_map_sheets.engine.input_sources.time.sleep') as sleep:
            self.assertEqual(_network_call('SMB read', operation), 'ok')
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_permanent_network_error_reports_operation(self):
        with patch('dem_map_sheets.engine.input_sources.time.sleep'):
            with self.assertRaisesRegex(OSError, 'SMB listing'):
                _network_call('SMB listing', lambda: (_ for _ in ()).throw(OSError(errno.ENETUNREACH, 'offline')))

    def test_non_network_error_is_not_retried(self):
        attempts = []
        def operation():
            attempts.append(1)
            raise PermissionError(errno.EACCES, 'denied')
        with patch('dem_map_sheets.engine.input_sources.time.sleep') as sleep:
            with self.assertRaises(PermissionError):
                _network_call('permission', operation)
        self.assertEqual(len(attempts), 1)
        sleep.assert_not_called()
