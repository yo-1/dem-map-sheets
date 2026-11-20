"""Pure-Python lifecycle contracts and AST gate regression cases."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
try:
    from . import qgis_runtime as runtime
except ImportError:
    import qgis_runtime as runtime
from validation.check_qgis_initialization import violations


class RuntimeContracts(unittest.TestCase):
    def setUp(self):
        owned = patch.object(runtime, "_owned", None)
        owned.start()
        self.addCleanup(owned.stop)

    def setup_core(self, existing=None, desktop=False, providers=True, algorithms=True):
        calls = []
        class App:
            def __init__(self, *args):
                calls.append('create')
            @staticmethod
            def instance():
                return existing
            @staticmethod
            def processingRegistry():
                return SimpleNamespace(algorithms=lambda: [1] if algorithms else [])
            def initQgis(self):
                calls.append('init')
            def exitQgis(self):
                calls.append('exit')
        registry = SimpleNamespace(instance=lambda: SimpleNamespace(providerList=lambda: ['ogr'] if providers else []))
        core = SimpleNamespace(QgsApplication=App, QgsProviderRegistry=registry)
        iface = SimpleNamespace(mainWindow=lambda: object()) if desktop else None
        return core, iface, calls

    def test_desktop_borrows_without_any_lifecycle_calls(self):
        app = object()
        core, iface, calls = self.setup_core(app, desktop=True)
        with patch.object(runtime, '_runtime', return_value=(core, iface)):
            lease = runtime.acquire('desktop'); lease.close(); lease.close()
            self.assertIs(lease.app, app)
            self.assertFalse(lease.owned)
        self.assertEqual(calls, [])

    def test_unknown_instance_is_not_assumed_initialized(self):
        core, iface, calls = self.setup_core(object())
        with patch.object(runtime, '_runtime', return_value=(core, iface)):
            with self.assertRaisesRegex(RuntimeError, 'Unowned'):
                runtime.acquire()
        self.assertEqual(calls, [])

    def test_owned_standalone_exits_once(self):
        core, iface, calls = self.setup_core()
        with patch.object(runtime, '_runtime', return_value=(core, iface)), patch.object(runtime, '_processing') as processing:
            lease = runtime.acquire('standalone')
            self.assertEqual(lease.mode, 'standalone')
            processing.assert_called_once()
            lease.close(); lease.close()
            self.assertEqual(calls, ['create', 'init'])
            runtime._shutdown(); runtime._shutdown()
        self.assertEqual(calls, ['create', 'init', 'exit'])

    def test_failed_startup_releases_only_owned_app(self):
        core, iface, calls = self.setup_core()
        with patch.object(runtime, '_runtime', return_value=(core, iface)), patch.object(runtime, '_processing', side_effect=RuntimeError('processing missing')):
            with self.assertRaisesRegex(RuntimeError, 'processing missing'):
                runtime.acquire()
        self.assertEqual(calls, ['create', 'init', 'exit'])

    def test_desktop_missing_processing_fails_without_reinitialization(self):
        core, iface, calls = self.setup_core(object(), desktop=True, algorithms=False)
        with patch.object(runtime, '_runtime', return_value=(core, iface)):
            with self.assertRaisesRegex(RuntimeError, 'Processing'):
                runtime.acquire()
        self.assertEqual(calls, [])

    def test_environment_specific_tests_skip_wrong_environment(self):
        for existing, desktop, mode in [(object(), True, 'standalone'), (None, False, 'desktop')]:
            core, iface, calls = self.setup_core(existing, desktop=desktop)
            with patch.object(runtime, '_runtime', return_value=(core, iface)):
                with self.assertRaises(unittest.SkipTest):
                    runtime.acquire(mode)
            self.assertEqual(calls, [])

    def test_ast_rejects_direct_and_aliased_initialization(self):
        for code in ['from qgis.core import QgsApplication as App\na=App([],False)',
                     'from qgis import core\na=core.QgsApplication([],False)',
                     'a.initQgis()', 'a.exitQgis()']:
            with self.subTest(code=code):
                self.assertTrue(violations(code))

    def test_ast_accepts_comments_strings_and_registry_access(self):
        self.assertEqual(violations('# a.initQgis()\nx="QgsApplication([])"\nQgsApplication.processingRegistry()'), [])
