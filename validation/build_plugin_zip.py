"""Build the QGIS plugin ZIP deterministically from a git commit.

    python validation/build_plugin_zip.py --commit HEAD --out OUTDIR [--name NAME.zip]

The archive holds every file git tracks under dem_map_sheets/ at that commit
(top-level folder dem_map_sheets/, as QGIS expects), read from the commit
itself (not the working tree), sorted by path, with the commit's timestamp,
fixed permissions and DEFLATE level 6, so the same commit always yields the
same bytes.  Also writes <name>.manifest.json (path, size, sha256 per file)
and prints the ZIP size and SHA-256.

NOTE: the 2849ef4 hand-off ZIP (162,646 bytes, 58 files, SHA-256 742b3c7f...)
was built by a separate procedure recorded by Muse. Building 2849ef4 with this
script gives the same file count (58) and the same size (162,646 bytes), so the
file set and compression match; the SHA-256 differs (ZIP metadata such as
timestamps), and byte equality with that ZIP is NOT claimed.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, check=True).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--commit', default='HEAD')
    parser.add_argument('--out', required=True)
    parser.add_argument('--name', default='')
    args = parser.parse_args()
    sha = git('rev-parse', args.commit).decode().strip()
    when = int(git('show', '-s', '--format=%ct', sha).decode().strip())
    stamp = time.gmtime(max(when, 315532800))[:6]  # ZIP cannot hold dates before 1980
    names = sorted(n for n in git('ls-tree', '-r', '--name-only', '-z', sha, '--', 'dem_map_sheets')
                   .decode('utf-8').split('\0') if n)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / (args.name or f'DEM_Map_Sheets_QGIS_{sha[:7]}.zip')
    if zip_path.exists():
        sys.exit('refusing to overwrite ' + str(zip_path))
    manifest = []
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in names:
            data = git('show', f'{sha}:{name}')
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, data, compresslevel=6)
            manifest.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (out / (zip_path.name + '.manifest.json')).write_text(json.dumps(
        {'commit': sha, 'zip': zip_path.name, 'zip_bytes': zip_path.stat().st_size, 'zip_sha256': digest,
         'files': manifest}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'commit {sha}\nfiles  {len(manifest)}\nbytes  {zip_path.stat().st_size}\nsha256 {digest}\n{zip_path}')


if __name__ == '__main__':
    main()
