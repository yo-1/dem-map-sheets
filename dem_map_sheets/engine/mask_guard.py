"""Read-only, fail-fast protection for explicit raster masks (Issue #29).

This module never repairs data or changes the source's NoData/mask metadata.
The numerical mask is evaluated independently of the explicit mask using a
metadata-only VRT. Ambiguous combinations stop instead of being guessed.
GPL-3.0-only.
"""
import math
import numpy as np

try:
    from .progress import check_cancel as cancelled
    from ._constants import nodata_window
except ImportError:
    from progress import check_cancel as cancelled
    from _constants import nodata_window


class MaskSemanticsConflictError(ValueError):
    """A source's explicit validity mask cannot safely survive the pipeline."""


def inspect_explicit_mask(src, source, effective_nodata, origin, gdal,
                          stage, feedback=None, band_index=1, output_nodata=None):
    """Raise with structured diagnostics on loss, revival risk or uncertainty.

    Revival counts are *potential* cells, not measured output differences.
    A conservative stop is intentional even on a GDAL version which happens
    to preserve those cells for a particular processing window.
    """
    band = src.GetRasterBand(band_index)
    flags = None
    declared = None
    version = str(gdal.VersionInfo('RELEASE_NAME'))

    def number(value):
        try:
            return float(value) if value is not None and math.isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def entry(reason, count=0, kind='not_evaluated', details=None):
        result = dict(source=str(source), stage=stage, role='input_mask',
                      reason=reason, band=band_index, mask_flags=flags,
                      declared_nodata=number(declared),
                      effective_source_nodata=number(effective_nodata),
                      effective_source_nodata_origin=origin,
                      gdal_version=version, count=int(count), count_kind=kind,
                      values=[], positions=[])
        if declared is not None and number(declared) is None:
            result['declared_nodata_repr'] = str(declared)
        if effective_nodata is not None and number(effective_nodata) is None:
            result['effective_source_nodata_repr'] = str(effective_nodata)
        if details:
            result.update(values=sorted(details['values']), positions=details['positions'])
        return result

    def stop(records, cause=None):
        counts = []
        for record in records:
            if record['reason'] == 'valid_near_effective_nodata':
                counts.append(f"有効標高の消失候補 {record['count']} 件")
            elif record['reason'] == 'explicit_invalid_may_become_valid':
                counts.append(f"無効セルの復活リスク候補 {record['count']} 件（潜在的な件数で、実際の復活件数ではありません）")
            else:
                counts.append('安全なマスク比較を実施できません: ' + record['reason'])
        message = (f'入力「{source}」の有効範囲マスクを安全に保持できないため処理を停止しました。'
                   + '。'.join(counts) + '。'
                   '詳細は run.json の failure_detail を確認し、マスクを考慮した前処理を行ってください。'
                   '元ファイルの.msk削除、標高値の丸め・補間、出力NoData変更だけによる回避は行わないでください。'
                   ' (Explicit input mask safety stop; no automatic data correction.)')
        error = MaskSemanticsConflictError(message)
        error.detail = records
        if cause is not None:
            raise error from cause
        raise error

    def read(method, reason):
        try:
            return method()
        except Exception as exc:
            stop([entry(reason)], exc)

    flags = read(band.GetMaskFlags, 'mask_metadata_unreadable')
    # These system masks keep the existing PR #27/output checks unchanged.
    if flags in (gdal.GMF_ALL_VALID, gdal.GMF_NODATA):
        return
    declared = read(band.GetNoDataValue, 'nodata_metadata_unreadable')
    if flags not in (0, gdal.GMF_PER_DATASET):
        stop([entry('unsupported_mask_flags')])
    supported = (gdal.GDT_Byte, gdal.GDT_Int16, gdal.GDT_UInt16, gdal.GDT_Float32)
    if band.DataType not in supported:
        stop([entry('unsupported_mask_dtype')])
    if (read(band.GetScale, 'mask_metadata_unreadable') not in (None, 1) or
            read(band.GetOffset, 'mask_metadata_unreadable') not in (None, 0)):
        stop([entry('unsupported_mask_scale_offset')])
    if effective_nodata is not None:
        try:
            value = float(effective_nodata)
        except (ValueError, TypeError, OverflowError) as exc:
            stop([entry('unsupported_effective_nodata')], exc)
        if math.isinf(value) or (math.isnan(value) and band.DataType != gdal.GDT_Float32):
            stop([entry('unsupported_effective_nodata')])
        if math.isfinite(value) and band.DataType == gdal.GDT_Float32 and abs(value) > np.finfo(np.float32).max:
            stop([entry('unsupported_effective_nodata')])
        if band.DataType != gdal.GDT_Float32 and (not value.is_integer()):
            stop([entry('unsupported_effective_nodata')])
    mask = read(band.GetMaskBand, 'mask_unreadable')
    if mask is None or mask.XSize != band.XSize or mask.YSize != band.YSize:
        stop([entry('invalid_mask_dimensions')])
    if mask.DataType != gdal.GDT_Byte:
        stop([entry('unsupported_mask_dtype')])
    # Translate to VRT changes metadata only. No pixel copying/cleaning or
    # source mutation is allowed. Verify its values and generated mask below.
    probe = None
    try:
        if effective_nodata is not None:
            probe = read(lambda: gdal.Translate('', src, format='VRT',
                         bandList=[band_index], maskBand='none', noData=effective_nodata),
                         'numeric_mask_unverifiable')
            if probe is None:
                stop([entry('numeric_mask_unverifiable')])
            numeric_band = probe.GetRasterBand(1)
            if numeric_band.GetMaskFlags() != gdal.GMF_NODATA:
                stop([entry('numeric_mask_unverifiable')])
            numeric_mask = numeric_band.GetMaskBand()
        geotransform = src.GetGeoTransform()
        counts = [0, 0]
        samples = [dict(values=set(), positions=[]), dict(values=set(), positions=[])]

        def sample(hits, data, x, y, detail):
            if len(detail['values']) < 16:
                detail['values'].update(float(v) for v in np.unique(data[hits])[:16-len(detail['values'])])
            rows, cols = np.nonzero(hits)
            for r, c in zip(rows[:5], cols[:5]):
                if len(detail['positions']) >= 5:
                    break
                row, col = int(y+r), int(x+c)
                gt = geotransform
                detail['positions'].append(dict(row=row, col=col, value=float(data[r, c]),
                    x=gt[0]+(col+.5)*gt[1]+(row+.5)*gt[2],
                    y=gt[3]+(col+.5)*gt[4]+(row+.5)*gt[5]))

        for y in range(0, band.YSize, 512):
            for x in range(0, band.XSize, 512):
                cancelled(feedback)
                window = (x, y, min(512, band.XSize-x), min(512, band.YSize-y))
                data = read(lambda: band.ReadAsArray(*window), 'source_read_failed')
                validity = read(lambda: mask.ReadAsArray(*window), 'mask_read_failed')
                shape = (window[3], window[2])
                if data is None or validity is None or data.shape != shape or validity.shape != shape:
                    stop([entry('invalid_mask_read')])
                finite = np.isfinite(data)
                numeric_valid = np.ones(shape, dtype=bool)
                exact = np.zeros(shape, dtype=bool)
                if probe is not None:
                    other = read(lambda: numeric_band.ReadAsArray(*window), 'numeric_mask_unverifiable')
                    numerical = read(lambda: numeric_mask.ReadAsArray(*window), 'numeric_mask_unverifiable')
                    if (other is None or numerical is None or numerical.shape != shape or
                            not np.array_equal(data, other, equal_nan=True)):
                        stop([entry('numeric_mask_unverifiable')])
                    numeric_valid = numerical != 0
                    if math.isfinite(float(effective_nodata)):
                        # Integer definitions outside their range cannot match.
                        if data.dtype.kind == 'f' or np.iinfo(data.dtype).min <= float(effective_nodata) <= np.iinfo(data.dtype).max:
                            with np.errstate(over='ignore'):
                                exact = data == data.dtype.type(effective_nodata)
                lost = finite & (validity != 0) & ~exact & ~numeric_valid
                revival = finite & (validity == 0) & numeric_valid
                if output_nodata is not None:
                    # Preserve the existing output-collision enforcement and
                    # role. Never count the same cell as input_mask as well.
                    _value, lo, hi = nodata_window(output_nodata)
                    output_hits = finite & ~exact & (data >= lo) & (data <= hi)
                    lost &= ~output_hits
                    revival &= ~output_hits
                for i, hits in enumerate((lost, revival)):
                    counts[i] += int(hits.sum())
                    if hits.any():
                        sample(hits, data, x, y, samples[i])
        records = []
        for i, (reason, kind) in enumerate((('valid_near_effective_nodata', 'detected_candidates'),
                                            ('explicit_invalid_may_become_valid', 'potential'))):
            if counts[i]:
                records.append(entry(reason, counts[i], kind, samples[i]))
        if records:
            stop(records)
    except MaskSemanticsConflictError:
        raise
    except Exception as exc:
        # Cancellation must keep the existing cancelled status, not be
        # relabelled as an unverifiable mask.
        cancelled(feedback)
        stop([entry('numeric_mask_unverifiable')], exc)
    finally:
        probe = None
