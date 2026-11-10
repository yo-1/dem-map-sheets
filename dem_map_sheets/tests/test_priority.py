import json
import tempfile
import unittest
from pathlib import Path
from dem_map_sheets.engine.dem_pipeline import (
    infer_plane_zone_from_epsg, load_priority_metadata, normalize_priority_record, priority_key)

class PriorityTests(unittest.TestCase):
    def test_priority_order_is_lexicographic(self):
        base=dict(survey_year=2024,accuracy_m=0.2,ground_density=4.0)
        newer=dict(survey_year=2025,accuracy_m=9.0,ground_density=.1)
        self.assertGreater(priority_key(newer,.1),priority_key(base,1.0))
        better_accuracy=dict(survey_year=2024,accuracy_m=0.1,ground_density=.1)
        self.assertGreater(priority_key(better_accuracy,.1),priority_key(base,1.0))
        denser=dict(survey_year=2024,accuracy_m=.2,ground_density=5.0)
        self.assertGreater(priority_key(denser,.1),priority_key(base,1.0))
        self.assertGreater(priority_key(base,.9),priority_key(base,.8))

    def test_known_value_beats_unknown_at_same_criterion(self):
        known=dict(survey_year=2024,accuracy_m=None,ground_density=None)
        unknown=dict(survey_year=None,accuracy_m=.01,ground_density=100)
        self.assertGreater(priority_key(known,.1),priority_key(unknown,1.0))

    def test_metadata_csv_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'m.csv'
            p.write_text('source,survey_year,accuracy_m,ground_density\na.tif,2024,0.15,3.2\n',encoding='utf-8')
            r=load_priority_metadata(p)[0]
            self.assertEqual(r['survey_year'],2024);self.assertAlmostEqual(r['accuracy_m'],.15)
            j=Path(d)/'m.json';j.write_text(json.dumps({'sources':[{'source':'b.laz','survey_year':2025}]}),encoding='utf-8')
            self.assertEqual(load_priority_metadata(j)[0]['survey_year'],2025)

    def test_metadata_rejects_bad_values(self):
        with self.assertRaises(ValueError):normalize_priority_record({'source':'x','accuracy_m':0})
        with self.assertRaises(ValueError):normalize_priority_record({'source':'x','ground_density':'bad'})

    def test_plane_zone_epsg(self):
        self.assertEqual(infer_plane_zone_from_epsg(6677),9)
        self.assertEqual(infer_plane_zone_from_epsg(2443),1)
        self.assertEqual(infer_plane_zone_from_epsg(6687),19)
        with self.assertRaises(ValueError):infer_plane_zone_from_epsg(4326)

if __name__=='__main__':unittest.main()
