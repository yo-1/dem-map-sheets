"""Canonical NoData constants shared by every engine module. GPL-3.0-only.

NODATA is the internal sentinel carried by intermediate rasters
(the LEM/GSI/text/PDAL conversions and their aligned products). It
is deliberately distinct from any value a deliverable may carry:
the NoData value of final outputs is the pipeline-level
``output_nodata`` config key (see input_sources.validate_input),
whose default is DEFAULT_OUTPUT_NODATA. Normalization and
materialization convert the internal sentinel - and whatever
missing representation an input declares - to that output value.

This module is a leaf on purpose: input_sources imports forest_dem
and gsi_dem, so those modules cannot take the canonical value from
input_sources without an import cycle.
"""

NODATA = -999999.0
DEFAULT_OUTPUT_NODATA = -9999.0


def nodata_window(value):
    """(float32 value, lo, hi): every double GDAL will read back as that NoData value.

    Output rasters are Float32 and GDAL's NoData comparison is not exact: it
    treats a cell within about 4*FLT_EPSILON*|value| of the NoData value as
    NoData (measured on GDAL 3.8.4 with BuildVRT srcNodata / mask bands:
    -9999 +-4 float32 steps, -32768 +-7, -999999 +-7; the Windows run on
    GDAL 3.13.3 lost exactly the values within 4 steps of -9999).  A valid
    value inside that window would come back missing downstream although it
    is not exactly equal, so every NoData collision check (points, raster
    inputs, materialized outputs) uses this one window.  It spans
    5*FLT_EPSILON*|value| (a margin over GDAL's rule) and never less than the
    float32 rounding step, which also keeps point checks independent of LAS
    scale/offset rounding.  tests/test_nodata_window.py measures the rule on
    the installed GDAL, so a GDAL version that widens its tolerance fails CI
    instead of silently re-opening the hole.
    """
    import numpy as np
    v=np.float32(value)
    eps=float(np.finfo(np.float32).eps)
    half_step=(float(np.nextafter(v,np.float32(np.inf)))-float(v))/2
    tol=max(5*eps*abs(float(v)),half_step)
    return float(v),float(v)-tol,float(v)+tol
