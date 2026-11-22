from .basins import report
from .field import cells
from .geometry import read_boxes
from .grid import regrid
from .supply import feasibility

DESCRIPTION = 'Reduce a synthetic equal-area SWE grid to independent grid and basin products.'


def materialize(doc):
    source = cells(doc['source_cells'])
    basin_boxes = read_boxes(doc['basins'])
    result = {'survey': doc['survey'],
              'target_grid': list(regrid(source, read_boxes(doc['targets'], nonoverlap=True))),
              'basins': list(report(source, basin_boxes))}
    if 'supply_plan' in doc:
        result['supply_feasibility'] = feasibility(source, basin_boxes, doc['supply_plan'])
    return result
