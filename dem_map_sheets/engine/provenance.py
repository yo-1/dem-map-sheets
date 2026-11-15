"""run.json の来歴（provenance）記録: SHA-256・サイズ・環境版。

方針:
- 成功時のみハッシュを計算する。失敗・中断時は計算せず、名前とサイズだけ
  記録する（失敗経路を重くせず、元の例外を隠さない）。
- ハッシュ失敗は run を失敗にしない。該当ファイルに sha256=None と
  例外種別のみを記録し、provenance.status を incomplete にする。
- キャンセルはハッシュ計算中でも尊重する（HashCancelled を送出）。
- 漏えい対策: ファイル名は basename のみ、出力は run ディレクトリからの
  相対パス。環境変数値・ホスト名・ユーザー名・実行ファイルのパスは記録しない。
"""
import hashlib
import json
import platform
from pathlib import Path

CHUNK = 1024 * 1024

STATUS_COMPLETE = 'complete'
STATUS_INCOMPLETE = 'incomplete'  # 一部のハッシュが取得できなかった
STATUS_PARTIAL = 'partial'        # 失敗/中断のためハッシュ未計算
STATUS_SKIPPED = 'skipped'        # record_hashes=False


class HashCancelled(RuntimeError):
    pass


def sha256_of(path, is_canceled=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            if is_canceled is not None and is_canceled():
                raise HashCancelled('Cancelled（SHA-256 計算中にキャンセルされました）')
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path, name, is_canceled=None, compute=True):
    """{name, bytes, sha256, sha256_status[, error]}。例外は HashCancelled 以外は握る。"""
    rec = {'name': name, 'bytes': None, 'sha256': None, 'sha256_status': 'not_computed'}
    try:
        rec['bytes'] = Path(path).stat().st_size
    except OSError as exc:
        rec['sha256_status'] = 'error'
        rec['error'] = type(exc).__name__
        return rec
    if not compute:
        return rec
    try:
        rec['sha256'] = sha256_of(path, is_canceled)
        rec['sha256_status'] = 'ok'
    except HashCancelled:
        raise
    except OSError as exc:
        rec['sha256_status'] = 'error'
        rec['error'] = type(exc).__name__
    return rec


def _input_records(inputs, is_canceled, compute):
    records = []
    for index, path in enumerate(inputs or []):
        rec = file_record(path, Path(str(path)).name, is_canceled, compute)
        rec = {'index': index, **rec}
        records.append(rec)
    return records


def output_files(out_dir):
    """run ディレクトリ配下の成果物（work/ と run.json を除く）。"""
    out_dir = Path(out_dir)
    result = []
    for path in sorted(out_dir.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(out_dir)
        if rel.parts[0] == 'work' or rel.as_posix() == 'run.json':
            continue
        result.append((path, rel.as_posix()))
    return result


def _status_of(records, compute):
    if not compute:
        return STATUS_SKIPPED
    if all(r['sha256_status'] == 'ok' for r in records):
        return STATUS_COMPLETE
    return STATUS_INCOMPLETE


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def collect_environment(gdal=None, osr=None, config=None, input_report=None):
    """取得できた版のみ記録する（取得不能な項目はキー自体を省略）。"""
    env = {'python': platform.python_version()}
    try:
        from ..version import VERSION
        env['plugin'] = VERSION
    except Exception:
        pass
    try:
        if gdal is not None:
            env['gdal'] = gdal.VersionInfo('RELEASE_NAME')
    except Exception:
        pass
    try:
        if osr is not None:
            env['proj'] = '%d.%d.%d' % (osr.GetPROJVersionMajor(),
                                         osr.GetPROJVersionMinor(), osr.GetPROJVersionMicro())
    except Exception:
        pass
    try:
        import numpy
        env['numpy'] = numpy.__version__
    except Exception:
        pass
    qgis = (config or {}).get('qgis_version')
    if qgis:
        env['qgis'] = str(qgis)
    pdal = _find_key(input_report, 'pdal_version') if input_report is not None else None
    if pdal:
        # 先頭行のみ（実行ファイルのパス等を含み得るため全文は記録しない）
        env['pdal'] = str(pdal).strip().splitlines()[0][:200]
    try:
        info = Path(__file__).resolve().parents[1] / 'build_info.json'
        if info.is_file():
            commit = json.loads(info.read_text(encoding='utf-8')).get('commit')
            if isinstance(commit, str) and len(commit) <= 64:
                env['commit'] = commit
    except Exception:
        pass
    return env


def build_success_provenance(config, out_dir, inputs, gdal=None, osr=None,
                             input_report=None, is_canceled=None):
    compute = bool(config.get('record_hashes', True))
    in_recs = _input_records(inputs, is_canceled, compute)
    out_recs = []
    for path, rel in output_files(out_dir):
        rec = file_record(path, rel, is_canceled, compute)
        rec['path'] = rec.pop('name')
        out_recs.append(rec)
    return {'status': _status_of(in_recs + out_recs, compute),
            'hash_algorithm': 'sha256',
            'inputs': in_recs, 'outputs': out_recs,
            'environment': collect_environment(gdal, osr, config, input_report)}


def build_failure_provenance(config, out_dir, inputs, gdal=None, osr=None):
    """失敗・中断時: ハッシュは計算しない。名前とサイズのみ。"""
    return {'status': STATUS_PARTIAL, 'hash_algorithm': 'sha256',
            'inputs': _input_records(inputs, None, False),
            'outputs_note': 'see outputs_present',
            'environment': collect_environment(gdal, osr, config)}


def add_output_file(manifest, out_dir, path, is_canceled=None):
    """run() 後に algorithm が追加した成果物（xyz.json 等）を追記する。"""
    prov = manifest.get('provenance')
    if not isinstance(prov, dict) or 'outputs' not in prov:
        return
    compute = prov.get('status') != STATUS_SKIPPED
    rel = Path(path).resolve().relative_to(Path(out_dir).resolve()).as_posix()
    rec = file_record(path, rel, is_canceled, compute)
    rec['path'] = rec.pop('name')
    prov['outputs'].append(rec)
    if compute and rec['sha256_status'] != 'ok':
        prov['status'] = STATUS_INCOMPLETE
