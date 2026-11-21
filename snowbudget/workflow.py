from .basins import report
from .field import cells, extra_occlusion_rows, patch_rows
from .geometry import read_boxes
from .grid import regrid

DESCRIPTION = 'Reduce a synthetic equal-area SWE grid to independent grid and basin products.'


def materialize(doc):
    source = cells(doc['source_cells'], extra_occlusion_rows(doc))
    remeasured = patch_rows(doc, doc['source_cells'])
    # Old whole-cell bundles keep their exact byte-for-byte rows. The evidence
    # receipt appears only when this bundle actually carries occlusion or patch
    # evidence, so legacy editions and replays do not drift.
    detailed = bool(remeasured) or any(cell.occlusions or cell.occluded_fraction is not None for cell in source)
    return {'survey': doc['survey'],
            'target_grid': list(regrid(source, read_boxes(doc['targets'], nonoverlap=True), remeasured, detailed)),
            'basins': list(report(source, read_boxes(doc['basins']), remeasured, detailed))}
