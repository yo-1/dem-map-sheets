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


class KeepWorkMirrorTests(unittest.TestCase):
    """keep_work parity: with a non-ASCII work dir the PDAL area is
    temporary, but keep_work must still preserve its diagnostics
    (they lived in the work dir before the area was separated)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.exe = self.root / 'pdal'
        self.exe.write_bytes(b'x')
        self.work = self.root / '日本語の作業'
        self.work.mkdir()

    def _run_fake(self, **cfg):
        def fake_impl(c, w, gdal, osr, feedback, exe, staged, pdal_root):
            (pdal_root / 'block_000000_pipeline.json').write_text('{}', encoding='utf-8')
            return [], {}

        base = {'pdal_path': str(self.exe)}
        base.update(cfg)
        with mock.patch.object(point_inputs, '_points_to_rasters', fake_impl):
            point_inputs.points_to_rasters(base, self.work, None, None)

    def test_keep_work_mirrors_pdal_diagnostics(self):
        self._run_fake(keep_work=True)
        mirrored = self.work / 'pdal_ascii_area' / 'block_000000_pipeline.json'
        self.assertTrue(mirrored.is_file())

    def test_without_keep_work_nothing_is_mirrored(self):
        self._run_fake(keep_work=False)
        self.assertFalse((self.work / 'pdal_ascii_area').exists())

    def _run_fake_empty(self, **cfg):
        def fake_impl(c, w, gdal, osr, feedback, exe, staged, pdal_root):
            return [], {}

        base = {'pdal_path': str(self.exe)}
        base.update(cfg)
        with mock.patch.object(point_inputs, '_points_to_rasters', fake_impl):
            point_inputs.points_to_rasters(base, self.work, None, None)

    def test_keep_work_leaves_no_empty_mirror(self):
        # A broken input fails before PDAL writes anything: the
        # mirror must not remain as an empty directory.
        self._run_fake_empty(keep_work=True)
        self.assertFalse((self.work / 'pdal_ascii_area').exists())

    def test_keep_work_leaves_no_mirror_when_impl_raises(self):
        def fake_impl(c, w, gdal, osr, feedback, exe, staged, pdal_root):
            raise RuntimeError('PDAL failed')

        with mock.patch.object(point_inputs, '_points_to_rasters', fake_impl):
            with self.assertRaises(RuntimeError):
                point_inputs.points_to_rasters(
                    {'pdal_path': str(self.exe), 'keep_work': True}, self.work, None, None)
        self.assertFalse((self.work / 'pdal_ascii_area').exists())


class AsciiTempAreaFallbackTests(unittest.TestCase):
    """When TEMP itself is non-ASCII (e.g. a Japanese user profile),
    _ascii_temp_area falls back to $DEM_MAP_SHEETS_TEMP; with no
    ASCII candidate it returns the non-ASCII area and the staging
    check raises its bilingual error."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.jp_temp = self.root / '一時フォルダ'
        self.jp_temp.mkdir()
        self.ascii_env = self.root / 'ascii_temp'
        self.ascii_env.mkdir()

    def test_env_fallback_is_used_when_temp_is_japanese(self):
        with mock.patch.object(tempfile, 'tempdir', str(self.jp_temp)), \
                mock.patch.dict('os.environ', {'DEM_MAP_SHEETS_TEMP': str(self.ascii_env)}):
            area = point_inputs._ascii_temp_area()
        self.addCleanup(lambda: area.exists() and __import__('shutil').rmtree(area, ignore_errors=True))
        self.assertTrue(str(area).isascii())
        self.assertEqual(area.parent, self.ascii_env)
        self.assertEqual(list(self.jp_temp.glob('dem_map_sheets_pdal_*')), [],
                         'non-ASCII probe areas must not leak')

    def test_no_ascii_candidate_returns_non_ascii_area(self):
        env = {k: v for k, v in __import__('os').environ.items() if k != 'DEM_MAP_SHEETS_TEMP'}
        with mock.patch.object(tempfile, 'tempdir', str(self.jp_temp)), \
                mock.patch.dict('os.environ', env, clear=True):
            area = point_inputs._ascii_temp_area()
        self.addCleanup(lambda: area.exists() and __import__('shutil').rmtree(area, ignore_errors=True))
        self.assertFalse(str(area).isascii())
