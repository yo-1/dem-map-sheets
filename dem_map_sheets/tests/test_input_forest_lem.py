"""Forestry airborne-laser DEM inputs: LEM, source classification, ZIP safety (no GDAL).

Covers forest_dem.read_lem / classify_sources / expand_sources / extract_archive.
GDAL-dependent end-to-end checks of the same formats live in
test_input_gdal_formats.py.
"""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_fixtures import (LEM_META_TEMPLATE, lem_row, standard_lem, write_lem,  # noqa: E402
                            write_text, write_zip)

from dem_map_sheets.engine import forest_dem  # noqa: E402
from dem_map_sheets.engine.forest_dem import (  # noqa: E402
    NODATA, classify_sources, expand_sources, extract_archive, is_lem_metadata,
    read_lem, read_lem_metadata)

BIG = 10 ** 9


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class LemReadTests(TempDirCase):
    def test_values_nodata_codes_and_georeference(self):
        lem, meta = standard_lem(self.root)
        array, gt, detail = read_lem(lem, meta, BIG)
        # raw units are 0.1 m; -9999 and -1111 are the two NoData codes
        expected = np.array([[10.0, 11.0, NODATA], [NODATA, 12.0, 13.0]], dtype='float32')
        np.testing.assert_allclose(array, expected)
        self.assertEqual(array.dtype, np.float32)
        self.assertEqual(gt, (0.0, 5.0, 0.0, 10.0, 0.0, -5.0))
        # survey_year is kept as the metadata text; dem_pipeline._report_year() int()s it.
        self.assertEqual((detail['zone'], int(detail['survey_year']), detail['coordinate_scale']),
                         (9, 2020, 0.01))
        self.assertEqual(detail['nodata_codes'], [-9999, -1111])

    def test_negative_elevations_and_zero(self):
        lem, meta = write_lem(self.root, 'neg', [lem_row(1, [-50, 0, 5]), lem_row(2, [-9998, 1, 2])])
        array, _, _ = read_lem(lem, meta, BIG)
        np.testing.assert_allclose(array[0], [-5.0, 0.0, 0.5], atol=1e-6)
        self.assertAlmostEqual(float(array[1, 0]), -999.8, places=3)

    def test_txt_metadata_companion(self):
        lem, meta = standard_lem(self.root, meta_suffix='.txt')
        array, _, _ = read_lem(lem, meta, BIG)
        self.assertEqual(array.shape, (2, 3))

    def test_cp932_metadata(self):
        lem, meta = standard_lem(self.root, encoding='cp932')
        self.assertEqual(read_lem_metadata(meta)['encoding'], 'cp932')
        read_lem(lem, meta, BIG)

    def test_metadata_in_meter_units_is_detected(self):
        lem, meta = standard_lem(self.root, scale=1)
        _, gt, detail = read_lem(lem, meta, BIG)
        self.assertEqual(detail['coordinate_scale'], 1.0)
        self.assertEqual(gt[3], 10.0)

    def test_explicit_scale_overrides_detection(self):
        lem, meta = standard_lem(self.root)
        _, _, detail = read_lem(lem, meta, BIG, coordinate_scale='0.01')
        self.assertEqual(detail['coordinate_scale'], 0.01)

    def test_inconsistent_bounds_demand_explicit_scale(self):
        lem, meta = standard_lem(self.root, bounds=(99999, 99999))
        with self.assertRaisesRegex(ValueError, 'forest_lem_coordinate_scale'):
            read_lem(lem, meta, BIG)

    def test_invalid_explicit_scale(self):
        lem, meta = standard_lem(self.root)
        for bad in ('0', '-1', 'nan', 'inf'):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    read_lem(lem, meta, BIG, coordinate_scale=bad)

    def test_pixel_limit_boundary(self):
        lem, meta = standard_lem(self.root)  # 3 x 2 = 6 cells
        read_lem(lem, meta, 6)
        with self.assertRaisesRegex(ValueError, 'input pixel limit'):
            read_lem(lem, meta, 5)

    def test_limit_is_checked_before_the_data_file_is_read(self):
        # A huge declared grid must be refused from the header alone.
        meta_text = LEM_META_TEMPLATE.format(nx=100000, ny=100000, dx=5, dy=5, south=0, west=0,
                                             north=50000000, east=50000000, zone=9, year=2020, sheet='h')
        lem, meta = write_lem(self.root, 'huge', [lem_row(1, [1])], meta_override=meta_text)
        with self.assertRaisesRegex(ValueError, 'pixel limit'):
            read_lem(lem, meta, 25_000_000)

    def test_row_count_mismatch(self):
        lem, meta = write_lem(self.root, 'short', [lem_row(1, [1, 2, 3])])
        with self.assertRaisesRegex(ValueError, 'row count mismatch'):
            read_lem(lem, meta, BIG)

    def test_extra_rows(self):
        rows = [lem_row(i, [1, 2, 3]) for i in (1, 2, 3)]
        lem, meta = write_lem(self.root, 'long', rows)
        with self.assertRaisesRegex(ValueError, 'row count mismatch'):
            read_lem(lem, meta, BIG)

    def test_truncated_row(self):
        lem, meta = write_lem(self.root, 'trunc', [lem_row(1, [1, 2, 3]), lem_row(2, [1, 2, 3])[:-3]])
        with self.assertRaisesRegex(ValueError, 'row 2 is shorter'):
            read_lem(lem, meta, BIG)

    def test_record_number_mismatch(self):
        lem, meta = write_lem(self.root, 'rec', [lem_row(1, [1, 2, 3]), lem_row(7, [1, 2, 3])])
        with self.assertRaisesRegex(ValueError, 'record number mismatch'):
            read_lem(lem, meta, BIG)

    def test_non_numeric_cell_reports_row_and_column(self):
        bad = lem_row(2, [1, 2, 3]).replace('    3', '  ab3')
        lem, meta = write_lem(self.root, 'abc', [lem_row(1, [1, 2, 3]), bad])
        with self.assertRaisesRegex(ValueError, 'row 2, column 3'):
            read_lem(lem, meta, BIG)

    def test_blank_lines_between_rows_are_tolerated(self):
        lem, meta = standard_lem(self.root)
        lem.write_text('\n'.join([lem_row(1, [100, 110, -9999]), '', lem_row(2, [-1111, 120, 130])]) + '\n')
        read_lem(lem, meta, BIG)

    def test_undecodable_data_file(self):
        lem, meta = standard_lem(self.root)
        lem.write_bytes(b'\xff\xfe\x00\xfa\xfb')
        with self.assertRaises(ValueError):
            read_lem(lem, meta, BIG)

    def test_missing_required_header_field(self):
        text = LEM_META_TEMPLATE.format(nx=3, ny=2, dx=5, dy=5, south=0, west=0, north=1000,
                                        east=1500, zone=9, year=2020, sheet='x')
        text = text.replace('東西方向の点数,3\n', '')
        lem, meta = write_lem(self.root, 'nox', [lem_row(1, [1, 2, 3]), lem_row(2, [1, 2, 3])],
                              meta_override=text)
        with self.assertRaisesRegex(ValueError, 'missing: nx'):
            read_lem(lem, meta, BIG)

    def test_cancellation(self):
        lem, meta = standard_lem(self.root)

        class Cancelled:
            def isCanceled(self):
                return True
        with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
            read_lem(lem, meta, BIG, feedback=Cancelled())


