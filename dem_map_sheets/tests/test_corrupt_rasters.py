"""Corrupt TIFF battery (coordinator request 2026-11-10): 25 broken
variants must stop explicitly by default with Japanese guidance
(the GDAL detail is preserved), and with skip_invalid_rasters they
are recorded in the input report with at most 20 individual warnings
plus one summary line."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import run
from dem_map_sheets.engine.input_sources import DEFAULTS, prepare_inputs


class _Feedback:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def isCanceled(self):
        return False

    def setProgress(self, _value):
        pass

    def pushWarning(self, message):
        self.warnings.append(str(message))

    def pushInfo(self, message):
        self.infos.append(str(message))


def _valid_bytes(folder):
    path = Path(folder) / 'valid.tif'
    ds = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((0.0, 1.0, 0.0, 5.0, 0.0, -1.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(6677)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)
    band.WriteArray(np.full((5, 5), 100.0, dtype='float32'))
    ds = None
    return path.read_bytes(), path


def _variants(base):
    random_bytes = bytes((i * 37 + 11) % 256 for i in range(512))

    def patch(data, offset, chunk):
        return data[:offset] + chunk + data[offset + len(chunk):]

    n = len(base)
    return {
        'empty': b'',
        'one_byte': b'I',
        'header_only': b'II*\x00\x08\x00\x00\x00',
        'text_garbage': b'this is not a tiff at all',
        'zeros': bytes(1024),
        'ones': bytes([255]) * 1024,
        'random': random_bytes,
        'trunc_16': base[:16],
        'trunc_64': base[:64],
        'trunc_128': base[:128],
        'trunc_half': base[: n // 2],
        'trunc_90pct': base[: n * 9 // 10],
        'trunc_3q': base[: n * 3 // 4],
        'magic_99': patch(base, 2, b'\x63\x00'),
        'byte_order_zz': patch(base, 0, b'ZZ'),
        'ifd_offset_huge': patch(base, 4, b'\xf0\xff\xff\xff'),
        'head_ff': patch(base, 0, bytes([255]) * 8),
        'tag_area_zeroed': patch(base, 100, bytes(8)),
        'trunc_tail8': base[: n - 8],
        'reversed': base[::-1],
        'header_plus_garbage': base[:8] + random_bytes[:200],
        'png_magic_garbage': b'\x89PNG\r\n\x1a\n' + random_bytes[:200],
        'byte_swapped': bytes(b for pair in zip(base[1::2], base[0::2]) for b in pair),
        'tail_only': base[-64:],
        'second_half': base[n // 2:],
    }


class CorruptRasterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.base, self.valid = _valid_bytes(self.root)
        self.variants = _variants(self.base)
        self.assertEqual(len(self.variants), 25)
        self.files = {}
        for name, data in self.variants.items():
            p = self.root / f'broken_{name}.tif'
            p.write_bytes(data)
            self.files[name] = p

    def _cfg(self, inputs, **kw):
        c = dict(DEFAULTS)
        c.update(input_type='raster', inputs=[str(p) for p in inputs],
                 input_crs='', target_crs='EPSG:6677', cell_size=1.0,
                 source_nodata=None, max_pixels=10 ** 9)
        c.update(kw)
        return c

    def test_each_variant_stops_explicitly_by_default(self):
        for name, path in self.files.items():
            with self.subTest(variant=name):
                work = self.root / f'w_{name}'
                with self.assertRaises(ValueError) as ctx:
                    prepare_inputs(self._cfg([path]), work, gdal, osr, _Feedback())
                self.assertIn('読み込めません', str(ctx.exception))

    def test_skip_records_all_and_caps_warnings(self):
        feedback = _Feedback()
        inputs = [self.valid] + [self.files[n] for n in sorted(self.files)]
        prepared, report = prepare_inputs(
            self._cfg(inputs, skip_invalid_rasters=True),
            self.root / 'w_skip', gdal, osr, feedback)
        self.assertEqual(len(prepared['inputs']), 1)
        self.assertEqual(len(report['ignored']), 25)
        individual = [w for w in feedback.warnings if 'ほか' not in w]
        summary = [w for w in feedback.warnings if 'ほか' in w]
        self.assertLessEqual(len(individual), 20)
        self.assertEqual(len(summary), 1)
        self.assertIn('ほか 5 件', summary[0])

    def test_full_run_failure_is_recorded_in_run_json(self):
        out = self.root / 'out_fail'
        with self.assertRaises(ValueError):
            run(self._cfg([self.files['trunc_half']]), out, gdal, osr, _Feedback())
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'failed')
        self.assertIn('読み込めません', record['error'])

    def test_auto_mode_skips_empty_tif_and_processes_valid(self):
        # D-2: with skipping on and merge_priority='auto', a folder
        # holding one valid DEM and one empty TIFF must still
        # process the valid DEM, and the bad file must stay recorded.
        folder = self.root / 'mixed'
        folder.mkdir()
        (folder / 'valid.tif').write_bytes(self.base)
        (folder / 'empty.tif').write_bytes(b'')
        for normalize in (False, True):
            with self.subTest(normalize_rasters=normalize):
                out = self.root / f'out_auto_skip_{normalize}'
                manifest = run(self._cfg([folder], merge_priority='auto',
                                         skip_invalid_rasters=True,
                                         normalize_rasters=normalize),
                               out, gdal, osr, _Feedback())
                self.assertTrue(Path(manifest['merged_dem']).exists())
                sources = manifest['input_report']['sources']
                dropped = [e for e in sources if e.get('dropped')]
                self.assertEqual(len(dropped), 1)
                self.assertIn('empty.tif', dropped[0]['ignored'][0]['path'])

    def test_auto_mode_all_invalid_still_fails_with_skip(self):
        folder = self.root / 'only_bad'
        folder.mkdir()
        (folder / 'empty.tif').write_bytes(b'')
        with self.assertRaises(ValueError) as ctx:
            run(self._cfg([folder], merge_priority='auto', skip_invalid_rasters=True),
                self.root / 'out_auto_allbad', gdal, osr, _Feedback())
        self.assertIn('使用可能なDEMが見つかりません', str(ctx.exception))

    def test_auto_mode_also_stops(self):
        out = self.root / 'out_auto'
        with self.assertRaises(ValueError):
            run(self._cfg([self.files['magic_99']], merge_priority='auto'),
                out, gdal, osr, _Feedback())


if __name__ == '__main__':
    unittest.main()
