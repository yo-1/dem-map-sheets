"""Issue #16 PR-3: run.json の SHA-256・サイズ・環境版の記録テスト。"""
import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from osgeo import gdal, osr
    _HAS_GDAL = True
except Exception:
    _HAS_GDAL = False

from dem_map_sheets.engine import dem_pipeline, provenance
from dem_map_sheets.tests.test_run_json_schema import SCHEMA, FakeFeedback, validate


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ProvenanceUnitTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def test_sha256_matches_hashlib_across_chunks(self):
        data = os.urandom(provenance.CHUNK * 2 + 17)
        p = self.root / 'x.bin'
        p.write_bytes(data)
        self.assertEqual(provenance.sha256_of(p), hashlib.sha256(data).hexdigest())

    def test_empty_file(self):
        p = self.root / 'e.bin'
        p.write_bytes(b'')
        rec = provenance.file_record(p, 'e.bin')
        self.assertEqual((rec['bytes'], rec['sha256']), (0, hashlib.sha256(b'').hexdigest()))

    def test_missing_file_is_recorded_not_raised(self):
        rec = provenance.file_record(self.root / 'nope', 'nope')
        self.assertEqual(rec['sha256_status'], 'error')
        self.assertEqual(rec['error'], 'FileNotFoundError')
        self.assertIsNone(rec['sha256'])

    def test_read_error_is_recorded_with_type_only(self):
        p = self.root / 'a.bin'
        p.write_bytes(b'abc')
        with mock.patch.object(provenance, 'sha256_of', side_effect=PermissionError('/secret/path')):
            rec = provenance.file_record(p, 'a.bin')
        self.assertEqual(rec['sha256_status'], 'error')
        self.assertEqual(rec['error'], 'PermissionError')
        self.assertNotIn('/secret/path', json.dumps(rec))
        self.assertEqual(rec['bytes'], 3)

    def test_cancel_during_hash(self):
        p = self.root / 'a.bin'
        p.write_bytes(b'abc')
        with self.assertRaises(provenance.HashCancelled):
            provenance.sha256_of(p, lambda: True)
        with self.assertRaises(provenance.HashCancelled):
            provenance.file_record(p, 'a.bin', lambda: True)

    def test_compute_false_records_size_only(self):
        p = self.root / 'a.bin'
        p.write_bytes(b'abc')
        rec = provenance.file_record(p, 'a.bin', compute=False)
        self.assertEqual((rec['bytes'], rec['sha256'], rec['sha256_status']), (3, None, 'not_computed'))

    def test_environment_omits_unavailable_and_has_no_secrets(self):
        with mock.patch.dict(os.environ, {'API_TOKEN': 'sekrit-value-123'}):
            env = provenance.collect_environment(None, None, {'qgis_version': '3.44.1'}, None)
        self.assertEqual(env['qgis'], '3.44.1')
        self.assertIn('python', env)
        self.assertNotIn('gdal', env)
        self.assertNotIn('pdal', env)
        self.assertNotIn('sekrit-value-123', json.dumps(env))

    def test_pdal_version_first_line_only(self):
        env = provenance.collect_environment(None, None, {}, {'a': [{'pdal_version': 'pdal 2.10.2 (git-version: x)\n/usr/bin/pdal\n'}]})
        self.assertEqual(env['pdal'], 'pdal 2.10.2 (git-version: x)')

    def test_output_files_exclude_work_and_run_json(self):
        (self.root / 'work').mkdir()
        (self.root / 'work' / 'tmp.bin').write_bytes(b'1')
        (self.root / 'run.json').write_text('{}')
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'a.tif').write_bytes(b'2')
        self.assertEqual([r for _, r in provenance.output_files(self.root)], ['sub/a.tif'])


