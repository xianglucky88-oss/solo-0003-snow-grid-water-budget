"""Acceptance table for overlapping tiles of one time step.

Every region is split on all cell and retraction edges, so each atomic
footprint has one constant answer.  A retraction removes only the withdrawn
part of one tile; within a footprint the strongest remaining evidence grade
wins.  Equal grades with differing depths are a conflict and are never
averaged, while a retracted tile lets the next evidence grade fill in.

Footprints partition a region into exactly one class:
``unique``, ``replaceable``, ``backfilled``, ``resolved``, ``contested`` or
``no_evidence`` (``unsurveyed`` for area beyond every tile).
"""
from fractions import Fraction

from .common import number, require, scalar, unique
from .geometry import area, overlap


def _cuts(lo, hi, edges):
    return sorted({lo, hi, *(value for value in edges if lo <= value <= hi)})


def _withdraws(cell, atom, withdrawn):
    return any(box[0] <= atom[0] and box[1] <= atom[1] and atom[2] <= box[2] and atom[3] <= box[3]
               for box in withdrawn.get(cell.id, ()))


def _grade(cell):
    return -1 if cell.evidence is None else cell.evidence


def _piece(atom, status, **extra):
    return {'box': atom, 'm2': area(atom), 'status': status, **extra}


def _decide(atom, candidates, withdrawn):
    covering = [cell for cell in candidates if overlap(cell.box, atom) > 0]
    if not covering:
        return _piece(atom, 'no_evidence', reason='outside_grid', cells=[])
    removed = [cell for cell in covering if _withdraws(cell, atom, withdrawn)]
    valid = [cell for cell in covering if cell not in removed and cell.depth is not None]
    if not valid:
        unmeasured = any(cell.depth is not None for cell in removed)
        return _piece(atom, 'no_evidence', reason='retracted' if unmeasured else 'missing',
                      cells=sorted(cell.id for cell in covering))
    strongest = max(_grade(cell) for cell in valid)
    rivals = [cell for cell in valid if _grade(cell) == strongest]
    if len({cell.depth for cell in rivals}) > 1:
        ordered = sorted(rivals, key=lambda cell: cell.id)
        return _piece(atom, 'conflict', evidence=strongest, cells=[cell.id for cell in ordered],
                      values_mm=[cell.depth for cell in ordered])
    winner = sorted(rivals, key=lambda cell: cell.id)[0]
    withdrawn_valid = [cell for cell in removed if cell.depth is not None]
    return _piece(atom, 'accepted', cell=winner.id, evidence=winner.evidence, swe_mm=winner.depth,
                  water=winner.depth * area(atom) / 1000,
                  alternatives=sorted(cell.id for cell in valid if cell.id != winner.id),
                  withdrawn_cells=sorted(cell.id for cell in withdrawn_valid),
                  backfilled=any(_grade(cell) > strongest for cell in withdrawn_valid),
                  resolved=any(_grade(cell) == strongest and cell.depth != winner.depth
                               for cell in withdrawn_valid))


def retractions(rows, source):
    """Read local withdrawals as cell key -> withdrawn rectangle; a missing box withdraws the whole tile."""
    require(isinstance(rows, list), 'retractions must be a list')
    known = {cell.id: cell for cell in source}
    withdrawn = {}
    for key, row in sorted(unique(rows).items()):
        require(key in known, f'unknown retracted cell: {key}')
        if row.get('box_m') is None:
            withdrawn[key] = (known[key].box,)
            continue
        box = tuple(number(value, 'coordinate', minimum=None) for value in row['box_m'])
        require(len(box) == 4 and box[0] < box[2] and box[1] < box[3], f'invalid rectangle: {key}')
        require(box[0] >= known[key].box[0] and box[1] >= known[key].box[1]
                and box[2] <= known[key].box[2] and box[3] <= known[key].box[3],
                f'retraction outside cell: {key}')
        withdrawn[key] = (box,)
    return withdrawn


def seamed(source, withdrawn):
    """True once any seam or retraction needs the acceptance table."""
    return bool(withdrawn) or any(overlap(a.box, b.box) > 0
                                  for index, a in enumerate(source) for b in source[index + 1:])


