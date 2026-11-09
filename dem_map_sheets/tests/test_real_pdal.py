"""Real-PDAL verification (opt-in; skipped in normal CI).

Set REAL_PDAL_PATH to a pdal executable (>= 2.9) to enable. The LAS
input is generated with that same PDAL (readers.text -> writers.las),
so the whole points_to_rasters path - version check, info --summary,
SMRF-free classified filtering, writers.gdal rasterisation - runs
against the real binary, unlike the mocked tests. Verified
2026-11-09 with PDAL 2.10.2 / GDAL 3.13.3 from
environment-pointcloud.yml.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from osgeo import gdal, osr

from dem_map_sheets.engine.input_sources import DEFAULTS
from dem_map_sheets.engine.point_inputs import points_to_rasters

PDAL = os.environ.get('REAL_PDAL_PATH', '')


def _make_las(pdal, csv_path, las_path):
    pipeline = {'pipeline': [
        {'type': 'readers.text', 'filename': str(csv_path)},
        {'type': 'writers.las', 'filename': str(las_path),
         'scale_x': 0.001, 'scale_y': 0.001, 'scale_z': 0.001}]}
    spec = Path(las_path).with_suffix('.json')
    spec.write_text(json.dumps(pipeline), encoding='utf-8')
    result = subprocess.run([pdal, 'pipeline', str(spec)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError('LAS generation failed: ' + result.stderr[:400])


@unittest.skipUnless(PDAL, 'REAL_PDAL_PATH is not set')
class RealPdalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        csv = root / 'points.csv'
        rows = ['X,Y,Z,Classification']
        for y in (0, 10, 20, 30):
            for x in (0, 10, 20, 30):
                rows.append(f'{x},{y},{100 + x // 10 + y // 10},2')
        csv.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        self.las = root / 'points.las'
        _make_las(PDAL, csv, self.las)
        xyz = root / 'points_xyz.csv'
        xyz.write_text('X,Y,Z\n' + '\n'.join(r.rsplit(',', 1)[0] for r in rows[1:]) + '\n',
                       encoding='utf-8')
        self.xyz = xyz
        self.root = root

    def _cfg(self, **kw):
        c = dict(DEFAULTS)
        # point_radius must cover the 10 m point spacing at 10 m cells
        # (the production default 2.0 targets ~1 m cells / dense lidar).
        c.update(input_crs='EPSG:6677', target_crs='EPSG:6677', cell_size=10.0,
                 point_tile_size=4, max_pixels=10 ** 9, source_nodata=None,
                 point_radius=15.0, pdal_path=PDAL)
        c.update(kw)
        return c

    def _check_block(self, outputs):
        self.assertEqual(len(outputs), 1)
        ds = gdal.Open(outputs[0])
        self.assertIsNotNone(ds)
        arr = ds.GetRasterBand(1).ReadAsArray()
        ds = None
        valid = arr[arr != -999999.0]
        self.assertGreaterEqual(valid.size, 10)
        # corner cell centre (5, 35) interpolates the z=103/104 points
        self.assertTrue(102.0 < float(arr[0, 0]) < 105.5, arr[0, 0])

    def test_lidar_classified_with_real_pdal(self):
        work = self.root / 'work_lidar'
        work.mkdir()
        outputs, meta = points_to_rasters(
            self._cfg(input_type='lidar', inputs=[str(self.las)]),
            work, gdal, osr, None)
        self._check_block(outputs)
        self.assertEqual(meta['sources'][0]['count'], 16)

    def test_text_points_with_real_pdal(self):
        work = self.root / 'work_text'
        work.mkdir()
        outputs, _meta = points_to_rasters(
            self._cfg(input_type='text', text_mode='points',
                      inputs=[str(self.xyz)]),
            work, gdal, osr, None)
        self._check_block(outputs)


if __name__ == '__main__':
    unittest.main()