class LemMetadataDetectionTests(TempDirCase):
    def test_probe_recognises_real_metadata_and_rejects_xyz(self):
        _, meta = standard_lem(self.root)
        self.assertTrue(is_lem_metadata(meta))
        self.assertFalse(is_lem_metadata(write_text(self.root / 'grid.txt', 'X,Y,Z\n0,0,1\n')))
        self.assertFalse(is_lem_metadata(self.root / 'absent.txt'))
        self.assertFalse(is_lem_metadata(write_text(self.root / 'empty.csv', '')))

    def test_probe_reads_only_a_bounded_prefix(self):
        # A ~3 MiB text grid must not be read in full just to be ruled out.
        big = self.root / 'big.txt'
        big.write_text('X,Y,Z\n' + '0,0,1\n' * 500_000)
        with patch.object(forest_dem.Path, 'read_bytes',
                          side_effect=AssertionError('whole file read')):
            self.assertFalse(is_lem_metadata(big))

    def test_zero_point_counts_are_not_metadata(self):
        text = LEM_META_TEMPLATE.format(nx=0, ny=0, dx=5, dy=5, south=0, west=0, north=0,
                                        east=0, zone=9, year=2020, sheet='z')
        self.assertFalse(is_lem_metadata(write_text(self.root / 'z.csv', text)))


