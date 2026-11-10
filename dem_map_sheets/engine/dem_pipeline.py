"""DEM normalization, metadata-aware overlap selection and merge pipeline. GPL-3.0-only."""
from pathlib import Path
import csv
import json
import math
import os
import shutil
import numpy as np
from .input_sources import DEFAULTS as INPUT_DEFAULTS, crs_of, discover, prepare_inputs, proj_epsg_version, validate_input, NODATA
from .map_sheets import intersecting_sheets, dimensions


def _has_valid_cells(path,gdal):
    """True when the raster holds at least one finite, non-NoData cell.

    Scans in row blocks and stops at the first valid cell, so the
    common case costs one block read; only a genuinely empty output
    is read in full.
    """
    ds=gdal.Open(str(path))
    if ds is None:return False
    band=ds.GetRasterBand(1);nodata=band.GetNoDataValue()
    try:
        for y in range(0,band.YSize,512):
            block=band.ReadAsArray(0,y,band.XSize,min(512,band.YSize-y))
            valid=np.isfinite(block)
            if nodata is not None:valid&=block!=nodata
            if valid.any():return True
        return False
    finally:
        ds=None


def gtiff_options():
    return ['TILED=YES','COMPRESS=DEFLATE','PREDICTOR=3','BIGTIFF=IF_SAFER']


def materialize_geotiff(source,dest,gdal):
    # Write to a temporary sibling and rename, so a failure can never
    # leave a partial file at the final path (deliverable protection:
    # every output file is complete-or-absent).
    dest=Path(dest);tmp=dest.with_name(dest.name+'.part')
    try:
        ds=gdal.Translate(str(tmp),str(source),format='GTiff',outputType=gdal.GDT_Float32,
            noData=NODATA,creationOptions=gtiff_options())
        if ds is None: raise RuntimeError('GeoTIFF creation failed: '+str(dest))
        ds.FlushCache();ds=None
        os.replace(tmp,dest)
    except BaseException:
        try:tmp.unlink()
        except OSError:pass
        raise
    return str(dest)


def prune_work_after_failure(work,keep_suffixes=('.log','.stderr','.stdout','.json','.txt')):
    """Delete bulky intermediates under work after a failure, keeping
    diagnostic files (PDAL/GDAL logs, stderr captures, JSON records,
    version text). Returns (removed_bytes, retained_bytes). Never
    raises: pruning must not mask the original failure."""
    work=Path(work);removed=0;retained=0
    if not work.is_dir():return removed,retained
    entries=sorted(work.rglob('*'),key=lambda p:len(p.parts),reverse=True)
    for path in entries:
        try:
            if path.is_dir() and not path.is_symlink():
                try:path.rmdir()
                except OSError:pass
                continue
            size=path.stat().st_size
            if path.name.endswith(keep_suffixes):retained+=size
            else:path.unlink();removed+=size
        except OSError:pass
    return removed,retained


