"""Japanese output folders: PDAL-facing files must live in an ASCII
area even when the run work dir (under the output folder) is not
ASCII. points_to_rasters chooses the area and removes a temporary
one on exit; only GDAL-written block GeoTIFFs stay in the work dir."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dem_map_sheets.engine import point_inputs


class AsciiAreaSelectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.exe = self.root / 'pdal'
        self.exe.write_bytes(b'x')

    def _capture(self, work):
        captured = {}

        def fake_impl(c, w, gdal, osr, feedback, exe, staged, pdal_root):
            captured['pdal_root'] = pdal_root
            captured['exists_during'] = pdal_root.is_dir()
            return [], {}

        with mock.patch.object(point_inputs, '_points_to_rasters', fake_impl):
            point_inputs.points_to_rasters({'pdal_path': str(self.exe)}, work, None, None)
        return captured

    def test_ascii_work_is_used_directly(self):
        work = self.root / 'work'
        work.mkdir()
        captured = self._capture(work)
        self.assertEqual(captured['pdal_root'], work)

    def test_japanese_work_gets_a_temporary_ascii_area(self):
        work = self.root / '日本語の作業'
        work.mkdir()
        captured = self._capture(work)
        area = captured['pdal_root']
        self.assertNotEqual(area, work)
        self.assertTrue(str(area).isascii())
        self.assertTrue(captured['exists_during'])
        self.assertFalse(area.exists(), 'temporary PDAL area is removed on exit')


if __name__ == '__main__':
    unittest.main()
