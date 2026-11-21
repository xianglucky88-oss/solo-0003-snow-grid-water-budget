from .banding import banding
from .basins import report
from .elevation import effective_coverage, elevation_bands, elevation_schemes
from .field import cells
from .geometry import read_boxes
from .grid import regrid

DESCRIPTION = 'Reduce a synthetic equal-area SWE grid to independent grid and basin products.'

ELEVATION_KEYS = ('elevation_bands', 'elevation_schemes', 'coverage_choice')


def materialize(doc):
    source = cells(doc['source_cells'])
    basins = read_boxes(doc['basins'])
    result = {'survey': doc['survey'],
              'target_grid': list(regrid(source, read_boxes(doc['targets'], nonoverlap=True))),
              'basins': list(report(source, basins))}
    present = [key for key in ELEVATION_KEYS if key in doc]
    if present:
        missing = [key for key in ELEVATION_KEYS if key not in doc]
        if missing:
            raise ValueError(f'elevation inputs are incomplete, missing: {", ".join(missing)}')
        bands = elevation_bands(doc['elevation_bands'])
        schemes = elevation_schemes(doc['elevation_schemes'])
        primary = doc['coverage_choice']
        if not isinstance(primary, str) or not primary.strip():
            raise ValueError('coverage_choice must name one elevation scheme')
        coverage = effective_coverage(schemes, primary)
        result['elevation_banding'] = list(banding(source, basins, bands, coverage, primary))
    return result