class ClassifySourcesTests(TempDirCase):
    def test_lem_pair_grid_and_rasters_are_classified(self):
        lem, meta = standard_lem(self.root)
        grid = write_text(self.root / 'grid.csv', 'X,Y,Z\n0,0,1\n')
        tif = self.root / 'a.tif'
        tif.write_bytes(b'x')
        kinds = {r['path'].name: r['kind'] for r in classify_sources([lem, meta, grid, tif])}
        self.assertEqual(kinds, {'ok.lem': 'lem', 'grid.csv': 'grid', 'a.tif': 'raster'})

    def test_sidecars_are_never_primary_inputs(self):
        tif = self.root / 'a.tif'
        tif.write_bytes(b'x')
        side = [self.root / n for n in ('a.tfw', 'a.tifw', 'a.wld', 'a.prj', 'a.tif.aux.xml')]
        for p in side:
            p.write_bytes(b'x')
        self.assertEqual([r['path'].name for r in classify_sources([tif, *side])], ['a.tif'])

    def test_lem_without_metadata_is_rejected(self):
        lem = self.root / 'orphan.lem'
        lem.write_text('x')
        with self.assertRaisesRegex(ValueError, 'requires its companion'):
            classify_sources([lem])

    def test_lem_with_non_metadata_companion_is_rejected(self):
        lem = self.root / 'p.lem'
        lem.write_text('x')
        write_text(self.root / 'p.csv', 'X,Y,Z\n0,0,1\n')
        with self.assertRaisesRegex(ValueError, 'requires its companion'):
            classify_sources([lem, self.root / 'p.csv'])

    def test_metadata_without_lem_is_rejected(self):
        _, meta = standard_lem(self.root)
        with self.assertRaisesRegex(ValueError, 'no companion .lem'):
            classify_sources([meta])

    def test_csv_wins_over_txt_when_both_exist(self):
        lem, meta_csv = standard_lem(self.root)
        write_text(self.root / 'ok.txt', 'garbage\n')
        got = classify_sources([lem, self.root / 'ok.txt', meta_csv])
        self.assertEqual([r for r in got if r['kind'] == 'lem'][0]['metadata'].suffix, '.csv')

    def test_nothing_usable(self):
        with self.assertRaisesRegex(ValueError, 'No primary'):
            classify_sources([self.root / 'a.prj'])

    def test_expand_sources_pulls_in_companion_by_stem(self):
        lem, meta = standard_lem(self.root, meta_suffix='.txt')
        expanded, archives = expand_sources([str(lem)], self.root / 'work')
        self.assertEqual({p.name for p in expanded}, {'ok.lem', 'ok.txt'})
        self.assertEqual(archives, [])


