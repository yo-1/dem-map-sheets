import unittest
import numpy as np
from dem_map_sheets.engine.terrainrgb import encode_elevation, decode_elevation, tile_range, HALF_WORLD

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

    def test_out_of_range_elevation_is_rejected(self):
        for value in (-10000.1, 1667721.6):
            with self.subTest(value=value), self.assertRaises(ValueError):
                encode_elevation(np.array([[value]], dtype="float64"))

    def test_nonfinite_default_is_transparent(self):
        rgba = encode_elevation(np.array([[float("nan"), float("inf")]]))
        self.assertTrue((rgba[3] == 0).all())

    def test_invalid_masked_elevation_is_transparent(self):
        rgba = encode_elevation(np.array([[float("nan"), 42.0]]),
                                np.array([[False, True]]))
        self.assertEqual(int(rgba[3, 0, 0]), 0)
        self.assertEqual(int(rgba[3, 0, 1]), 255)

    def test_tile_range(self):
        self.assertEqual(tile_range((-1,-1,1,1),0),(0,0,0,0))

    def test_encodable_range_endpoints_are_accepted(self):
        values=np.array([[-10000.0,1667721.5]],dtype='float64')
        rgba=encode_elevation(values)
        decoded=decode_elevation(rgba[:3])
        np.testing.assert_allclose(decoded,values,atol=1e-9)
        self.assertTrue((rgba[3]==255).all())

    def test_quantization_snaps_to_nearest_step(self):
        values=np.array([[0.04,0.06],[-0.04,-0.06]],dtype='float64')
        decoded=decode_elevation(encode_elevation(values)[:3])
        np.testing.assert_allclose(decoded,np.array([[0.0,0.1],[0.0,-0.1]]),atol=1e-9)

    def test_half_step_stays_within_half_step(self):
        values=np.array([[0.05,-0.05]],dtype='float64')
        decoded=decode_elevation(encode_elevation(values)[:3])
        self.assertTrue((np.abs(decoded-values)<=0.05+1e-9).all())

    def test_tile_range_zoom_and_clamping(self):
        self.assertEqual(tile_range((-HALF_WORLD,-HALF_WORLD,HALF_WORLD,HALF_WORLD),1),(0,1,0,1))
        self.assertEqual(tile_range((-2*HALF_WORLD,0.0,0.0,HALF_WORLD),1),(0,0,0,0))

if __name__=='__main__': unittest.main()
