"""Reproducible cancel-latency check for the PDAL path (not run by CI).

Builds a synthetic LAS spanning many point blocks, starts a run with
a feedback stub that flips to cancelled after a fixed delay, and
reports: time from the flip to the run aborting, and how many blocks
had started by then. Run with the PDAL environment's Python:

  python validation/cancel_latency_check.py [cancel_after_seconds]
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import run
from dem_map_sheets.engine.input_sources import DEFAULTS

PDAL = str(Path(sys.executable).parent / 'pdal')


class TimedFeedback:
    def __init__(self, cancel_after):
        self._cancelled = False
        self.flip_time = None
        self.block_starts = []
        self._timer = threading.Timer(cancel_after, self._flip)
        self._timer.start()

    def _flip(self):
        self._cancelled = True
        self.flip_time = time.monotonic()

    def isCanceled(self):
        return self._cancelled

    def setProgress(self, _value):
        pass

    def pushWarning(self, _message):
        pass

    def pushInfo(self, message):
        if 'Point DEM block' in str(message) or 'Extracting' in str(message):
            self.block_starts.append(time.monotonic())


def make_las(folder):
    points = Path(folder) / 'points.csv'
    with points.open('w') as f:
        f.write('X,Y,Z,Classification\n')
        for ix in range(0, 2000, 4):
            for iy in range(0, 2000, 4):
                f.write(f'{ix}.0,{iy}.0,100.0,2\n')
    las = Path(folder) / 'points.las'
    pipeline = {'pipeline': [
        {'type': 'readers.text', 'filename': str(points)},
        {'type': 'writers.las', 'filename': str(las), 'a_srs': 'EPSG:6677'}]}
    pj = Path(folder) / 'make_las.json'
    pj.write_text(json.dumps(pipeline), encoding='utf-8')
    subprocess.run([PDAL, 'pipeline', str(pj)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return las


def main():
    cancel_after = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        las = make_las(root)
        c = dict(DEFAULTS)
        c.update(input_type='lidar', inputs=[str(las)], input_crs='EPSG:6677',
                 target_crs='EPSG:6677', cell_size=5.0, point_tile_size=64,
                 pdal_path=PDAL, max_pixels=10 ** 9)
        feedback = TimedFeedback(cancel_after)
        started = time.monotonic()
        outcome = 'completed'
        try:
            run(c, root / 'out', gdal, osr, feedback)
        except BaseException as exc:
            outcome = f'{type(exc).__name__}: {str(exc)[:60]}'
        ended = time.monotonic()
        blocks_after = [t for t in feedback.block_starts
                        if feedback.flip_time is not None and t > feedback.flip_time]
        print(json.dumps({
            'cancel_after_s': cancel_after,
            'flip_at_s': None if feedback.flip_time is None else round(feedback.flip_time - started, 2),
            'abort_latency_after_flip_s': None if feedback.flip_time is None else round(ended - feedback.flip_time, 2),
            'total_s': round(ended - started, 2),
            'blocks_started_total': len(feedback.block_starts),
            'blocks_started_after_flip': len(blocks_after),
            'outcome': outcome,
        }, ensure_ascii=False))


if __name__ == '__main__':
    main()
