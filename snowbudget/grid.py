from .common import scalar
from .field import meet
from .geometry import area


class GridTarget:
    """Accumulates measured overlap for one target cell across a no-rewind band stream."""

    def __init__(self, key, box):
        self.id = key
        self.box = box
        self.measured = 0
        self.water = 0
        self.known = []

    def accept(self, source_cell):
        hit = meet(source_cell, self.box)
        if hit is None:
            return
        cell, section, depth = hit
        if depth is None:
            # In-grid missing cells contribute no measured area; they are not
            # separable from out-of-grid area in the grid contract.
            return
        self.known.append(cell)
        self.measured += section
        self.water += section * depth / 1000

    def finalize(self):
        known = sorted(self.known)
        return {'id': self.id, 'box_m': [scalar(v) for v in self.box],
                'valid_m2': scalar(self.measured),
                'water_m3': scalar(self.water) if known else None,
                'valid_mean_mm': scalar(self.water * 1000 / self.measured) if known else None,
                'coverage': scalar(self.measured / area(self.box)), 'source_cells': known}


def regrid(source, targets):
    """Legacy whole-source contract, kept as an exact accumulator-based wrapper."""
    accumulators = [GridTarget(key, box) for key, box in targets.items()]
    for source_cell in source:
        for target in accumulators:
            target.accept(source_cell)
    for target in accumulators:
        yield target.finalize()
