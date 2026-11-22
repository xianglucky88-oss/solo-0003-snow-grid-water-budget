"""Joint delivery feasibility for overlapping supply basin responsibility areas.

The same proven qualifying ground can be assigned to at most one responsibility
area. Axis-aligned boundary cuts are allowed, so each atomic ground piece is
splittable in any proportion. Feasibility is an exact-rational linear program:
each selected area must simultaneously reach its minimum qualifying area and
its minimum *complete* water volume. The layer above the depth threshold is
accounted separately and never mixed with the complete volume.
"""
from fractions import Fraction

from .common import number, require, scalar, unique
from .geometry import area, overlap

ZERO = Fraction(0)
ONE = Fraction(1)
PIVOT_LIMIT = 10000


def _parse(plan, basins):
    require(isinstance(plan, dict), 'supply_plan must be an object')
    threshold = number(plan['threshold_mm'], 'supply threshold')
    rows = plan['requirements']
    require(isinstance(rows, list) and rows, 'supply requirements must be a non-empty list')
    index = unique(rows, field='basin')
    needs = {}
    for key, row in index.items():
        require(key in basins, f'unknown supply basin: {key}')
        require(isinstance(row, dict), f'supply requirement must be an object: {key}')
        min_area = number(row['min_qualifying_m2'], 'minimum qualifying area')
        min_water = number(row['min_complete_m3'], 'minimum complete water')
        require(min_area >= 0 and min_water >= 0, f'supply minimums must be non-negative: {key}')
        needs[key] = (min_area, min_water)
    return threshold, needs


def _pieces(source, basin_boxes):
    """Axis-aligned atomic pieces: every basin edge is a cut line, then clip to cells."""
    xs = sorted({c for box in basin_boxes.values() for c in (box[0], box[2])})
    ys = sorted({c for box in basin_boxes.values() for c in (box[1], box[3])})
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            for cell_id, cell_box, depth in source:
                sx0, sy0 = max(x0, cell_box[0]), max(y0, cell_box[1])
                sx1, sy1 = min(x1, cell_box[2]), min(y1, cell_box[3])
                if sx0 < sx1 and sy0 < sy1:
                    shared = (sx0, sy0, sx1, sy1)
                    zones = [z for z, box in basin_boxes.items() if overlap(shared, box) > 0]
                    yield {'box': shared, 'area': area(shared), 'depth': depth,
                           'cell': cell_id, 'zones': zones}


def _simplex(raw, nvar):
    """Phase-I feasibility over Gx <= h. Exact rationals, Bland's rule.

    Returns a feasible basic solution vector for the nvar original columns,
    or None when the constraints are jointly infeasible.
    """
    m = len(raw)
    art_rows = [i for i, (_, rhs) in enumerate(raw) if rhs < 0]
    width = nvar + m + len(art_rows) + 1  # originals, slacks, artificials, RHS
    table = [[ZERO] * width for _ in range(m)]
    basis = [0] * m
    for i, (coeff, rhs) in enumerate(raw):
        sign = -1 if rhs < 0 else 1  # negative-RHS rows are flipped so the basic value starts non-negative
        for k, value in enumerate(coeff):
            table[i][k] = sign * value
        table[i][nvar + i] = Fraction(sign)  # the slack keeps its +1 on the unflipped <= equation
        table[i][-1] = sign * rhs
        if rhs < 0:
            art_col = nvar + m + art_rows.index(i)
            table[i][art_col] = ONE
            basis[i] = art_col
        else:
            basis[i] = nvar + i
    objective = [ZERO] * width
    for i in art_rows:
        for k in range(width):
            objective[k] += table[i][k]
    pivots = 0
    while objective[-1] > 0:
        # The stored objective row carries the negated reduced costs, so an
        # improving non-artificial column has a strictly positive coefficient.
        entering = next((k for k in range(nvar + m) if objective[k] > 0), None)
        if entering is None:
            return None
        pivot_row, best_ratio = None, None
        for i in range(m):
            coefficient = table[i][entering]
            if coefficient > 0:
                ratio = table[i][-1] / coefficient
                if best_ratio is None or ratio < best_ratio or \
                        (ratio == best_ratio and basis[i] < basis[pivot_row]):
                    pivot_row, best_ratio = i, ratio
        if pivot_row is None:
            return None
        pivots += 1
        require(pivots <= PIVOT_LIMIT, 'supply feasibility pivot limit exceeded')
        divisor = table[pivot_row][entering]
        table[pivot_row] = [value / divisor for value in table[pivot_row]]
        for i in range(m):
            if i != pivot_row and (factor := table[i][entering]):
                table[i] = [value - factor * pivot_value
                            for value, pivot_value in zip(table[i], table[pivot_row])]
        factor = objective[entering]
        objective = [value - factor * pivot_value
                     for value, pivot_value in zip(objective, table[pivot_row])]
        basis[pivot_row] = entering
    solution = [ZERO] * nvar
    for i, basic in enumerate(basis):
        if basic < nvar:
            solution[basic] = table[i][-1]
    return solution


