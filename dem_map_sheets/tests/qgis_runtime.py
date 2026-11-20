"""Explicit QGIS test ownership; importing this module never starts QGIS."""
import atexit
import sys
from pathlib import Path
import unittest

_owned = None


def _runtime():
    try:
        from qgis import core, utils
    except ImportError as exc:
        raise unittest.SkipTest(f'QGIS Python unavailable: {exc}') from exc
    return core, getattr(utils, 'iface', None)


def _processing():
    from qgis.core import QgsApplication
    plugins = str(Path(QgsApplication.pkgDataPath()) / 'python' / 'plugins')
    if plugins not in sys.path:
        sys.path.append(plugins)
    from processing.core.Processing import Processing
    Processing.initialize()


class Lease:
    def __init__(self, app, owned, mode):
        self.app, self.owned, self.mode = app, owned, mode

    def close(self):
        # QGIS exitQgis unregisters GDAL drivers shared by later tests.
        # Release the lease now; end the owned process session only at exit.
        self.owned = False


def _shutdown():
    global _owned
    if _owned is not None:
        _owned.exitQgis()
        _owned = None


atexit.register(_shutdown)


def acquire(mode='auto'):
    """Use Desktop without mutation, or own a fresh standalone application.

    An unknown pre-existing instance is rejected, never guessed initialized.
    Call from setUpClass/fixture, not at module import. Close only this lease.
    """
    global _owned
    if mode not in ('auto', 'desktop', 'standalone'):
        raise ValueError('Unknown QGIS test mode: ' + str(mode))
    core, iface = _runtime()
    app = core.QgsApplication.instance()
    desktop = iface is not None and iface.mainWindow() is not None
    if desktop:
        if mode == 'standalone':
            raise unittest.SkipTest('Standalone-only test: use an external QGIS Python process')
        if app is None or not core.QgsProviderRegistry.instance().providerList():
            raise RuntimeError('Desktop providers are unavailable; no initialization attempted')
        if not core.QgsApplication.processingRegistry().algorithms():
            raise RuntimeError('Desktop Processing is unavailable; enable it before testing')
        return Lease(app, False, 'desktop')
    if mode == 'desktop':
        raise unittest.SkipTest('Desktop-only test: run inside QGIS Desktop')
    if app is not None and app is _owned:
        return Lease(app, True, 'standalone')
    if app is not None:
        raise RuntimeError('Unowned QGIS/Qt instance: use a fresh standalone process or QGIS Desktop')
    app = core.QgsApplication([], False)
    _owned = app
    try:
        app.initQgis()
        _processing()
        if not core.QgsProviderRegistry.instance().providerList():
            raise RuntimeError('Standalone QGIS providers are unavailable')
    except BaseException:
        app.exitQgis()
        _owned = None
        raise
    return Lease(app, True, 'standalone')
