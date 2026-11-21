from .common import number, unique
from .geometry import overlap, read_boxes


def cells(rows):
    geometry = read_boxes(rows, nonoverlap=True)
    values = unique(rows)
    return [(key, box, None if (depth := values[key]['swe_mm']) is None else number(depth, 'SWE depth'))
            for key, box in geometry.items()]


def meet(source_cell, box):
    """Intersect one source cell with one target box; the shared reduction primitive."""
    key, extent, depth = source_cell
    shared = overlap(box, extent)
    return None if shared <= 0 else (key, shared, depth)


def intersect(source, box):
    for source_cell in source:
        if (hit := meet(source_cell, box)) is not None:
            yield hit
