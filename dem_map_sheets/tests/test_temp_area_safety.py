"""TEMP work-area safety (final pre-release fix): the ASCII PDAL
area must come only from user-private or explicitly designated
locations. Shared scratch locations are never auto-created or
silently adopted, and no probe directories are left behind."""
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dem_map_sheets.engine import point_inputs


class TempAreaPolicyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_no_shared_locations_in_module(self):
        source = Path(point_inputs.__file__).read_text(encoding='utf-8')
        self.assertNotIn('ProgramData', source)
        self.assertNotIn('C:' + chr(92) + 'Temp', source)

    def test_explicit_env_wins_over_default_temp(self):
        env_dir = self.root / 'designated'
        env_dir.mkdir()
        with mock.patch.dict(os.environ, {'DEM_MAP_SHEETS_TEMP': str(env_dir)}):
            area = point_inputs._ascii_temp_area()
        self.addCleanup(lambda: shutil.rmtree(area, ignore_errors=True))
        self.assertEqual(area.parent, env_dir)
        self.assertTrue(str(area).isascii())

    def test_two_areas_never_collide(self):
        first = point_inputs._ascii_temp_area()
        second = point_inputs._ascii_temp_area()
        for area in (first, second):
            self.addCleanup(lambda a=area: shutil.rmtree(a, ignore_errors=True))
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir())

    @unittest.skipIf(os.name == 'nt', 'POSIX permission bits')
    def test_area_is_owner_only(self):
        area = point_inputs._ascii_temp_area()
        self.addCleanup(lambda: shutil.rmtree(area, ignore_errors=True))
        mode = stat.S_IMODE(area.stat().st_mode)
        self.assertEqual(mode & 0o077, 0, 'temp area must not be group/other accessible')


class CancelMessageTests(unittest.TestCase):
    """Cancel notifications keep the English technical message and
    carry the Japanese warning not to use partial outputs. Cancel
    behaviour itself (child termination, cleanup) is unchanged."""

    def test_prepare_cancel_message_is_bilingual(self):
        from dem_map_sheets.engine.input_sources import cancelled

        class _Feedback:
            def isCanceled(self):
                return True

        with self.assertRaises(RuntimeError) as ctx:
            cancelled(_Feedback())
        self.assertIn('Input conversion cancelled', str(ctx.exception))
        self.assertIn('処理がキャンセルされました', str(ctx.exception))
        self.assertIn('使用しないでください', str(ctx.exception))

    def test_run_cancel_message_is_bilingual(self):
        import numpy as np
        from osgeo import gdal, osr
        from dem_map_sheets.engine.dem_pipeline import run
        from dem_map_sheets.engine.input_sources import DEFAULTS

        class _Feedback:
            def isCanceled(self):
                return True

            def setProgress(self, _v):
                pass

            def pushInfo(self, _m):
                pass

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / 'tiny.tif'
            ds = gdal.GetDriverByName('GTiff').Create(str(src), 5, 5, 1, gdal.GDT_Float32)
            ds.SetGeoTransform((0.0, 1.0, 0.0, 5.0, 0.0, -1.0))
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(6677)
            ds.SetProjection(srs.ExportToWkt())
            band = ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)
            band.WriteArray(np.full((5, 5), 100.0, dtype='float32'))
            ds = None
            c = dict(DEFAULTS)
            c.update(input_type='raster', inputs=[str(src)], input_crs='EPSG:6677',
                     target_crs='EPSG:6677', cell_size=2.0, source_nodata=-9999.0,
                     max_pixels=10 ** 9)
            with self.assertRaises(RuntimeError) as ctx:
                run(c, root / 'out', gdal, osr, _Feedback())
            self.assertIn('使用しないでください', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
