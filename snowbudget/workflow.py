from .basins import report
from .claims import declaration_view
from .field import cells
from .geometry import read_boxes
from .grid import regrid

DESCRIPTION = 'Reduce a synthetic equal-area SWE grid to independent grid and basin products.'


def materialize(doc):
    source = cells(doc['source_cells'])
    basins = read_boxes(doc['basins'])
    result = {'survey': doc['survey'], 'target_grid': list(regrid(source, read_boxes(doc['targets'], nonoverlap=True))),
              'basins': list(report(source, basins))}
    if 'declaration' in doc:
        result['declaration'] = declaration_view(source, basins, doc['declaration'])
    return result
