"""Create the point-cloud fixtures for the Issue #9 NoData verification.

Usage (any OS; pdal is needed only for the LAS/LAZ files):
    python validation/make_nodata_fixtures.py --out OUTDIR [--pdal PATH_TO_pdal(.exe)]

Writes, per source format (csv / las / laz) and per extra-point elevation, a
16-point ground grid (X,Y in {0,10,20,30} m, Z = 100 + x/10 + y/10) plus ONE
extra ground point at (5,5):

    nodata_<format>_normal.*        Z = 105.5      -> must complete
    nodata_<format>_m9999.*         Z = -9999      -> must stop (output NoData collision)
    nodata_<format>_m999999.*       Z = -999999    -> must stop (internal sentinel)
    nodata_<format>_near9999.*      Z = -9999.002  -> must stop (inside GDAL's tolerance)
    nodata_<format>_clean.*         no extra point -> must complete (baseline)
plus a Japanese-named copy of the LAS fixture in 日本語フォルダ/ (path test).
Coordinates are EPSG:6677 (JGD2011 / Japan Plane Rectangular CS IX).
Nothing is overwritten: an existing OUTDIR is refused.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

GRID = [(x, y, 100 + x // 10 + y // 10) for y in (0, 10, 20, 30) for x in (0, 10, 20, 30)]
CASES = {'normal': 105.5, 'm9999': -9999, 'm999999': -999999, 'near9999': -9999.002, 'clean': None}


def write_csv(path, extra, with_class):
    rows = list(GRID) + ([(5, 5, extra)] if extra is not None else [])
    header = 'X,Y,Z,Classification' if with_class else 'X,Y,Z'
    body = ''.join(f'{x},{y},{z}' + (',2' if with_class else '') + '\n' for x, y, z in rows)
    path.write_text(header + '\n' + body, encoding='utf-8')


def make_las(pdal, csv_path, las_path):
    spec = {'pipeline': [
        {'type': 'readers.text', 'filename': str(csv_path)},
        {'type': 'writers.las', 'filename': str(las_path), 'scale_x': 0.001, 'scale_y': 0.001,
         'scale_z': 0.001, 'offset_x': 0, 'offset_y': 0, 'offset_z': 0,
         **({'compression': 'laszip'} if str(las_path).endswith('.laz') else {})}]}
    spec_file = Path(str(las_path) + '.make.json')
    spec_file.write_text(json.dumps(spec), encoding='utf-8')
    done = subprocess.run([pdal, 'pipeline', str(spec_file)], capture_output=True, text=True)
    spec_file.unlink()
    if done.returncode != 0:
        raise SystemExit('pdal failed for ' + str(las_path) + ': ' + done.stderr[:400])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--pdal', default='')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    made = []
    for name, z in CASES.items():
        csv = out / f'nodata_csv_{name}.csv'
        write_csv(csv, z, with_class=False)
        made.append(csv.name)
        if not args.pdal:
            continue
        src = out / f'_src_{name}.csv'
        write_csv(src, z, with_class=True)
        for ext in ('las', 'laz'):
            target = out / f'nodata_{ext}_{name}.{ext}'
            make_las(args.pdal, src, target)
            made.append(target.name)
        src.unlink()
    if args.pdal:
        jp = out / '日本語フォルダ'
        jp.mkdir()
        src = out / '_src_jp.csv'
        write_csv(src, 105.5, with_class=True)
        make_las(args.pdal, src, jp / '点群_normal.las')
        src.unlink()
        made.append('日本語フォルダ/点群_normal.las')
    print('\n'.join(made))
    print(f'{len(made)} files in {out}')
    if not args.pdal:
        print('NOTE: no --pdal given, LAS/LAZ fixtures were NOT created.', file=sys.stderr)


if __name__ == '__main__':
    main()
