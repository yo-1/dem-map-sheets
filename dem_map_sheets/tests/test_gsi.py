import unittest
from dem_map_sheets.engine.gsi_dem import traversal_indices

class GsiTraversalTests(unittest.TestCase):
    def test_gsi_common_order(self):
        got=list(traversal_indices([0,0],[2,1],[0,0],'+x-y'))
        self.assertEqual(got,[(0,0),(1,0),(2,0),(0,1),(1,1),(2,1)])

if __name__=='__main__': unittest.main()
