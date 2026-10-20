"""DEM -> XYZ TerrainRGB PNG writer.

Encoding follows the Forestry Open Data Standard:
height = -10000 + (R*256^2 + G*256 + B) * 0.1
"""
import json
import math
from pathlib import Path
import numpy as np

HALF_WORLD = 20037508.342789244
NODATA = -999999.0


def encode_elevation(values, valid=None):
    arr = np.asarray(values, dtype='float64')
    if valid is None:
        valid = np.isfinite(arr) & (arr != NODATA)
    if np.any(valid & (~np.isfinite(arr) | (arr < -10000.0) | (arr > 1667721.5))):
        raise ValueError("TerrainRGB valid elevation outside encodable range [-10000, 1667721.5] m")
    # Standard tile NoData decodes to -9999 m; keep alpha transparent as well.
    code = np.rint((np.where(valid,arr,-9999.0) + 10000.0) * 10.0)
    code = np.clip(code, 0, 256**3 - 1).astype('uint32')
    r = ((code >> 16) & 255).astype('uint8')
    g = ((code >> 8) & 255).astype('uint8')
    b = (code & 255).astype('uint8')
    a = np.where(valid, 255, 0).astype('uint8')
    return np.stack([r, g, b, a])


def decode_elevation(rgb):
    a = np.asarray(rgb, dtype='uint32')
    return -10000.0 + (a[0] * 256 * 256 + a[1] * 256 + a[2]) * 0.1


def tile_bounds(z, x, y):
    span = 2 * HALF_WORLD / (1 << z)
    return (-HALF_WORLD + x * span, HALF_WORLD - (y + 1) * span,
            -HALF_WORLD + (x + 1) * span, HALF_WORLD - y * span)


def tile_range(bounds, z):
    west, south, east, north = bounds
    n = 1 << z
    span = 2 * HALF_WORLD / n
    x0 = max(0, math.floor((west + HALF_WORLD) / span))
    x1 = min(n - 1, math.ceil((east + HALF_WORLD) / span) - 1)
    y0 = max(0, math.floor((HALF_WORLD - north) / span))
    y1 = min(n - 1, math.ceil((HALF_WORLD - south) / span) - 1)
    return x0, x1, y0, y1


def _bounds_3857(src, osr):
    srs = osr.SpatialReference()
    if not src.GetProjection() or srs.ImportFromWkt(src.GetProjection()) != 0:
        raise ValueError('DEM CRS is missing or invalid')
    merc = osr.SpatialReference(); merc.ImportFromEPSG(3857)
    for crs in (srs, merc): crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    gt = src.GetGeoTransform()
    corners = [(gt[0] + gt[1]*x + gt[2]*y, gt[3] + gt[4]*x + gt[5]*y)
               for x in (0, src.RasterXSize) for y in (0, src.RasterYSize)]
    return osr.CoordinateTransformation(srs, merc).TransformBounds(
        min(x for x,y in corners), min(y for x,y in corners),
        max(x for x,y in corners), max(y for x,y in corners), 41)


def write_terrainrgb_xyz(source, destination, min_zoom, max_zoom, max_tiles, gdal, osr, feedback=None):
    src = gdal.Open(str(source))
    if src is None or src.RasterCount != 1:
        raise ValueError('TerrainRGB input must be a single-band DEM')
    bounds = _bounds_3857(src, osr)
    ranges = [(z, tile_range(bounds, z)) for z in range(min_zoom, max_zoom + 1)]
    total = sum(max(0, x1-x0+1) * max(0, y1-y0+1) for _, (x0,x1,y0,y1) in ranges)
    if total <= 0 or total > max_tiles:
        raise ValueError(f'XYZ candidate tiles={total:,}; limit={max_tiles:,}')
    out = Path(destination); out.mkdir(parents=True, exist_ok=False)
    meta = {'status':'running','scheme':'xyz','crs':'EPSG:3857','tile_size':256,
            'format':'terrainrgb-png','minzoom':min_zoom,'maxzoom':max_zoom,
            'candidate_tiles':total,'written_tiles':0,'skipped_empty_tiles':0,
            'template':'{z}/{x}/{y}.png', 'encoding':'H=-10000+(R*65536+G*256+B)*0.1'}
    def save(): (out/'xyz.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    save(); done = 0
    png = gdal.GetDriverByName('PNG')
    if png is None: raise RuntimeError('GDAL PNG driver is unavailable')
    try:
        for z,(x0,x1,y0,y1) in ranges:
            for x in range(x0,x1+1):
                for y in range(y0,y1+1):
                    if feedback is not None and feedback.isCanceled():
                        raise RuntimeError('XYZ generation cancelled')
                    tile = gdal.Warp('', src, format='MEM', dstSRS='EPSG:3857',
                        outputBounds=tile_bounds(z,x,y), width=256, height=256,
                        outputType=gdal.GDT_Float32, dstNodata=NODATA,
                        resampleAlg='bilinear', errorThreshold=0)
                    if tile is None: raise RuntimeError('XYZ DEM warp failed')
                    elev = tile.GetRasterBand(1).ReadAsArray()
                    nd = tile.GetRasterBand(1).GetNoDataValue()
                    valid = np.isfinite(elev) & (elev != (nd if nd is not None else NODATA))
                    if valid.any():
                        rgba = encode_elevation(elev, valid)
                        mem = gdal.GetDriverByName('MEM').Create('',256,256,4,gdal.GDT_Byte)
                        for i,ci in enumerate((gdal.GCI_RedBand,gdal.GCI_GreenBand,gdal.GCI_BlueBand,gdal.GCI_AlphaBand)):
                            band=mem.GetRasterBand(i+1); band.WriteArray(rgba[i]); band.SetColorInterpretation(ci)
                        folder=out/str(z)/str(x); folder.mkdir(parents=True, exist_ok=True)
                        written=png.CreateCopy(str(folder/f'{y}.png'),mem)
                        if written is None: raise RuntimeError('PNG write failed')
                        written.FlushCache(); written=mem=None
                        meta['written_tiles'] += 1
                    else:
                        meta['skipped_empty_tiles'] += 1
                    done += 1
                    if feedback is not None:
                        feedback.setProgress(70 + 29 * done / total)
        meta['status']='completed'; save(); return meta
    except BaseException as exc:
        meta.update(status='failed', error=str(exc)); save(); raise
    finally:
        src = None