class ZipSafetyTests(TempDirCase):
    _counter = 0

    def extract(self, members, **limits):
        # Every call gets its own archive/destination: extract_archive() insists
        # on creating a fresh destination folder.  Destinations sit one level
        # below self.root so that even a regression that honours '..' or an
        # absolute name can only write inside the temporary directory.
        ZipSafetyTests._counter += 1
        n = ZipSafetyTests._counter
        archive = write_zip(self.root / f'in{n}.zip', members)
        dest = self.root / 'sandbox' / f'out{n}'
        dest.parent.mkdir(exist_ok=True)
        limits = {'ARCHIVE_MEMBER_LIMIT': forest_dem.ARCHIVE_MEMBER_LIMIT,
                  'ARCHIVE_TOTAL_LIMIT': forest_dem.ARCHIVE_TOTAL_LIMIT, **limits}
        with patch.multiple(forest_dem, **limits):
            return extract_archive(archive, dest), dest

    def test_supported_members_and_sidecars_are_extracted_unsupported_skipped(self):
        got, dest = self.extract([('dir/A.TIF', b'1'), ('dir/A.TFW', b'2'),
                                  ('dir/a.tif.aux.xml', b'3'), ('readme.pdf', b'4'),
                                  ('dir/', b'')])
        self.assertEqual(sorted(p.name for p in got), ['A.TFW', 'A.TIF', 'a.tif.aux.xml'])
        self.assertFalse((dest / 'readme.pdf').exists())

    def test_unsafe_member_paths_are_refused_and_nothing_escapes(self):
        target = self.root / 'abs_target' / 'evil.tif'  # an absolute name that stays in the sandbox
        for name in ('../evil.tif', '../../evil.tif', str(target), 'C:\\evil.tif',
                     'a\\..\\..\\evil.tif', 'ok/../../evil.tif', '../notsupported.exe'):
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, 'Unsafe ZIP member'):
                    self.extract([('fine.tif', b'1'), (name, b'x')])
                self.assertEqual(list(self.root.rglob('evil.tif')), [])
                self.assertEqual(list(self.root.rglob('notsupported.exe')), [])
        self.assertFalse(target.parent.exists())

    def test_zip_without_supported_members(self):
        for members in ([], [('readme.pdf', b'x')], [('nested.zip', b'PK')]):
            with self.subTest(members):
                with self.assertRaisesRegex(ValueError, 'no supported forestry DEM'):
                    self.extract(members)

    def test_member_size_limit(self):
        with self.assertRaisesRegex(ValueError, 'exceeds 512 MiB'):
            self.extract([('big.tif', b'x' * 100)], ARCHIVE_MEMBER_LIMIT=10)

    def test_member_size_limit_boundary_is_inclusive(self):
        got, _ = self.extract([('edge.tif', b'x' * 10)], ARCHIVE_MEMBER_LIMIT=10)
        self.assertEqual(len(got), 1)

    def test_total_size_limit(self):
        with self.assertRaisesRegex(ValueError, 'exceed 8 GiB'):
            self.extract([('a.tif', b'x' * 100), ('b.tif', b'x' * 100)], ARCHIVE_TOTAL_LIMIT=150)

    def test_unsupported_members_do_not_count_toward_limits(self):
        got, _ = self.extract([('a.tif', b'x'), ('huge.bin', b'x' * 1000)],
                              ARCHIVE_MEMBER_LIMIT=10, ARCHIVE_TOTAL_LIMIT=10)
        self.assertEqual(len(got), 1)

    def test_corrupt_zip(self):
        bad = self.root / 'bad.zip'
        bad.write_bytes(b'this is not a zip')
        with self.assertRaises(zipfile.BadZipFile):
            extract_archive(bad, self.root / 'out')

    def test_truncated_zip(self):
        good = write_zip(self.root / 'g.zip', [('a.tif', b'x' * 5000)])
        data = good.read_bytes()
        (self.root / 't.zip').write_bytes(data[: len(data) // 2])
        with self.assertRaises((zipfile.BadZipFile, OSError)):
            extract_archive(self.root / 't.zip', self.root / 'out')

    def test_destination_must_be_new(self):
        write_zip(self.root / 'a.zip', [('a.tif', b'x')])
        (self.root / 'out').mkdir()
        with self.assertRaises(FileExistsError):
            extract_archive(self.root / 'a.zip', self.root / 'out')

    def test_japanese_member_names(self):
        got, _ = self.extract([('森林/図郭一.tif', b'x')])
        self.assertEqual(got[0].name, '図郭一.tif')

    def test_expand_sources_names_extraction_folders_per_archive(self):
        write_zip(self.root / 'a.zip', [('x.tif', b'1')])
        write_zip(self.root / 'b.zip', [('x.tif', b'2')])
        expanded, archives = expand_sources([str(self.root / 'a.zip'), str(self.root / 'b.zip')],
                                            self.root / 'work')
        self.assertEqual(len(expanded), 2)
        self.assertEqual(len({p.parent for p in expanded}), 2)
        self.assertEqual([a['members'] for a in archives], [1, 1])


if __name__ == '__main__':
    unittest.main()
