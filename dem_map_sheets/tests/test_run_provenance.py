"""run.json provenance (coordinator request 2026-11-10): the record
carries the datum label the inputs themselves declared and the EPSG
dataset version of the PROJ data in effect, plus the same
work_retained vocabulary as the failure record. Path failures get a
Japanese message naming the failing path."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import _path_error, _source_datum_labels, run
from dem_map_sheets.engine.input_sources import DEFAULTS, epsg_dataset_version, proj_epsg_version


def _make_proj_db(folder, version='v99.999'):
    db = Path(folder) / 'proj.db'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE metadata (key TEXT, value TEXT)')
    if version is not None:
        con.execute('INSERT INTO metadata VALUES (?, ?)', ('EPSG.VERSION', version))
    con.commit()
    con.close()
    return db


class EpsgDatasetVersionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_reads_epsg_version_from_metadata(self):
        db = _make_proj_db(self.root)
        self.assertEqual(epsg_dataset_version(db), 'v99.999')

    def test_missing_row_is_none(self):
        db = _make_proj_db(self.root, version=None)
        self.assertIsNone(epsg_dataset_version(db))

    def test_missing_file_is_none(self):
        self.assertIsNone(epsg_dataset_version(self.root / 'nope.db'))

    def test_proj_epsg_version_uses_search_paths(self):
        _make_proj_db(self.root)

        class _Osr:
            def GetPROJSearchPaths(self):
                return [str(self_root)]

        self_root = self.root
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(proj_epsg_version(_Osr()), 'v99.999')

    def test_proj_epsg_version_unavailable_is_none(self):
        class _Osr:
            pass

        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertIsNone(proj_epsg_version(_Osr()))


class DatumLabelTests(unittest.TestCase):
    def test_collects_original_srs_from_nested_report(self):
        report = {'sources': [{'path': 'a', 'original_srs': 'fguuid:jgd2011.bl'},
                              {'path': 'b', 'original_srs': 'fguuid:jgd2011.bl'}],
                  'groups': [[{'original_srs': 'fguuid:jgd2024.bl'}]]}
        self.assertEqual(_source_datum_labels(report),
                         {'fguuid:jgd2011.bl', 'fguuid:jgd2024.bl'})

    def test_no_labels(self):
        self.assertEqual(_source_datum_labels({'sources': [{'path': 'a'}]}), set())


class PathErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / 'out'

    def _oserror(self, errno_, filename):
        exc = OSError(errno_, 'os message')
        exc.filename = filename
        return exc

    def test_output_side_long_path(self):
        target = str(self.out / ('x' * 250))
        message = _path_error(self._oserror(3, target), self.out)
        self.assertIn('出力先', message)
        self.assertIn(target, message)
        self.assertIn('短いパス', message)

    def test_input_side(self):
        message = _path_error(self._oserror(206, 'C:/inputs/dem.tif'), self.out)
        self.assertIn('入力側', message)

    def test_ancestor_of_output_is_output_side(self):
        # Creating a long output path fails on an intermediate
        # folder (an ancestor of the result dir) first; that is an
        # output-side failure, not an input one (D-1).
        out = self.out / 'demmap_result'
        exc = self._oserror(3, str(self.out))
        self.assertIn('（出力先）', _path_error(exc, out))

    def test_extended_length_prefix_is_output_side(self):
        # GDAL/Windows may report the failing path with an
        # extended-length prefix the output dir does not carry.
        exc = self._oserror(206, chr(92) * 2 + '?' + chr(92) + str(self.out / '02_merged_dem.tif'))
        self.assertIn('（出力先）', _path_error(exc, self.out))

    def test_other_errno_is_not_translated(self):
        self.assertIsNone(_path_error(self._oserror(13, str(self.out / 'x')), self.out))

    def test_no_filename_is_not_translated(self):
        self.assertIsNone(_path_error(OSError(3, 'os message'), self.out))


def _small_tif(path):
    ds = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((0.0, 1.0, 0.0, 5.0, 0.0, -1.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(6677)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)
    band.WriteArray(np.full((5, 5), 100.0, dtype='float32'))
    ds = None
    return path


class ManifestProvenanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.src = _small_tif(self.root / 'small.tif')

    def _cfg(self, **kw):
        c = dict(DEFAULTS)
        c.update(input_type='raster', inputs=[str(self.src)], input_crs='EPSG:6677',
                 target_crs='EPSG:6677', cell_size=2.0, source_nodata=-9999.0,
                 max_pixels=10 ** 9)
        c.update(kw)
        return c

    def test_success_manifest_carries_provenance_and_work_state(self):
        manifest = run(self._cfg(), self.root / 'out', gdal, osr, None)
        self.assertEqual(manifest['work_retained'], 'none')
        self.assertIn('source_datum_label', manifest)
        self.assertIsNone(manifest['source_datum_label'],
                          'a plain GeoTIFF declares no datum label')
        self.assertIn('proj_epsg_version', manifest)
        self.assertTrue(manifest['proj_epsg_version'] is None
                        or manifest['proj_epsg_version'].startswith('v'))

    def test_keep_work_marks_work_retained_all(self):
        out = self.root / 'out_keep'
        manifest = run(self._cfg(keep_work=True), out, gdal, osr, None)
        self.assertEqual(manifest['work_retained'], 'all')
        self.assertTrue((out / 'work').is_dir())


if __name__ == '__main__':
    unittest.main()
