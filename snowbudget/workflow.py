from .acceptance import retractions
from .basins import report
from .field import cells
from .geometry import read_boxes
from .grid import regrid

DESCRIPTION = 'Reduce a synthetic equal-area SWE grid to independent grid and basin products.'


def materialize(doc):
    source = cells(doc['source_cells'])
    withdrawn = retractions(doc.get('retractions') or [], source)
    return {'survey': doc['survey'],
            'target_grid': list(regrid(source, read_boxes(doc['targets'], nonoverlap=True), withdrawn)),
            'basins': list(report(source, read_boxes(doc['basins']), withdrawn))}
