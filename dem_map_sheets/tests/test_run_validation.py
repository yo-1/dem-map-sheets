"""KD-1/KD-2 regressions: configuration validation at run() entry.

validate_input() existed but was never called, so inconsistent
configurations reached GDAL: a negative cell_size could hang GDAL
and NaN could abort the host process. run() now validates the
merged configuration (including cell_size, target_crs and
max_pixels, which validate_input did not cover) before creating
any directory or calling GDAL.
"""
import tempfile
import unittest
from pathlib import Path

from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import run
from dem_map_sheets.engine.input_sources import DEFAULTS, validate_input


def _valid_config(**kw):
    c = dict(DEFAULTS)
    c.update(input_type='raster', target_crs='EPSG:6677', cell_size=1.0,
             max_pixels=25000000)
    c.update(kw)
    return c


class ValidateInputTests(unittest.TestCase):
    def test_valid_config_passes(self):
        validate_input(_valid_config())

    def test_cell_size_rejected(self):
        for bad in (0, 0.0, -1.0, float('nan'), float('inf'), 'x', None, True):
            with self.subTest(cell_size=bad), self.assertRaises(ValueError):
                validate_input(_valid_config(cell_size=bad))

    def test_target_crs_required(self):
        for bad in ('', '   ', None, 6677):
            with self.subTest(target_crs=bad), self.assertRaises(ValueError):
                validate_input(_valid_config(target_crs=bad))

    def test_max_pixels_must_be_positive_int(self):
        for bad in (0, -5, 1.5, '100'):
            with self.subTest(max_pixels=bad), self.assertRaises(ValueError):
                validate_input(_valid_config(max_pixels=bad))

    def test_duplicate_text_columns_rejected(self):
        with self.assertRaises(ValueError):
            validate_input(_valid_config(text_columns=[1, 1, 3]))

    def test_empty_ground_classes_rejected(self):
        with self.assertRaises(ValueError):
            validate_input(_valid_config(ground_classes=[]))


class RunEntryValidationTests(unittest.TestCase):
    def test_invalid_cell_size_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'out'
            cfg = _valid_config(cell_size=float('nan'), inputs=[])
            with self.assertRaises(ValueError):
                run(cfg, out, gdal, osr, None)
            self.assertFalse(out.exists())

    def test_inconsistent_config_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'out'
            cfg = _valid_config(text_columns=[1, 1, 3], inputs=[])
            with self.assertRaises(ValueError):
                run(cfg, out, gdal, osr, None)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()


class RunCrsValidationTests(unittest.TestCase):
    def test_unparseable_target_crs_rejected_at_entry(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'out'
            cfg = _valid_config(target_crs='EPSG:99999', inputs=[])
            with self.assertRaises(ValueError):
                run(cfg, out, gdal, osr, None)
            self.assertFalse(out.exists())

    def test_3d_target_crs_rejected_at_entry(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'out'
            cfg = _valid_config(target_crs='EPSG:4979', inputs=[])
            with self.assertRaises(ValueError):
                run(cfg, out, gdal, osr, None)
            self.assertFalse(out.exists())


class EntryMessageLanguageTests(unittest.TestCase):
    """Entry-validation errors carry a Japanese explanation for GUI
    users while keeping the English technical message for API use."""

    def test_cell_size_error_has_japanese_guidance(self):
        cfg = _valid_config(cell_size=float('nan'), inputs=[])
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError) as ctx:
                run(cfg, Path(td) / 'out', gdal, osr, None)
        self.assertIn('セルサイズ', str(ctx.exception))

    def test_target_crs_error_has_japanese_guidance(self):
        cfg = _valid_config(target_crs='EPSG:99999', inputs=[])
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError) as ctx:
                run(cfg, Path(td) / 'out', gdal, osr, None)
        self.assertIn('対象CRS', str(ctx.exception))