def _record_failure(out,work,config,exc,feedback):
    """Prune the failed run's work directory (unless keep_work) and
    record the failure in run.json. Never raises."""
    try:
        keep=bool(config.get('keep_work',False))
        removed=None;retained=None
        if keep:
            retained_mode='all'
            retained=sum(f.stat().st_size for f in Path(work).rglob('*')
                         if f.is_file()) if Path(work).is_dir() else 0
            removed=0
        elif Path(work).is_dir():
            removed,retained=prune_work_after_failure(work)
            retained_mode='diagnostics_only'
        else:
            retained_mode='none'
        # Inventory what the failed run leaves outside work/ (r4 O-1):
        # the per-source GeoTIFFs in 01_source_dem are large, so
        # run.json records exactly which files remain and their sizes.
        # They are kept deliberately: the run directory is created
        # fresh for every run (never a pre-existing deliverable
        # location), a completed per-source DEM is a valid product,
        # and materialize/merge now guarantee complete-or-absent, so
        # nothing partial masquerades as a deliverable.
        try:
            outputs_present=[]
            for path in sorted(Path(out).rglob('*')):
                rel=path.relative_to(out)
                if path.is_file() and rel.parts[0]!='work' and path.name!='run.json':
                    outputs_present.append({'path':rel.as_posix(),
                        'bytes':path.stat().st_size})
        except OSError:
            outputs_present='unknown'
        record={'status':'cancelled' if (feedback is not None and feedback.isCanceled()) else 'failed',
            'error':str(exc),'merge_priority':config.get('merge_priority','last'),
            'work_dir':str(work),'work_retained':retained_mode,
            'work_removed_bytes':removed,'work_retained_bytes':retained,
            'outputs_present':outputs_present}
        (Path(out)/'run.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:
        pass


def merge_geotiffs(inputs,dest,gdal,first_wins=False,output_bounds=None,width=None,height=None):
    ordered=list(reversed(inputs)) if first_wins else list(inputs)
    vrt_path=Path(dest).with_suffix('.vrt')
    try:
        vrt=gdal.BuildVRT(str(vrt_path),ordered,srcNodata=NODATA,VRTNodata=NODATA,
                          resolution='highest',strict=True)
        if vrt is None: raise RuntimeError('DEM mosaic VRT creation failed')
        vrt.FlushCache();vrt=None
        if output_bounds is not None:
            # Translate outputBounds assigns georeferencing; Warp actually clips.
            ds=gdal.Warp(str(dest),str(vrt_path),format='GTiff',outputType=gdal.GDT_Float32,
                dstNodata=NODATA,creationOptions=gtiff_options(),outputBounds=output_bounds,
                width=width,height=height,resampleAlg='near',errorThreshold=0)
        else:
            ds=gdal.Translate(str(dest),str(vrt_path),format='GTiff',outputType=gdal.GDT_Float32,
                noData=NODATA,creationOptions=gtiff_options())
        if ds is None: raise RuntimeError('GeoTIFF creation failed: '+str(dest))
        ds.FlushCache();ds=None
        return str(dest)
    except BaseException:
        # Do not leave a partial mosaic behind on failure (M-1/R-10).
        Path(dest).unlink(missing_ok=True)
        raise
    finally:
        vrt_path.unlink(missing_ok=True)

def _finite_number(value,name,positive=False,integer=False):
    if value in (None,''): return None
    try: number=int(value) if integer else float(value)
    except (TypeError,ValueError) as exc: raise ValueError(f'{name} must be numeric') from exc
    if not math.isfinite(float(number)) or (positive and number<=0):
        raise ValueError(f'{name} must be finite'+(' and positive' if positive else ''))
    return number


def normalize_priority_record(record):
    """Normalize one priority-metadata record without guessing missing values."""
    source=str(record.get('source') or record.get('path') or '').strip()
    if not source: raise ValueError('Priority metadata record requires source/path')
    return dict(source=source,
        survey_year=_finite_number(record.get('survey_year'), 'survey_year', integer=True),
        accuracy_m=_finite_number(record.get('accuracy_m'), 'accuracy_m', positive=True),
        ground_density=_finite_number(record.get('ground_density'), 'ground_density', positive=True))


def load_priority_metadata(path):
    """Read optional CSV/JSON source metadata used by automatic overlap selection.

    CSV columns: source,survey_year,accuracy_m,ground_density
    JSON: a list of records, or {"sources": [...]}.
    """
    if not path: return []
    p=Path(path)
    if not p.is_file(): raise ValueError('Priority metadata file not found: '+str(p)+'（優先順位メタデータファイルが見つかりません。パスを確認してください）')
    if p.suffix.lower()=='.json':
        data=json.loads(p.read_text(encoding='utf-8-sig'))
        rows=data.get('sources',[]) if isinstance(data,dict) else data
        if not isinstance(rows,list): raise ValueError('Priority JSON must be a list or {"sources": [...]}')
    elif p.suffix.lower()=='.csv':
        with p.open('r',encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    else: raise ValueError('Priority metadata must be CSV or JSON')
    return [normalize_priority_record(r) for r in rows]


def priority_key(meta,coverage):
    """Lexicographic priority: year desc, accuracy asc, density desc, coverage desc.

    Known values always outrank unknown values at the criterion where they are compared.
    """
    year=meta.get('survey_year');acc=meta.get('accuracy_m');density=meta.get('ground_density')
    return ((year is not None), year if year is not None else -math.inf,
            (acc is not None), -acc if acc is not None else -math.inf,
            (density is not None), density if density is not None else -math.inf,
            float(coverage))


def infer_plane_zone_from_epsg(epsg):
    """Return Japanese plane rectangular zone for JGD2011/JGD2000 EPSG codes."""
    if 6669<=epsg<=6687:return epsg-6668
    if 2443<=epsg<=2461:return epsg-2442
    raise ValueError('Automatic sheet priority requires a JGD2011/JGD2000 plane rectangular CRS (zones I-XIX)')


def _zone_from_dataset(path,gdal,osr):
    ds=gdal.Open(str(path))
    if ds is None: raise ValueError('Cannot open DEM for CRS inspection: '+str(path))
    srs=ds.GetSpatialRef()
    if srs is None: raise ValueError('DEM CRS is missing')
    srs=srs.Clone();srs.AutoIdentifyEPSG()
    code=srs.GetAuthorityCode(None) or srs.GetAuthorityCode('PROJCS')
    ds=None
    if not code: raise ValueError('Could not identify EPSG code of output CRS')
    return infer_plane_zone_from_epsg(int(code))


def _bounds(path,gdal):
    ds=gdal.Open(str(path));gt=ds.GetGeoTransform()
    corners=[(gt[0]+gt[1]*x+gt[2]*y, gt[3]+gt[4]*x+gt[5]*y)
             for x in (0,ds.RasterXSize) for y in (0,ds.RasterYSize)]
    ds=None
    return (min(x for x,y in corners), min(y for x,y in corners),
            max(x for x,y in corners), max(y for x,y in corners))


def _union_bounds(paths,gdal):
    values=[_bounds(p,gdal) for p in paths]
    return (min(b[0] for b in values),min(b[1] for b in values),max(b[2] for b in values),max(b[3] for b in values))


def _group_vrt(paths,path,gdal):
    vrt=gdal.BuildVRT(str(path),paths,srcNodata=NODATA,VRTNodata=NODATA,resolution='highest',strict=True)
    if vrt is None: raise RuntimeError('Source-group VRT creation failed')
    vrt.FlushCache();vrt=None
    return str(path)


def _coverage_for_sheet(path,sheet,cell,gdal):
    width=int(round((sheet.east-sheet.west)/cell));height=int(round((sheet.north-sheet.south)/cell))
    if width<=0 or height<=0: raise ValueError('Invalid sheet/cell dimensions')
    mem=gdal.Warp('',str(path),format='MEM',outputBounds=(sheet.west,sheet.south,sheet.east,sheet.north),
                  width=width,height=height,outputType=gdal.GDT_Float32,dstNodata=NODATA,
                  resampleAlg='near',errorThreshold=0)
    if mem is None:return 0.0
    a=mem.GetRasterBand(1).ReadAsArray();nd=mem.GetRasterBand(1).GetNoDataValue();mem=None
    if a is None:return 0.0
    valid=np.isfinite(a)&(a!=(nd if nd is not None else NODATA))
    return float(valid.sum()/valid.size)


def _metadata_lookup(records,sources):
    """Match metadata by normalized full path first, then unique basename."""
    full={}
    for r in records:
        key=os.path.normcase(os.path.abspath(os.path.expanduser(r['source'])))
        if key in full:raise ValueError('Duplicate priority metadata source: '+r['source'])
        full[key]=r
    source_names={}
    for source in sources:
        name=Path(source).name.lower();source_names[name]=source_names.get(name,0)+1
    by_name={}
    for r in records: by_name.setdefault(Path(r['source']).name.lower(),[]).append(r)
    result=[]
    for source in sources:
        key=os.path.normcase(os.path.abspath(os.path.expanduser(source)))
        r=full.get(key)
        if r is None:
            matches=by_name.get(Path(source).name.lower(),[])
            if len(matches)==1 and source_names.get(Path(source).name.lower())==1:r=matches[0]
        result.append(dict(r) if r else dict(source=source,survey_year=None,accuracy_m=None,ground_density=None))
    return result


def _report_year(report):
    years=[]
    def walk(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k=='survey_year' and v not in (None,''):
                    try: years.append(int(float(v)))
                    except (TypeError,ValueError): pass
                else: walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    walk(report)
    # Mixed-year archives cannot be assigned their newest member's year.
    return years[0] if years and len(set(years))==1 else None


def _prepare_source_groups(c,work,source_dir,gdal,osr,feedback=None):
    """Normalize each top-level source independently so provenance survives overlap selection."""
    metadata=load_priority_metadata(c.get('priority_metadata',''))
    meta_rows=_metadata_lookup(metadata,c['inputs'])
    groups=[];counter=0
    for source_index,(source,meta) in enumerate(zip(c['inputs'],meta_rows),1):
        source_work=work/f'source_{source_index:05d}'
        prepared,report=prepare_inputs({**c,'inputs':[source]},source_work,gdal,osr,feedback)
        rasters=[]
        for path in prepared['inputs']:
            counter+=1;dest=source_dir/f'dem_{counter:05d}.tif'
            rasters.append(materialize_geotiff(path,dest,gdal))
        auto_year=_report_year(report)
        if meta['survey_year'] is None and auto_year is not None:meta['survey_year']=auto_year
        # When input is explicitly ground-only point data, raw point count / bounds area is a valid ground density.
        if meta['ground_density'] is None and c['input_type']=='lidar' and c.get('lidar_mode')=='ground_only':
            records=report.get('sources',[]);dens=[]
            for r in records:
                b=r.get('bounds');count=r.get('count')
                if b and count and (b[2]-b[0])>0 and (b[3]-b[1])>0:
                    dens.append(float(count)/((b[2]-b[0])*(b[3]-b[1])))
            target=osr.SpatialReference();target.SetFromUserInput(c['target_crs'])
            if len(dens)==1 and target.IsProjected() and abs(target.GetLinearUnits()-1.0)<1e-9:
                meta['ground_density']=dens[0]
        group_vrt=_group_vrt(rasters,work/f'source_{source_index:05d}.vrt',gdal)
        groups.append(dict(source=source,rasters=rasters,vrt=group_vrt,metadata=meta,input_report=report))
    return groups


def _merge_auto(c,groups,out,gdal,osr,feedback=None):
    cell=float(c['cell_size']);all_vrts=[g['vrt'] for g in groups]
    zone=_zone_from_dataset(all_vrts[0],gdal,osr);bounds=_union_bounds(all_vrts,gdal)
    sheet_dir=out/'02_merged_sheets';sheet_dir.mkdir()
    width_m,height_m=dimensions(2500);width=int(round(width_m/cell));height=int(round(height_m/cell))
    if abs(width*cell-width_m)>1e-6 or abs(height*cell-height_m)>1e-6:
        raise ValueError('Automatic sheet priority requires cell size to divide 2000 m and 1500 m exactly')
    candidates=list(intersecting_sheets(bounds,zone,2500,c.get('max_priority_sheets',100000)))
    decisions=[];written=[]
    for number,sheet in enumerate(candidates,1):
        if feedback is not None and feedback.isCanceled():raise RuntimeError('Cancelled（処理がキャンセルされました。途中まで生成された出力ファイルは不完全なため、使用しないでください）')
        scored=[]
        for i,g in enumerate(groups):
            coverage=_coverage_for_sheet(g['vrt'],sheet,cell,gdal)
            if coverage>0:
                scored.append((priority_key(g['metadata'],coverage),i,coverage))
        if not scored:continue
        scored.sort()
        ordered=[groups[i]['vrt'] for _,i,_ in scored]  # low -> high; GDAL VRT last valid pixel wins
        dest=sheet_dir/f'{sheet.code}.tif'
        merge_geotiffs(ordered,dest,gdal,False,(sheet.west,sheet.south,sheet.east,sheet.north),width,height)
        written.append(str(dest))
        ranking=[]
        for rank,(_,i,coverage) in enumerate(reversed(scored),1):
            g=groups[i];ranking.append(dict(rank=rank,source=g['source'],survey_year=g['metadata'].get('survey_year'),
                accuracy_m=g['metadata'].get('accuracy_m'),ground_density=g['metadata'].get('ground_density'),
                sheet_coverage=round(coverage,8)))
        decisions.append(dict(sheet_code=sheet.code,selected_source=ranking[0]['source'],ranking=ranking))
        if feedback is not None:
            feedback.pushInfo(f'Priority sheet {sheet.code}: {Path(ranking[0]["source"]).name}')
            feedback.setProgress(min(65,10+55*number/max(1,len(candidates))))
    if not written:raise ValueError('Automatic priority produced no nonempty map sheets（自動優先順位の結果、対象範囲に重なる図郭が1件もありませんでした。入力DEMの範囲と座標系が対象地域に合っているか、優先順位メタデータの対象範囲を確認してください）')
    merged=merge_geotiffs(written,out/'02_merged_dem.tif',gdal,False)
    return merged,dict(mode='automatic',plane_zone=zone,sheet_level=2500,
        criteria=['survey_year_desc','accuracy_m_asc','ground_density_desc','sheet_coverage_desc'],
        missing_values='known values outrank unknown values at each criterion',sheet_count=len(written),decisions=decisions)


def _source_datum_labels(node,found=None):
    """Original datum/CRS labels declared by the inputs, collected
    from the input report (GSI sources record them as original_srs).
    These are the labels as written in the source data - not names
    derived from the output CRS - so a run can be traced to the
    realization its inputs declared."""
    if found is None:found=set()
    if isinstance(node,dict):
        for key,value in node.items():
            if key=='original_srs' and isinstance(value,str) and value:
                found.add(value)
            else:
                _source_datum_labels(value,found)
    elif isinstance(node,(list,tuple)):
        for item in node:_source_datum_labels(item,found)
    return found


def _path_error(exc,output_dir):
    """Japanese guidance for an OS path failure, or None when the
    error is not a path problem. A Windows long path surfaces as a
    bare '[WinError 3]' with no hint which path failed or what to
    do; the failing path is carried on the exception, so name it,
    say which side it belongs to (under the output dir, or an
    input-side path) and give the remedy. The original error text
    stays in run.json via the failure record."""
    filename=getattr(exc,'filename',None)
    if not filename or getattr(exc,'errno',None) not in (2,3,36,206):
        return None
    path=str(filename)
    # Compare normalized components, not a raw string prefix. This handles
    # relative output directories and avoids treating output2 as output.
    try:
        normalized_path = os.path.normcase(os.path.abspath(os.path.normpath(path)))
        normalized_output = os.path.normcase(os.path.abspath(os.path.normpath(str(output_dir))))
        side = ('出力先' if os.path.commonpath((normalized_path, normalized_output))
                == normalized_output else '入力側')
    except (ValueError, OSError, TypeError):
        side = '入力側'
    length=f'パスの長さは{len(path)}文字です。' if len(path)>200 else ''
    return (f'ファイルのパスにアクセスできませんでした（{side}）。対象: {path}。'
        f'{length}フォルダーのパスが長すぎるとWindowsでは開けません。'
        '出力先には短いパスのフォルダーを指定し、入力ファイルも短いパスへ置いてください。'
        '対象ファイルが存在することも確認してください。'
        f'（OSエラー{exc.errno}）')


def run(config,output_dir,gdal,osr,feedback=None):
    try:
        return _run_scoped(config,output_dir,gdal,osr,feedback)
    except OSError as exc:
        message=_path_error(exc,output_dir)
        if message is None:raise
        raise ValueError(message) from exc


def _run_scoped(config,output_dir,gdal,osr,feedback=None):
    # The pipeline relies on GDAL raising exceptions, but enabling
    # them is process-global state (W-13): scope it to this call and
    # restore the caller's setting - QGIS, another plugin, a script -
    # exactly on exit. The restore is a manual flag flip on purpose:
    # GDAL 3.8.4's ExceptionMgr.__exit__ re-raises a still-pending
    # GDAL error (e.g. a failed CRS lookup) and would mask the
    # pipeline's own exception (found by CI).
    get_state=getattr(gdal,'GetUseExceptions',None)
    if get_state is None:
        return _run(config,output_dir,gdal,osr,feedback)
    previous=get_state()
    try:
        if not previous:gdal.UseExceptions()
        return _run(config,output_dir,gdal,osr,feedback)
    finally:
        if not previous:gdal.DontUseExceptions()


def _run(config,output_dir,gdal,osr,feedback=None):
    c={**INPUT_DEFAULTS,**config}
    # KD-2: validate_input existed but was never called; configuration
    # inconsistencies now fail here with a clear error, before any
    # directory is created or GDAL is invoked (also covers KD-1).
    for key in ('cell_size','target_crs'):
        if key not in c:
            ja={'cell_size':'セルサイズ','target_crs':'対象CRS'}[key]
            raise ValueError(f'{key} is required（{ja}を指定してください）')
    c.setdefault('max_pixels',25000000)
    validate_input(c)
    # CRS validation depth (r5): reject an unparseable, compound or
    # 3D target CRS here, before any directory or GDAL work, instead
    # of failing midway through PDAL/GDAL processing.
    try:
        crs_of(c['target_crs'],osr)
    except ValueError as exc:
        raise ValueError('Invalid target_crs: '+str(c['target_crs'])
            +'（対象CRSが解釈できません。有効なCRS（例：EPSG:6677）を指定してください）') from exc
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=False)
    work=out/'work';source_dir=out/'01_source_dem';source_dir.mkdir()
    c['inputs']=discover(c['inputs'],'',c,feedback)
    try:
        mode=config.get('merge_priority','last')
        if mode=='auto':
            work.mkdir(parents=True,exist_ok=False)
            groups=_prepare_source_groups(c,work,source_dir,gdal,osr,feedback)
            materialized=[p for g in groups for p in g['rasters']]
            merged,priority_report=_merge_auto(c,groups,out,gdal,osr,feedback)
            report={'mode':'per_source','sources':[g['input_report'] for g in groups]}
        else:
            prepared,report=prepare_inputs(c,work,gdal,osr,feedback)
            materialized=[]
            for i,path in enumerate(prepared['inputs'],1):
                if feedback is not None and feedback.isCanceled():raise RuntimeError('Cancelled（処理がキャンセルされました。途中まで生成された出力ファイルは不完全なため、使用しないでください）')
                dest=source_dir/f'dem_{i:05d}.tif';materialized.append(materialize_geotiff(path,dest,gdal))
                if feedback is not None:feedback.setProgress(min(55,5+50*i/max(1,len(prepared['inputs']))))
            merged=merge_geotiffs(materialized,out/'02_merged_dem.tif',gdal,first_wins=(mode=='first'))
            priority_report={'mode':mode}
        # An output with no valid cells at all (e.g. a 5x5 m input
        # rendered at a 10 m cell, whose single output pixel centre
        # falls outside the input) must not look like a successful
        # DEM: warn loudly, but do not fail the run - the cause may
        # be a legitimate (if useless) parameter combination.
        output_warnings=[]
        if not _has_valid_cells(merged,gdal):
            message=('出力DEMの全セルがNoDataです。入力の標高値が1つも反映されていません。'
                'セルサイズが入力範囲に対して大きすぎる、入力と出力の座標系・範囲のずれ、'
                'source_nodata（元データの無効値）の設定が実データと合っていない、'
                'などの可能性があります。入力範囲とセルサイズ、座標系、NoData設定を確認してください。'
                ' (The merged DEM contains no valid elevation cells.)')
            output_warnings.append('all_nodata_output')
            if feedback is not None and hasattr(feedback,'pushWarning'):
                feedback.pushWarning(message)
        datum_labels=sorted(_source_datum_labels(report))
        manifest={'status':'completed','input_type':c['input_type'],'source_count':len(materialized),
            'warnings':output_warnings,
            'source_dems':materialized,'merged_dem':merged,'target_crs':c['target_crs'],'cell_size':c['cell_size'],
            'vertical_reference':c['vertical_reference'],'vertical_conversion':False,'merge_priority':mode,
            'priority_selection':priority_report,'input_report':report,'output_nodata':NODATA,
            'source_datum_label':datum_labels[0] if len(datum_labels)==1 else (datum_labels or None),
            'proj_epsg_version':proj_epsg_version(osr),
            'work_retained':'all' if config.get('keep_work') else 'none'}
    except BaseException as exc:
        _record_failure(out,work,config,exc,feedback)
        raise
    (out/'run.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if not config.get('keep_work',False):shutil.rmtree(work,ignore_errors=True)
    return manifest
