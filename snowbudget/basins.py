from .common import scalar
from .field import meet
from .geometry import area


class BasinTarget:
    """Accumulates valid, in-grid-missing and out-of-grid area for one basin per band."""

    def __init__(self, key, box):
        self.id = key
        self.box = box
        self.nominal = area(box)
        self.valid = 0
        self.missing = 0
        self.water = 0
        self.evidence = {}
        self.holes = set()

    def accept(self, source_cell):
        hit = meet(source_cell, self.box)
        if hit is None:
            return
        cell, section, depth = hit
        if depth is None:
            self.missing += section
            self.holes.add(cell)
        else:
            volume = section * depth / 1000
            self.valid += section
            self.water += volume
            self.evidence[cell] = (section, volume)

    def finalize(self):
        evidence = [{'cell': cell, 'm2': scalar(self.evidence[cell][0]),
                     'water_m3': scalar(self.evidence[cell][1])} for cell in sorted(self.evidence)]
        return {'id': self.id, 'area_m2': scalar(self.nominal), 'valid_m2': scalar(self.valid),
                'missing_in_grid_m2': scalar(self.missing),
                'outside_grid_m2': scalar(self.nominal - self.valid - self.missing),
                'water_m3': scalar(self.water) if self.valid else None,
                'valid_mean_mm': scalar(self.water * 1000 / self.valid) if self.valid else None,
                'contributions': evidence, 'missing_cells': sorted(self.holes)}


def report(source, basins):
    """Legacy whole-source contract, kept as an exact accumulator-based wrapper."""
    accumulators = [BasinTarget(key, box) for key, box in basins.items()]
    for source_cell in source:
        for basin in accumulators:
            basin.accept(source_cell)
    for basin in accumulators:
        yield basin.finalize()
