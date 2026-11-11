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
