from .field import cells
from .geometry import read_boxes
from .streaming import reduce_banded

DESCRIPTION = 'Reduce a synthetic equal-area SWE grid to independent grid and basin products.'


def materialize(doc):
    source = cells(doc['source_cells'])
    target_grid, basin_rows = reduce_banded(source, read_boxes(doc['targets'], nonoverlap=True),
                                            read_boxes(doc['basins']))
    return {'survey': doc['survey'], 'target_grid': target_grid, 'basins': basin_rows}
