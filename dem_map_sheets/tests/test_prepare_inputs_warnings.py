"""M-3 regressions: ignored forest inputs warn the user; all-invalid stops.

An input that cannot be used (format undetectable, points not on the
declared grid, grid over the pixel limit) is recorded in
report['ignored'] AND surfaced via feedback.pushWarning, while the
remaining inputs continue. If every input is unusable, prepare_inputs
stops with a clear error instead of producing an empty result.
"""
import tempfile
import unittest
from pathlib import Path

from osgeo import gdal, osr

from dem_map_sheets.engine.input_sources import DEFAULTS, prepare_inputs


class _Feedback:
    def __init__(self):
        self.warnings = []

    def isCanceled(self):
        return False

    def pushInfo(self, text):
        pass

    def pushWarning(self, text):
        self.warnings.append(text)

    def setProgress(self, value):
        pass


class ForestIgnoredWarningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        good = self.dir / 'good.csv'
        good.write_text('0,0,100\n10,0,101\n0,10,102\n10,10,103\n', encoding='utf-8')
        self.good = str(good)
        bad = self.dir / 'bad.csv'
        bad.write_text('name,note\nfoo,bar\n', encoding='utf-8')
        self.bad = str(bad)
        offgrid = self.dir / 'offgrid.csv'
        offgrid.write_text('0,0,100\n15,0,101\n', encoding='utf-8')
        self.offgrid = str(offgrid)

    def _cfg(self, inputs):
        c = dict(DEFAULTS)
        c.update(input_type='forest', inputs=inputs, input_crs='EPSG:6677',
                 target_crs='EPSG:6677', cell_size=10.0,
                 forest_axis_order='east_north',
                 forest_grid_cell=10.0, max_pixels=10 ** 9, source_nodata=None)
        return c

    def test_mixed_inputs_warn_once_and_continue(self):
        feedback = _Feedback()
        prepared, report = prepare_inputs(self._cfg([self.bad, self.good]),
                                          self.dir / 'work1', gdal, osr, feedback)
        self.assertEqual(len(prepared['inputs']), 1)
        self.assertEqual(len(report['ignored']), 1)
        self.assertEqual(len(feedback.warnings), 1)
        self.assertIn(self.bad, feedback.warnings[0])

    def test_offgrid_input_is_ignored_with_warning(self):
        feedback = _Feedback()
        prepared, report = prepare_inputs(self._cfg([self.offgrid, self.good]),
                                          self.dir / 'work2', gdal, osr, feedback)
        self.assertEqual(len(prepared['inputs']), 1)
        self.assertEqual(len(report['ignored']), 1)
        self.assertEqual(len(feedback.warnings), 1)

    def test_warnings_capped_at_20_with_summary(self):
        import json
        bads = []
        for i in range(25):
            bad = self.dir / f'bad{i:02d}.csv'
            bad.write_text('name,note\nfoo,bar\n', encoding='utf-8')
            bads.append(str(bad))
        feedback = _Feedback()
        work = self.dir / 'work_cap'
        prepared, report = prepare_inputs(self._cfg(bads + [self.good]),
                                          work, gdal, osr, feedback)
        self.assertEqual(len(prepared['inputs']), 1)
        # every ignored input is recorded; only the warnings are capped
        self.assertEqual(len(report['ignored']), 25)
        saved = json.loads((work / 'input_report.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['ignored']), 25)
        self.assertEqual(len(feedback.warnings), 21)
        self.assertIn('ほか 5 件', feedback.warnings[-1])

    def test_all_inputs_invalid_stops_with_error(self):
        feedback = _Feedback()
        with self.assertRaises(ValueError):
            prepare_inputs(self._cfg([self.bad]), self.dir / 'work3',
                           gdal, osr, feedback)
        self.assertEqual(len(feedback.warnings), 1)


class ConfigVersusInputTests(ForestIgnoredWarningTests):
    """M-3 follow-up: configuration errors must not look like bad inputs."""

    def test_bad_forest_grid_cell_raises_not_ignored(self):
        good = self._grid_csv('good.csv', [(0, 0), (2, 0), (0, 2), (2, 2)])
        cfg = _config(inputs=[good], forest_grid_cell='abc')
        with self.assertRaises(ValueError):
            prepare_inputs(cfg, self.work, _Feedback(), gdal, osr)

    def test_nonpositive_forest_grid_cell_raises(self):
        good = self._grid_csv('good.csv', [(0, 0), (2, 0), (0, 2), (2, 2)])
        cfg = _config(inputs=[good], forest_grid_cell='0')
        with self.assertRaises(ValueError):
            prepare_inputs(cfg, self.work, _Feedback(), gdal, osr)


if __name__ == '__main__':
    unittest.main()
