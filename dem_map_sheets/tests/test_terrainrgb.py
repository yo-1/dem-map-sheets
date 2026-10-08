import unittest
import numpy as np
from dem_map_sheets.engine.terrainrgb import encode_elevation, decode_elevation, tile_range

class TerrainRGBTests(unittest.TestCase):
    def test_round_trip(self):
        values=np.array([[0.0,123.4],[-12.3,3000.0]],dtype='float32')
        rgba=encode_elevation(values)
        decoded=decode_elevation(rgba[:3])
        np.testing.assert_allclose(decoded,values,atol=0.051)
        self.assertTrue((rgba[3]==255).all())

    def test_nodata_alpha(self):
        values=np.array([[1.0,-999999.0]],dtype='float32')
        rgba=encode_elevation(values)
        self.assertEqual(int(rgba[3,0,0]),255)
        self.assertEqual(int(rgba[3,0,1]),0)

    def test_tile_range(self):
        self.assertEqual(tile_range((-1,-1,1,1),0),(0,0,0,0))

if __name__=='__main__': unittest.main()