def feasibility(source, basins, plan):
    threshold, needs = _parse(plan, basins)
    zones = sorted(needs)
    selected = {z: basins[z] for z in zones}
    pieces = list(_pieces(source, selected))
    qualifying = [p for p in pieces if p['depth'] is not None and p['depth'] >= threshold]

    columns = {}
    for j, piece in enumerate(qualifying):
        for z in zones:
            if z in piece['zones']:
                columns[(z, j)] = len(columns)

    raw = []
    for j, piece in enumerate(qualifying):  # one capacity row per atomic piece
        row = [ZERO] * len(columns)
        for z in zones:
            if (key := (z, j)) in columns:
                row[columns[key]] = ONE
        raw.append((row, piece['area']))
    for z in zones:  # minimum area and complete-water rows: Gx >= h  <=>  -Gx <= -h
        min_area, min_water = needs[z]
        area_row, water_row = [ZERO] * len(columns), [ZERO] * len(columns)
        for j, piece in enumerate(qualifying):
            key = (z, j)
            if key in columns:
                area_row[columns[key]] = -ONE
                water_row[columns[key]] = -piece['depth'] / 1000
        raw.append((area_row, -min_area))
        raw.append((water_row, -min_water))

    if columns:
        solution = _simplex(raw, len(columns))
        feasible = solution is not None
    else:  # only feasible when every selected zone asks for nothing
        solution = None
        feasible = all(min_area == 0 and min_water == 0 for min_area, min_water in needs.values())

    standalone = {z: [ZERO, ZERO, ZERO] for z in zones}
    for piece in qualifying:
        complete_rate = piece['depth'] / 1000
        excess_rate = (piece['depth'] - threshold) / 1000
        for z in piece['zones']:
            entry = standalone[z]
            entry[0] += piece['area']
            entry[1] += piece['area'] * complete_rate
            entry[2] += piece['area'] * excess_rate

    union_area = sum((piece['area'] for piece in qualifying), ZERO)
    union_complete = sum((piece['area'] * piece['depth'] / 1000 for piece in qualifying), ZERO)
    union_excess = sum((piece['area'] * (piece['depth'] - threshold) / 1000
                        for piece in qualifying), ZERO)

    if feasible:
        # Within each atomic piece, lay the zones' strips out left to right so
        # that two responsibility areas never draw the same ground twice.
        strip_map = {}
        for j, piece in enumerate(qualifying):
            x0, y0, x1, y1 = piece['box']
            strip_height = y1 - y0
            cursor = ZERO
            for z in zones:
                key = (z, j)
                if key not in columns or not (share := solution[columns[key]]):
                    continue
                start, end = cursor, cursor + share / strip_height
                strip_map[(z, j)] = (x0 + start, x0 + end)
                cursor = end

    zone_reports = []
    for z in zones:
        min_area, min_water = needs[z]
        alone_area, alone_complete, alone_excess = standalone[z]
        entry = {
            'id': z,
            'min_qualifying_m2': scalar(min_area),
            'min_complete_m3': scalar(min_water),
            'standalone_qualifying_m2': scalar(alone_area),
            'standalone_complete_m3': scalar(alone_complete),
            'standalone_excess_m3': scalar(alone_excess),
            'meets_minimums': alone_area >= min_area and alone_complete >= min_water,
        }
        if feasible:
            assigned_area = assigned_complete = assigned_excess = ZERO
            strips = []
            for j, piece in enumerate(qualifying):
                key = (z, j)
                if key not in columns or not (share := solution[columns[key]]):
                    continue
                x0, y0, x1, y1 = piece['box']
                strip_x0, strip_x1 = strip_map[key]
                assigned_area += share
                assigned_complete += share * piece['depth'] / 1000
                assigned_excess += share * (piece['depth'] - threshold) / 1000
                strips.append({
                    'piece_m': [scalar(v) for v in piece['box']],
                    'strip_m': [scalar(strip_x0), scalar(y0), scalar(strip_x1), scalar(y1)],
                    'cell': piece['cell'],
                    'depth_mm': scalar(piece['depth']),
                    'm2': scalar(share),
                    'water_m3': scalar(share * piece['depth'] / 1000),
                    'excess_m3': scalar(share * (piece['depth'] - threshold) / 1000),
                })
            entry.update({
                'assigned_m2': scalar(assigned_area),
                'assigned_complete_m3': scalar(assigned_complete),
                'assigned_excess_m3': scalar(assigned_excess),
                'witness': strips,
            })
        zone_reports.append(entry)

    return {
        'threshold_mm': scalar(threshold),
        'feasible': feasible,
        'zones': zone_reports,
        'totals': {
            'qualifying_union_m2': scalar(union_area),
            'complete_union_m3': scalar(union_complete),
            'excess_union_m3': scalar(union_excess),
            'required_m2': scalar(sum((needs[z][0] for z in zones), ZERO)),
            'required_complete_m3': scalar(sum((needs[z][1] for z in zones), ZERO)),
        },
    }
