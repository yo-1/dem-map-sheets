"""Execute this file from the QGIS Python Console after plugin installation."""
from pathlib import Path
import json,uuid,platform
import numpy as np
import processing
from osgeo import gdal
from qgis.core import Qgis
from dem_map_sheets.version import VERSION
assert VERSION=='0.2.1-rc4', 'rc4をインストールしQGISを再起動してください: '+VERSION
ROOT=Path(__file__).resolve().parent
session=ROOT/'qgis_results'/uuid.uuid4().hex[:8];session.mkdir(parents=True)
results=[]
for case in ['year','accuracy','density','coverage','first','last','unknown','json']:
    folder=ROOT/'data'/case
    r=processing.run('demmapsheets:create',{
        'VERTICAL':'検証用仮想標高','INPUT_TYPE':0,'FILES':[str(folder/'a.asc'),str(folder/'b.asc')],
        'INPUT_CRS':'EPSG:6677','CRS':'EPSG:6677','CELL':100,
        'MERGE_PRIORITY':1 if case=='first' else 0 if case=='last' else 2,
        'PRIORITY_METADATA':str(folder/('metadata.json' if case=='json' else 'metadata.csv')),
        'TERRAINRGB':True,'XYZ_MIN':14,'XYZ_MAX':14,'XYZ_LIMIT':50,
        'OUTPUT':str(session/case)})
    ds=gdal.Open(r['MERGED_DEM']);a=ds.ReadAsArray()
    expected=(300,200) if case=='coverage' else (100,200) if case in ('first','unknown') else (300,400)
    assert a.shape==(15,40),(case,a.shape)
    assert (a[5,5],a[5,35])==expected,(case,'selection')
    assert a[0,0]==300 and a[0,39]==200,(case,'fallback')
    assert ds.GetGeoTransform()==(0.,100.,0.,0.,0.,-100.)
    assert ds.GetSpatialRef().GetAuthorityCode(None)=='6677'
    ds=None
    m=json.loads(Path(r['MANIFEST']).read_text(encoding='utf-8'))
    assert m['status']=='completed' and m['terrainrgb_xyz']['written_tiles']>0
    if case not in ('first','last'):
        selected={d['sheet_code']:Path(d['selected_source']).name for d in m['priority_selection']['decisions']}
        expected_sources={'09KE001':'b.asc','09KE002':'a.asc'} if case=='coverage' else {k:('a.asc' if case=='unknown' else 'b.asc') for k in selected}
        assert len(selected)==2 and selected==expected_sources,(case,selected,expected_sources)
    # Decode every generated PNG independently, without using the plugin decoder.
    for tile in Path(r['XYZ_FOLDER']).glob('*/*/*.png'):
        png=gdal.Open(str(tile));rgba=png.ReadAsArray();png=None
        assert rgba.shape==(4,256,256)
        rgb=rgba[:3].astype(np.uint32)
        heights=-10000.0+(rgb[0]*65536+rgb[1]*256+rgb[2])/10.0
        valid=rgba[3]>0
        assert np.all((heights[valid]>=99.9)&(heights[valid]<=400.1))
        assert np.all(heights[~valid]==-9999)
    results.append({'case':case,'status':'passed','output':r})
# Failure must not retain a completed run.json.
try:
    processing.run('demmapsheets:create',{
        'VERTICAL':'検証用仮想標高','INPUT_TYPE':0,'FILES':[str(ROOT/'data/year/a.asc')],
        'INPUT_CRS':'EPSG:6677','CRS':'EPSG:6677','CELL':100,
        'MERGE_PRIORITY':0,'TERRAINRGB':True,'XYZ_MIN':14,'XYZ_MAX':14,
        'XYZ_LIMIT':1,'OUTPUT':str(session/'tile_limit_failure')})
except Exception:
    failed=list((session/'tile_limit_failure').rglob('run.json'))
    assert failed and all(json.loads(p.read_text(encoding='utf-8'))['status']=='failed' for p in failed)
    results.append({'case':'tile_limit_failure','status':'passed'})
else:raise AssertionError('Tile limit should stop this extent')
(session/'qgis_validation_results.json').write_text(json.dumps(dict(plugin_version=VERSION,qgis=Qgis.QGIS_VERSION,python=platform.python_version(),platform=platform.platform(),gdal=gdal.VersionInfo('RELEASE_NAME'),checks=results),ensure_ascii=False,indent=2),encoding='utf-8')
print('QGIS integration PASS; report:',session/'qgis_validation_results.json')
