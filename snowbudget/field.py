"""Source tiles: geometry, exact SWE depth and acceptance evidence grade."""
from collections import namedtuple

from .common import integer, number, unique
from .geometry import overlap, read_boxes

Cell = namedtuple('Cell', 'id box depth evidence')


def _evidence(row):
    return None if row.get('evidence') is None else integer(row['evidence'], 'evidence', minimum=0)


def cells(rows):
    """Read source tiles, allowing seams only where every tile grades its evidence.

    A seam without explicit grades on both sides is rejected exactly as before,
    so overlap-free input keeps its historical reading.
    """
    geometry = read_boxes(rows)
    values = unique(rows)
    source = [Cell(key, box, None if (depth := values[key]['swe_mm']) is None else number(depth, 'SWE depth'),
                   _evidence(values[key]))
              for key, box in geometry.items()]
    seen = {}
    for cell in source:
        early = [other for other in seen.values() if overlap(other.box, cell.box) > 0]
        if any(other.evidence is None for other in early) or (early and cell.evidence is None):
            raise ValueError(f'overlapping grid cell: {cell.id}')
        seen[cell.id] = cell
    return source


def intersect(source, box):
    for cell in source:
        if (shared := overlap(box, cell.box)) > 0:
            yield cell, shared
