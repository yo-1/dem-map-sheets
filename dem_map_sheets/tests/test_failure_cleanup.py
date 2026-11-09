"""M-1 run-level regressions: dem_pipeline.run() failure handling.

Covers prune_work_after_failure (allowlist pruning), the failure
record written to run.json, keep_work behaviour, and merge_geotiffs
removing a partial destination on failure. The run() success case
drives a fake PDAL executable (mocked; no real PDAL involved).
"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import (
    merge_geotiffs, prune_work_after_failure, run)

FAKE_PDAL = r'''
import json, sys
from pathlib import Path

def main():
    args = sys.argv[1:]
    if args and args[0] == '--version':
        print('pdal 2.9.0 (fake)')
        return 0
    if args and args[0] == 'pipeline':
        spec = json.loads(Path(args[1]).read_text())
        writer = next(s for s in spec['pipeline'] if s.get('type') == 'writers.gdal')
        from osgeo import gdal
        import numpy as np
        w = int(writer['width']); h = int(writer['height']); res = float(writer['resolution'])
        ds = gdal.GetDriverByName('GTiff').Create(writer['filename'], w, h, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((float(writer['origin_x']), res, 0,
                            float(writer['origin_y']) + h * res, 0, -res))
        ds.GetRasterBand(1).WriteArray(np.full((h, w), 100.0, dtype='float32'))
        ds.FlushCache(); ds = None
        return 0
    return 2

sys.exit(main())
'''


class _Feedback:
    def isCanceled(self):
        return False

    def pushInfo(self, text):
        pass

    def pushWarning(self, text):
        pass

    def setProgress(self, value):
        pass


class PruneTests(unittest.TestCase):
    def test_prune_keeps_only_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / 'work'
            (work / 'sub').mkdir(parents=True)
            (work / 'big.tif').write_bytes(b'x' * 100000)
            (work / 'mosaic.vrt').write_text('<VRTDataset/>')
            (work / 'grid.grid.bin').write_bytes(b'x' * 50000)
            (work / 'sub' / 'block.tif').write_bytes(b'x' * 10000)
            (work / 'run.log').write_text('log')
            (work / 'run.log.stderr').write_text('err')
            (work / 'pipeline.json').write_text('{}')
            (work / 'version.txt').write_text('2.9.0')
            removed, retained = prune_work_after_failure(work)
            self.assertGreaterEqual(removed, 160000)
            self.assertGreater(retained, 0)
            remaining = sorted(str(p.relative_to(work)) for p in work.rglob('*') if p.is_file())
            self.assertEqual(remaining, ['pipeline.json', 'run.log',
                                         'run.log.stderr', 'version.txt'])
            self.assertFalse((work / 'sub').exists())

    def test_prune_missing_directory_is_zero(self):
        self.assertEqual(prune_work_after_failure(Path('/nonexistent/nope')), (0, 0))


@unittest.skipIf(os.name == 'nt', 'fake PDAL executable is a POSIX script')
class RunFailureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.csv = root / 'pts.csv'
        self.csv.write_text('X,Y,Z\n0,0,100\n10,0,101\n0,10,102\n10,10,103\n', encoding='utf-8')
        fake = root / 'fake_pdal'
        fake.write_text('#!/usr/bin/env python3\n' + FAKE_PDAL, encoding='utf-8')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.fake = str(fake)

    def _cfg(self, **kw):
        c = dict(input_type='text', text_mode='points', inputs=[str(self.csv)],
                 input_crs='EPSG:6677', target_crs='EPSG:6677', cell_size=10.0,
                 point_tile_size=4, max_pixels=10 ** 9, source_nodata=None,
                 pdal_path=self.fake, merge_priority='last')
        c.update(kw)
        return c

    def test_failure_records_and_prunes(self):
        out = Path(self._tmp.name) / 'out_fail'
        with self.assertRaises(ValueError):
            run(self._cfg(pdal_path='/nonexistent/pdal'), out, gdal, osr, _Feedback())
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['work_retained'], 'diagnostics_only')
        work = out / 'work'
        self.assertTrue(work.is_dir())
        for p in work.rglob('*'):
            if p.is_file():
                self.assertTrue(p.name.endswith(('.log', '.stderr', '.stdout', '.json', '.txt')), p.name)

    def test_failure_with_keep_work_retains_everything(self):
        out = Path(self._tmp.name) / 'out_keep'
        with self.assertRaises(ValueError):
            run(self._cfg(pdal_path='/nonexistent/pdal', keep_work=True),
                out, gdal, osr, _Feedback())
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['work_retained'], 'all')
        self.assertEqual(record['work_removed_bytes'], 0)
        self.assertTrue((out / 'work' / 'input_report.json').is_file())

    def test_success_removes_work_and_records_completed(self):
        out = Path(self._tmp.name) / 'out_ok'
        manifest = run(self._cfg(), out, gdal, osr, _Feedback())
        self.assertEqual(manifest['status'], 'completed')
        self.assertFalse((out / 'work').exists())
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'completed')


class MergeFailureTests(unittest.TestCase):
    def test_failed_merge_removes_partial_destination(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'merged.tif'
            dest.write_bytes(b'partial junk')
            with self.assertRaises(RuntimeError):
                merge_geotiffs([str(Path(td) / 'missing.tif')], dest, gdal)
            self.assertFalse(dest.exists())
            self.assertFalse(Path(str(dest).replace('.tif', '.vrt')).exists())


if __name__ == '__main__':
    unittest.main()
