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


class GsiEncodingGuardTests(unittest.TestCase):
    def test_valid_utf8_parses(self):
        results = list(parse_dem(XML.format(enc='UTF-8').encode('utf-8')))
        self.assertEqual(len(results), 1)
        arr = results[0][0]
        self.assertEqual(sorted(arr.flatten().tolist()), [100.0, 101.0, 102.0, 103.0])

    def test_valid_shift_jis_declaration_parses(self):
        results = list(parse_dem(XML.format(enc='Shift_JIS').encode('shift_jis')))
        self.assertEqual(len(results), 1)

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
