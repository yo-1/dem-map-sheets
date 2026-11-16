"""Best-effort, non-judgmental provenance for DEM Map Sheets runs.

Hash original files and completed output files in bounded memory.
No QA pass/fail decision is made here.
"""
import hashlib
import os
from pathlib import Path

from ..version import VERSION


def _file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(path, root=None, kind="file", complete=True):
    path = Path(path)
    if root is not None:
        identifier = path.relative_to(root).as_posix()
    else:
        # Do not serialize absolute paths, URL query parameters or credentials.
        identifier = path.name
    record = {"path": identifier, "kind": kind, "complete": complete,
              "bytes": None, "sha256": None, "hash_status": "unavailable"}
    try:
        if not path.is_file():
            record["hash_reason"] = "directory_not_hashed" if path.is_dir() else "not_a_local_file"
            return record
        record["bytes"] = path.stat().st_size
        record["sha256"] = _file_hash(path)
        record["hash_status"] = "ok"
    except (OSError, ValueError) as exc:
        record["hash_reason"] = type(exc).__name__
    return record


def _environment(gdal=None, osr=None):
    env = {"plugin_version": VERSION,
           "git_commit": None, "qgis_version": None,
           "gdal_version": None, "pdal_version": None, "proj_version": None}
    unavailable = {}
    for key in ("git_commit", "qgis_version", "pdal_version"):
        unavailable[key] = "not_available_in_runtime"
    try:
        if gdal is not None:
            env["gdal_version"] = gdal.VersionInfo("--version")
    except Exception:
        unavailable["gdal_version"] = "version_lookup_failed"
    if env["gdal_version"] is None:
        unavailable.setdefault("gdal_version", "not_available_in_runtime")
    try:
        if osr is not None:
            env["proj_version"] = ".".join(str(x) for x in
                (osr.GetPROJVersionMajor(), osr.GetPROJVersionMinor(), osr.GetPROJVersionMicro()))
    except Exception:
        unavailable["proj_version"] = "version_lookup_failed"
    if env["proj_version"] is None:
        unavailable.setdefault("proj_version", "not_available_in_runtime")
    try:
        from qgis.core import Qgis
        env["qgis_version"] = Qgis.QGIS_VERSION
        unavailable.pop("qgis_version", None)
    except (ImportError, AttributeError):
        pass
    return {"versions": env, "unavailable": unavailable}


def build_provenance(inputs, output_dir, gdal=None, osr=None, complete=True):
    """Rebuild output inventory after TerrainRGB generation; omit work and run.json."""
    root = Path(output_dir)
    if isinstance(inputs, (str, os.PathLike)):
        inputs = [inputs]
    originals = []
    for source in inputs or []:
        if isinstance(source, (str, os.PathLike)):
            source = str(source)
            # URI/query components must never appear in the manifest.
            if "://" in source or "?" in source:
                originals.append({"path": "remote_or_virtual_input", "kind": "input",
                    "complete": False, "bytes": None, "sha256": None,
                    "hash_status": "unavailable", "hash_reason": "nonlocal_or_query_uri"})
            else:
                originals.append(_entry(source, kind="input"))
    outputs = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if rel.parts[0] == "work" or rel.as_posix() == "run.json":
                continue
            kind = ("terrainrgb" if "03_terrainrgb_xyz" in rel.parts else
                    "merged_dem" if rel.name == "02_merged_dem.tif" else
                    "source_dem" if "01_source_dem" in rel.parts else "other")
            outputs.append(_entry(p, root=root, kind=kind, complete=complete))
    return {"inputs": originals, "outputs": outputs,
            "environment": _environment(gdal, osr)}


def provenance_warnings(provenance):
    if any(e["hash_status"] != "ok" for key in ("inputs", "outputs")
           for e in provenance[key]):
        return ["provenance_hash_unavailable"]
    return []