def pieces(region, source, withdrawn):
    """Yield the adjudicated atomic footprints that partition ``region``."""
    candidates = [cell for cell in source if overlap(cell.box, region) > 0]
    edges = [box for cell in candidates for box in (cell.box,)]
    for boxes in withdrawn.values():
        edges.extend(boxes)
    xs = _cuts(region[0], region[2], [box[0] for box in edges] + [box[2] for box in edges])
    ys = _cuts(region[1], region[3], [box[1] for box in edges] + [box[3] for box in edges])
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            if x0 < x1 and y0 < y1:
                yield _decide((x0, y0, x1, y1), candidates, withdrawn)


def classify(piece):
    """Exactly one class per atomic footprint."""
    if piece['status'] == 'conflict':
        return 'contested'
    if piece['status'] == 'no_evidence':
        return 'unsurveyed' if piece['reason'] == 'outside_grid' else 'no_evidence'
    if piece['backfilled']:
        return 'backfilled'
    if piece['resolved']:
        return 'resolved'
    return 'replaceable' if piece['alternatives'] else 'unique'


def footprint(piece):
    """One atomic footprint with its geometry and adjudication."""
    row = {'box_m': [scalar(value) for value in piece['box']], 'm2': scalar(piece['m2']),
           'status': piece['status'], 'class': classify(piece)}
    if piece['status'] == 'accepted':
        row.update(cell=piece['cell'], evidence=piece['evidence'], swe_mm=scalar(piece['swe_mm']),
                   water_m3=scalar(piece['water']), alternative_cells=piece['alternatives'],
                   withdrawn_cells=piece['withdrawn_cells'])
    elif piece['status'] == 'conflict':
        row.update(cells=piece['cells'], values_mm=[scalar(value) for value in piece['values_mm']])
    else:
        row.update(cells=piece['cells'], reason=piece['reason'])
    return row


def contributions(atom):
    """Merge atomic footprints into one row per accepted cell, keeping exact sums."""
    merged = {}
    for piece in atom:
        if piece['status'] != 'accepted':
            continue
        row = merged.setdefault(piece['cell'], {'m2': Fraction(0), 'water': Fraction(0),
                                                'evidence': piece['evidence'],
                                                'alternative_cells': set(), 'withdrawn_cells': set(),
                                                'classes': set()})
        row['m2'] += piece['m2']
        row['water'] += piece['water']
        row['alternative_cells'].update(piece['alternatives'])
        row['withdrawn_cells'].update(piece['withdrawn_cells'])
        row['classes'].add(classify(piece))
    return [{'cell': key, 'm2': scalar(row['m2']), 'water_m3': scalar(row['water']),
             'evidence': row['evidence'], 'classes': sorted(row['classes']),
             'alternative_cells': sorted(row['alternative_cells'] - {key}),
             'withdrawn_cells': sorted(row['withdrawn_cells'] - {key})}
            for key, row in sorted(merged.items())]


def totals(atom):
    """Exact sums over one region's atomic footprints, grouped by class."""
    sums = {name: {'m2': Fraction(0), 'water': Fraction(0), 'pieces': []}
            for name in ('unique', 'replaceable', 'backfilled', 'resolved', 'contested', 'no_evidence',
                         'unsurveyed')}
    for piece in atom:
        entry = sums[classify(piece)]
        entry['m2'] += piece['m2']
        if piece['status'] == 'accepted':
            entry['water'] += piece['water']
        entry['pieces'].append(piece)
    sums['valid'] = sum((entry['m2'] for name, entry in sums.items()
                         if name in ('unique', 'replaceable', 'backfilled', 'resolved')), Fraction(0))
    sums['water'] = sum((entry['water'] for name, entry in sums.items()
                         if name in ('unique', 'replaceable', 'backfilled', 'resolved')), Fraction(0))
    sums['accepted'] = [piece for name in ('unique', 'replaceable', 'backfilled', 'resolved')
                        for piece in sums[name]['pieces']]
    return sums
