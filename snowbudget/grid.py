"""Grid products: the historical reader, plus seam adjudication for graded tiles."""
from .acceptance import contributions, footprint, pieces, seamed, totals
from .common import scalar
from .field import intersect
from .geometry import area


def legacy(source, targets):
    """Original reading, unchanged for input with no seams and no retractions."""
    for key, box in targets.items():
        known = [(cell.id, section, cell.depth) for cell, section in intersect(source, box) if cell.depth is not None]
        measured = sum(section for _, section, _ in known)
        water = sum(section * depth / 1000 for _, section, depth in known)
        yield {'id': key, 'box_m': [scalar(v) for v in box], 'valid_m2': scalar(measured),
               'water_m3': scalar(water) if known else None,
               'valid_mean_mm': scalar(water * 1000 / measured) if known else None,
               'coverage': scalar(measured / area(box)), 'source_cells': [cell for cell, _, _ in known]}


def adjudicated(source, targets, withdrawn):
    for key, box in targets.items():
        atom = list(pieces(box, source, withdrawn))
        sums = totals(atom)
        yield {'id': key, 'box_m': [scalar(v) for v in box], 'valid_m2': scalar(sums['valid']),
               'water_m3': scalar(sums['water']) if sums['accepted'] else None,
               'valid_mean_mm': scalar(sums['water'] * 1000 / sums['valid']) if sums['valid'] else None,
               'coverage': scalar(sums['valid'] / area(box)),
               'conflict_m2': scalar(sums['contested']['m2']),
               'conflict_cells': sorted({cell for piece in sums['contested']['pieces'] for cell in piece['cells']}),
               'replaceable_m2': scalar(sums['replaceable']['m2']),
               'substituted_m2': scalar(sums['backfilled']['m2'] + sums['resolved']['m2']),
               'missing_m2': scalar(sum((piece['m2'] for piece in sums['no_evidence']['pieces']
                                         if piece['reason'] == 'missing'), 0)),
               'retracted_m2': scalar(sum((piece['m2'] for piece in sums['no_evidence']['pieces']
                                           if piece['reason'] == 'retracted'), 0)),
               'outside_m2': scalar(sums['unsurveyed']['m2']),
               'source_cells': sorted({piece['cell'] for piece in sums['accepted']}),
               'contributions': contributions(atom),
               'footprints': [footprint(piece) for piece in atom]}


def regrid(source, targets, withdrawn=None):
    """Pick the historical reader when nothing overlaps and nothing is withdrawn."""
    return adjudicated(source, targets, withdrawn or {}) if seamed(source, withdrawn) else legacy(source, targets)
