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


    def test_cross_crs_reprojection_lands_where_gdal_expects(self):
        # E-1 probe: EPSG:6677 -> EPSG:6678 (adjacent plane zone).
        # If PDAL applied the axis order differently from GDAL, the
        # points would land far away and the crop would yield no
        # valid cells near the GDAL-computed destination bounds.
        from dem_map_sheets.engine.input_sources import crs_of
        from dem_map_sheets.engine.point_inputs import transform_bounds
        work = self.root / 'work_cross'
        work.mkdir()
        cfg = self._cfg(input_type='lidar', inputs=[str(self.las)],
                        target_crs='EPSG:6678')
        outputs, _meta = points_to_rasters(cfg, work, gdal, osr, None)
        self.assertEqual(len(outputs), 1)
        expected = transform_bounds((0.0, 0.0, 30.0, 30.0),
                                    crs_of('EPSG:6677', osr),
                                    crs_of('EPSG:6678', osr), osr)
        ds = gdal.Open(outputs[0])
        gt = ds.GetGeoTransform()
        arr = ds.GetRasterBand(1).ReadAsArray()
        ds = None
        west, north = gt[0], gt[3]
        east, south = west + gt[1] * arr.shape[1], north + gt[5] * arr.shape[0]
        cx = (expected[0] + expected[2]) / 2
        cy = (expected[1] + expected[3]) / 2
        self.assertTrue(west - 1000 < cx < east + 1000, (west, east, cx))
        self.assertTrue(south - 1000 < cy < north + 1000, (south, north, cy))
        self.assertGreater((arr != -999999.0).sum(), 0)


    def test_geographic_crs_input_axis_order(self):
        # Task 4 probe: EPSG:4326 (lat/lon authority order) LAS input.
        # If PDAL honoured the authority axis order instead of the
        # GIS order the plugin assumes, the Tokyo points would land
        # near (lat=139.7) - invalid or thousands of km away - and
        # no valid block would appear near the GDAL-expected bounds.
        from dem_map_sheets.engine.input_sources import crs_of
        from dem_map_sheets.engine.point_inputs import transform_bounds
        csv = self.root / 'tokyo.csv'
        rows = ['X,Y,Z,Classification']
        for j, lat in enumerate((35.60, 35.61, 35.62, 35.63)):
            for i, lon in enumerate((139.70, 139.71, 139.72, 139.73)):
                rows.append(f'{lon},{lat},{100 + i + j},2')
        csv.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        las = self.root / 'tokyo.las'
        pipeline = {'pipeline': [
            {'type': 'readers.text', 'filename': str(csv)},
            {'type': 'writers.las', 'filename': str(las), 'a_srs': 'EPSG:4326',
             'scale_x': 0.0000001, 'scale_y': 0.0000001, 'scale_z': 0.001}]}
        spec = self.root / 'tokyo.json'
        spec.write_text(json.dumps(pipeline), encoding='utf-8')
        result = subprocess.run([PDAL, 'pipeline', str(spec)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[:300])
        work = self.root / 'work_geo'
        work.mkdir()
        cfg = self._cfg(input_type='lidar', inputs=[str(las)],
                        input_crs='EPSG:4326', target_crs='EPSG:6677',
                        cell_size=50.0, point_radius=200.0)
        outputs, _meta = points_to_rasters(cfg, work, gdal, osr, None)
        self.assertGreaterEqual(len(outputs), 1)
        expected = transform_bounds((139.70, 35.60, 139.73, 35.63),
                                    crs_of('EPSG:4326', osr),
                                    crs_of('EPSG:6677', osr), osr)
        total_valid = 0
        centres_inside = False
        for path in outputs:
            ds = gdal.Open(path)
            gt = ds.GetGeoTransform()
            arr = ds.GetRasterBand(1).ReadAsArray()
            ds = None
            total_valid += int((arr != -999999.0).sum())
            west, north = gt[0], gt[3]
            east = west + gt[1] * arr.shape[1]
            south = north + gt[5] * arr.shape[0]
            cx = (expected[0] + expected[2]) / 2
            cy = (expected[1] + expected[3]) / 2
            if west - 1000 < cx < east + 1000 and south - 1000 < cy < north + 1000:
                centres_inside = True
        self.assertTrue(centres_inside, (expected, outputs))
        self.assertGreater(total_valid, 0)


if __name__ == '__main__':
    unittest.main()
