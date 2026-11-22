"""Basin products: the historical reader, plus seam adjudication with footprint classes."""
from .acceptance import contributions, footprint, pieces, seamed, totals
from .common import scalar
from .field import intersect
from .geometry import area


def legacy(source, basins):
    """Original reading, unchanged for input with no seams and no retractions."""
    for key, box in basins.items():
        nominal = area(box)
        valid = missing = water = 0
        evidence, holes = [], []
        for cell, section in intersect(source, box):
            if cell.depth is None:
                missing += section
                holes.append(cell.id)
            else:
                valid += section
                water += section * cell.depth / 1000
                evidence.append({'cell': cell.id, 'm2': scalar(section), 'water_m3': scalar(section * cell.depth / 1000)})
        yield {'id': key, 'area_m2': scalar(nominal), 'valid_m2': scalar(valid), 'missing_in_grid_m2': scalar(missing),
               'outside_grid_m2': scalar(nominal - valid - missing), 'water_m3': scalar(water) if valid else None,
               'valid_mean_mm': scalar(water * 1000 / valid) if valid else None,
               'contributions': evidence, 'missing_cells': holes}


def _class(sums, name, water=False):
    row = {'m2': scalar(sums[name]['m2'])}
    if water:
        row['water_m3'] = scalar(sums[name]['water'])
    row['footprints'] = [footprint(piece) for piece in sums[name]['pieces']]
    return row


ALL_CLASSES = ('unique', 'replaceable', 'backfilled', 'resolved', 'contested', 'no_evidence', 'unsurveyed')


def _reason(sum_, reason):
    return sum((piece['m2'] for piece in sum_['no_evidence']['pieces'] if piece['reason'] == reason), 0)


def adjudicated(source, basins, withdrawn):
    for key, box in basins.items():
        atom = list(pieces(box, source, withdrawn))
        sums = totals(atom)
        classes = {name: _class(sums, name, water=name in ('unique', 'replaceable', 'backfilled', 'resolved'))
                   for name in ALL_CLASSES}
        yield {'id': key, 'area_m2': scalar(area(box)),
               'valid_m2': scalar(sums['valid']),
               'water_m3': scalar(sums['water']) if sums['accepted'] else None,
               'valid_mean_mm': scalar(sums['water'] * 1000 / sums['valid']) if sums['valid'] else None,
               'missing_in_grid_m2': scalar(_reason(sums, 'missing')),
               'retracted_m2': scalar(_reason(sums, 'retracted')),
               'conflict_m2': scalar(sums['contested']['m2']),
               'conflict_cells': sorted({cell for piece in sums['contested']['pieces'] for cell in piece['cells']}),
               'outside_grid_m2': scalar(sums['unsurveyed']['m2']),
               'contributions': contributions(atom),
               'missing_cells': sorted({cell for piece in sums['no_evidence']['pieces']
                                        if piece['reason'] == 'missing' for cell in piece['cells']}),
               'retracted_cells': sorted({cell for piece in sums['no_evidence']['pieces']
                                          if piece['reason'] == 'retracted' for cell in piece['cells']}),
               'classes': classes}


def report(source, basins, withdrawn=None):
    """Pick the historical reader when nothing overlaps and nothing is withdrawn."""
    return adjudicated(source, basins, withdrawn or {}) if seamed(source, withdrawn) else legacy(source, basins)
