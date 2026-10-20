import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from dem_map_sheets.engine.dem_pipeline import _metadata_lookup,_report_year,merge_geotiffs,_merge_auto

class Regressions(unittest.TestCase):
    def test_tile_nodata_decodes_to_standard_value(self):
        from dem_map_sheets.engine.terrainrgb import encode_elevation,decode_elevation
        rgba=encode_elevation([float('nan'),-999999.])
        self.assertTrue((decode_elevation(rgba[:3])==-9999.).all())
        self.assertTrue((rgba[3]==0).all())
    def test_mixed_archive_year_unknown(self):
        self.assertIsNone(_report_year({'sources':[{'survey_year':2020},{'survey_year':2025}]}))
        self.assertEqual(_report_year({'sources':[{'survey_year':2025},{'survey_year':2025}]}),2025)
    def test_ambiguous_basename_not_applied(self):
        rows=[dict(source='same.tif',survey_year=2025)]
        result=_metadata_lookup(rows,['/one/same.tif','/two/same.tif'])
        self.assertTrue(all(x['survey_year'] is None for x in result))
    def test_duplicate_metadata_rejected(self):
        with self.assertRaises(ValueError):_metadata_lookup([{'source':'a.tif'},{'source':'a.tif'}],['a.tif'])
    def test_sheet_bounds_use_warp(self):
        gdal=Mock();gdal.GDT_Float32=6
        with tempfile.TemporaryDirectory() as d:
            merge_geotiffs(['a.tif','b.tif'],Path(d)/'out.tif',gdal,output_bounds=(0,-1500,2000,0),width=20,height=15)
        gdal.Warp.assert_called_once();gdal.Translate.assert_not_called()
        self.assertEqual(gdal.Warp.call_args.kwargs['outputBounds'],(0,-1500,2000,0))
    def test_merge_failure_removes_temporary_vrt(self):
        gdal=Mock();gdal.GDT_Float32=6
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'out.tif'
            vrt=target.with_suffix('.vrt')
            def build_vrt(path, *args, **kwargs):
                Path(path).write_text('temporary')
                return Mock()
            gdal.BuildVRT.side_effect=build_vrt
            gdal.Translate.side_effect=RuntimeError('translation failed')
            with self.assertRaisesRegex(RuntimeError,'translation failed'):
                merge_geotiffs(['a.tif'],target,gdal)
            self.assertFalse(vrt.exists())
    def test_auto_crs_call_supplies_osr(self):
        from unittest.mock import patch
        from dem_map_sheets.engine import dem_pipeline as p
        with tempfile.TemporaryDirectory() as d:
            with patch.object(p,'_zone_from_dataset',side_effect=ValueError('probe')) as z:
                with self.assertRaisesRegex(ValueError,'probe'):
                    _merge_auto({'cell_size':100},[{'vrt':'a.vrt'}],Path(d),Mock(),'osr-marker')
                self.assertEqual(z.call_args.args[2],'osr-marker')
if __name__=='__main__':unittest.main()
