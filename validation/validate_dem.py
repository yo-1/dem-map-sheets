"""Run in QGIS Python console or Python with GDAL. Writes only below validation/results."""
import json, sys, platform, hashlib
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent
try:
    from osgeo import gdal,osr
except ImportError:
    raise RuntimeError('GDAL is required: run this script in QGIS Python Console')
try:
    from dem_map_sheets.engine.dem_pipeline import run, merge_geotiffs
    from dem_map_sheets.engine.terrainrgb import write_terrainrgb_xyz
except ImportError:
    sys.path.insert(0,str(ROOT.parent))
    from dem_map_sheets.engine.dem_pipeline import run, merge_geotiffs
    from dem_map_sheets.engine.terrainrgb import write_terrainrgb_xyz

def check_all():
    gdal.UseExceptions()
    cases=['year','accuracy','density','coverage','first','last','unknown','json']
    results=[]
    result_root=ROOT/'results';result_root.mkdir(exist_ok=True)
    import uuid
    session=result_root/uuid.uuid4().hex[:8];session.mkdir()
    for case in cases:
        inputs=ROOT/'data'/case
        out=session/case
        meta=inputs/('metadata.json' if case=='json' else 'metadata.csv')
        m=run(dict(inputs=[str(inputs/'a.asc'),str(inputs/'b.asc')],input_type='raster',
            source_nodata=None,vertical_reference='検証用仮想標高',normalize_rasters=True,input_crs='EPSG:6677',target_crs='EPSG:6677',cell_size=100,
            merge_priority=case if case in ('first','last') else 'auto',priority_metadata=str(meta)),out,gdal,osr)
        ds=gdal.Open(m['merged_dem']);a=ds.ReadAsArray()
        assert ds.GetSpatialRef().GetAuthorityCode(None)=='6677'
        assert ds.RasterXSize==40 and ds.RasterYSize==15,(case,a.shape)
        assert ds.GetGeoTransform()==(0.,100.,0.,0.,0.,-100.)
        if case=='coverage':
            assert a[5,5]==300 and a[5,35]==200
        elif case in ('first','unknown'):
            assert a[5,5]==100 and a[5,35]==200
        else:
            assert a[5,5]==300 and a[5,35]==400
        # Both inputs have one unique hole. Even lower-ranked sources must fill it.
        assert a[0,0]==300 and a[0,39]==200,(case,'NoData fallback')
        ds=None
        if case not in ('first','last'):
            decisions=m['priority_selection']['decisions']
            selected={d['sheet_code']:Path(d['selected_source']).name for d in decisions}
            expected={'09KE001':'b.asc','09KE002':'a.asc'} if case=='coverage' else {k:('a.asc' if case=='unknown' else 'b.asc') for k in selected}
            assert len(selected)==2 and selected==expected,(case,selected,expected)
        xyz=write_terrainrgb_xyz(m['merged_dem'],out/'03_terrainrgb_xyz',14,14,50,gdal,osr)
        assert xyz['written_tiles']>0
        for tile in (out/'03_terrainrgb_xyz').rglob('*.png'):
            t=gdal.Open(str(tile));rgba=t.ReadAsArray();t=None
            rgb=rgba[:3].astype(np.uint32)
            h=-10000.0+(rgb[0]*65536+rgb[1]*256+rgb[2])/10.0;valid=rgba[3]>0
            assert rgba.shape==(4,256,256)
            assert np.all(np.isfinite(h[valid]))
            assert np.all((h[valid]>=99.9)&(h[valid]<=400.1))
            assert np.all(h[~valid] == -9999)
        m['terrainrgb_xyz']=xyz
        (out/'run.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
        results.append({'case':case,'status':'passed','output':str(out),'tiles':xyz['written_tiles']})
    (session/'validation_results.json').write_text(json.dumps(dict(python=platform.python_version(),platform=platform.platform(),gdal=gdal.VersionInfo('RELEASE_NAME'),checks=results),indent=2),encoding='utf-8')
    print('PASS:',len(results),'cases; results:',session)
    return results
if __name__=='__main__':check_all()
