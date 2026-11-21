"""Target-grid product: evidenced area, water and coverage for each target box."""
from .common import scalar
from .evidence import evaluate
from .geometry import area


def _receipt(parts):
    return {
        'original_m2': scalar(parts.original_area),
        'patch_m2': scalar(parts.patch_area),
        'missing_m2': scalar(parts.missing_area),
        'disputed_m2': scalar(parts.disputed_area),
        'unlocated_m2': scalar(parts.unlocated_area),
        'outside_m2': scalar(parts.outside_area),
        'unused_patch_m2': scalar(parts.ignored_patch_area),
        'patches': sorted(parts.patch_evidence),
        'disputed_patches': sorted(parts.disputed_patches),
        'missing_cells': sorted(parts.missing_cells),
        'filled_cells': sorted(parts.filled_cells),
        'disputed_cells': sorted(parts.disputed_cells),
        'unlocated_cells': sorted(parts.unlocated_cells),
    }


def row(key, box, parts, detailed):
    measured = parts.valid_area
    water = parts.water
    result = {'id': key, 'box_m': [scalar(v) for v in box],
              'valid_m2': scalar(measured),
              'water_m3': scalar(water) if measured else None,
              'valid_mean_mm': scalar(water * 1000 / measured) if measured else None,
              'coverage': scalar(measured / area(box)),
              'source_cells': sorted(parts.cells)}
    if detailed:
        result.update(_receipt(parts))
    return result


def regrid(source, targets, patches=(), detailed=False):
    for key, box in targets.items():
        yield row(key, box, evaluate(source, patches, box), detailed)
