"""Per-format input tests that need the real GDAL/OSR (skipped when osgeo is missing).

Exercises engine.input_sources.prepare_inputs / dem_pipeline.run end to end on
synthetic files for:  GeoTIFF, TIFF+world file, regular XYZ/CSV/TXT grids,
forestry LEM(+ZIP), GSI DEM XML(+ZIP).  LAS/LAZ (needs PDAL) is in
test_input_lidar_mocked.py.

Real GDAL is used on purpose: CRS recognition, world-file discovery, NoData
mapping and warping are GDAL behaviours that a mock cannot vouch for.
Verified GDAL in CI: 3.8.4 (ubuntu-24.04 python3-gdal).  Not verified: GDAL >= 3.10
(the planned conda environment), QGIS-bundled GDAL, Windows.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_fixtures import (  # noqa: E402
    gsi_xml, have_gdal, lem_row, make_geotiff, standard_lem, write_lem, write_text,
    write_world_file, write_zip)

HAVE_GDAL = have_gdal()
if HAVE_GDAL:
    from osgeo import gdal, osr
    from dem_map_sheets.engine.dem_pipeline import run
    from dem_map_sheets.engine.input_sources import (
        DEFAULTS, DEFAULT_OUTPUT_NODATA, NODATA, crs_of, has_world_file,
        prepare_inputs, text_grid)

# GDAL raises RuntimeError (UseExceptions) or the plugin raises ValueError;
# the plugin enables UseExceptions in algorithm.py, so both are legitimate.
CLEAR_ERRORS = (ValueError, RuntimeError)


@unittest.skipUnless(HAVE_GDAL, 'GDAL (osgeo) is not available')
class GdalCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gdal.UseExceptions()
        osr.UseExceptions()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._n = 0

    def config(self, input_type, inputs, **kw):
        c = {**DEFAULTS, 'input_type': input_type, 'inputs': [str(p) for p in inputs],
             'input_crs': 'EPSG:6677', 'target_crs': 'EPSG:6677', 'cell_size': 10.0,
             'source_nodata': None, 'max_pixels': 25_000_000, 'normalize_rasters': True}
        c.update(kw)
        return c

    def prepare(self, input_type, inputs, **kw):
        """Run prepare_inputs in a fresh work dir; return (prepared_config, report, workdir)."""
        self._n += 1
        work = self.root / f'work{self._n}'
        prepared, report = prepare_inputs(self.config(input_type, inputs, **kw), work, gdal, osr)
        return prepared, report, work

    def read(self, path):
        ds = gdal.Open(str(path))
        array, gt = ds.ReadAsArray(), ds.GetGeoTransform()
        nodata = ds.GetRasterBand(1).GetNoDataValue()
        srs = ds.GetSpatialRef()
        ds = None
        return array, gt, nodata, srs

    def prepare_first(self, input_type, inputs, **kw):
        prepared, report, work = self.prepare(input_type, inputs, **kw)
        return (*self.read(prepared['inputs'][0]), report)

    def assertNoData(self, array, mask):
        np.testing.assert_array_equal(array == NODATA, mask)

    def assertOutputNoData(self, array, mask):
        # Prepared (normalized) rasters carry the pipeline's output
        # NoData (the default below), not the internal sentinel.
        np.testing.assert_array_equal(array == DEFAULT_OUTPUT_NODATA, mask)


# ------------------------------------------------------------------ CRS handling

class CrsRecognitionTests(GdalCase):
    def test_accepted_horizontal_crs(self):
        # Assert authority codes and structure, not display names:
        # the EPSG database renames datums between versions (the
        # JGD2011 -> JGD2024 label change on GDAL 3.13 broke exact
        # name equality while the CRS codes stayed the same).
        for text, code, projected in [('EPSG:6677', '6677', True),
                                      ('EPSG:6668', '6668', False),
                                      ('EPSG:4326', '4326', False),
                                      ('EPSG:2451', '2451', True)]:
            with self.subTest(text):
                crs = crs_of(text, osr)
                self.assertEqual(crs.GetAxesCount(), 2)
                self.assertEqual(crs.GetAuthorityCode(None), code)
                self.assertEqual(bool(crs.IsProjected()), projected)

    def test_axis_mapping_is_x_east_y_north_even_for_authority_axis_order_crs(self):
        # EPSG:6677 and EPSG:4326 are northing/latitude-first in the registry.
        crs = crs_of('EPSG:6677', osr)
        self.assertEqual(crs.GetAxisMappingStrategy(), osr.OAMS_TRADITIONAL_GIS_ORDER)
        wgs = crs_of('EPSG:4326', osr)
        tx = osr.CoordinateTransformation(wgs, crs_of('EPSG:6668', osr))
        x, y, _ = tx.TransformPoint(139.7, 35.7)
        self.assertAlmostEqual(x, 139.7, places=4)
        self.assertAlmostEqual(y, 35.7, places=4)

    def test_rejected_crs(self):
        for label, text in {'empty': '', 'unknown code': 'EPSG:99999', 'garbage': 'not a crs',
                            'compound': 'EPSG:6677+6695', 'vertical only': 'EPSG:6695',
                            '3d geographic': 'EPSG:4979'}.items():
            with self.subTest(label):
                with self.assertRaises(CLEAR_ERRORS):
                    crs_of(text, osr)

    def test_crs_message_names_the_problem(self):
        with self.assertRaisesRegex(ValueError, 'two-dimensional horizontal'):
            crs_of('EPSG:6677+6695', osr)
        with self.assertRaisesRegex(ValueError, 'Invalid/missing input CRS'):
            crs_of('', osr)


# ------------------------------------------------------------------ GeoTIFF / generic raster

class GeoTiffTests(GdalCase):
    def test_geotiff_values_nodata_and_georeference_are_preserved(self):
        values = np.arange(12, dtype='float32').reshape(3, 4)
        values[1, 2] = -9999.0
        tif = make_geotiff(self.root / 'a.tif', values)
        array, gt, nodata, srs, _ = self.prepare_first('raster', [tif], input_crs='')
        self.assertEqual(gt, (1000.0, 10.0, 0.0, 2030.0, 0.0, -10.0))
        self.assertEqual(nodata, DEFAULT_OUTPUT_NODATA)
        self.assertEqual(srs.GetAuthorityCode(None), '6677')
        expected = values.copy()
        expected[1, 2] = DEFAULT_OUTPUT_NODATA
        np.testing.assert_array_equal(array, expected)

    def test_source_nodata_override_for_rasters_without_a_definition(self):
        values = np.array([[1, -32768, 3, 4]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'n.tif', values, nodata=None)
        with self.assertRaisesRegex(ValueError, 'no NoData definition'):
            self.prepare('raster', [tif], input_crs='')
        array, *_ = self.prepare_first('raster', [tif], input_crs='', source_nodata=-32768.0)
        self.assertOutputNoData(array, values == -32768)

    def test_nonfinite_pixels_never_become_numeric_elevations(self):
        values = np.array([[1, np.nan, np.inf, 4]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'nf.tif', values)
        array, *_ = self.prepare_first('raster', [tif], input_crs='')
        bad = array[:, 1:3]
        # NaN and +/-inf are not elevations: both become the output
        # NoData value exactly, never a surviving non-finite number.
        self.assertTrue(np.all(bad == DEFAULT_OUTPUT_NODATA))

    def test_geotiff_without_crs_needs_input_crs(self):
        tif = make_geotiff(self.root / 'nocrs.tif', epsg=None)
        with self.assertRaises(CLEAR_ERRORS):
            self.prepare('raster', [tif], input_crs='')
        array, gt, *_ = self.prepare_first('raster', [tif], input_crs='EPSG:6677')
        self.assertEqual(gt[0], 1000.0)

    def test_invalid_or_compound_input_crs(self):
        tif = make_geotiff(self.root / 'a.tif')
        for crs in ('EPSG:99999', 'junk', 'EPSG:6677+6695'):
            with self.subTest(crs):
                with self.assertRaises(CLEAR_ERRORS):
                    self.prepare('raster', [tif], input_crs=crs)

    def test_reprojection_lands_where_an_independent_transform_says(self):
        lon, lat = 139.70, 35.70
        res = 0.0001
        tif = make_geotiff(self.root / 'geo.tif', np.full((4, 4), 7.0, 'float32'),
                           gt=(lon, res, 0, lat, 0, -res), epsg=6668)
        _, gt, _, srs, _ = self.prepare_first('raster', [tif], input_crs='', target_crs='EPSG:6677',
                                              cell_size=1.0)
        tx = osr.CoordinateTransformation(crs_of('EPSG:6668', osr), crs_of('EPSG:6677', osr))
        ex, ey, _ = tx.TransformPoint(lon, lat)
        self.assertLess(abs(gt[0] - ex), 2.0)
        self.assertLess(abs(gt[3] - ey), 2.0)
        self.assertEqual(srs.GetAuthorityCode(None), '6677')

    def test_wrong_input_crs_is_applied_not_second_guessed(self):
        # The override is authoritative: declaring a metre grid as JGD2011 lon/lat
        # moves it.  This pins "user CRS wins over the file's CRS".
        tif = make_geotiff(self.root / 'a.tif', gt=(139.0, 0.001, 0, 36.0, 0, -0.001), epsg=6668)
        _, gt, _, _, _ = self.prepare_first('raster', [tif], input_crs='EPSG:4326',
                                            target_crs='EPSG:6668', cell_size=0.001)
        self.assertAlmostEqual(gt[0], 139.0, places=3)

    def test_multiband_scaled_unreadable_and_missing_inputs(self):
        multi = make_geotiff(self.root / 'm.tif', bands=3)
        with self.assertRaisesRegex(ValueError, 'single-band'):
            self.prepare('raster', [multi], input_crs='')
        scaled = make_geotiff(self.root / 's.tif')
        ds = gdal.Open(str(scaled), gdal.GA_Update)
        ds.GetRasterBand(1).SetScale(0.1)
        ds = None
        with self.assertRaisesRegex(ValueError, 'scale/offset'):
            self.prepare('raster', [scaled], input_crs='')
        junk = self.root / 'junk.tif'
        junk.write_bytes(b'not a tiff at all')
        with self.assertRaises(CLEAR_ERRORS):
            self.prepare('raster', [junk], input_crs='')
        with self.assertRaises(CLEAR_ERRORS):
            self.prepare('raster', [self.root / 'missing.tif'], input_crs='')

    def test_tiff_without_any_georeferencing_is_refused(self):
        tif = make_geotiff(self.root / 'plain.tif', gt=None, epsg=None)
        with self.assertRaises(CLEAR_ERRORS):
            self.prepare('raster', [tif], input_crs='EPSG:6677')

    def test_ascii_grid_is_accepted_as_raster(self):
        asc = self.root / 'g.asc'
        asc.write_text('ncols 3\nnrows 2\nxllcorner 1000\nyllcorner 2000\ncellsize 10\n'
                       'NODATA_value -9999\n1 2 -9999\n4 5 6\n')
        array, gt, *_ = self.prepare_first('raster', [asc], input_crs='EPSG:6677')
        self.assertEqual(gt, (1000.0, 10.0, 0.0, 2020.0, 0.0, -10.0))
        self.assertOutputNoData(array, np.array([[0, 0, 1], [0, 0, 0]], bool))

    def test_failed_preparation_is_recorded_in_the_input_report(self):
        junk = self.root / 'junk.tif'
        junk.write_bytes(b'xx')
        work = self.root / 'wfail'
        with self.assertRaises(CLEAR_ERRORS):
            prepare_inputs(self.config('raster', [junk], input_crs=''), work, gdal, osr)
        report = json.loads((work / 'input_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'failed')
        self.assertIn('junk.tif', report['error'])


# ------------------------------------------------------------------ TIFF + world file

class TiffWorldFileTests(GdalCase):
    GRID = (1000.0, 10.0, 0.0, 2030.0, 0.0, -10.0)

    def plain_tif(self, name='t.tif', **kw):
        return make_geotiff(self.root / name, gt=None, epsg=None, **kw)

    def test_each_world_file_extension_is_found(self):
        for tif_name, wf in [('a.tif', 'a.tfw'), ('b.tif', 'b.wld'), ('c.tif', 'c.tifw'),
                             ('d.tiff', 'd.tfw'), ('e.TIF', 'e.TFW'), ('f.tif', 'f.WLD')]:
            with self.subTest(tif_name + '+' + wf):
                tif = self.plain_tif(tif_name)
                write_world_file(self.root / wf)
                self.assertTrue(has_world_file(tif))
                array, gt, *_ = self.prepare_first('forest', [tif])
                self.assertEqual(gt, self.GRID)
                np.testing.assert_array_equal(array, np.arange(12).reshape(3, 4))

    def test_tiff_without_world_file_is_not_reported_as_having_one(self):
        tif = self.plain_tif('lonely.tif')
        (self.root / 'other.tfw').write_text('x')
        self.assertFalse(has_world_file(tif))

    def test_forest_tiff_without_crs_or_world_file_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'requires a same-stem TFW'):
            self.prepare('forest', [self.plain_tif()])

    def test_world_file_without_input_crs_is_rejected(self):
        tif = self.plain_tif()
        write_world_file(self.root / 't.tfw')
        with self.assertRaisesRegex(ValueError, 'no embedded CRS'):
            self.prepare('forest', [tif], input_crs='')

    def test_report_marks_world_file_and_geotiff_sources(self):
        wf = self.plain_tif('w.tif')
        write_world_file(self.root / 'w.tfw')
        geo = make_geotiff(self.root / 'g.tif')
        _, report, _ = self.prepare('forest', [wf, geo])
        self.assertEqual([d['kind'] for d in report['detected']], ['tiff_worldfile', 'geotiff'])

    def test_world_file_applies_to_the_plain_raster_input_type_too(self):
        tif = self.plain_tif()
        write_world_file(self.root / 't.tfw')
        array, gt, *_ = self.prepare_first('raster', [tif], input_crs='EPSG:6677')
        self.assertEqual(gt, self.GRID)

    def test_corrupt_world_file_never_yields_a_silently_wrong_dem(self):
        tif = self.plain_tif()
        (self.root / 't.tfw').write_text('not numbers\n')
        try:
            _, gt, *_ = self.prepare_first('forest', [tif])
        except CLEAR_ERRORS:
            return
        self.assertEqual(gt, self.GRID)  # only acceptable non-error outcome

    def test_forest_geotiff_nodata_rules(self):
        values = np.array([[1, -9999, 3, 4]] * 3, dtype='float32')
        no_def = make_geotiff(self.root / 'nd.tif', values, nodata=None)
        # forest_nodata (default -9999) is the fallback for files without a definition ...
        array, *_ = self.prepare_first('forest', [no_def])
        self.assertOutputNoData(array, values == -9999)
        # ... and disabling it leaves nothing to interpret the file with.
        with self.assertRaisesRegex(ValueError, 'no NoData definition'):
            self.prepare('forest', [no_def], forest_nodata=None)
        # an embedded definition wins over forest_nodata
        own = make_geotiff(self.root / 'own.tif', np.array([[1, -1, 3, 4]] * 3, 'float32'), nodata=-1.0)
        array, *_ = self.prepare_first('forest', [own], forest_nodata=-9999.0)
        self.assertOutputNoData(array, np.array([[0, 1, 0, 0]] * 3, bool))

    def test_unreadable_forest_tiff(self):
        junk = self.root / 'junk.tif'
        junk.write_bytes(b'garbage')
        with self.assertRaises(CLEAR_ERRORS):
            self.prepare('forest', [junk])


# ------------------------------------------------------------------ regular text grids

class TextGridTests(GdalCase):
    def grid_csv(self, name='g.csv', origin=(100.0, 200.0), step=1.0, nx=3, ny=2, z0=1.0):
        """Row-major points with north rows first, like most DEM XYZ exports."""
        lines = ['X,Y,Z']
        for j in range(ny):
            for i in range(nx):
                y = origin[1] + (ny - 1 - j) * step
                lines.append(f'{origin[0] + i * step},{y},{z0 + j * nx + i}')
        return write_text(self.root / name, '\n'.join(lines) + '\n')

    def make(self, path, **kw):
        c = self.config('text', [path], text_grid_cell=1.0, **kw)
        dest = self.root / 'g.tif'
        count = text_grid(path, dest, c, gdal, osr)
        return count, *self.read(dest)

    def test_orientation_georeference_and_values(self):
        count, array, gt, nodata, srs = self.make(self.grid_csv())
        self.assertEqual(count, 6)
        # points are cell centres: the raster edge sits half a cell outside them
        self.assertEqual(gt, (99.5, 1.0, 0.0, 201.5, 0.0, -1.0))
        np.testing.assert_array_equal(array, [[1, 2, 3], [4, 5, 6]])
        self.assertEqual(nodata, NODATA)
        self.assertEqual(srs.GetAuthorityCode(None), '6677')

    def test_point_order_in_the_file_does_not_matter(self):
        lines = write_text(self.root / 'shuf.csv', 'X,Y,Z\n102,200,6\n100,201,1\n101,200,5\n'
                           '100,200,4\n102,201,3\n101,201,2\n')
        _, array, gt, *_ = self.make(lines)
        np.testing.assert_array_equal(array, [[1, 2, 3], [4, 5, 6]])

    def test_missing_points_become_nodata(self):
        path = write_text(self.root / 'holes.csv', 'X,Y,Z\n100,201,1\n102,201,3\n100,200,4\n')
        _, array, *_ = self.make(path)
        self.assertNoData(array, np.array([[0, 1, 0], [0, 1, 1]], bool))

    def test_source_nodata_values_are_holes(self):
        # NoData rows are dropped before the extent is computed, so the hole must be interior.
        path = write_text(self.root / 'nd.csv', 'X,Y,Z\n100,201,1\n101,201,-9999\n102,201,3\n')
        _, array, *_ = self.make(path, source_nodata=-9999.0)
        self.assertNoData(array, np.array([[0, 1, 0]], bool))

    def test_north_east_column_order(self):
        path = write_text(self.root / 'ne.csv', 'N,E,Z\n201,100,1\n201,101,2\n')
        _, array, gt, *_ = self.make(path, text_axis_order='north_east')
        self.assertEqual(gt[0], 99.5)
        np.testing.assert_array_equal(array, [[1, 2]])

    def test_fractional_cell_size(self):
        path = write_text(self.root / 'half.csv', 'X,Y,Z\n0,0,1\n0.5,0,2\n')
        c = self.config('text', [path], text_grid_cell=0.5)
        text_grid(path, self.root / 'h.tif', c, gdal, osr)
        array, gt, *_ = self.read(self.root / 'h.tif')
        self.assertEqual(gt[1], 0.5)
        np.testing.assert_array_equal(array, [[1, 2]])

    def test_single_point(self):
        path = write_text(self.root / 'one.csv', 'X,Y,Z\n5,5,9\n')
        count, array, gt, *_ = self.make(path)
        self.assertEqual((count, array.shape, gt[0], gt[3]), (1, (1, 1), 4.5, 5.5))

    def test_duplicate_points_equal_values_are_fine_conflicting_are_not(self):
        same = write_text(self.root / 'dup.csv', 'X,Y,Z\n0,0,1\n0,0,1.00001\n')
        self.make(same)
        with self.assertRaisesRegex(ValueError, 'Conflicting elevations'):
            self.make(write_text(self.root / 'conflict.csv', 'X,Y,Z\n0,0,1\n0,0,2\n'))

    def test_points_off_the_declared_grid_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not a regular grid'):
            self.make(write_text(self.root / 'irr.csv', 'X,Y,Z\n0,0,1\n0.5,0,2\n1,0,3\n'))

    def test_no_valid_points(self):
        with self.assertRaisesRegex(ValueError, 'No valid text elevations'):
            self.make(write_text(self.root / 'e.csv', 'X,Y,Z\n'))
        with self.assertRaisesRegex(ValueError, 'No valid text elevations'):
            self.make(write_text(self.root / 'e2.csv', 'X,Y,Z\n0,0,-9999\n'), source_nodata=-9999.0)

    def test_grid_requires_a_projected_metre_crs(self):
        path = self.grid_csv()
        for crs in ('EPSG:6668', 'EPSG:4326', 'EPSG:2263'):  # lon/lat x2, US-foot state plane
            with self.subTest(crs):
                with self.assertRaisesRegex(ValueError, 'projected metre CRS'):
                    self.make(path, input_crs=crs)

    def test_missing_or_invalid_crs(self):
        path = self.grid_csv()
        with self.assertRaises(CLEAR_ERRORS):
            self.make(path, input_crs='')
        with self.assertRaises(CLEAR_ERRORS):
            self.make(path, input_crs='EPSG:99999')

    def test_prepare_inputs_text_end_to_end(self):
        path = self.grid_csv(origin=(105.0, 205.0), step=10.0, nx=3, ny=2)
        array, gt, nodata, srs, report = self.prepare_first(
            'text', [path], text_grid_cell=10.0, cell_size=10.0, text_mode='grid')
        self.assertEqual(gt[1], 10.0)
        self.assertEqual(report['sources'][0]['points'], 6)
        np.testing.assert_array_equal(array, [[1, 2, 3], [4, 5, 6]])

    def test_cp932_tab_separated_txt(self):
        path = write_text(self.root / 'jp.txt', '東\t北\t高\n100\t200\t1\n101\t200\t2\n', 'cp932')
        c = self.config('text', [path], text_grid_cell=1.0, text_encoding='cp932',
                        text_delimiter='tab')
        self.assertEqual(text_grid(path, self.root / 'jp.tif', c, gdal, osr), 2)

    def test_work_files_are_not_left_behind_after_a_grid_conflict(self):
        bad = write_text(self.root / 'conflict.csv', 'X,Y,Z\n0,0,1\n0,0,2\n')
        with self.assertRaises(ValueError):
            text_grid(bad, self.root / 'c.tif', self.config('text', [bad], text_grid_cell=1.0),
                      gdal, osr)
        self.assertEqual(list(self.root.glob('*.grid.bin')), [])


# ------------------------------------------------------------------ forestry LEM / XYZ grid / ZIP

class ForestLemTests(GdalCase):
    def test_lem_end_to_end(self):
        lem, _ = standard_lem(self.root)
        array, gt, nodata, srs, report = self.prepare_first('forest', [lem], cell_size=5.0)
        self.assertEqual(gt, (0.0, 5.0, 0.0, 10.0, 0.0, -5.0))
        np.testing.assert_allclose(array, [[10, 11, DEFAULT_OUTPUT_NODATA],
                                           [DEFAULT_OUTPUT_NODATA, 12, 13]])
        self.assertEqual(report['detected'][0]['kind'], 'lem')
        self.assertEqual(int(report['detected'][0]['survey_year']), 2020)

    def test_lem_with_txt_metadata_and_cp932(self):
        lem, _ = standard_lem(self.root, meta_suffix='.txt', encoding='cp932')
        array, *_ = self.prepare_first('forest', [lem], cell_size=5.0)
        self.assertEqual(array.shape, (2, 3))

    def test_lem_needs_input_crs(self):
        lem, _ = standard_lem(self.root)
        with self.assertRaisesRegex(ValueError, '入力CRS'):
            self.prepare('forest', [lem], input_crs='')

    def test_lem_pixel_limit(self):
        lem, _ = standard_lem(self.root)
        self.prepare('forest', [lem], cell_size=5.0, max_input_pixels=6)
        with self.assertRaisesRegex(ValueError, 'pixel limit'):
            self.prepare('forest', [lem], cell_size=5.0, max_input_pixels=5)

    def test_corrupt_lem_data_is_rejected_with_a_row_number(self):
        lem, _ = write_lem(self.root, 'bad', [lem_row(1, [1, 2, 3]), lem_row(5, [1, 2, 3])])
        with self.assertRaisesRegex(ValueError, 'record number mismatch'):
            self.prepare('forest', [lem])

    def test_lem_without_metadata(self):
        lem = self.root / 'orphan.lem'
        lem.write_text(lem_row(1, [1, 2, 3]) + '\n')
        with self.assertRaisesRegex(ValueError, 'companion metadata'):
            self.prepare('forest', [lem])

    def test_two_lem_sheets_are_mosaicked_by_run(self):
        # second mesh sits 15 m east of the first (3 cells x 5 m)
        standard_lem(self.root, 'a')
        rows = [lem_row(1, [200, 210, 220]), lem_row(2, [230, 240, 250])]
        _, meta = write_lem(self.root, 'b', rows)
        meta.write_text(meta.read_text(encoding='utf-8')
                        .replace('区画左下Y座標,0', '区画左下Y座標,1500')
                        .replace('区画右上Y座標,1500', '区画右上Y座標,3000'), encoding='utf-8')
        out = self.root / 'out'
        manifest = run(self.config('forest', [self.root], cell_size=5.0), out, gdal, osr)
        array, gt, *_ = self.read(manifest['merged_dem'])
        self.assertEqual(array.shape, (2, 6))
        self.assertEqual(gt[0], 0.0)
        self.assertEqual(array[1, 4], 24.0)


class ForestXyzGridTests(GdalCase):
    def grid(self, name='g.csv', body='X,Y,Z\n102.5,107.5,1\n107.5,107.5,2\n102.5,102.5,3\n107.5,102.5,4\n'):
        return write_text(self.root / name, body)

    def test_grid_with_cell_centre_coordinates_keeps_exact_values(self):
        # centres at 2.5 + 5k land on the 5 m output grid, so no resampling blur
        array, gt, *_, report = self.prepare_first(
            'forest', [self.grid()], cell_size=5.0, forest_axis_order='east_north')
        self.assertEqual(gt[1], 5.0)
        np.testing.assert_array_equal(array, [[1, 2], [3, 4]])
        d = report['detected'][0]
        self.assertEqual((d['kind'], d['grid_cell'], d['points']), ('xyz_grid', 5.0, 4))

    def test_axis_order_north_east(self):
        body = 'N,E,Z\n107.5,102.5,1\n107.5,107.5,2\n102.5,102.5,3\n102.5,107.5,4\n'
        array, *_ = self.prepare_first('forest', [self.grid('ne.csv', body)], cell_size=5.0,
                                       forest_axis_order='north_east')
        np.testing.assert_array_equal(array, [[1, 2], [3, 4]])

    def test_forest_nodata_marks_holes(self):
        body = 'X,Y,Z\n102.5,107.5,1\n107.5,107.5,-9999\n102.5,102.5,3\n107.5,102.5,4\n'
        array, *_ = self.prepare_first('forest', [self.grid('nd.csv', body)], cell_size=5.0,
                                       forest_axis_order='east_north')
        self.assertOutputNoData(array, np.array([[0, 1], [0, 0]], bool))

    def test_explicit_grid_cell_overrides_inference(self):
        _, report, _ = self.prepare('forest', [self.grid()], cell_size=5.0,
                                    forest_axis_order='east_north', forest_grid_cell='5')
        self.assertEqual(report['detected'][0]['grid_cell'], 5.0)

    def test_grid_needs_input_crs(self):
        with self.assertRaisesRegex(ValueError, '入力CRS'):
            self.prepare('forest', [self.grid()], input_crs='', forest_axis_order='east_north')

    def test_unequal_xy_spacing_is_refused(self):
        body = 'X,Y,Z\n0,0,1\n5,0,2\n0,10,3\n'
        # Either the specific message (rc4 baseline) or, once unusable grids are skipped with a
        # warning (M-3), the generic 'No usable DEMs' stop; what must never happen is success.
        with self.assertRaisesRegex(ValueError, 'different X/Y intervals|No usable DEMs'):
            self.prepare('forest', [self.grid('uneven.csv', body)], forest_axis_order='east_north')

    def test_non_dem_text_is_ignored_and_recorded_when_other_inputs_exist(self):
        notes = write_text(self.root / 'notes.txt', 'この成果について\n説明文だけです\n')
        geo = make_geotiff(self.root / 'g.tif')
        _, report, _ = self.prepare('forest', [notes, geo])
        self.assertEqual([Path(i['path']).name for i in report['ignored']], ['notes.txt'])
        self.assertEqual(len(report['detected']), 1)

    def test_all_inputs_ignored_stops_with_a_clear_error(self):
        notes = write_text(self.root / 'notes.txt', 'text only\n')
        with self.assertRaisesRegex(ValueError, 'No usable DEMs'):
            self.prepare('forest', [notes])


class ForestZipTests(GdalCase):
    def test_zip_with_tiff_world_file_and_lem_is_read_without_touching_outside_paths(self):
        tif = make_geotiff(self.root / 'src.tif', gt=None, epsg=None)
        lem, meta = standard_lem(self.root, 'sheet')
        zpath = write_zip(self.root / 'pack.zip', [
            ('成果/図郭1.tif', tif.read_bytes()),
            ('成果/図郭1.tfw', '10\n0\n0\n-10\n1005\n2025\n'),
            ('成果/sheet.lem', lem.read_bytes()), ('成果/sheet.csv', meta.read_bytes()),
            ('成果/readme.pdf', b'ignored')])
        prepared, report, work = self.prepare('forest', [zpath], cell_size=5.0)
        self.assertEqual(report['archives'][0]['members'], 4)
        self.assertEqual(len(prepared['inputs']), 2)
        self.assertTrue(all(str(work) in str(p) for p in map(str, prepared['inputs'])))

    def test_traversal_entry_aborts_before_any_conversion(self):
        zpath = write_zip(self.root / 'evil.zip', [('../escape.tif', b'x'), ('ok.tif', b'x')])
        with self.assertRaisesRegex(ValueError, 'Unsafe ZIP member'):
            self.prepare('forest', [zpath])
        self.assertEqual(list(self.root.rglob('escape.tif')), [])

    def test_zip_without_dem_members(self):
        zpath = write_zip(self.root / 'docs.zip', [('readme.pdf', b'x')])
        with self.assertRaisesRegex(ValueError, 'no supported forestry'):
            self.prepare('forest', [zpath])

    def test_corrupt_zip(self):
        bad = self.root / 'bad.zip'
        bad.write_bytes(b'PK\x03\x04 truncated')
        with self.assertRaises(Exception):
            self.prepare('forest', [bad])


# ------------------------------------------------------------------ GSI DEM XML / ZIP

class GsiEndToEndTests(GdalCase):
    def gsi_config(self, **kw):
        # 0.05 degree cells align with the sample file, so values survive warping exactly
        return dict(input_crs='', target_crs='EPSG:6668', cell_size=0.05, **kw)

    def xml(self, name='a.xml', **kw):
        return write_text(self.root / name, gsi_xml(**kw))

    def test_xml_end_to_end(self):
        array, gt, nodata, srs, report = self.prepare_first('gsi', [self.xml()], **self.gsi_config())
        self.assertEqual(srs.GetAuthorityCode(None), '6668')
        self.assertAlmostEqual(gt[0], 139.0, places=6)
        self.assertAlmostEqual(gt[3], 35.1, places=6)
        np.testing.assert_allclose(array, [[10.5, 11.5], [12.5, DEFAULT_OUTPUT_NODATA]])
        self.assertEqual(report['sources'][0]['mesh'], '5139')

    def test_zip_members_are_read_in_memory(self):
        zpath = write_zip(self.root / 'fg.zip', [
            ('FG-GML-5139-dem5a.xml', gsi_xml()), ('dir/readme.txt', 'x'),
            ('../outside.xml', gsi_xml(lower='35.1 139.1', upper='35.2 139.2'))])
        _, report, work = self.prepare('gsi', [zpath], **self.gsi_config())
        members = [s['member'] for s in report['sources']]
        self.assertEqual(members, ['../outside.xml', 'FG-GML-5139-dem5a.xml'])
        # the whole sandbox (work dir included) must not contain any written XML copy
        self.assertEqual([p.name for p in self.root.rglob('*.xml')], [])

    def test_zip_without_xml_and_corrupt_zip(self):
        with self.assertRaisesRegex(ValueError, 'No usable DEMs'):
            self.prepare('gsi', [write_zip(self.root / 'n.zip', [('a.txt', 'x')])], **self.gsi_config())
        bad = self.root / 'bad.zip'
        bad.write_bytes(b'not a zip')
        with self.assertRaises(Exception):
            self.prepare('gsi', [bad], **self.gsi_config())

    def test_zip_member_with_dtd_is_rejected(self):
        evil = gsi_xml().replace('<Dataset', '<!DOCTYPE x [<!ENTITY a "b">]><Dataset', 1)
        with self.assertRaisesRegex(ValueError, 'DTD|entity'):
            self.prepare('gsi', [write_zip(self.root / 'e.zip', [('x.xml', evil)])], **self.gsi_config())

    def test_metadata_only_xml_has_no_usable_dem(self):
        path = write_text(self.root / 'meta.xml', '<Dataset></Dataset>')
        with self.assertRaisesRegex(ValueError, 'No usable DEMs'):
            self.prepare('gsi', [path], **self.gsi_config())

    def test_pixel_limit(self):
        path = self.xml()
        self.prepare('gsi', [path], max_input_pixels=4, **self.gsi_config())
        with self.assertRaisesRegex(ValueError, 'pixel limit'):
            self.prepare('gsi', [path], max_input_pixels=3, **self.gsi_config())

    def test_horizontal_crs_override_must_be_geographic(self):
        with self.assertRaisesRegex(ValueError, 'geographic'):
            self.prepare('gsi', [self.xml()], input_crs='EPSG:6677', target_crs='EPSG:6677',
                         cell_size=10.0)

    def test_new_and_old_datum_labels_cannot_be_mixed(self):
        a = self.xml('new.xml', srs='fguuid:jgd2024.bl')
        b = self.xml('old.xml', srs='fguuid:jgd2011.bl', lower='35.1 139.1', upper='35.2 139.2')
        with self.assertRaisesRegex(ValueError, 'datum labels are mixed'):
            self.prepare('gsi', [a, b], **self.gsi_config())

    def test_crs_alias_note_states_name_change_only(self):
        # r6: the note must separate the horizontal CRS relabelling
        # (name change only, coordinates unchanged) from the
        # vertical realization, which this mapping does not convert.
        path = self.xml('new.xml', srs='fguuid:jgd2024.bl')
        _prepared, report, _work = self.prepare('gsi', [path], **self.gsi_config())
        note = report.get('crs_alias_note', '')
        self.assertIn('name change only', note)
        self.assertIn('vertical', note)

    def test_adjacent_sheets_are_mosaicked_by_run(self):
        a = self.xml('a.xml')
        b = self.xml('b.xml', lower='35.0 139.1', upper='35.1 139.2', tuples='x,1\nx,2\nx,3\nx,4')
        out = self.root / 'out'
        manifest = run(self.config('gsi', [a, b], **self.gsi_config()), out, gdal, osr)
        array, gt, *_ = self.read(manifest['merged_dem'])
        self.assertEqual(array.shape, (2, 4))
        self.assertAlmostEqual(gt[0], 139.0, places=6)
        np.testing.assert_allclose(array[:, 2:], [[1, 2], [3, 4]])

    def test_run_manifest_for_zip_input(self):
        zpath = write_zip(self.root / 'fg.zip', [('a.xml', gsi_xml())])
        out = self.root / 'out'
        manifest = run(self.config('gsi', [zpath], **self.gsi_config()), out, gdal, osr)
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(manifest['input_type'], 'gsi')
        self.assertTrue(Path(manifest['merged_dem']).is_file())
        self.assertFalse((out / 'work').exists(), 'work dir is removed after a successful run')
        self.assertEqual(json.loads((out / 'run.json').read_text(encoding='utf-8'))['status'],
                         'completed')


class RunEndToEndTests(GdalCase):
    """One whole-pipeline pass per remaining format: discovery -> DEM -> run.json."""

    def run_folder(self, input_type, **kw):
        out = self.root / 'out'
        manifest = run(self.config(input_type, [self.root / 'in'], **kw), out, gdal, osr)
        return manifest, self.read(manifest['merged_dem'])

    def make_in(self):
        folder = self.root / 'in'
        folder.mkdir()
        return folder

    def test_geotiff_folder(self):
        make_geotiff(self.make_in() / 'a.tif')
        manifest, (array, gt, nodata, srs) = self.run_folder('raster', input_crs='')
        self.assertEqual((manifest['status'], manifest['source_count']), ('completed', 1))
        self.assertEqual(gt, (1000.0, 10.0, 0.0, 2030.0, 0.0, -10.0))
        self.assertEqual(manifest['vertical_conversion'], False)

    def test_tiff_with_world_file_folder_skips_the_companion(self):
        folder = self.make_in()
        make_geotiff(folder / 'a.tif', gt=None, epsg=None)
        write_world_file(folder / 'a.tfw')
        manifest, (array, gt, *_) = self.run_folder('forest')
        self.assertEqual(manifest['source_count'], 1)
        self.assertEqual(gt[0], 1000.0)

    def test_text_grid_folder(self):
        folder = self.make_in()
        write_text(folder / 'g.csv', 'X,Y,Z\n105,115,1\n115,115,2\n105,105,3\n115,105,4\n')
        manifest, (array, *_) = self.run_folder('text', text_grid_cell=10.0, text_mode='grid')
        np.testing.assert_array_equal(array, [[1, 2], [3, 4]])

    def test_failure_leaves_a_diagnosable_input_report(self):
        folder = self.make_in()
        write_text(folder / 'bad.csv', 'X,Y,Z\n0,0,1\n0,0,2\n')
        out = self.root / 'out'
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            # cell_size == text_grid_cell on purpose: a 1-pixel raster finer than the output
            # cell can hang gdal.Warp (see validation/known_defects KD-5), which would turn a
            # regression of the conflict check into a hung suite instead of a failed test.
            run(self.config('text', [folder], text_grid_cell=1.0, cell_size=1.0, text_mode='grid'),
                out, gdal, osr)
        report = json.loads((out / 'work' / 'input_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'failed')
        self.assertIn('Conflicting', report['error'])


if __name__ == '__main__':
    unittest.main()
