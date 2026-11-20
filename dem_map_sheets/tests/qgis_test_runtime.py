"""QGIS test runtime lifecycle; never initialize QGIS during module import."""
from contextlib import contextmanager


@contextmanager
def qgis_test_application():
    """Yield a usable QGIS application, owning only standalone instances.

    A running QGIS Desktop already owns its application. In that case, do
    not call initQgis or exitQgis. The caller must import qgis.core first.
    """
    from qgis.core import QgsApplication

    existing = QgsApplication.instance()
    owned = existing is None
    app = QgsApplication([], False) if owned else existing
    if owned:
        app.initQgis()
    try:
        yield app
    finally:
        if owned:
            app.exitQgis()
