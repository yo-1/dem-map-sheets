"""Print CRS identity evidence for the GDAL/PROJ version in use.

Run in each CI job so the EPSG dataset behaviour (notably the
EPSG:6668 JGD2011 -> JGD2024 redefinition) is on record per version.
"""
import re

from osgeo import gdal, osr

print('GDAL', gdal.__version__)
for code in (6668, 6677, 4612, 4326):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(code)
    match = re.search(r'(?:DATUM|ENSEMBLE)\["([^"]+)"', srs.ExportToWkt())
    print(f'EPSG:{code} name={srs.GetName()!r} '
          f'datum={match.group(1) if match else None!r} '
          f'authority={srs.GetAuthorityCode(None)} '
          f'projected={bool(srs.IsProjected())} axes={srs.GetAxesCount()}')
