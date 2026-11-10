"""GSI JPGIS/GML DEM parser. NoData positions and grid start are preserved."""
import codecs
import io
import re
import xml.etree.ElementTree as ET  # nosec B405: DTD/entities rejected below
import numpy as np

NODATA = -999999.0


def local(tag): return tag.rsplit('}', 1)[-1]

def child(element, name, required=True):
    result = next((x for x in element.iter() if local(x.tag) == name), None)
    if result is None and required: raise ValueError('GSI XML: missing '+name)
    return result


def traversal_indices(lo, hi, start, order):
    match = re.fullmatch(r'([+-])([xy])([+-])([xy])', order.replace(' ', ''))
    if match is None or match.group(2) == match.group(4):
        raise ValueError('Unsupported GSI grid traversal order: '+order)
    axes = [(match.group(2), match.group(1)), (match.group(4), match.group(3))]
    limits = {'x': (lo[0], hi[0]), 'y': (lo[1], hi[1])}
    ranges = {}
    for axis, sign in axes:
        low, high = limits[axis]
        increasing = (sign == '+') if axis == 'x' else (sign == '-')
        ranges[axis] = range(low, high+1) if increasing else range(high, low-1, -1)
    fast, slow = axes[0][0], axes[1][0]
    # R-9: locate the start point arithmetically and generate lazily.
    # The previous implementation materialised all w*h coordinate tuples
    # (gigabytes at the max_pixels ceiling) just to slice from the start.
    def _position(axis):
        value=start[0] if axis=='x' else start[1]
        return ranges[axis].index(value)  # range.index is O(1)
    try: fast_pos,slow_pos=_position(fast),_position(slow)
    except ValueError as exc: raise ValueError('GSI startPoint outside grid traversal') from exc
    fast_values,slow_values=ranges[fast],ranges[slow]
    def _generate():
        for s_i in range(slow_pos,len(slow_values)):
            f_start=fast_pos if s_i==slow_pos else 0
            for f_i in range(f_start,len(fast_values)):
                point={'x':0,'y':0}; point[slow]=slow_values[s_i]; point[fast]=fast_values[f_i]
                yield (point['x'],point['y'])
    return _generate()


# R-8: only encodings that cannot smuggle a DTD past the byte-level
# check are accepted. UTF-16/32 are rejected outright: their byte
# patterns hide '<!DOCTYPE' from an ASCII search (review finding G-1).
# Canonical codec names (after codecs.lookup normalisation, R-8b).
_GSI_ENCODINGS = {'utf-8', 'utf-8-sig', 'shift_jis', 'cp932',
                  'euc_jp', 'iso8859-1', 'ascii'}
# Files declaring Shift_JIS are decoded as cp932 (Windows-31J), a
# superset: strict JIS X 0208 decoding rejects vendor-extension
# characters that real-world files may contain (r4 compatibility).


