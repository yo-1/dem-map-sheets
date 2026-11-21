"""QGIS test runtime lifecycle: one initialization per standalone process.

Do not shut down a QGIS singleton between test suites: in QGIS 3.44 an
exited QgsApplication may remain visible to instance(), but is unusable.
"""
from contextlib import contextmanager

_owned_app = None
_init_failed = False


@contextmanager
def qgis_test_application():
    """Reuse Desktop QGIS, or keep a standalone QGIS app alive until process exit.

    This intentionally never calls exitQgis. Running another suite in the
    same process must not encounter a stale, exited singleton.
    """
    global _owned_app, _init_failed
    if _init_failed:
        raise RuntimeError(
            "QGIS initialization previously failed in this process; "
            "restart the QGIS Python process"
        )
    from qgis.core import QgsApplication

    existing = QgsApplication.instance()
    if _owned_app is not None:
        if existing is not _owned_app:
            raise RuntimeError(
                "QGIS application singleton changed during this test process; "
                "run the next suite in a fresh QGIS Python process"
            )
        app = _owned_app
    elif existing is not None:
        # A pre-existing QGIS Desktop application is owned by the host.
        app = existing
    else:
        app = QgsApplication([], False)
        # Keep a strong reference even if initQgis raises; do not mistake
        # a partially initialized singleton for an externally owned app.
        _owned_app = app
        try:
            app.initQgis()
        except BaseException:
            _init_failed = True
            raise
    yield app
