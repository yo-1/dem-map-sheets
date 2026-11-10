"""M-1b verification for algorithm.py failure recording (QGIS required).

Skipped when QGIS is not importable (the normal CI has no QGIS); run
it in a QGIS 3.44 environment. Verifies the r4 checklist:
- the original exception always reaches the caller as
  QgsProcessingException, even when recording itself fails;
- a corrupt run.json falls back to a minimal record with
  status/error instead of masking the original error;
- keys written earlier by dem_pipeline.run() (work_dir,
  work_retained, ...) survive algorithm.py's status/error update.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from qgis.core import (
        QgsApplication, QgsProcessingContext, QgsProcessingException,
        QgsProcessingFeedback)
    _HAS_QGIS = True
except Exception:
    _HAS_QGIS = False

if _HAS_QGIS:
    _APP = QgsApplication([], False)
    _APP.initQgis()


def _algorithm_and_params(parent, files, input_type=2):
    from dem_map_sheets.algorithm import DEMMapSheetsAlgorithm
    algo = DEMMapSheetsAlgorithm()
    algo.initAlgorithm()
    params = {
        'INPUT_TYPE': input_type,
        'FILES': files,
        'INPUT_FOLDER': '',
        'RECURSIVE': True,
        'INPUT_CRS': 'EPSG:6677',
        'CRS': 'EPSG:6677',
        'CELL': 10.0,
        'VERTICAL_MODE': 0,
        'VERTICAL': '',
        'MERGE_PRIORITY': 0,
        'TERRAINRGB': False,
        'LIDAR_MODE': 0,
        'GROUND_CLASSES': '2',
        'PDAL': '',
        'POINT_METHOD': 0,
        'POINT_RADIUS': 2.0,
        'TEXT_MODE': 0,
        'TEXT_DELIMITER': 0,
        'TEXT_ENCODING': 'utf-8-sig',
        'TEXT_SKIP': 1,
        'TEXT_COLUMNS': '1,2,3',
        'TEXT_AXES': 0,
        'TEXT_CELL': 10.0,
        'FOREST_AXES': 0,
        'FOREST_CELL': 'auto',
        'LEM_SCALE': 'auto',
        'FOREST_NODATA': -9999.0,
        'KEEP_WORK': False,
        'OUTPUT': str(parent),
    }
    return algo, params


@unittest.skipUnless(_HAS_QGIS, 'QGIS is not available in this environment')
class AlgorithmFailureRecordTests(unittest.TestCase):
    def _run_expecting_failure(self, algo, params):
        context = QgsProcessingContext()
        feedback = QgsProcessingFeedback()
        with self.assertRaises(QgsProcessingException) as ctx:
            algo.processAlgorithm(params, context, feedback)
        return ctx.exception

    def _only_result_dir(self, parent):
        dirs = [p for p in Path(parent).iterdir() if p.is_dir()]
        self.assertEqual(len(dirs), 1)
        return dirs[0]

    def test_run_failure_record_is_preserved(self):
        # Real run(): a junk text file fails inside prepare_inputs,
        # run() writes its own failure record, and algorithm.py must
        # keep work_dir/work_retained while updating status/error.
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / 'bad.csv'
            bad.write_text('not,a,grid\nx,y,z\n', encoding='utf-8')
            parent = Path(td) / 'out'
            parent.mkdir()
            algo, params = _algorithm_and_params(parent, [str(bad)])
            exc = self._run_expecting_failure(algo, params)
            result_dir = self._only_result_dir(parent)
            record = json.loads((result_dir / 'run.json').read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['error'], str(exc))
            self.assertEqual(record['work_retained'], 'diagnostics_only')
            self.assertIn('work_dir', record)

    def test_corrupt_run_json_falls_back(self):
        import dem_map_sheets.engine.dem_pipeline as pipeline

        def fake_run(config, output_dir, gdal, osr, feedback=None):
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / 'run.json').write_text('{{{ not json', encoding='utf-8')
            raise RuntimeError('original failure')

        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / 'out'
            parent.mkdir()
            algo, params = _algorithm_and_params(parent, ['unused.csv'])
            with mock.patch.object(pipeline, 'run', fake_run):
                exc = self._run_expecting_failure(algo, params)
            self.assertEqual(str(exc), 'original failure')
            result_dir = self._only_result_dir(parent)
            record = json.loads((result_dir / 'run.json').read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['error'], 'original failure')

    def test_existing_record_keys_survive_update(self):
        import dem_map_sheets.engine.dem_pipeline as pipeline

        def fake_run(config, output_dir, gdal, osr, feedback=None):
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / 'run.json').write_text(json.dumps({
                'status': 'failed', 'error': 'first',
                'work_dir': '/somewhere/work',
                'work_retained': 'diagnostics_only',
                'custom_key': 'keep-me'}), encoding='utf-8')
            raise RuntimeError('second failure')

        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / 'out'
            parent.mkdir()
            algo, params = _algorithm_and_params(parent, ['unused.csv'])
            with mock.patch.object(pipeline, 'run', fake_run):
                exc = self._run_expecting_failure(algo, params)
            self.assertEqual(str(exc), 'second failure')
            result_dir = self._only_result_dir(parent)
            record = json.loads((result_dir / 'run.json').read_text(encoding='utf-8'))
            self.assertEqual(record['error'], 'second failure')
            self.assertEqual(record['work_retained'], 'diagnostics_only')
            self.assertEqual(record['work_dir'], '/somewhere/work')
            self.assertEqual(record['custom_key'], 'keep-me')


if __name__ == '__main__':
    unittest.main()