@unittest.skipUnless(_HAS_GDAL, 'GDAL が必要')
class ProvenanceRunTests(unittest.TestCase):
    def setUp(self):
        from dem_map_sheets.tests.input_fixtures import make_geotiff
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        (self.root / 'd1').mkdir()
        (self.root / 'd2').mkdir()
        # 同名ファイルを別フォルダに置き、basename 衝突でも区別できることを確認する
        self.a = make_geotiff(self.root / 'd1' / 'dem.tif')
        self.b = make_geotiff(self.root / 'd2' / 'dem.tif', gt=(1020.0, 10.0, 0.0, 2030.0, 0.0, -10.0))

    def _cfg(self, inputs, **kw):
        from dem_map_sheets.engine.input_sources import DEFAULTS
        c = dict(DEFAULTS)
        c.update(input_type='raster', inputs=[str(p) for p in inputs], input_crs='',
                 target_crs='EPSG:6677', cell_size=10.0, source_nodata=None, max_pixels=10 ** 9)
        c.update(kw)
        return c

    def _run(self, name, inputs, feedback=None, **kw):
        out = self.root / name
        err = None
        try:
            dem_pipeline.run(self._cfg(inputs, **kw), out, gdal, osr, feedback or FakeFeedback())
        except BaseException as exc:
            err = exc
        rec = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        return out, rec, err

    def test_success_hashes_match_independent_computation(self):
        out, rec, err = self._run('ok', [self.a])
        self.assertIsNone(err)
        self.assertEqual(validate(rec, SCHEMA), [])
        prov = rec['provenance']
        self.assertEqual(prov['status'], 'complete')
        self.assertEqual(prov['inputs'][0]['sha256'], _sha(self.a))
        self.assertEqual(prov['inputs'][0]['bytes'], self.a.stat().st_size)
        paths = {o['path']: o for o in prov['outputs']}
        self.assertIn('02_merged_dem.tif', paths)
        for rel, o in paths.items():
            self.assertEqual(o['sha256'], _sha(out / rel))
            self.assertEqual(o['bytes'], (out / rel).stat().st_size)
        self.assertNotIn('run.json', paths)
        self.assertNotIn('provenance_incomplete', rec['warnings'])

    def test_multiple_dems_with_same_basename(self):
        _, rec, err = self._run('multi', [self.a, self.b])
        self.assertIsNone(err)
        ins = rec['provenance']['inputs']
        self.assertEqual([i['index'] for i in ins], [0, 1])
        self.assertEqual({i['sha256'] for i in ins}, {_sha(self.a), _sha(self.b)})
        self.assertEqual({i['name'] for i in ins}, {'dem.tif'})

    def test_environment_recorded(self):
        _, rec, _ = self._run('env', [self.a], qgis_version='3.44.1')
        env = rec['provenance']['environment']
        for key in ('python', 'gdal', 'proj', 'plugin', 'qgis'):
            self.assertIn(key, env)
        self.assertEqual(env['qgis'], '3.44.1')

    def test_no_path_or_secret_leak_in_provenance(self):
        with mock.patch.dict(os.environ, {'DEM_TEST_SECRET': 'sekrit-value-123'}):
            _, rec, _ = self._run('leak', [self.a])
        text = json.dumps(rec['provenance'], ensure_ascii=False)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn('sekrit-value-123', text)
        host = socket.gethostname()
        if len(host) > 3:
            self.assertNotIn(host, text)
        for o in rec['provenance']['outputs']:
            self.assertFalse(o['path'].startswith('/'))

    def test_hash_failure_does_not_fail_run(self):
        real = provenance.sha256_of

        def flaky(path, is_canceled=None):
            if Path(path).name == 'dem.tif' and 'd1' in str(path):
                raise PermissionError('denied')
            return real(path, is_canceled)
        with mock.patch.object(provenance, 'sha256_of', side_effect=flaky):
            _, rec, err = self._run('hashfail', [self.a, self.b])
        self.assertIsNone(err)
        self.assertEqual(rec['status'], 'completed')
        self.assertEqual(rec['provenance']['status'], 'incomplete')
        self.assertIn('provenance_incomplete', rec['warnings'])
        bad = rec['provenance']['inputs'][0]
        self.assertEqual((bad['sha256'], bad['error']), (None, 'PermissionError'))
        self.assertEqual(rec['provenance']['inputs'][1]['sha256'], _sha(self.b))
        self.assertEqual(validate(rec, SCHEMA), [])

    def test_record_hashes_false_skips(self):
        with mock.patch.object(provenance, 'sha256_of', side_effect=AssertionError('must not hash')):
            _, rec, err = self._run('skip', [self.a], record_hashes=False)
        self.assertIsNone(err)
        self.assertEqual(rec['provenance']['status'], 'skipped')
        self.assertIsNone(rec['provenance']['inputs'][0]['sha256'])
        self.assertEqual(rec['provenance']['inputs'][0]['bytes'], self.a.stat().st_size)
        self.assertEqual(validate(rec, SCHEMA), [])

    def test_failure_does_not_compute_hashes_and_is_partial(self):
        bad = self.root / 'bad.tif'
        bad.write_bytes(b'not a tiff')
        with mock.patch.object(provenance, 'sha256_of', side_effect=AssertionError('must not hash')):
            out, rec, err = self._run('fail', [bad])
        self.assertIsNotNone(err)
        self.assertEqual(rec['status'], 'failed')
        self.assertEqual(rec['provenance']['status'], 'partial')
        self.assertIsNone(rec['provenance']['inputs'][0]['sha256'])
        self.assertEqual(rec['provenance']['inputs'][0]['bytes'], 10)
        self.assertEqual(validate(rec, SCHEMA), [])

    def test_cancel_during_hashing_is_recorded_as_cancelled(self):
        fb = FakeFeedback()
        real = provenance.sha256_of

        def cancel_on_first(path, is_canceled=None):
            fb.cancelled = True
            return real(path, is_canceled)
        with mock.patch.object(provenance, 'sha256_of', side_effect=cancel_on_first):
            out, rec, err = self._run('cancel', [self.a], fb)
        self.assertIsNotNone(err)
        self.assertEqual(rec['status'], 'cancelled')
        self.assertEqual(rec['provenance']['status'], 'partial')
        self.assertEqual(validate(rec, SCHEMA), [])

    def test_hashing_does_not_change_dem_output(self):
        out1, _, _ = self._run('h_on', [self.a, self.b])
        out2, _, _ = self._run('h_off', [self.a, self.b], record_hashes=False)
        self.assertEqual(_sha(out1 / '02_merged_dem.tif'), _sha(out2 / '02_merged_dem.tif'))

    def test_legacy_keys_still_present(self):
        _, rec, _ = self._run('legacy', [self.a])
        for k in ('status', 'merged_dem', 'output_nodata', 'warnings', 'source_dems', 'merge_priority'):
            self.assertIn(k, rec)

    def test_add_output_file_appends_relative_record(self):
        out, rec, _ = self._run('addout', [self.a])
        extra = out / '03_terrainrgb_xyz'
        extra.mkdir()
        (extra / 'xyz.json').write_text('{"a":1}')
        provenance.add_output_file(rec, out, extra / 'xyz.json')
        last = rec['provenance']['outputs'][-1]
        self.assertEqual(last['path'], '03_terrainrgb_xyz/xyz.json')
        self.assertEqual(last['sha256'], _sha(extra / 'xyz.json'))

    def test_late_output_hash_failure_adds_warning(self):
        # algorithm 層が run() 後に追加する xyz.json のハッシュ失敗
        out, rec, _ = self._run('latefail', [self.a])
        self.assertEqual(rec['provenance']['status'], 'complete')
        self.assertNotIn('provenance_incomplete', rec['warnings'])
        extra = out / '03_terrainrgb_xyz'
        extra.mkdir()
        (extra / 'xyz.json').write_text('{"a":1}')
        with mock.patch.object(provenance, 'sha256_of', side_effect=PermissionError('denied')):
            provenance.add_output_file(rec, out, extra / 'xyz.json')
        self.assertEqual(rec['status'], 'completed')
        self.assertEqual(rec['qa_status'], 'not_evaluated')
        self.assertEqual(rec['provenance']['status'], 'incomplete')
        self.assertEqual(rec['warnings'].count('provenance_incomplete'), 1)
        last = rec['provenance']['outputs'][-1]
        self.assertEqual((last['sha256'], last['error']), (None, 'PermissionError'))
        self.assertEqual(validate(rec, SCHEMA), [])
        # 二重追加しない
        with mock.patch.object(provenance, 'sha256_of', side_effect=PermissionError('denied')):
            provenance.add_output_file(rec, out, extra / 'xyz.json')
        self.assertEqual(rec['warnings'].count('provenance_incomplete'), 1)

    def test_late_output_failure_keeps_other_hashes_and_dem(self):
        out, rec, _ = self._run('latekeep', [self.a])
        before = [dict(o) for o in rec['provenance']['outputs']]
        dem_hash = _sha(out / '02_merged_dem.tif')
        extra = out / 'x'
        extra.mkdir()
        (extra / 'xyz.json').write_text('{}')
        with mock.patch.object(provenance, 'sha256_of', side_effect=OSError('boom')):
            provenance.add_output_file(rec, out, extra / 'xyz.json')
        self.assertEqual(rec['provenance']['outputs'][:len(before)], before)
        self.assertEqual(_sha(out / '02_merged_dem.tif'), dem_hash)

    def test_late_output_with_record_hashes_false_no_hash_no_warning(self):
        out, rec, _ = self._run('lateskip', [self.a], record_hashes=False)
        extra = out / 'x'
        extra.mkdir()
        (extra / 'xyz.json').write_text('{}')
        with mock.patch.object(provenance, 'sha256_of', side_effect=AssertionError('must not hash')):
            provenance.add_output_file(rec, out, extra / 'xyz.json')
        self.assertEqual(rec['provenance']['status'], 'skipped')
        self.assertNotIn('provenance_incomplete', rec['warnings'])
        self.assertIsNone(rec['provenance']['outputs'][-1]['sha256'])


if __name__ == '__main__':
    unittest.main()
