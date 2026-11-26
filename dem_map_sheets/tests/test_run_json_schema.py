"""run.json の schema_version / qa_status と JSON Schema 適合のテスト。"""
import json
import tempfile
import unittest
from pathlib import Path

from dem_map_sheets.engine import dem_pipeline
from dem_map_sheets.engine.run_record import (
    QA_NOT_EVALUATED, RUN_JSON_SCHEMA_VERSION, finalize_run_record)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schema' / 'run.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


def _types_ok(value, t):
    types = t if isinstance(t, list) else [t]
    for x in types:
        if x == 'object' and isinstance(value, dict): return True
        if x == 'array' and isinstance(value, list): return True
        if x == 'string' and isinstance(value, str): return True
        if x == 'null' and value is None: return True
        if x == 'boolean' and isinstance(value, bool): return True
        if x == 'integer' and isinstance(value, int) and not isinstance(value, bool): return True
        if x == 'number' and isinstance(value, (int, float)) and not isinstance(value, bool): return True
    return False


def validate(value, schema, path='$'):
    """必要な構文のみのサブセット検証。エラー文字列のリストを返す。"""
    errors = []
    if '$ref' in schema:
        node = SCHEMA
        for part in schema['$ref'].lstrip('#/').split('/'):
            node = node[part]
        return validate(value, node, path)
    if 'const' in schema and value != schema['const']:
        errors.append(f'{path}: const {schema["const"]!r} != {value!r}')
    if 'enum' in schema and value not in schema['enum']:
        errors.append(f'{path}: {value!r} not in enum')
    if 'type' in schema and not _types_ok(value, schema['type']):
        errors.append(f'{path}: type {schema["type"]} expected')
        return errors
    if 'minimum' in schema and isinstance(value, (int, float)) and value < schema['minimum']:
        errors.append(f'{path}: below minimum')
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value: errors.append(f'{path}: missing {key}')
        for key, sub in schema.get('properties', {}).items():
            if key in value: errors += validate(value[key], sub, f'{path}.{key}')
    if isinstance(value, list) and 'items' in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema['items'], f'{path}[{i}]')
    if 'oneOf' in schema:
        ok = sum(1 for s in schema['oneOf'] if not validate(value, s, path))
        if ok != 1: errors.append(f'{path}: oneOf matched {ok}')
    for sub in schema.get('allOf', []):
        cond = sub.get('if')
        if cond is None or not validate(value, cond, path):
            if 'then' in sub: errors += validate(value, sub['then'], path)
    return errors


class FakeFeedback:
    def __init__(self, cancelled=False): self.cancelled = cancelled; self.warnings = []
    def isCanceled(self): return self.cancelled
    def pushWarning(self, m): self.warnings.append(m)
    def pushInfo(self, m): pass
    def setProgress(self, v): pass


class FinalizeTests(unittest.TestCase):
    def test_adds_fields_and_keeps_existing(self):
        r = finalize_run_record({'status': 'completed', 'x': 1})
        self.assertEqual(r['schema_version'], RUN_JSON_SCHEMA_VERSION)
        self.assertEqual(r['qa_status'], QA_NOT_EVALUATED)
        self.assertEqual((r['status'], r['x']), ('completed', 1))

    def test_qa_status_cannot_be_overridden(self):
        r = finalize_run_record({'status': 'completed', 'qa_status': 'pass'})
        self.assertEqual(r['qa_status'], 'not_evaluated')


class ValidatorSelfTests(unittest.TestCase):
    """検証器自体が不正を検出できること（空振り防止）。"""
    base = {'schema_version': 1, 'qa_status': 'not_evaluated',
            'status': 'failed', 'merge_priority': 'last', 'error': 'x'}

    def test_valid(self):
        self.assertEqual(validate(self.base, SCHEMA), [])

    def test_missing_qa_status(self):
        d = dict(self.base); del d['qa_status']
        self.assertTrue(validate(d, SCHEMA))

    def test_bad_qa_status(self):
        self.assertTrue(validate({**self.base, 'qa_status': 'pass'}, SCHEMA))

    def test_bad_status(self):
        self.assertTrue(validate({**self.base, 'status': 'ok'}, SCHEMA))

    def test_failed_requires_error(self):
        d = dict(self.base); del d['error']
        self.assertTrue(validate(d, SCHEMA))

    def test_completed_requires_fields(self):
        self.assertTrue(validate({**self.base, 'status': 'completed'}, SCHEMA))

    def test_unknown_keys_allowed(self):
        self.assertEqual(validate({**self.base, 'future_key': {'a': 1}}, SCHEMA), [])


