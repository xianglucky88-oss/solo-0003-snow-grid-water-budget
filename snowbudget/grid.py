from .common import scalar
from .field import intersect
from .geometry import area


def regrid(source, targets):
    for key, box in targets.items():
        known = [(cell, section, section * depth / 1000) for cell, section, depth in intersect(source, box)
                 if depth is not None]
        measured = sum(section for _, section, _ in known)
        water = sum(volume for _, _, volume in known)
        yield {'id': key, 'box_m': [scalar(v) for v in box], 'valid_m2': scalar(measured),
               'water_m3': scalar(water) if known else None,
               'valid_mean_mm': scalar(water * 1000 / measured) if known else None,
               'coverage': scalar(measured / area(box)), 'source_cells': [cell for cell, _, _ in known]}
