from .common import scalar
from .field import intersect
from .geometry import area


def report(source, basins):
    for key, box in basins.items():
        nominal = area(box)
        valid = missing = water = 0
        evidence, holes = [], []
        for cell, section, depth in intersect(source, box):
            if depth is None:
                missing += section
                holes.append(cell)
            else:
                valid += section
                water += section * depth / 1000
                evidence.append({'cell': cell, 'm2': scalar(section), 'water_m3': scalar(section * depth / 1000)})
        yield {'id': key, 'area_m2': scalar(nominal), 'valid_m2': scalar(valid), 'missing_in_grid_m2': scalar(missing),
               'outside_grid_m2': scalar(nominal - valid - missing), 'water_m3': scalar(water) if valid else None,
               'valid_mean_mm': scalar(water * 1000 / valid) if valid else None,
               'contributions': evidence, 'missing_cells': holes}
