"""Reproductions of defects found while testing the input formats (E track).

Every test here asserts the *desired* behaviour and therefore FAILS today.
Not part of CI; see README.md.  Hang/abort cases run in a child process with a
timeout so a failing run never blocks the suite or kills the runner.
"""
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'dem_map_sheets' / 'tests'))
from input_fixtures import gsi_xml, have_gdal, make_geotiff, standard_lem, write_text  # noqa: E402

CHILD_TIMEOUT = 25

PRELUDE = '''
import sys
sys.path.insert(0, {root!r})
from pathlib import Path
from osgeo import gdal, osr
gdal.UseExceptions(); osr.UseExceptions()
from dem_map_sheets.engine.dem_pipeline import run
from dem_map_sheets.engine.input_sources import DEFAULTS, normalize
'''


def run_child(body, **fmt):
    """Run code in a child interpreter. Returns (returncode|'timeout', combined output)."""
    code = textwrap.dedent(PRELUDE).format(root=str(ROOT)) + textwrap.dedent(body).format(**fmt)
    try:
        done = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                              timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'timeout', ''
    return done.returncode, done.stdout + done.stderr


@unittest.skipUnless(have_gdal(), 'GDAL (osgeo) is not available')
class KnownDefects(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    # -- KD-1 ---------------------------------------------------------------
    def test_kd1_invalid_cell_size_is_rejected_cleanly(self):
        """cell_size <= 0 / NaN must raise ValueError, not hang or abort the process.

        Observed (GDAL 3.8.4): -5 -> gdal.Warp never returns; NaN -> std::length_error
        -> SIGABRT of the whole process (would take QGIS down).  The QGIS dialog limits
        CELL to 0.01..100, so only direct engine/API callers (FME, scripts) are exposed.
        """
        make_geotiff(self.root / 'a.tif')
        for value in ('-5.0', 'float("nan")', '0.0'):
            with self.subTest(cell_size=value):
                code, out = run_child('''
                    c = dict(DEFAULTS, input_type='raster', inputs=[{src!r}], input_crs='EPSG:6677',
                             target_crs='EPSG:6677', cell_size={value}, source_nodata=None,
                             max_pixels=25000000, normalize_rasters=True)
                    try:
                        run(c, Path({out!r}) / 'run_{n}', gdal, osr)
                        print('NO-ERROR')
                    except ValueError as exc:
                        print('REJECTED', exc)
                ''', src=str(self.root / 'a.tif'), out=str(self.root), value=value,
                    n=abs(hash(value)))
                self.assertEqual(code, 0, f'child did not exit cleanly ({code}): {out[-200:]}')
                self.assertIn('REJECTED', out)

    # -- KD-2 ---------------------------------------------------------------
    def test_kd2_run_validates_options(self):
        """validate_input() exists but nothing calls it; malformed options reach the converters."""
        write_text(self.root / 'g.csv', 'X,Y,Z\n0,0,1\n1,0,2\n')
        code, out = run_child('''
            c = dict(DEFAULTS, input_type='text', inputs=[{src!r}], input_crs='EPSG:6677',
                     target_crs='EPSG:6677', cell_size=1.0, source_nodata=None,
                     max_pixels=25000000, text_grid_cell=1.0, text_columns=[1, 1, 3])
            try:
                run(c, Path({out!r}) / 'run', gdal, osr)
                print('NO-ERROR')
            except ValueError as exc:
                print('REJECTED', exc)
        ''', src=str(self.root / 'g.csv'), out=str(self.root))
        self.assertIn('REJECTED', out, out[-300:])
        self.assertIn('text_columns', out)

    # -- KD-3 (M-2) ---------------------------------------------------------
    def test_kd3_text_grid_refuses_huge_extent_before_allocating(self):
        """Two far-apart points must be rejected by the pixel limit, not allocate a memmap."""
        from osgeo import gdal, osr
        from unittest.mock import patch
        from dem_map_sheets.engine import input_sources as I
        path = write_text(self.root / 'far.csv', 'X,Y,Z\n0,0,1\n10000,10000,2\n')  # 100 Mpx at 1 m
        c = {**I.DEFAULTS, 'input_type': 'text', 'input_crs': 'EPSG:6677', 'target_crs': 'EPSG:6677',
             'cell_size': 1.0, 'source_nodata': None, 'text_grid_cell': 1.0,
             'max_input_pixels': 25_000_000}
        with patch.object(I.np, 'memmap', side_effect=AssertionError('memmap allocated')):
            with self.assertRaisesRegex(ValueError, 'pixel limit'):
                I.text_grid(path, self.root / 'far.tif', c, gdal, osr)

    # -- KD-4 ---------------------------------------------------------------
    def test_kd4_lem_non_positive_spacing_is_rejected_with_a_clear_message(self):
        from dem_map_sheets.engine.forest_dem import read_lem
        for dx in ('0.0', '-5.0'):
            with self.subTest(dx=dx):
                lem, meta = standard_lem(self.root, f'dx{dx}', dx=float(dx))
                # explicit scale bypasses the bounds/spacing consistency check of 'auto'
                with self.assertRaisesRegex(ValueError, r'spacing|interval|positive'):
                    read_lem(lem, meta, 10 ** 9, coordinate_scale='0.01')

    # -- KD-5 ---------------------------------------------------------------
    def test_kd5_tiny_raster_finer_than_output_cell_does_not_hang(self):
        """A 5x5 px, 1 m raster straddling a 10 m output-cell boundary hangs gdal.Warp (3.8.4)."""
        make_geotiff(self.root / 'tiny.tif', __import__('numpy').full((5, 5), 5, 'float32'),
                     gt=(97.0, 1.0, 0.0, 102.0, 0.0, -1.0), epsg=6677)
        code, out = run_child('''
            c = dict(DEFAULTS, input_crs='EPSG:6677', target_crs='EPSG:6677', cell_size=10.0,
                     source_nodata=None)
            normalize({src!r}, Path({out!r}) / 'o.tif', c, gdal, osr)
            print('RETURNED')
        ''', src=str(self.root / 'tiny.tif'), out=str(self.root))
        self.assertEqual(code, 0, 'normalize() did not return within %ss' % CHILD_TIMEOUT)

    # -- KD-6 ---------------------------------------------------------------
    def test_kd6_gsi_bad_number_message_names_the_tuple(self):
        from dem_map_sheets.engine.gsi_dem import parse_dem
        xml = gsi_xml('a,xyz\na,1\na,1\na,1').encode()
        with self.assertRaises(ValueError) as ctx:
            list(parse_dem(xml))
        self.assertIn('xyz', str(ctx.exception))
        self.assertRegex(str(ctx.exception), r'GSI|tuple')


if __name__ == '__main__':
    unittest.main()