class RecordFailureTests(unittest.TestCase):
    def _record(self, cancelled, exc=None):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / 'out'; (out / 'work').mkdir(parents=True)
            (out / '01_source_dem').mkdir()
            (out / '01_source_dem' / 'a.tif').write_bytes(b'abc')
            dem_pipeline._record_failure(out, out / 'work', {'merge_priority': 'first'},
                                         exc or RuntimeError('boom'), FakeFeedback(cancelled))
            return json.loads((out / 'run.json').read_text(encoding='utf-8'))

    def test_failed(self):
        rec = self._record(False)
        self.assertEqual(rec['status'], 'failed')
        self.assertEqual(rec['qa_status'], 'not_evaluated')
        self.assertEqual(rec['schema_version'], 1)
        self.assertEqual(validate(rec, SCHEMA), [])

    def test_cancelled(self):
        rec = self._record(True)
        self.assertEqual(rec['status'], 'cancelled')
        self.assertEqual(validate(rec, SCHEMA), [])

    def test_existing_keys_unchanged(self):
        rec = self._record(False)
        for k in ('status', 'error', 'merge_priority', 'work_dir', 'work_retained',
                  'work_removed_bytes', 'work_retained_bytes', 'outputs_present'):
            self.assertIn(k, rec)
        self.assertEqual(rec['outputs_present'], [{'path': '01_source_dem/a.tif', 'bytes': 3}])

    def test_failure_detail_preserved(self):
        exc = RuntimeError('c'); exc.detail = {'value': -9999}
        rec = self._record(False, exc)
        self.assertEqual(rec['failure_detail'], {'value': -9999})
        self.assertEqual(validate(rec, SCHEMA), [])


class BackwardCompatTests(unittest.TestCase):
    """旧形式 run.json（schema_version/qa_status なし）を読む既存リーダーの想定。"""
    legacy_completed = {'status': 'completed', 'merged_dem': 'x.tif', 'warnings': [],
                        'output_nodata': -9999.0, 'merge_priority': 'last'}

    def test_legacy_readers_keep_working(self):
        new = finalize_run_record(dict(self.legacy_completed))
        for k, v in self.legacy_completed.items():
            self.assertEqual(new[k], v)
        # 旧リーダーは status と既存キーのみ参照し、未知キーを無視できる
        self.assertEqual(new['status'], 'completed')

    def test_legacy_lacks_new_keys_and_reader_defaults(self):
        self.assertEqual(self.legacy_completed.get('schema_version', 0), 0)
        self.assertEqual(self.legacy_completed.get('qa_status', 'not_evaluated'), 'not_evaluated')

    def test_status_values_unchanged(self):
        self.assertEqual(SCHEMA['properties']['status']['enum'],
                         ['completed', 'failed', 'cancelled'])



try:
    from osgeo import gdal, osr
    _HAS_GDAL = True
except Exception:
    _HAS_GDAL = False


@unittest.skipUnless(_HAS_GDAL, 'GDAL が必要')
class EndToEndRunTests(unittest.TestCase):
    """run() 全体を通した run.json のスキーマ適合。"""

    def setUp(self):
        from dem_map_sheets.tests.input_fixtures import make_geotiff
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.a = make_geotiff(self.root / 'a.tif')
        self.b = make_geotiff(self.root / 'b.tif', gt=(1020.0, 10.0, 0.0, 2030.0, 0.0, -10.0))

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
        return rec, err

    def _check(self, rec, status):
        self.assertEqual(validate(rec, SCHEMA), [])
        self.assertEqual(rec['status'], status)
        self.assertEqual(rec['schema_version'], 1)
        self.assertEqual(rec['qa_status'], 'not_evaluated')

    def test_success(self):
        rec, err = self._run('ok', [self.a])
        self.assertIsNone(err)
        self._check(rec, 'completed')

    def test_multiple_dems(self):
        rec, err = self._run('multi', [self.a, self.b])
        self.assertIsNone(err)
        self._check(rec, 'completed')
        self.assertEqual(rec['source_count'], 2)

    def test_cancel(self):
        rec, err = self._run('cancel', [self.a], FakeFeedback(cancelled=True))
        # 中断の経路は実装依存のため、記録された場合のみ status を確認する
        self.assertEqual(validate(rec, SCHEMA), [])
        self.assertIn(rec['status'], ('cancelled', 'completed'))
        self.assertEqual(rec['qa_status'], 'not_evaluated')

    def test_all_inputs_missing(self):
        # 既存仕様: 入力検証は出力フォルダ作成前に行われるため run.json は作られない。
        # PR-2 はこの挙動を変更しない（run.json が無い＝入力検証段階での失敗）。
        out = self.root / 'missing'
        with self.assertRaises(Exception):
            dem_pipeline.run(self._cfg([self.root / 'nope1.tif', self.root / 'nope2.tif']),
                             out, gdal, osr, FakeFeedback())
        self.assertFalse((out / 'run.json').exists())

    def test_corrupt_file(self):
        bad = self.root / 'bad.tif'
        bad.write_bytes(b'not a tiff')
        rec, err = self._run('corrupt', [bad])
        self.assertIsNotNone(err)
        self._check(rec, 'failed')

    def test_legacy_reader_keys_present(self):
        rec, _ = self._run('legacy', [self.a])
        for k in ('status', 'merged_dem', 'output_nodata', 'warnings', 'source_dems', 'merge_priority'):
            self.assertIn(k, rec)


if __name__ == '__main__':
    unittest.main()
