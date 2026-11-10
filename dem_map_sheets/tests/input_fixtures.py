"""Synthetic input builders shared by the test_input_*.py suites.

All builders write small deterministic files.  Nothing here touches the
production code, and nothing needs GDAL except ``make_geotiff``, which imports
osgeo lazily so that the pure-Python suites keep running without it.
"""
import os
import stat
import zipfile
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- LEM

LEM_META_TEMPLATE = (
    "東西方向の点数,{nx}\n南北方向の点数,{ny}\n"
    "東西方向のデータ間隔,{dx}\n南北方向のデータ間隔,{dy}\n"
    "区画左下X座標,{south}\n区画左下Y座標,{west}\n"
    "区画右上X座標,{north}\n区画右上Y座標,{east}\n"
    "平面直角座標系番号,{zone}\n測量年,{year}\n図名,{sheet}\n")


def lem_row(number, values):
    """One fixed-width LEM record: 6 blanks, 4-digit record no., 5-digit cells."""
    return f'{"":6}{number:4d}' + ''.join(f'{v:5d}' for v in values)


def write_lem(directory, stem, rows, nx=3, ny=2, dx=5.0, dy=5.0, scale=100,
              bounds=None, encoding='utf-8', meta_suffix='.csv', zone=9, year=2020,
              meta_override=None):
    """Write ``stem.lem`` plus its companion metadata; return (lem, meta) paths.

    Default bounds are SW origin (0, 0) expressed in ``1/scale`` metre units
    (the usual centimetre integers), so the mesh covers 0..nx*dx / 0..ny*dy m.
    """
    directory = Path(directory)
    south, west = 0, 0
    north, east = bounds or (int(ny * dy * scale), int(nx * dx * scale))
    meta = meta_override if meta_override is not None else LEM_META_TEMPLATE.format(
        nx=nx, ny=ny, dx=dx, dy=dy, south=south, west=west, north=north, east=east,
        zone=zone, year=year, sheet=stem)
    meta_path = directory / (stem + meta_suffix)
    meta_path.write_bytes(meta.encode(encoding))
    lem_path = directory / (stem + '.lem')
    lem_path.write_text('\n'.join(rows) + '\n', encoding='ascii')
    return lem_path, meta_path


def standard_lem(directory, stem='ok', **kwargs):
    """3x2 mesh:  row1 = 100,110,-9999   row2 = -1111,120,130  (0.1 m units)."""
    rows = [lem_row(1, [100, 110, -9999]), lem_row(2, [-1111, 120, 130])]
    return write_lem(directory, stem, rows, **kwargs)


# ---------------------------------------------------------------- GSI XML

GSI_TEMPLATE = '''<?xml version="1.0" encoding="{encoding}"?>
<Dataset xmlns="http://fgd.gsi.go.jp/spec/2008/FGD_GMLSchema" xmlns:gml="http://www.opengis.net/gml/3.2">
<DEM><mesh>{mesh}</mesh><coverage>
<gml:boundedBy><gml:Envelope srsName="{srs}"><gml:lowerCorner>{lower}</gml:lowerCorner><gml:upperCorner>{upper}</gml:upperCorner></gml:Envelope></gml:boundedBy>
<gml:gridDomain><gml:Grid><gml:limits><gml:GridEnvelope><gml:low>{low}</gml:low><gml:high>{high}</gml:high></gml:GridEnvelope></gml:limits></gml:Grid></gml:gridDomain>
<gml:rangeSet><gml:DataBlock><gml:tupleList>{tuples}</gml:tupleList></gml:DataBlock></gml:rangeSet>
<gml:coverageFunction><gml:GridFunction>{sequence}<gml:startPoint>{start}</gml:startPoint></gml:GridFunction></gml:coverageFunction>
</coverage></DEM></Dataset>'''

GSI_GOOD_TUPLES = '地表面,10.5\n地表面,11.5\n地表面,12.5\nデータなし,-9999.0'


def gsi_xml(tuples=GSI_GOOD_TUPLES, lower='35.0 139.0', upper='35.1 139.1',
            low='0 0', high='1 1', start='0 0', srs='fguuid:jgd2011.bl',
            order='+x-y', rule='Linear', mesh='5139', encoding='UTF-8'):
    sequence = f'<gml:sequenceRule order="{order}">{rule}</gml:sequenceRule>'
    return GSI_TEMPLATE.format(tuples=tuples, lower=lower, upper=upper, low=low,
                               high=high, start=start, srs=srs, sequence=sequence,
                               mesh=mesh, encoding=encoding)


# ---------------------------------------------------------------- misc files

def write_zip(path, members):
    """members: iterable of (arcname, bytes|str). Names are stored verbatim."""
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return Path(path)


def write_text(path, body, encoding='utf-8'):
    path = Path(path)
    path.write_bytes(body.encode(encoding))
    return path


def make_geotiff(path, array=None, gt=(1000.0, 10.0, 0.0, 2030.0, 0.0, -10.0),
                 epsg=6677, nodata=-9999.0, bands=1):
    """Create a Float32 GeoTIFF (``epsg=None`` -> no CRS, ``gt=None`` -> no georef)."""
    from osgeo import gdal, osr
    if array is None:
        array = np.arange(12, dtype='float32').reshape(3, 4)
    h, w = array.shape
    ds = gdal.GetDriverByName('GTiff').Create(str(path), w, h, bands, gdal.GDT_Float32)
    if gt is not None:
        ds.SetGeoTransform(gt)
    if epsg is not None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(epsg)
        ds.SetProjection(srs.ExportToWkt())
    for index in range(1, bands + 1):
        band = ds.GetRasterBand(index)
        band.WriteArray(array)
        if nodata is not None:
            band.SetNoDataValue(nodata)
    ds.FlushCache()
    ds = None
    return Path(path)


def write_world_file(path, origin_x=1005.0, origin_y=2025.0, pixel=10.0):
    """Centre-of-first-pixel world file equivalent to make_geotiff's default grid."""
    Path(path).write_text(f'{pixel}\n0\n0\n{-pixel}\n{origin_x}\n{origin_y}\n')
    return Path(path)


# ---------------------------------------------------------------- fake PDAL

FAKE_PDAL_INFO = r'''#!/bin/sh
# Minimal PDAL stand-in: only `--version` and `info --summary` are implemented.
# FAKE_PDAL_VERSION and FAKE_PDAL_INFO_JSON (a file whose content is printed)
# are read from the environment.  Anything else exits 2.
case "$1" in
  --version) echo "pdal ${FAKE_PDAL_VERSION:-2.9.0} (fake)" ;;
  info) cat "$FAKE_PDAL_INFO_JSON" ;;
  *) echo "unexpected pdal call: $*" >&2; exit 2 ;;
esac
'''


def write_fake_pdal(directory):
    path = Path(directory) / 'fake_pdal_info'
    path.write_text(FAKE_PDAL_INFO, encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def have_gdal():
    try:
        from osgeo import gdal, osr  # noqa: F401
        return True
    except Exception:
        return False


IS_POSIX = os.name != 'nt'
