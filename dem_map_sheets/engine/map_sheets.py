"""Japanese national large-scale map sheet helpers. GPL-3.0-only.

2500 sheets are 2000 m (E-W) x 1500 m (N-S) in a plane rectangular zone.
The code generation follows the same implementation used by CS Map Sheets.
"""
from dataclasses import dataclass
import math

SUBDIVISIONS = {5000: 1, 2500: 2, 1000: 5, 500: 10}

@dataclass(frozen=True)
class Sheet:
    code: str
    west: float
    south: float
    east: float
    north: float

def dimensions(level):
    if level not in SUBDIVISIONS:
        raise ValueError('sheet_level must be 5000, 2500, 1000 or 500')
    div=SUBDIVISIONS[level]
    return 4000//div,3000//div

def sheet_at(easting,northing,zone,level):
    if not isinstance(zone,int) or isinstance(zone,bool) or not 1<=zone<=19:
        raise ValueError('plane_zone must be an integer in 1..19')
    if not (-160000<=easting<160000 and -300000<northing<=300000):
        raise ValueError('Coordinate outside standard sheet range')
    width,height=dimensions(level)
    col=math.floor((easting+160000)/width)
    row=math.floor((300000-northing)/height)
    div=SUBDIVISIONS[level]
    row5,subrow=divmod(row,div); col5,subcol=divmod(col,div)
    majorrow,minorrow=divmod(row5,10); majorcol,minorcol=divmod(col5,10)
    code=f'{zone:02d}{chr(65+majorrow)}{chr(65+majorcol)}{minorrow}{minorcol}'
    if level==2500: code+=str(subrow*2+subcol+1)
    elif level==1000: code+=f'{subrow}{chr(65+subcol)}'
    elif level==500: code+=f'{subrow}{subcol}'
    west=-160000+col*width; north=300000-row*height
    return Sheet(code,west,north-height,west+width,north)

def intersecting_sheets(bounds,zone,level,max_sheets=100000):
    west,south,east,north=bounds
    if not all(math.isfinite(v) for v in bounds) or not (west<east and south<north):
        raise ValueError('Invalid bounds')
    if west < -160000 or east > 160000 or south < -300000 or north > 300000:
        raise ValueError('DEM bounds exceed standard national sheet domain; split by the correct plane zone')
    width,height=dimensions(level)
    c0=math.floor((west+160000)/width); c1=math.ceil((east+160000)/width)
    r0=math.floor((300000-north)/height); r1=math.ceil((300000-south)/height)
    if (r1-r0)*(c1-c0)>max_sheets:
        raise ValueError('Too many sheets; check extent, sheet_level and max_sheets')
    for r in range(r0,r1):
        for c in range(c0,c1):
            yield sheet_at(-160000+(c+.5)*width,300000-(r+.5)*height,zone,level)
