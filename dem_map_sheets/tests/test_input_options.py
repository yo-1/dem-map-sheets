"""Input option validation, input discovery and text-row parsing (no GDAL).

Covers the format-independent front door of every input type:
- validate_input(): rejects malformed option dictionaries
- discover(): file/folder/glob expansion, extension rules, .tfw companions
- text_rows()/autodetect_xyz()/infer_grid_cell(): CSV/XYZ/TXT parsing

Pure Python (numpy only): runs in any environment.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_fixtures import write_text  # noqa: E402

from dem_map_sheets.engine.forest_dem import autodetect_xyz  # noqa: E402
from dem_map_sheets.engine.input_sources import (  # noqa: E402
    DEFAULTS, discover, explicit_file_parameter, infer_grid_cell, text_rows,
    validate_input)


def cfg(**kw):
    # source_nodata/cell_size/target_crs are supplied by the caller (algorithm.py),
    # not by DEFAULTS, so the tests provide them explicitly.
    return {**DEFAULTS, 'source_nodata': None, 'cell_size': 1.0,
            'target_crs': 'EPSG:6677', **kw}


class ValidateInputTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        validate_input(cfg())

    def test_text_requires_input_crs_but_other_types_do_not(self):
        with self.assertRaisesRegex(ValueError, 'input_crs'):
            validate_input(cfg(input_type='text', input_crs='  '))
        validate_input(cfg(input_type='text', input_crs='EPSG:6677'))
        for kind in ('raster', 'gsi', 'lidar', 'forest'):
            validate_input(cfg(input_type=kind, input_crs=''))

    def test_invalid_options_are_rejected(self):
        bad = {
            'unknown input_type': dict(input_type='shapefile'),
            'recursive not bool': dict(recursive=1),
            'normalize not bool': dict(normalize_rasters='yes'),
            'tile size too small': dict(point_tile_size=63),
            'tile size too large': dict(point_tile_size=2049),
            'tile size float': dict(point_tile_size=512.0),
            'max_input_pixels zero': dict(max_input_pixels=0),
            'max_input_pixels bool': dict(max_input_pixels=True),
            'max_input_files negative': dict(max_input_files=-1),
            'skip rows negative': dict(text_skip_rows=-1),
            'grid cell zero': dict(text_grid_cell=0),
            'grid cell negative': dict(text_grid_cell=-1.0),
            'grid cell nan': dict(text_grid_cell=float('nan')),
            'grid cell inf': dict(text_grid_cell=float('inf')),
            'grid cell bool': dict(text_grid_cell=True),
            'radius string': dict(point_radius='2'),
            'bad delimiter': dict(text_delimiter='pipe'),
            'bad axis order': dict(text_axis_order='up_down'),
            'bad text mode': dict(text_mode='mesh'),
            'bad point method': dict(point_method='kriging'),
            'bad lidar mode': dict(lidar_mode='fancy'),
            'bad forest axis': dict(forest_axis_order='xy'),
            'columns too few': dict(text_columns=[1, 2]),
            'columns duplicated': dict(text_columns=[1, 1, 3]),
            'columns zero based': dict(text_columns=[0, 1, 2]),
            'columns not int': dict(text_columns=[1, 2, '3']),
            'ground classes empty': dict(ground_classes=[]),
            'ground class 256': dict(ground_classes=[256]),
            'ground class negative': dict(ground_classes=[-1]),
            'crs not text': dict(input_crs=6677),
            'encoding not text': dict(text_encoding=None),
            'lem scale zero': dict(forest_lem_coordinate_scale=0),
            'lem scale nan': dict(forest_lem_coordinate_scale='nan'),
            'lem scale garbage': dict(forest_lem_coordinate_scale='big'),
            'forest cell negative': dict(forest_grid_cell=-5),
            'forest cell garbage': dict(forest_grid_cell='fine'),
            'forest nodata nan': dict(forest_nodata=float('nan')),
            'forest nodata bool': dict(forest_nodata=True),
        }
        for label, override in bad.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    validate_input(cfg(**override))

    def test_accepted_boundary_values(self):
        validate_input(cfg(point_tile_size=64))
        validate_input(cfg(point_tile_size=2048))
        validate_input(cfg(text_skip_rows=0))
        validate_input(cfg(ground_classes=[0, 255]))
        validate_input(cfg(forest_nodata=None))
        validate_input(cfg(forest_lem_coordinate_scale='0.01', forest_grid_cell='2.5'))
        validate_input(cfg(text_columns=[3, 1, 2]))


class ExplicitFileParameterTests(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertEqual(explicit_file_parameter(None), [])
        self.assertEqual(explicit_file_parameter(['', None, '  ']), [])

    def test_list_semicolon_and_quotes(self):
        self.assertEqual(explicit_file_parameter(['a.tif;"b.tif"', ' c.tif ']),
                         ['a.tif', 'b.tif', 'c.tif'])


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / 'sub').mkdir()
        for name in ('a.tif', 'a.tfw', 'B.TIF', 'note.txt', 'sub/c.tiff', 'sub/d.asc'):
            (self.root / name).write_bytes(b'x')

    def names(self, inputs, **kw):
        c = cfg(**kw)
        return sorted(Path(p).name for p in discover([str(i) for i in inputs], '', c))

    def test_folder_recursive_filters_by_extension_case_insensitively(self):
        self.assertEqual(self.names([self.root]), ['B.TIF', 'a.tif', 'c.tiff', 'd.asc'])

    def test_folder_non_recursive(self):
        self.assertEqual(self.names([self.root], recursive=False), ['B.TIF', 'a.tif'])

    def test_glob_pattern(self):
        self.assertEqual(self.names([self.root / '*.tif']), ['a.tif'])

    def test_duplicate_selection_is_deduplicated(self):
        self.assertEqual(self.names([self.root / 'a.tif', self.root / 'a.tif', self.root]),
                         ['B.TIF', 'a.tif', 'c.tiff', 'd.asc'])

    def test_text_type_picks_text_extensions(self):
        (self.root / 'g.csv').write_text('x')
        self.assertEqual(self.names([self.root], input_type='text'), ['g.csv', 'note.txt'])

    def test_lidar_extensions(self):
        for name in ('p.las', 'q.LAZ', 'r.copc.laz', 's.e57'):
            (self.root / name).write_bytes(b'x')
        self.assertEqual(self.names([self.root], input_type='lidar'),
                         ['p.las', 'q.LAZ', 'r.copc.laz'])

    def test_gsi_extensions(self):
        for name in ('x.xml', 'y.zip'):
            (self.root / name).write_bytes(b'x')
        self.assertEqual(self.names([self.root], input_type='gsi'), ['x.xml', 'y.zip'])

    def test_explicit_wrong_extension_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'does not match input_type'):
            discover([str(self.root / 'note.txt')], '', cfg())

    def test_world_file_alone_is_not_an_input(self):
        with self.assertRaisesRegex(ValueError, 'companion-only'):
            discover([str(self.root / 'a.tfw')], '', cfg())

    def test_world_file_next_to_tif_is_silently_skipped(self):
        got = discover([str(self.root / 'a.tif'), str(self.root / 'a.tfw')], '', cfg())
        self.assertEqual([Path(p).name for p in got], ['a.tif'])

    def test_missing_path_and_empty_inputs(self):
        with self.assertRaisesRegex(ValueError, 'No input matched'):
            discover([str(self.root / 'missing.tif')], '', cfg())
        with self.assertRaisesRegex(ValueError, 'Specify input'):
            discover([], '', cfg())
        with self.assertRaisesRegex(ValueError, 'Specify input'):
            discover('a.tif', '', cfg())

    def test_empty_folder(self):
        (self.root / 'empty').mkdir()
        with self.assertRaisesRegex(ValueError, 'No supported files'):
            discover([str(self.root / 'empty')], '', cfg())

    def test_file_count_limit(self):
        with self.assertRaisesRegex(ValueError, 'Too many input files'):
            discover([str(self.root)], '', cfg(max_input_files=2))

    def test_japanese_file_and_folder_names(self):
        folder = self.root / '森林_成果'
        folder.mkdir()
        (folder / '図郭一.tif').write_bytes(b'x')
        self.assertEqual(self.names([folder]), ['図郭一.tif'])

    def test_relative_input_uses_base_dir(self):
        got = discover(['a.tif'], str(self.root), cfg())
        self.assertEqual(Path(got[0]), self.root / 'a.tif')


class TextRowsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def rows(self, body, encoding='utf-8', name='t.csv', **kw):
        path = write_text(self.root / name, body, encoding)
        return list(text_rows(path, cfg(**kw)))

    def test_comma_with_header(self):
        self.assertEqual(self.rows('X,Y,Z\n0,0,1\n1,0,2.5\n'), [(0, 0, 1), (1, 0, 2.5)])

    def test_header_not_skipped_is_an_error_naming_the_line(self):
        with self.assertRaisesRegex(ValueError, r't\.csv:1: check columns'):
            self.rows('X,Y,Z\n0,0,1\n', text_skip_rows=0)

    def test_skip_rows_swallows_multiple_header_lines(self):
        self.assertEqual(self.rows('meta\nX,Y,Z\n0,0,1\n', text_skip_rows=2), [(0, 0, 1)])

    def test_delimiters(self):
        self.assertEqual(self.rows('X\tY\tZ\n0\t0\t1\n', text_delimiter='tab'), [(0, 0, 1)])
        self.assertEqual(self.rows('X;Y;Z\n0;0;1\n', text_delimiter='semicolon'), [(0, 0, 1)])
        self.assertEqual(self.rows('X Y Z\n0   0  1\n', text_delimiter='space'), [(0, 0, 1)])

    def test_wrong_delimiter_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'check columns, delimiter'):
            self.rows('X;Y;Z\n0;0;1\n', text_delimiter='comma')

    def test_column_selection_and_extra_columns(self):
        self.assertEqual(self.rows('a,b,c,d\n9,1,2,3\n', text_columns=[2, 3, 4]), [(1, 2, 3)])

    def test_column_out_of_range(self):
        with self.assertRaisesRegex(ValueError, 'check columns'):
            self.rows('X,Y,Z\n0,0,1\n', text_columns=[1, 2, 9])

    def test_north_east_axis_order_swaps_columns(self):
        self.assertEqual(self.rows('N,E,Z\n10,20,5\n', text_axis_order='north_east'),
                         [(20, 10, 5)])

    def test_blank_and_whitespace_lines_are_ignored(self):
        self.assertEqual(self.rows('X,Y,Z\n\n0,0,1\n  \n1,0,2\n'), [(0, 0, 1), (1, 0, 2)])

    def test_missing_trailing_newline_and_crlf(self):
        self.assertEqual(self.rows('X,Y,Z\r\n0,0,1\r\n1,0,2'), [(0, 0, 1), (1, 0, 2)])

    def test_source_nodata_rows_are_dropped(self):
        self.assertEqual(self.rows('X,Y,Z\n0,0,-9999\n1,0,2\n', source_nodata=-9999.0),
                         [(1, 0, 2)])

    def test_nodata_not_dropped_when_not_configured(self):
        self.assertEqual(self.rows('X,Y,Z\n0,0,-9999\n', source_nodata=None), [(0, 0, -9999)])

    def test_non_numeric_value_rejected(self):
        with self.assertRaisesRegex(ValueError, 'check columns'):
            self.rows('X,Y,Z\n0,0,abc\n')

    def test_short_row_rejected(self):
        with self.assertRaisesRegex(ValueError, 'check columns'):
            self.rows('X,Y,Z\n0,0\n')

    def test_nan_and_inf_rejected_rather_than_treated_as_nodata(self):
        # Pins current behaviour: a textual NaN/Inf is a hard error, not a hole.
        for token in ('nan', 'inf', '-inf'):
            with self.subTest(token):
                with self.assertRaisesRegex(ValueError, 'non-finite'):
                    self.rows(f'X,Y,Z\n0,0,{token}\n')

    def test_cp932_file_with_matching_encoding(self):
        self.assertEqual(self.rows('標高X,標高Y,標高Z\n0,0,1\n', encoding='cp932',
                                   text_encoding='cp932'), [(0, 0, 1)])

    def test_utf8_bom_header_is_tolerated_by_default_encoding(self):
        self.assertEqual(self.rows('﻿X,Y,Z\n0,0,1\n'), [(0, 0, 1)])

    def test_wrong_encoding_gives_a_decode_error_not_garbage(self):
        with self.assertRaises(UnicodeDecodeError):
            self.rows('標高X,標高Y,標高Z\n0,0,1\n', encoding='cp932')

    def test_unknown_encoding_name(self):
        with self.assertRaises(LookupError):
            self.rows('X,Y,Z\n0,0,1\n', text_encoding='no-such-codec')

    def test_empty_file_yields_no_rows(self):
        self.assertEqual(self.rows(''), [])

    def test_float_formats(self):
        self.assertEqual(self.rows('X,Y,Z\n1e2,-0.5,+3\n'), [(100.0, -0.5, 3.0)])


class AutodetectAndGridCellTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_autodetect_header_and_delimiters(self):
        for body, delim, skip in [('X,Y,Z\n1,2,3\n', 'comma', 1),
                                  ('X\tY\tZ\n1\t2\t3\n', 'tab', 1),
                                  ('1 2 3\n4 5 6\n', 'space', 0),
                                  ('1;2;3\n', 'semicolon', 0)]:
            with self.subTest(delim):
                d = autodetect_xyz(write_text(self.root / 'a.txt', body), 'east_north')
                self.assertEqual((d['text_delimiter'], d['text_skip_rows']), (delim, skip))
                self.assertEqual(d['text_columns'], [1, 2, 3])

    def test_autodetect_skips_extra_non_numeric_prelude(self):
        d = autodetect_xyz(write_text(self.root / 'a.csv', 'title\nX,Y,Z\n1,2,3\n'))
        self.assertEqual(d['text_skip_rows'], 2)

    def test_autodetect_rejects_text_without_three_numeric_columns(self):
        with self.assertRaisesRegex(ValueError, 'three numeric'):
            autodetect_xyz(write_text(self.root / 'a.txt', 'hello world\nfoo,bar\n'))

    def test_autodetect_rejects_non_utf8_non_cp932_bytes(self):
        path = self.root / 'bin.txt'
        path.write_bytes(b'\xff\xfe\x00\x81\x00')
        with self.assertRaises(ValueError):
            autodetect_xyz(path)

    def test_autodetect_accepts_cp932(self):
        d = autodetect_xyz(write_text(self.root / 'j.csv', '東,北,高\n1,2,3\n', 'cp932'))
        self.assertEqual(d['text_encoding'], 'cp932')

    def test_infer_grid_cell_row_major(self):
        path = write_text(self.root / 'g.csv', 'X,Y,Z\n0,0,1\n5,0,1\n0,5,1\n5,5,1\n')
        self.assertEqual(infer_grid_cell(path, cfg(text_columns=[1, 2, 3])), 5.0)

    def test_infer_grid_cell_rejects_unequal_intervals(self):
        path = write_text(self.root / 'g.csv', 'X,Y,Z\n0,0,1\n5,0,1\n0,10,1\n')
        with self.assertRaisesRegex(ValueError, 'different X/Y'):
            infer_grid_cell(path, cfg(text_columns=[1, 2, 3]))

    def test_infer_grid_cell_single_point_cannot_be_inferred(self):
        path = write_text(self.root / 'g.csv', 'X,Y,Z\n0,0,1\n')
        with self.assertRaisesRegex(ValueError, 'fewer than two'):
            infer_grid_cell(path, cfg(text_columns=[1, 2, 3]))


if __name__ == '__main__':
    unittest.main()
