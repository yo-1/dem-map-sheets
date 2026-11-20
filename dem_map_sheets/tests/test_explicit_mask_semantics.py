"""Issue #29 A: real-GDAL loss/revival protection, not a conversion repair."""
import hashlib
import gc
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

HAVE_GDAL = importlib.util.find_spec('osgeo') is not None
if HAVE_GDAL:
    from osgeo import gdal, osr
    from dem_map_sheets.engine import input_sources as inputs
    from dem_map_sheets.engine.dem_pipeline import run, materialize_geotiff
    from dem_map_sheets.engine.mask_guard import MaskSemanticsConflictError, inspect_explicit_mask


@unittest.skipUnless(HAVE_GDAL, 'GDAL is required')
class ExplicitMaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def tif(self, mask='internal', values=None, zero=False, nan=False,
            nodata=-9999., dtype=None, shape=(20, 20), flags=None):
        self.counter += 1
        path = self.root / f'input_{self.counter}.tif'
        data = np.full(shape, 100., dtype='float32')
        if values is None:
            values = [-9999.0005, -9999.002, -9999.004, -9999.008,
                      -9999.016, -9998.99, -9998.996]
        data[5, 2:2+len(values)] = values
        if nan:
            data[12, 12] = np.nan
        # Use process-local options and always restore them (GDAL 3.8+).
        previous = gdal.GetConfigOption('GDAL_TIFF_INTERNAL_MASK')
        gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', 'YES' if mask == 'internal' else 'NO')
        try:
            ds = gdal.GetDriverByName('GTiff').Create(str(path), shape[1], shape[0], 1,
                                                     dtype or gdal.GDT_Float32)
            ds.SetGeoTransform((1000., 1., 0., 2020., 0., -1.))
            crs = osr.SpatialReference(); crs.ImportFromEPSG(6677)
            ds.SetProjection(crs.ExportToWkt())
            band = ds.GetRasterBand(1)
            band.WriteArray(data)
            if nodata is not None:
                band.SetNoDataValue(nodata)
            if mask != 'none':
                band.CreateMaskBand(gdal.GMF_PER_DATASET if flags is None else flags)
                validity = np.full(shape, 255, dtype='uint8')
                if zero:
                    validity[0, 0] = 0
                band.GetMaskBand().WriteArray(validity)
            ds.FlushCache(); ds = None
        finally:
            gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', previous)
        return path

    def cfg(self, paths, **changes):
        result = {**inputs.DEFAULTS, 'input_type': 'raster', 'inputs': [str(p) for p in paths],
                  'target_crs': 'EPSG:6677', 'cell_size': 1., 'output_nodata': -32768.,
                  'source_nodata': None, 'max_pixels': 25_000_000,
                  'normalize_rasters': True, 'merge_priority': 'last'}
        result.update(changes)
        return result

    def fingerprints(self, paths):
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for path in paths for p in (path, Path(str(path)+'.msk')) if p.exists()}

    def stopped(self, paths, **changes):
        self.counter += 1
        out = self.root / f'run_{self.counter}'
        before = self.fingerprints(paths)
        with self.assertRaises(MaskSemanticsConflictError) as caught:
            run(self.cfg(paths, **changes), out, gdal, osr, None)
        record = json.loads((out / 'run.json').read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['schema_version'], 1)
        self.assertEqual(record['qa_status'], 'not_evaluated')
        self.assertEqual(record['failure_detail'], caught.exception.detail)
        self.assertTrue(record['mask_safety_stop'])
        self.assertEqual(record['outputs_present'], [])
        self.assertFalse(list(out.rglob('*.tif')))
        self.assertFalse(list(out.rglob('*.vrt')))
        self.assertEqual(before, self.fingerprints(paths))
        for entry in record['failure_detail']:
            self.assertEqual(entry['role'], 'input_mask')
            for key in ('source', 'reason', 'stage', 'effective_source_nodata',
                        'effective_source_nodata_origin', 'mask_flags', 'count',
                        'count_kind', 'values', 'positions', 'gdal_version'):
                self.assertIn(key, entry)
        return record

    def completed(self, path, **changes):
        self.counter += 1
        out = self.root / f'good_{self.counter}'
        result = run(self.cfg([path], **changes), out, gdal, osr, None)
        self.assertEqual(result['status'], 'completed')
        ds = gdal.Open(result['merged_dem'])
        return ds.ReadAsArray(), ds.GetRasterBand(1).GetMaskBand().ReadAsArray()

    def test_internal_and_external_four_loss_candidates_before_warp(self):
        for kind in ('internal', 'external'):
            with self.subTest(kind=kind):
                path = self.tif(mask=kind)
                self.assertEqual(Path(str(path)+'.msk').exists(), kind == 'external')
                with patch.object(gdal, 'Warp', side_effect=AssertionError('must stop pre-Warp')):
                    record = self.stopped([path])
                entry = record['failure_detail'][0]
                self.assertEqual(entry['reason'], 'valid_near_effective_nodata')
                self.assertEqual(entry['count'], 4)
                self.assertEqual(entry['count_kind'], 'detected_candidates')
                self.assertEqual([(p['row'], p['col']) for p in entry['positions']],
                                 [(5, 2), (5, 3), (5, 4), (5, 8)])
                self.assertEqual(entry['positions'][0]['x'], 1002.5)
                self.assertEqual(entry['positions'][0]['y'], 2014.5)

    def test_loss_and_revival_are_disjoint_reasons(self):
        record = self.stopped([self.tif(zero=True)])
        self.assertEqual([(d['reason'], d['count']) for d in record['failure_detail']],
                         [('valid_near_effective_nodata', 4), ('explicit_invalid_may_become_valid', 1)])
        self.assertEqual(record['failure_detail'][1]['count_kind'], 'potential')
        self.assertIn('潜在的な件数', record['error'])
        self.assertNotIn('全入力をスキャン済み', record['error'])

    def test_revival_without_near_values_is_intentionally_conservative(self):
        for kind in ('internal', 'external'):
            with self.subTest(kind=kind):
                record = self.stopped([self.tif(mask=kind, values=[], zero=True)])
                self.assertEqual(record['failure_detail'][0]['count'], 1)
                self.assertEqual(record['failure_detail'][0]['count_kind'], 'potential')

    def test_nan_cleaning_does_not_reenable_an_explicit_hole(self):
        for kind in ('internal', 'external'):
            with self.subTest(kind=kind):
                self.stopped([self.tif(mask=kind, values=[], zero=True, nan=True)])

    def test_flags_zero_band_specific_mask_is_not_all_valid(self):
        record = self.stopped([self.tif(mask='external', values=[], zero=True, flags=0)])
        self.assertEqual(record['failure_detail'][0]['mask_flags'], 0)
        self.assertEqual(record['failure_detail'][0]['reason'], 'explicit_invalid_may_become_valid')

    def test_source_override_matches_the_effective_definition(self):
        for value in (None, -9999.):
            with self.subTest(value=value):
                record = self.stopped([self.tif()], source_nodata=value)
                self.assertEqual(record['failure_detail'][0]['effective_source_nodata_origin'],
                                 'band' if value is None else 'override')
        data, mask = self.completed(self.tif(), source_nodata=-999999.)
        self.assertEqual(int(np.count_nonzero(mask)), 400)
        self.assertEqual(float(data[5, 2]), float(np.float32(-9999.0005)))
        record = self.stopped([self.tif(values=[-999999.0625])], source_nodata=-999999.)
        self.assertEqual(record['failure_detail'][0]['count'], 1)
        self.assertEqual(record['failure_detail'][0]['effective_source_nodata'], -999999.)

    def test_override_false_uses_band_definition_not_configured_override(self):
        path = self.tif()
        cfg = self.cfg([path], source_nodata=-999999.)
        with self.assertRaises(MaskSemanticsConflictError) as error:
            inputs.normalize(path, self.root/'no_override.vrt', cfg, gdal, osr, override_nodata=False)
        self.assertEqual(error.exception.detail[0]['effective_source_nodata'], -9999.)
        self.assertEqual(error.exception.detail[0]['effective_source_nodata_origin'], 'band')

    def test_zero_candidates_all_valid_masks_complete_without_value_changes(self):
        for kind in ('internal', 'external'):
            for values in ([], [-9999.008, -9999.016, -9998.99], [-9999., 55.]):
                with self.subTest(kind=kind, values=values):
                    path = self.tif(mask=kind, values=values)
                    original = gdal.Open(str(path)).ReadAsArray()
                    data, _mask = self.completed(path)
                    expected = np.where(original == -9999., -32768., original)
                    np.testing.assert_array_equal(data, expected)

    def test_nan_all_valid_mask_remains_the_existing_missing_path(self):
        data, mask = self.completed(self.tif(values=[], nan=True))
        self.assertEqual(data[12, 12], -32768.)
        self.assertEqual(int(np.count_nonzero(mask)), 399)

    def test_integer_exact_holes_and_ordinary_neighbours_remain_supported(self):
        for dtype, declared, values in ((gdal.GDT_Int16, -9999., [-9999., -9998.]),
                                        (gdal.GDT_UInt16, 0., [0., 1.]),
                                        (gdal.GDT_Byte, 0., [0., 1.])):
            with self.subTest(dtype=dtype):
                path = self.tif(values=values, nodata=declared, dtype=dtype)
                data, mask = self.completed(path)
                self.assertEqual(data[5, 2], -32768.)
                self.assertEqual(data[5, 3], values[1])
                self.assertEqual(int(np.count_nonzero(mask)), 399)

    def test_float64_explicit_mask_stops_as_unverified_not_as_loss_count(self):
        record = self.stopped([self.tif(values=[], dtype=gdal.GDT_Float64)])
        self.assertEqual(record['failure_detail'][0]['reason'], 'unsupported_mask_dtype')
        self.assertEqual(record['failure_detail'][0]['count_kind'], 'not_evaluated')
        self.assertEqual(record['failure_detail'][0]['count'], 0)

    def test_unrepresentable_effective_nodata_stops_without_guessing(self):
        record = self.stopped([self.tif(values=[])], source_nodata=1e100)
        self.assertEqual(record['failure_detail'][0]['reason'], 'unsupported_effective_nodata')
        self.assertEqual(record['failure_detail'][0]['count_kind'], 'not_evaluated')

    def test_passthrough_and_direct_materialize_are_both_protected(self):
        path = self.tif()
        record = self.stopped([path], normalize_rasters=False)
        self.assertEqual(record['failure_detail'][0]['stage'], 'passthrough_preflight')
        destination = self.root/'direct.tif'
        with self.assertRaises(MaskSemanticsConflictError) as error:
            materialize_geotiff(path, destination, gdal, -32768.)
        self.assertEqual(error.exception.detail[0]['stage'], 'materialize_preflight')
        self.assertFalse(destination.exists())
        self.assertFalse(Path(str(destination)+'.part').exists())
        record = self.stopped([path], normalize_rasters=False, source_nodata=-999999.)
        self.assertEqual(record['failure_detail'][0]['effective_source_nodata'], -9999.)
        self.assertEqual(record['failure_detail'][0]['effective_source_nodata_origin'], 'band')

    def test_all_priorities_skip_and_keep_settings_stop_and_prune_prior_outputs(self):
        good = self.tif(mask='none', values=[])
        bad = self.tif()
        for mode in ('last', 'first', 'auto'):
            for skip in (False, True):
                for keep in (False, True):
                    with self.subTest(mode=mode, skip=skip, keep=keep):
                        self.stopped([good, bad], merge_priority=mode,
                                     skip_invalid_rasters=skip, keep_work=keep)

    def test_forestry_geotiff_and_worldfile_paths_do_not_bypass_guard(self):
        geo = self.tif()
        self.stopped([geo], input_type='forest')
        plain = self.tif()
        ds = gdal.Open(str(plain), gdal.GA_Update); ds.SetProjection(''); ds = None
        plain.with_suffix('.tfw').write_text('1\n0\n0\n-1\n1000.5\n2019.5\n')
        self.stopped([plain], input_type='forest', input_crs='EPSG:6677')

    def test_missing_definition_is_not_silently_accepted(self):
        path = self.tif(values=[], nodata=None)
        self.counter += 1
        with self.assertRaisesRegex(ValueError, 'no NoData definition'):
            run(self.cfg([path]), self.root/f'missing_{self.counter}', gdal, osr, None)

    def test_mask_read_failure_is_not_a_successful_skip(self):
        path = self.tif(values=[])
        read = gdal.Band.ReadAsArray
        def unreadable(band, *args, **kwargs):
            return None if band.DataType == gdal.GDT_Byte else read(band, *args, **kwargs)
        with patch.object(gdal.Band, 'ReadAsArray', unreadable):
            record = self.stopped([path], skip_invalid_rasters=True)
        self.assertEqual(record['failure_detail'][0]['reason'], 'invalid_mask_read')

    def test_unverifiable_numeric_vrt_is_a_structured_stop(self):
        path = self.tif(values=[])
        try:
            with patch.object(gdal, 'Translate', return_value=None) as translate:
                try:
                    record = self.stopped([path], skip_invalid_rasters=True)
                finally:
                    # Mock call arguments own the source Dataset as well.
                    translate.reset_mock()
            self.assertEqual(record['failure_detail'][0]['reason'], 'numeric_mask_unverifiable')
        finally:
            # Guard diagnostic closures/exception frames can form cycles that
            # retain GDAL handles until collection. Windows cannot unlink them.
            gc.collect()

    def test_probe_value_change_is_not_trusted(self):
        path = self.tif(values=[])
        translate = gdal.Translate
        def changed(*args, **kwargs):
            probe = translate(*args, **kwargs)
            # A MEM copy is only a fake corrupt probe, not a repair path.
            fake = translate('', probe, format='MEM')
            data = fake.ReadAsArray(); data[0, 0] = 999.
            fake.GetRasterBand(1).WriteArray(data)
            return fake
        with patch.object(gdal, 'Translate', changed):
            record = self.stopped([path])
        self.assertEqual(record['failure_detail'][0]['reason'], 'numeric_mask_unverifiable')

    def test_tiled_counts_positions_and_sample_limits(self):
        path = self.tif(values=[], shape=(1030, 1040))
        ds = gdal.Open(str(path), gdal.GA_Update)
        data = ds.ReadAsArray(); data[1029, 1039] = -9999.002
        ds.GetRasterBand(1).WriteArray(data); ds.FlushCache(); ds = None
        source = gdal.Open(str(path)); windows = []
        read = gdal.Band.ReadAsArray
        def tracked(band, *args, **kwargs):
            windows.append(args)
            return read(band, *args, **kwargs)
        with patch.object(gdal.Band, 'ReadAsArray', tracked):
            with self.assertRaises(MaskSemanticsConflictError) as error:
                inspect_explicit_mask(source, path, -9999., 'band', gdal, 'test')
        self.assertTrue(all(len(w) == 4 and w[2] <= 512 and w[3] <= 512 for w in windows))
        position = error.exception.detail[0]['positions'][0]
        self.assertEqual((position['row'], position['col']), (1029, 1039))
        self.assertEqual(error.exception.detail[0]['count'], 1)

    def test_large_count_is_total_not_sample_count(self):
        path = self.tif(values=[])
        ds = gdal.Open(str(path), gdal.GA_Update)
        ds.GetRasterBand(1).WriteArray(np.full((20, 20), -9999.002, dtype='float32'))
        ds.FlushCache(); ds = None
        record = self.stopped([path])
        self.assertEqual(record['failure_detail'][0]['count'], 400)
        self.assertEqual(len(record['failure_detail'][0]['positions']), 5)

    def test_existing_output_collision_role_is_not_reclassified_or_counted_twice(self):
        path = self.tif(values=[-9999.002])
        out = self.root/'output_role'
        with self.assertRaises(inputs.NoDataCollisionError):
            run(self.cfg([path], output_nodata=-9999.), out, gdal, osr, None)
        record = json.loads((out/'run.json').read_text())
        self.assertEqual([(d['role'], d['count']) for d in record['failure_detail']], [('output', 1)])

    def test_simultaneous_output_and_input_risks_do_not_share_cells(self):
        record = self.stopped([self.tif(values=[-9999.002, -32768.002])])
        self.assertEqual([(d['role'], d['count']) for d in record['failure_detail']], [('input_mask', 1)])
        self.assertEqual(record['failure_detail'][0]['values'], [float(np.float32(-9999.002))])

    def test_cancel_during_mask_scan_is_not_relabelled_as_mask_uncertainty(self):
        path = self.tif(values=[], shape=(520, 520))
        class Feedback:
            stopped = False
            def isCanceled(self):
                return self.stopped
        feedback = Feedback()
        read = gdal.Band.ReadAsArray
        def cancel(band, *args, **kwargs):
            result = read(band, *args, **kwargs)
            feedback.stopped = True
            return result
        source = gdal.Open(str(path))
        try:
            with patch.object(gdal.Band, 'ReadAsArray', cancel):
                with self.assertRaises(RuntimeError) as error:
                    inspect_explicit_mask(source, path, -9999., 'band', gdal, 'test', feedback)
            self.assertNotIsInstance(error.exception, MaskSemanticsConflictError)
        finally:
            # assertRaises strips the outer traceback, but chained cancellation
            # exceptions may still own frames containing the source Dataset.
            error = None
            source = None
            gc.collect()

    def test_cleanup_failure_preserves_the_mask_stop_and_records_leftovers(self):
        from dem_map_sheets.engine import dem_pipeline
        good = self.tif(mask='none', values=[])
        bad = self.tif()
        out = self.root/'locked_cleanup'
        remove = dem_pipeline.shutil.rmtree
        def locked(path, *args, **kwargs):
            if Path(path).name == '01_source_dem':
                raise PermissionError('simulated output lock')
            return remove(path, *args, **kwargs)
        with patch.object(dem_pipeline.shutil, 'rmtree', locked):
            with self.assertRaises(MaskSemanticsConflictError):
                run(self.cfg([good, bad], merge_priority='auto'), out, gdal, osr, None)
        record = json.loads((out/'run.json').read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['failure_detail'][0]['role'], 'input_mask')
        self.assertTrue(record['outputs_present'])
        self.assertFalse((out/'02_merged_dem.tif').exists())

    def test_unverified_scale_cannot_be_swallowed_by_skip_invalid(self):
        path = self.tif(values=[])
        ds = gdal.Open(str(path), gdal.GA_Update)
        ds.GetRasterBand(1).SetScale(2.0); ds.FlushCache(); ds = None
        record = self.stopped([path], skip_invalid_rasters=True)
        self.assertEqual(record['failure_detail'][0]['reason'], 'unsupported_mask_scale_offset')


if __name__ == '__main__':
    unittest.main()