def parse_dem(data, max_pixels=25000000, source_override='', feedback=None):
    if len(data) > 256*1024*1024:
        raise ValueError('GSI XML too large or contains a DTD/entity declaration')
    declaration = re.search(br'encoding=[\"\x27]([^\"\x27]+)', data[:200], re.I)
    declared = declaration.group(1).decode('ascii') if declaration else 'utf-8-sig'
    # R-8b: normalise aliases (UTF8, SJIS, Windows-31J, latin1, ...)
    # to canonical codec names before the allow-list check, so safe
    # aliases are accepted while UTF-16/32 and others stay rejected.
    # 'Windows-31J' is a common real-world label (WHATWG maps it to
    # the Shift_JIS decoder) but CPython's codec registry has no such
    # alias, so map it explicitly before the lookup.
    if declared.strip().lower().replace('-', '_') == 'windows_31j':
        encoding = 'cp932'
    else:
        try:
            encoding = codecs.lookup(declared).name
        except (LookupError, UnicodeDecodeError) as exc:
            raise ValueError('Unsupported GSI XML encoding: ' + declared) from exc
    if encoding not in _GSI_ENCODINGS:
        raise ValueError('Unsupported GSI XML encoding: ' + declared)
    codec = 'cp932' if encoding == 'shift_jis' else encoding
    try:
        text = data.decode(codec)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ValueError('GSI XML cannot be decoded as ' + encoding) from exc
    if '\x00' in text:
        raise ValueError('GSI XML cannot be decoded as ' + encoding)
    # The DTD check runs on the decoded text with exact case (XML is
    # case-sensitive); the previous data.upper() byte copies are gone.
    if '<!DOCTYPE' in text or '<!ENTITY' in text:
        raise ValueError('GSI XML too large or contains a DTD/entity declaration')
    text = re.sub(r'encoding=[\"\x27][^\"\x27]+[\"\x27]', 'encoding="UTF-8"', text, count=1, flags=re.I)
    try:
        root = ET.fromstring(text)  # nosec B314: bounded, DTD-free XML
    except ET.ParseError as exc:
        raise ValueError('GSI XML parse error: ' + str(exc)) from exc
    dems = [e for e in root.iter() if local(e.tag) == 'DEM']
    for dem in dems:
        env=child(dem,'Envelope'); lower=list(map(float,child(env,'lowerCorner').text.split())); upper=list(map(float,child(env,'upperCorner').text.split()))
        if len(lower)!=2 or len(upper)!=2: raise ValueError('GSI DEM expects latitude/longitude envelope')
        south,west=lower; north,east=upper
        if not (-90<=south<north<=90 and -180<=west<east<=180): raise ValueError('GSI latitude/longitude extent is invalid')
        original_srs=env.get('srsName','')
        aliases={'fguuid:jgd2000.bl':'EPSG:4612','fguuid:jgd2011.bl':'EPSG:6668','fguuid:jgd2024.bl':'EPSG:6668'}
        crs=source_override or aliases.get(original_srs.lower(),original_srs)
        if not crs: raise ValueError('GSI CRS is missing; specify input_crs explicitly')
        grid=child(dem,'GridEnvelope'); lo=list(map(int,child(grid,'low').text.split())); hi=list(map(int,child(grid,'high').text.split()))
        if len(lo)!=2 or len(hi)!=2: raise ValueError('GSI GridEnvelope must be 2D')
        w,h=hi[0]-lo[0]+1,hi[1]-lo[1]+1
        if min(w,h)<=0 or w*h>max_pixels: raise ValueError('GSI grid exceeds input pixel limit')
        seq=child(dem,'sequenceRule',False)
        if seq is not None and (seq.text or '').strip()!='Linear': raise ValueError('Unsupported GSI grid traversal; sequenceRule must be Linear')
        order=seq.get('order','+x+y') if seq is not None else '+x+y'
        start=child(dem,'startPoint',False); sx,sy=map(int,start.text.split()) if start is not None else lo
        if not lo[0]<=sx<=hi[0] or not lo[1]<=sy<=hi[1]: raise ValueError('GSI startPoint outside grid')
        arr=np.full(w*h,NODATA,dtype='float32'); indices=traversal_indices(lo,hi,(sx,sy),order); populated=0
        tuples=child(dem,'tupleList')
        if tuples.get('cs',',')!=',' or tuples.get('ts',' ') not in (' ','\n'): raise ValueError('Unsupported GSI tuple separators')
        for line in io.StringIO(tuples.text or ''):
            for item in line.split():
                if feedback is not None and populated%100000==0 and feedback.isCanceled(): raise RuntimeError('Cancelled during GSI XML conversion')
                try: gx,gy=next(indices)
                except StopIteration: raise ValueError('Too many GSI tuples for GridEnvelope/startPoint')
                parts=item.split(',')
                if len(parts)!=2: raise ValueError('Malformed GSI tuple: '+item[:80])
                kind,value=parts
                try: z=float(value)
                except ValueError as exc:
                    raise ValueError(f'GSI DEM invalid elevation at tuple #{populated}: '
                                     f'{value[:40]!r}') from exc
                if np.isfinite(z) and z!=-9999 and kind not in ('データなし','海水面'):
                    arr[(gy-lo[1])*w+(gx-lo[0])]=z
                populated+=1
        mesh=child(dem,'mesh',False)
        yield arr.reshape(h,w),(west,(east-west)/w,0,north,0,-(north-south)/h),crs,dict(
            mesh=(mesh.text if mesh is not None else ''),original_srs=original_srs,horizontal_crs=crs,
            start_point=[sx,sy],traversal_order=order,populated_count=populated,
            jgd2024_horizontal_alias=(original_srs.lower()=='fguuid:jgd2024.bl' and not source_override))
