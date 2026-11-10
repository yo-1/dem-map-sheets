"""GDAL integration regressions for rotated rasters and failed mosaics."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
from osgeo import gdal, osr
from dem_map_sheets.engine.dem_pipeline import _bounds, merge_geotiffs
from dem_map_sheets.engine.terrainrgb import _bounds_3857, write_terrainrgb_xyz

class GdalEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gdal.UseExceptions()

    def make_dem(self, path, gt, value=123.4):
        ds=gdal.GetDriverByName('GTiff').Create(str(path), 8, 6, 1, gdal.GDT_Float32)
        ds.SetGeoTransform(gt)
        crs=osr.SpatialReference()
        crs.ImportFromEPSG(3857)
        ds.SetProjection(crs.ExportToWkt())
        ds.GetRasterBand(1).WriteArray(np.full((6,8),value,dtype='float32'))
        ds.GetRasterBand(1).SetNoDataValue(-999999.0)
        ds=None

    def test_rotated_envelope_with_real_gdal(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'rotated.tif'
            self.make_dem(path, (100,10,3,200,2,-10))
            self.assertEqual(_bounds(path,gdal),(100,140,198,216))
            ds=gdal.Open(str(path))
            transformed=_bounds_3857(ds,osr)
            np.testing.assert_allclose(transformed,(100,140,198,216),atol=1e-5)
            ds=None

    def test_rotated_xyz_generates_tiles(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            path=root/'rotated.tif'
            self.make_dem(path,(100,10,3,200,2,-10))
            meta=write_terrainrgb_xyz(path,root/'xyz',15,15,16,gdal,osr)
            self.assertEqual(meta['status'],'completed')
            self.assertGreater(meta['written_tiles'],0)

    def test_failed_gdal_merge_leaves_no_vrt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            src=root/'source.tif'
            self.make_dem(src,(100,10,0,200,0,-10))
            dest=root/'mosaic.tif'
            with self.assertRaises(Exception):
                merge_geotiffs([str(src)],dest,gdal,output_bounds=(0,0,1,1),width=-1,height=1)
            self.assertFalse(dest.with_suffix('.vrt').exists())

if __name__=='__main__':
    unittest.main()
