"""GSI DEM XML (JPGIS/GML) parser contract tests (no GDAL).

Pins the externally visible behaviour of gsi_dem.parse_dem so that the planned
R-8 (XML guard) / R-9 (memory) rework by the Codex track cannot change what is
accepted, rejected, or how NoData and georeferencing come out.  Nothing here
depends on how the guard is implemented (DTD detection strategy, memory layout).
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_fixtures import gsi_xml  # noqa: E402

from dem_map_sheets.engine.gsi_dem import NODATA, parse_dem, traversal_indices  # noqa: E402


def parse(xml, **kw):
    data = xml.encode('utf-8') if isinstance(xml, str) else xml
    return list(parse_dem(data, **kw))


class GsiParseTests(unittest.TestCase):
    def test_good_file_values_nodata_and_georeference(self):
        (array, gt, crs, meta), = parse(gsi_xml())
        self.assertEqual(array.dtype, np.float32)
        np.testing.assert_allclose(array, [[10.5, 11.5], [12.5, NODATA]])
        west, xres, _, north, _, yres = gt
        self.assertAlmostEqual(west, 139.0)
        self.assertAlmostEqual(north, 35.1)
        self.assertAlmostEqual(xres, 0.05, places=9)
        self.assertAlmostEqual(yres, -0.05, places=9)
        self.assertEqual(crs, 'EPSG:6668')
        self.assertEqual(meta['mesh'], '5139')
        self.assertEqual(meta['populated_count'], 4)
        self.assertFalse(meta['jgd2024_horizontal_alias'])

    def test_nodata_conventions(self):
        # -9999 value, 'データなし' and '海水面' kinds, and NaN all become NODATA.
        tuples = 'データなし,5.0\n海水面,3.0\n地表面,-9999.0\n地表面,nan'
        (array, *_), = parse(gsi_xml(tuples))
        self.assertTrue((array == NODATA).all())

    def test_legitimate_zero_and_negative_elevations_are_kept(self):
        (array, *_), = parse(gsi_xml('地表面,0.0\n地表面,-1.5\n地表面,0.1\n地表面,-9998.9'))
        np.testing.assert_allclose(array, [[0.0, -1.5], [0.1, -9998.9]], atol=1e-3)

    def test_crs_aliases(self):
        cases = {'fguuid:jgd2000.bl': 'EPSG:4612', 'fguuid:jgd2011.bl': 'EPSG:6668',
                 'fguuid:jgd2024.bl': 'EPSG:6668', 'FGUUID:JGD2011.BL': 'EPSG:6668'}
        for label, expected in cases.items():
            with self.subTest(label):
                (_, _, crs, _), = parse(gsi_xml(srs=label))
                self.assertEqual(crs, expected)

    def test_jgd2024_alias_is_flagged_only_without_override(self):
        (_, _, _, meta), = parse(gsi_xml(srs='fguuid:jgd2024.bl'))
        self.assertTrue(meta['jgd2024_horizontal_alias'])
        (_, _, crs, meta), = parse(gsi_xml(srs='fguuid:jgd2024.bl'), source_override='EPSG:4612')
        self.assertEqual(crs, 'EPSG:4612')
        self.assertFalse(meta['jgd2024_horizontal_alias'])

    def test_unknown_srs_name_is_passed_through_for_later_crs_check(self):
        (_, _, crs, _), = parse(gsi_xml(srs='EPSG:4612'))
        self.assertEqual(crs, 'EPSG:4612')

    def test_missing_crs_requires_explicit_override(self):
        with self.assertRaisesRegex(ValueError, 'CRS is missing'):
            parse(gsi_xml(srs=''))
        (_, _, crs, _), = parse(gsi_xml(srs=''), source_override='EPSG:6668')
        self.assertEqual(crs, 'EPSG:6668')

    def test_traversal_orders(self):
        # '-y' walks the rows top-to-bottom from (0,0) (GSI default '+x-y');
        # '+y' walks upward, so its natural start point is the bottom-left (0,1).
        (array, *_), = parse(gsi_xml('a,1\na,2\na,3\na,4', order='+x-y'))
        np.testing.assert_allclose(array, [[1, 2], [3, 4]])
        (array, *_), = parse(gsi_xml('a,1\na,2\na,3\na,4', order='+x+y', start='0 1'))
        np.testing.assert_allclose(array, [[3, 4], [1, 2]])

    def test_partial_mesh_with_start_point_leaves_leading_cells_nodata(self):
        (array, _, _, meta), = parse(gsi_xml('a,7\na,8', start='0 1'))
        np.testing.assert_allclose(array, [[NODATA, NODATA], [7, 8]])
        self.assertEqual(meta['start_point'], [0, 1])

    def test_multiple_dem_elements_yield_multiple_grids(self):
        one = gsi_xml()
        body = one[one.index('<DEM>'):one.index('</DEM>') + 6]
        two = one.replace(body, body + body)
        self.assertEqual(len(parse(two)), 2)

    def test_shift_jis_declared_encoding(self):
        xml = gsi_xml(encoding='Shift_JIS').encode('shift_jis')
        (array, *_), = parse(xml)
        self.assertEqual(array.shape, (2, 2))

    def test_file_without_dem_element_yields_nothing(self):
        self.assertEqual(parse('<Dataset></Dataset>'), [])


class GsiRejectionTests(unittest.TestCase):
    def assertRejected(self, xml, pattern=None, exc=ValueError, **kw):
        ctx = self.assertRaisesRegex(exc, pattern) if pattern else self.assertRaises(exc)
        with ctx:
            parse(xml, **kw)

    def test_dtd_and_entity_declarations_are_rejected(self):
        good = gsi_xml()
        bombs = {
            'doctype': '<!DOCTYPE foo [<!ENTITY x "y">]>',
            'doctype no subset': '<!DOCTYPE Dataset>',
            'entity only': '<!ENTITY x "y">',
            'lower-case doctype': '<!doctype foo [<!entity x "y">]>',
        }
        for label, prefix in bombs.items():
            with self.subTest(label):
                xml = good.replace('<Dataset', prefix + '<Dataset', 1)
                # A lower-case DOCTYPE is not a valid XML DTD either: either the
                # guard or the parser must refuse it, never produce a grid.
                with self.assertRaises((ValueError, SyntaxError)):
                    parse(xml)

    def test_external_entity_reference_is_never_resolved(self):
        xml = gsi_xml().replace(
            '<Dataset', '<!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><Dataset', 1)
        self.assertRejected(xml, 'DTD|entity')

    def test_utf16_document_with_dtd_is_not_parsed(self):
        # Guards the "byte-level ASCII search misses UTF-16" bypass (review r3 G-1).
        # Today the file is refused at decode time; after R-8 it must still be refused.
        xml = gsi_xml(encoding='UTF-16').replace(
            '<Dataset', '<!DOCTYPE d [<!ENTITY x "y">]><Dataset', 1)
        with self.assertRaises((ValueError, SyntaxError)):
            parse(xml.encode('utf-16'))

    def test_extent_must_be_ordered_latitude_longitude(self):
        for label, kw in {
                'south above north': dict(lower='36 139', upper='35 139.1'),
                'west east of east': dict(lower='35 139.1', upper='35.1 139'),
                'zero-area': dict(lower='35 139', upper='35 139.1'),
                'lat/lon swapped (lon 139 as lat)': dict(lower='139 35', upper='139.1 35.1'),
                'latitude beyond 90': dict(lower='89 139', upper='91 139.1'),
                'longitude beyond 180': dict(lower='35 179', upper='35.1 181'),
                'three coordinates': dict(lower='35 139 0', upper='35.1 139.1 0'),
                }.items():
            with self.subTest(label):
                self.assertRejected(gsi_xml(**kw), 'extent|latitude/longitude')

    def test_nonnumeric_extent_is_an_error(self):
        self.assertRejected(gsi_xml(lower='north west'))

    def test_pixel_limit_boundary(self):
        # 2x2 grid: exactly the limit passes, one below fails.
        self.assertEqual(len(parse(gsi_xml(), max_pixels=4)), 1)
        self.assertRejected(gsi_xml(), 'input pixel limit', max_pixels=3)

    def test_empty_or_inverted_grid_envelope(self):
        self.assertRejected(gsi_xml('', high='-1 -1'), 'pixel limit')
        self.assertRejected(gsi_xml(low='2 2', high='1 1'), 'pixel limit')

    def test_grid_envelope_must_be_2d(self):
        self.assertRejected(gsi_xml(high='1 1 1'), '2D')

    def test_start_point_outside_grid(self):
        self.assertRejected(gsi_xml(start='5 5'), 'startPoint outside grid')

    def test_unsupported_traversal(self):
        self.assertRejected(gsi_xml(rule='Spiral'), 'Linear')
        self.assertRejected(gsi_xml(order='+x+x'), 'traversal')
        self.assertRejected(gsi_xml(order='xyz'), 'traversal')

    def test_more_tuples_than_cells(self):
        self.assertRejected(gsi_xml('a,1\n' * 5), 'Too many GSI tuples')

    def test_malformed_tuple(self):
        self.assertRejected(gsi_xml('a,1,2\na,1\na,1\na,1'), 'Malformed GSI tuple')
        self.assertRejected(gsi_xml('onlyonefield\na,1\na,1\na,1'), 'Malformed GSI tuple')

    def test_non_numeric_elevation_is_rejected(self):
        # Message is the bare float() error today (no tuple position): still a ValueError.
        self.assertRejected(gsi_xml('a,xyz\na,1\na,1\na,1'))

    def test_truncated_and_non_xml_input(self):
        good = gsi_xml()
        for label, data in {'truncated': good[:300], 'not xml': 'hello', 'empty': ''}.items():
            with self.subTest(label):
                self.assertRejected(data, exc=(ValueError, SyntaxError))

    def test_binary_garbage(self):
        self.assertRejected(b'\x00\x01\x02\xff\xfe', exc=(ValueError, SyntaxError))

    def test_unknown_declared_encoding(self):
        # LookupError today (review r3 G-6); after R-8 expected to become ValueError.
        xml = gsi_xml(encoding='x-no-such-codec')
        self.assertRejected(xml, exc=(ValueError, LookupError))

    def test_size_guard_for_oversized_payload(self):
        # The size check precedes parsing, so a few MiB of zeros stays cheap; the
        # limit itself (256 MiB) is not exercised to keep the suite light.
        self.assertRejected(b'x' * 16, exc=(ValueError, SyntaxError))

    def test_unsupported_tuple_separator(self):
        xml = gsi_xml().replace('<gml:tupleList>', '<gml:tupleList cs=";">')
        self.assertRejected(xml, 'separators')

    def test_cancellation_is_honoured(self):
        class Cancelled:
            def isCanceled(self):
                return True
        with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
            parse(gsi_xml(), feedback=Cancelled())


class TraversalTests(unittest.TestCase):
    def test_start_point_offsets_the_walk(self):
        got = list(traversal_indices([0, 0], [2, 1], [1, 0], '+x-y'))
        self.assertEqual(got[0], (1, 0))
        self.assertEqual(len(got), 5)

    def test_start_outside_traversal(self):
        with self.assertRaisesRegex(ValueError, 'startPoint'):
            traversal_indices([0, 0], [1, 1], [9, 9], '+x-y')


if __name__ == '__main__':
    unittest.main()
