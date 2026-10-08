"""Run with QGIS Python, or Python with GDAL, NumPy and Pillow installed.

Usage: python verify_terrainrgb.py /path/to/plugin_parent /path/to/new_output
Tests the installed plugin writer; decodes PNGs independently using Pillow.
"""
import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from osgeo import gdal, osr

sys.path.insert(0, sys.argv[1])
from dem_map_sheets.engine.terrainrgb import write_terrainrgb_xyz, tile_bounds, NODATA

gdal.UseExceptions()
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=False)
checks = []

def make_dem(path, values, epsg, bounds):
    west, south, east, north = bounds
    rows, cols = values.shape
    ds = gdal.GetDriverByName('GTiff').Create(str(path), cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((west, (east-west)/cols, 0, north, 0, -(north-south)/rows))
    crs = osr.SpatialReference(); crs.ImportFromEPSG(epsg)
    ds.SetProjection(crs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(NODATA)
    ds.GetRasterBand(1).WriteArray(values)
    ds.FlushCache()
    ds = None

def read_png(path):
    with Image.open(path) as im:
        assert im.mode == 'RGBA' and im.size == (256, 256), (im.mode, im.size)
        rgba = np.asarray(im).astype(np.uint32)
    # Independent decoder: deliberately do not call plugin decode_elevation.
    height = (rgba[:,:,0]*65536 + rgba[:,:,1]*256 + rgba[:,:,2])/10.0 - 10000.0
    return rgba, height

# Exact XYZ-aligned raster: varying elevations, negative elevations, mask hole.
z, x, y = 10, 910, 400
values = np.linspace(-20, 3700, 256*256, dtype=np.float32).reshape(256,256)
values[0,:4] = [0, 123.4, -12.3, 3000]
values[60:90,100:130] = NODATA
make_dem(out/'aligned.tif', values, 3857, tile_bounds(z,x,y))
meta = write_terrainrgb_xyz(out/'aligned.tif', out/'aligned_xyz', z, z, 10, gdal, osr)
rgba, height = read_png(out/f'aligned_xyz/{z}/{x}/{y}.png')
valid = values != NODATA
assert np.array_equal(rgba[:,:,3] == 255, valid)
assert np.all(height[~valid] == -9999)
error = float(np.max(np.abs(height[valid] - values[valid])))
assert error <= 0.05001, error
assert meta['status'] == 'completed' and meta['written_tiles'] == 1
checks.append(dict(name='aligned_xyz_png_roundtrip', status='passed', valid_pixels=int(valid.sum()), max_error_m=error, nodata_height_m=-9999, nodata_alpha=0))

# Projected Japanese CRS -> Web Mercator: constant elevation checks reprojection.
constant = np.full((128,128),123.4,dtype=np.float32)
make_dem(out/'japan.tif', constant, 6677, (0,-1280,1280,0))
meta = write_terrainrgb_xyz(out/'japan.tif',out/'japan_xyz',14,14,100,gdal,osr)
pixels=0
for png in (out/'japan_xyz').glob('*/*/*.png'):
    rgba,height = read_png(png)
    mask = rgba[:,:,3] == 255
    assert np.all(np.abs(height[mask]-123.4) < 0.000001)
    assert np.all(height[~mask] == -9999)
    pixels += int(mask.sum())
assert pixels > 0 and meta['status'] == 'completed'
checks.append(dict(name='epsg6677_to_xyz_png',status='passed',valid_pixels=pixels,written_tiles=meta['written_tiles']))

# Entirely missing source emits no elevation tiles.
make_dem(out/'empty.tif',np.full((256,256),NODATA,dtype=np.float32),3857,tile_bounds(z,x,y))
meta=write_terrainrgb_xyz(out/'empty.tif',out/'empty_xyz',z,z,10,gdal,osr)
assert meta['written_tiles']==0 and meta['skipped_empty_tiles']>=1 and meta['status']=='completed'
assert not list((out/'empty_xyz').glob('*/*/*.png'))
checks.append(dict(name='empty_source_no_png',status='passed'))

try:
    write_terrainrgb_xyz(out/'aligned.tif',out/'limited_xyz',z,z,0,gdal,osr)
except ValueError as exc:
    assert 'limit' in str(exc)
else:
    raise AssertionError('Tile limit was ignored')
assert not (out/'limited_xyz').exists()
checks.append(dict(name='tile_limit_before_output',status='passed'))

class Cancelled:
    def isCanceled(self): return True

try:
    write_terrainrgb_xyz(out/'aligned.tif',out/'cancelled_xyz',z,z,10,gdal,osr,Cancelled())
except RuntimeError as exc:
    assert 'cancelled' in str(exc)
else:
    raise AssertionError('Cancellation ignored')
assert json.loads((out/'cancelled_xyz/xyz.json').read_text())['status']=='failed'
checks.append(dict(name='cancelled_xyz_failure_record',status='passed'))

result=dict(python=platform.python_version(),platform=platform.platform(),gdal=gdal.VersionInfo('RELEASE_NAME'),
    writer_sha256=hashlib.sha256((Path(sys.argv[1])/'dem_map_sheets/engine/terrainrgb.py').read_bytes()).hexdigest(),checks=checks)
(out/'terrainrgb_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
