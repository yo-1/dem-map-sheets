"""Real run.json writing with a mocked PDAL boundary; not real LAS reading."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import importlib.util

HAVE_GDAL = importlib.util.find_spec('osgeo') is not None
if HAVE_GDAL:
    from osgeo import gdal, osr
    from dem_map_sheets.engine import point_inputs
    from dem_map_sheets.engine.input_sources import DEFAULTS
    from dem_map_sheets.engine.dem_pipeline import run


@unittest.skipUnless(HAVE_GDAL, 'GDAL required')
class PdalReportVersionTests(unittest.TestCase):
    def test_windows_linux_las_laz_csv_reports_share_provenance_rule(self):
        for raw in ('-----\npdal 2.10.0 (git-version: abc)\n-----\n', 'PDAL 2.10.0\n', 'tool build 2.10.0\n'):
            expected = 'pdal 2.10.0' if raw.startswith(('-----', 'PDAL')) else None
            for ext in ('las', 'laz', 'csv'):
                for priority in ('last', 'auto'):
                    with self.subTest(raw=raw, ext=ext, priority=priority), tempfile.TemporaryDirectory() as td:
                        root = Path(td)/'日本語'; root.mkdir()
                        source = root / ('input.'+ext); source.write_bytes(b'fixture')
                        cfg = {**DEFAULTS, 'input_type':'text' if ext=='csv' else 'lidar',
                               'text_mode':'points', 'lidar_mode':'ground_only', 'inputs':[str(source)],
                               'input_crs':'EPSG:6677','target_crs':'EPSG:6677','cell_size':1.,
                               'point_tile_size':64,'source_nodata':None,'max_pixels':25000000,
                               'merge_priority':priority,'pdal_path':'mocked'}
                        def process(exe, args, log, *a, **kw):
                            log = Path(log)
                            if args == ['--version']:
                                log.write_text(raw); return
                            spec = json.loads(Path(args[1]).read_text())
                            dest = Path(spec['pipeline'][0]['filename'])
                            ds = gdal.GetDriverByName('GTiff').Create(str(dest),64,64,1,gdal.GDT_Float32)
                            ds.SetGeoTransform((0.,1.,0.,64.,0.,-1.))
                            srs=osr.SpatialReference(); srs.ImportFromEPSG(6677)
                            ds.SetProjection(srs.ExportToWkt()); ds.GetRasterBand(1).Fill(100.)
                            ds.FlushCache(); ds=None
                        def pipeline(selected, bounds, c, bufferfile, classified):
                            return {'pipeline':[{'filename':str(bufferfile)}]}, 0
                        records=[dict(path=str(source),read_path=str(source),bounds=[0.,0.,.5,.5],count=1)]
                        with patch.object(point_inputs,'executable',return_value='mocked'), patch.object(point_inputs,'run_process',side_effect=process), patch.object(point_inputs,'inspect_sources',return_value=(records,[])), patch.object(point_inputs,'build_pipeline',side_effect=pipeline):
                            result=run(cfg,root/'out',gdal,osr,None)
                        saved=json.loads((root/'out'/'run.json').read_text())
                        report=saved['input_report']
                        def versions(node):
                            if isinstance(node,dict):
                                for k,v in node.items():
                                    if k=='pdal_version':yield v
                                    yield from versions(v)
                            elif isinstance(node,list):
                                for v in node:yield from versions(v)
                        self.assertEqual(list(versions(report)),[expected])
                        self.assertEqual(saved['provenance']['environment'].get('pdal'),expected)
                        self.assertNotIn('-----',json.dumps(report))
                        self.assertNotIn('git-version',json.dumps(report))
                        self.assertEqual(saved['schema_version'],1)
                        self.assertEqual(result['status'],'completed')
