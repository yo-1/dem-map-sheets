# QGIS-dependent test execution — Issue #31

## Scope

This runbook applies to DEM Map Sheets tests. It does not modify the product's DEM, mask, or NoData processing logic. A successful test discovery is **not** proof that QGIS-dependent tests ran.

## Runtime matrix

| Runtime | QGIS lifecycle | Allowed scope |
|---|---|---|
| Python without importable `qgis.core` | No QGIS initialization | QGIS-free tests; QGIS-dependent tests are skipped, not passed |
| Separate process with QGIS Python bindings | The shared fixture owns a newly created `QgsApplication` | Full suite only after verifying QGIS/Processing/GDAL are importable |
| Already-running QGIS Desktop / Dev Probe | Reuse `QgsApplication.instance()`; never initialize or exit it | Individual test module or focused test only |

## Commands and reporting

In a separate process with the correct QGIS environment:

```sh
python -c "from qgis.core import Qgis; from osgeo import gdal; import sys; print('QGIS', Qgis.QGIS_VERSION, 'GDAL', gdal.VersionInfo('--version'), 'Python', sys.version)"
python -m unittest discover -s dem_map_sheets/tests -p test_qgis_init_guard.py -v
python -m unittest discover -s dem_map_sheets/tests -p test_algorithm_failure_record.py -v
```

Run the full suite in a **separate process**, using the repository's documented test command and its required dependencies. Do not repeatedly reload the full suite inside QGIS Desktop.

Record: execution location (Desktop or separate process), full command, QGIS/GDAL/Python versions, test count, pass/fail/error/skip counts, and logs. The three `test_algorithm_failure_record` tests **must actually execute** before being counted as passing; `QGIS is not available` means unverified.

## Known limitations

- The AST guard checks direct `QgsApplication(...)`, `initQgis()`, and `exitQgis()` calls in test modules. Indirect and aliased calls are not necessarily detected.
- The shared fixture is not yet independently validated in Windows QGIS 3.44 Desktop and standalone processes.
- If `qgis.core` imports but Processing is unavailable, the focused test may error; do not reclassify it as a pass.
- A previously observed Desktop unresponsiveness after a later test-group reload has no established cause.
