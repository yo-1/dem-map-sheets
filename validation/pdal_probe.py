"""Real-PDAL probe: prints how the installed pdal reports point counts for a Z window.

Used to pin the exact PDAL behaviours the NoData point guard relies on
(filters.expression, filters.stats metadata layout, --stream). Output only;
asserts nothing. Run with REAL_PDAL_PATH=/path/to/pdal.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PDAL = os.environ['REAL_PDAL_PATH']


def run(args):
    r = subprocess.run([PDAL, *args], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def main():
    root = Path(tempfile.mkdtemp())
    print('pdal --version:', run(['--version'])[1].strip()[:200])
    csv = root / 'p.csv'
    zs = [100.0, 101.0, -9999.0, -9999.0004, -999999.0, 102.5]
    csv.write_text('X,Y,Z\n' + '\n'.join(f'{i},{i},{z}' for i, z in enumerate(zs)) + '\n')
    for ext, extra in (('las', {}), ('laz', {'compression': 'laszip'})):
        las = root / f'p.{ext}'
        spec = {'pipeline': [{'type': 'readers.text', 'filename': str(csv)},
                             {'type': 'writers.las', 'filename': str(las), 'scale_x': 0.001,
                              'scale_y': 0.001, 'scale_z': 0.001, 'offset_z': 0, **extra}]}
        (root / 'mk.json').write_text(json.dumps(spec))
        print(ext, 'make:', run(['pipeline', str(root / 'mk.json')])[0])
        rc, out, err = run(['info', '--summary', str(las)])
        print(ext, 'summary rc', rc, json.dumps(json.loads(out)['summary'].get('bounds')), err[:200])
        for label, expr in (('hit -9999', 'Z >= -9999.0005 && Z <= -9998.9995'),
                            ('hit -999999', 'Z >= -999999.0005 && Z <= -999998.9995'),
                            ('miss', 'Z >= -5.0 && Z <= -4.0'),
                            ('hit but declared-excluded', 'Z >= -9999.0005 && Z <= -9998.9995 && (Z < -9999.0005 || Z > -9998.9995)')):
            for stream in (True, False):
                spec = {'pipeline': [{'type': 'readers.las', 'filename': str(las)},
                                     {'type': 'filters.expression', 'expression': expr},
                                     {'type': 'filters.stats', 'dimensions': 'Z'}]}
                p = root / 'scan.json'
                p.write_text(json.dumps(spec))
                meta = root / 'meta.json'
                if meta.exists():
                    meta.unlink()
                args = ['pipeline', str(p), '--metadata', str(meta)] + (['--stream'] if stream else [])
                rc, out, err = run(args)
                body = meta.read_text() if meta.exists() else ''
                try:
                    data = json.loads(body)
                    stats = data['stages']['filters.stats']
                except Exception as exc:  # noqa: BLE001 - probe output only
                    stats = f'<no filters.stats: {exc!r}> keys={list(json.loads(body))[:5] if body else body}'
                print(f'{ext} [{label}] stream={stream} rc={rc} stderr={err.strip()[:150]!r}')
                print('   stats:', json.dumps(stats)[:600])
    return 0


if __name__ == '__main__':
    sys.exit(main())
