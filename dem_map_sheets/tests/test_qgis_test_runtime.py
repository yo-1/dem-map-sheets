"""QGIS lifecycle fixture tests with a mocked qgis.core module.

These tests validate ownership logic only. They do not substitute for
running QGIS-dependent integration tests in a real QGIS environment.
"""
import sys
import types
import unittest
from unittest import mock

from dem_map_sheets.tests.qgis_test_runtime import qgis_test_application


class QGISRuntimeFixtureTests(unittest.TestCase):
    def _qgis_modules(self, application_class):
        core = types.ModuleType("qgis.core")
        core.QgsApplication = application_class
        package = types.ModuleType("qgis")
        package.core = core
        return mock.patch.dict(sys.modules, {"qgis": package, "qgis.core": core})

    def test_existing_desktop_instance_is_never_initialized_or_closed(self):
        existing = mock.Mock()
        cls = mock.Mock()
        cls.instance.return_value = existing
        with self._qgis_modules(cls):
            with qgis_test_application() as app:
                self.assertIs(app, existing)
        cls.assert_not_called()
        existing.initQgis.assert_not_called()
        existing.exitQgis.assert_not_called()

    def test_standalone_instance_is_owned_and_closed(self):
        created = mock.Mock()
        cls = mock.Mock(return_value=created)
        cls.instance.return_value = None
        with self._qgis_modules(cls):
            with qgis_test_application() as app:
                self.assertIs(app, created)
                created.initQgis.assert_called_once_with()
                created.exitQgis.assert_not_called()
        cls.assert_called_once_with([], False)
        created.exitQgis.assert_called_once_with()

    def test_owned_instance_closes_on_test_exception(self):
        created = mock.Mock()
        cls = mock.Mock(return_value=created)
        cls.instance.return_value = None
        with self._qgis_modules(cls):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                with qgis_test_application():
                    raise RuntimeError("test failure")
        created.exitQgis.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
