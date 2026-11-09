"""R-8/R-9 regressions for the GSI XML parser.

R-8: the DTD/entity guard used to search the raw bytes, which UTF-16
input bypasses, and decode errors escaped as LookupError /
UnicodeDecodeError. The guard now runs on the decoded text, the
declared encoding is allowlisted, and decode failures are ValueError.
R-9: traversal_indices used to materialise all w*h coordinate tuples;
it now locates the start arithmetically and generates lazily, with
byte-identical ordering.
"""
import itertools
import re
import unittest

from dem_map_sheets.engine.gsi_dem import parse_dem, traversal_indices

XML = ('<?xml version="1.0" encoding="{enc}"?>'
       '<FG-GML><DEM><Envelope srsName="fguuid:jgd2011.bl">'
       '<lowerCorner>35.0 139.0</lowerCorner><upperCorner>35.1 139.1</upperCorner>'
       '</Envelope><GridEnvelope><low>0 0</low><high>1 1</high></GridEnvelope>'
       '<startPoint>0 1</startPoint>'
       '<tupleList>land,100.0 land,101.0 land,102.0 land,103.0</tupleList>'
       '</DEM></FG-GML>')


def _reference_traversal(lo, hi, start, order):
    """The pre-R-9 list-based algorithm, kept as the ordering oracle."""
    match = re.fullmatch(r'([+-])([xy])([+-])([xy])', order.replace(' ', ''))
    axes = [(match.group(2), match.group(1)), (match.group(4), match.group(3))]
    limits = {'x': (lo[0], hi[0]), 'y': (lo[1], hi[1])}
    ranges = {}
    for axis, sign in axes:
        low, high = limits[axis]
        increasing = (sign == '+') if axis == 'x' else (sign == '-')
        ranges[axis] = range(low, high + 1) if increasing else range(high, low - 1, -1)
    coords = []
    fast, slow = axes[0][0], axes[1][0]
    for slow_value in ranges[slow]:
        for fast_value in ranges[fast]:
            point = {'x': 0, 'y': 0}
            point[slow] = slow_value
            point[fast] = fast_value
            coords.append((point['x'], point['y']))
    return coords[coords.index(tuple(start)):]


class TraversalEquivalenceTests(unittest.TestCase):
    def test_orders_and_starts_match_reference(self):
        lo, hi = (0, 0), (3, 2)
        for order in ('+x+y', '+x-y', '-x+y', '-x-y',
                      '+y+x', '+y-x', '-y+x', '-y-x'):
            for start in ((0, 0), (3, 2), (1, 1), (2, 0)):
                with self.subTest(order=order, start=start):
                    self.assertEqual(list(traversal_indices(lo, hi, start, order)),
                                     _reference_traversal(lo, hi, start, order))

    def test_start_outside_grid_raises(self):
        with self.assertRaises(ValueError):
            list(traversal_indices((0, 0), (3, 2), (9, 9), '+x+y'))

    def test_huge_grid_is_lazy(self):
        indices = traversal_indices((0, 0), (19999, 19999), (0, 19999), '+x+y')
        self.assertEqual(list(itertools.islice(indices, 3)),
                         [(0, 19999), (1, 19999), (2, 19999)])


class TraversalEndToEndTests(unittest.TestCase):
    RULE_XML = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<FG-GML><DEM><Envelope srsName="fguuid:jgd2011.bl">'
                '<lowerCorner>35.0 139.0</lowerCorner><upperCorner>35.1 139.1</upperCorner>'
                '</Envelope><GridEnvelope><low>0 0</low><high>1 1</high></GridEnvelope>'
                '<sequenceRule order="{order}">Linear</sequenceRule>'
                '<startPoint>{start}</startPoint>'
                '<tupleList>{tuples}</tupleList>'
                '</DEM></FG-GML>')

    def test_parse_order_and_start_placement(self):
        data = self.RULE_XML.format(
            order='+x-y', start='0 0',
            tuples='land,100.0 land,101.0 land,102.0 land,103.0').encode('utf-8')
        arr, _gt, _crs, meta = list(parse_dem(data))[0]
        self.assertEqual(arr.tolist(), [[100.0, 101.0], [102.0, 103.0]])
        self.assertEqual(meta['traversal_order'], '+x-y')
        self.assertEqual(meta['start_point'], [0, 0])

    def test_parse_mid_grid_start_keeps_remaining_cells(self):
        data = self.RULE_XML.format(
            order='+x+y', start='0 0',
            tuples='land,102.0 land,103.0').encode('utf-8')
        arr, _gt, _crs, meta = list(parse_dem(data))[0]
        self.assertEqual(meta['populated_count'], 2)
        self.assertEqual(arr[0].tolist(), [102.0, 103.0])

    def test_traversal_outside_start_message(self):
        with self.assertRaises(ValueError) as ctx:
            list(traversal_indices((0, 0), (3, 2), (9, 9), '+x+y'))
        self.assertEqual(str(ctx.exception), 'GSI startPoint outside grid traversal')

    def test_max_pixels_default_unchanged(self):
        self.assertEqual(parse_dem.__defaults__[0], 25000000)


