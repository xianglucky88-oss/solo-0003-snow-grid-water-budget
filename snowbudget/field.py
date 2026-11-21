from .common import number, unique
from .geometry import overlap, read_boxes


def cells(rows):
    geometry = read_boxes(rows, nonoverlap=True)
    values = unique(rows)
    return [(key, box, None if (depth := values[key]['swe_mm']) is None else number(depth, 'SWE depth'))
            for key, box in geometry.items()]


def intersect(source, box):
    for key, extent, depth in source:
        if (shared := overlap(box, extent)) > 0:
            yield key, shared, depth
