"""Basin product: the same evidence buckets, computed independently of any target grid."""
from .common import scalar
from .evidence import evaluate
from .geometry import area


def _receipt(parts):
    return {
        'original_m2': scalar(parts.original_area),
        'patch_m2': scalar(parts.patch_area),
        'missing_in_grid_m2': scalar(parts.missing_area),
        'disputed_m2': scalar(parts.disputed_area),
        'unlocated_m2': scalar(parts.unlocated_area),
        'outside_grid_m2': scalar(parts.outside_area),
        'unused_patch_m2': scalar(parts.ignored_patch_area),
        'patch_contributions': [{'patch': key, 'm2': scalar(m2), 'water_m3': scalar(water)}
                                for key, (m2, water) in sorted(parts.patch_evidence.items())],
        'disputed_patches': sorted(parts.disputed_patches),
        'filled_cells': sorted(parts.filled_cells),
        'disputed_cells': sorted(parts.disputed_cells),
        'unlocated_cells': sorted(parts.unlocated_cells),
    }


def row(key, box, parts, detailed):
    nominal = area(box)
    measured = parts.valid_area
    water = parts.water
    result = {'id': key, 'area_m2': scalar(nominal), 'valid_m2': scalar(measured),
              'missing_in_grid_m2': scalar(parts.missing_area),
              'outside_grid_m2': scalar(nominal - measured - parts.missing_area
                                        - parts.disputed_area - parts.unlocated_area),
              'water_m3': scalar(water) if measured else None,
              'valid_mean_mm': scalar(water * 1000 / measured) if measured else None,
              'contributions': [{'cell': cell, 'm2': scalar(m2), 'water_m3': scalar(volume)}
                                for cell, (m2, volume) in sorted(parts.cells.items())],
              'missing_cells': sorted(parts.missing_cells)}
    if detailed:
        result.update(_receipt(parts))
    return result


def report(source, basins, patches=(), detailed=False):
    for key, box in basins.items():
        yield row(key, box, evaluate(source, patches, box), detailed)