class GsiEncodingGuardTests(unittest.TestCase):
    def test_valid_utf8_parses(self):
        results = list(parse_dem(XML.format(enc='UTF-8').encode('utf-8')))
        self.assertEqual(len(results), 1)
        arr = results[0][0]
        self.assertEqual(sorted(arr.flatten().tolist()), [100.0, 101.0, 102.0, 103.0])

    def test_valid_shift_jis_declaration_parses(self):
        results = list(parse_dem(XML.format(enc='Shift_JIS').encode('shift_jis')))
        self.assertEqual(len(results), 1)

    def test_no_declaration_defaults_to_utf8(self):
        body = XML.format(enc='UTF-8').split('?>', 1)[1]
        results = list(parse_dem(body.replace('{enc}', 'UTF-8').encode('utf-8')))
        self.assertEqual(len(results), 1)

    def test_utf8_bom_with_and_without_declaration(self):
        with_decl = b'\xef\xbb\xbf' + XML.format(enc='UTF-8').encode('utf-8')
        self.assertEqual(len(list(parse_dem(with_decl))), 1)
        body = XML.format(enc='UTF-8').split('?>', 1)[1]
        without_decl = b'\xef\xbb\xbf' + body.encode('utf-8')
        self.assertEqual(len(list(parse_dem(without_decl))), 1)

    def test_shift_jis_cp932_extension_characters(self):
        # U+9AD9 exists in cp932 (Windows-31J) but not in strict
        # JIS X 0208 shift_jis; a Shift_JIS-declared file containing
        # it must still parse.
        text = XML.format(enc='Shift_JIS').replace(
            '</DEM>', '<mesh>\u9ad9</mesh></DEM>')
        results = list(parse_dem(text.encode('cp932')))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][3]['mesh'], '\u9ad9')

    def test_doctype_in_utf8_is_rejected(self):
        data = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<!DOCTYPE FG-GML [<!ENTITY x "y">]>' + XML.format(enc='UTF-8')[39:]).encode('utf-8')
        with self.assertRaises(ValueError):
            list(parse_dem(data))

    def test_utf16_with_doctype_is_rejected(self):
        text = ('<?xml version="1.0" encoding="UTF-16"?>'
                '<!DOCTYPE FG-GML [<!ENTITY x "y">]><FG-GML/>')
        with self.assertRaises(ValueError):
            list(parse_dem(text.encode('utf-16')))

    def test_utf16_declaration_is_rejected(self):
        with self.assertRaises(ValueError):
            list(parse_dem(XML.format(enc='UTF-16').encode('utf-8')))

    def test_unlisted_encoding_is_rejected(self):
        with self.assertRaises(ValueError):
            list(parse_dem(XML.format(enc='koi8-r').encode('utf-8')))

    def test_undecodable_bytes_raise_value_error(self):
        with self.assertRaises(ValueError):
            list(parse_dem(b'<?xml version="1.0" encoding="UTF-8"?>\xff\xff\xff'))


if __name__ == '__main__':
    unittest.main()


class TupleErrorDetailTests(unittest.TestCase):
    """KD-6: a bad elevation value names its tuple position."""

    def test_invalid_elevation_names_tuple_and_value(self):
        body = XML.split('<tupleList>')[0] + \
            '<tupleList>a,1 a,xyz a,1 a,1</tupleList>' + \
            XML.split('</tupleList>')[1]
        with self.assertRaises(ValueError) as ctx:
            list(parse_dem(body.replace('{enc}', 'UTF-8').encode('utf-8')))
        self.assertIn('tuple #1', str(ctx.exception))
        self.assertIn('xyz', str(ctx.exception))

    def test_malformed_xml_is_value_error_with_position(self):
        with self.assertRaises(ValueError) as ctx:
            list(parse_dem(b'<Dataset><DEM></Dataset>'))
        self.assertIn('line', str(ctx.exception))


class EncodingAliasTests(unittest.TestCase):
    """R-8b: safe aliases are normalised; unsafe encodings stay out."""

    def test_aliases_accepted(self):
        for declared in ('UTF8', 'utf_8', 'SJIS', 'Windows-31J', 'eucjp',
                         'euc_jp', 'latin1', 'US-ASCII'):
            body_codec = 'cp932' if declared == 'Windows-31J' else declared
            data = XML.format(enc=declared).encode(body_codec)
            with self.subTest(declared=declared):
                self.assertEqual(len(list(parse_dem(data))), 1)

    def test_unsafe_or_unknown_encodings_rejected(self):
        for declared in ('UTF-16', 'utf-32', 'koi8-r', 'bogus-enc'):
            data = XML.format(enc=declared).encode('utf-8')
            with self.subTest(declared=declared), self.assertRaises(ValueError):
                list(parse_dem(data))
