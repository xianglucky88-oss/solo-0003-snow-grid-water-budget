"""Hand-derived acceptance oracle for the small handgrid fixture.

Nothing here imports or calls the production reducer.  Every expected
number below is the result of hand arithmetic on the fixture geometry:

    source: a=[0,1]x[0,2] 100mm, b=[1,2]x[0,2] 0mm (measured zero),
            f1=[2,3]x[0,2] 300mm, f2=[3,4]x[0,2] 300mm (same-value
            fragments of one 4 m2 / 300 mm cell), m=[4,5]x[0,2] null.

The checks deliberately keep three things apart:

* per-cell ledger errors (one cell low, another high, totals unchanged),
* area-normalisation errors (mean/coverage divided by the wrong area),
* treating overlapping basin totals as the whole-region total.

They also reconcile every product against the archived source depths and
against that product's own area basis: a target uses its box area for
coverage, a basin uses nominal = valid + missing + outside.
"""
import json
from fractions import Fraction
from pathlib import Path

Q = Fraction
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'handgrid.json'


def F(value):
    """Exact value from JSON ints/rational strings or hand constants; None stays None."""
    if value is None:
        return None
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f'not an exact JSON number: {value!r}')
    return Fraction(value)


def box_area(box):
    return (F(box[2]) - F(box[0])) * (F(box[3]) - F(box[1]))


def overlap(a, b):
    dx = min(F(a[2]), F(b[2])) - max(F(a[0]), F(b[0]))
    dy = min(F(a[3]), F(b[3])) - max(F(a[1]), F(b[1]))
    return dx * dy if dx > 0 and dy > 0 else Q(0)


CELLS = {
    'a': {'box': (0, 0, 1, 2), 'swe': Q(100)},
    'b': {'box': (1, 0, 2, 2), 'swe': Q(0)},
    'f1': {'box': (2, 0, 3, 2), 'swe': Q(300)},
    'f2': {'box': (3, 0, 4, 2), 'swe': Q(300)},
    'm': {'box': (4, 0, 5, 2), 'swe': None},
}

# entries are (cell, contributing area m2, volume m3)
BASINS = {
    'domain': {
        'box': (0, 0, 6, 2), 'valid': Q(8), 'missing': Q(2), 'outside': Q(2),
        'water': Q(7, 5), 'mean': Q(175), 'holes': ['m'],
        'entries': [('a', Q(2), Q(1, 5)), ('b', Q(2), Q(0)),
                    ('f1', Q(2), Q(3, 5)), ('f2', Q(2), Q(3, 5))],
    },
    'dry': {
        'box': (1, 0, 2, 2), 'valid': Q(2), 'missing': Q(0), 'outside': Q(0),
        'water': Q(0), 'mean': Q(0), 'holes': [],
        'entries': [('b', Q(2), Q(0))],
    },
    'frags': {
        'box': (2, 0, 4, 2), 'valid': Q(4), 'missing': Q(0), 'outside': Q(0),
        'water': Q(6, 5), 'mean': Q(300), 'holes': [],
        'entries': [('f1', Q(2), Q(3, 5)), ('f2', Q(2), Q(3, 5))],
    },
    'miss': {
        'box': (4, 0, 5, 2), 'valid': Q(0), 'missing': Q(2), 'outside': Q(0),
        'water': None, 'mean': None, 'holes': ['m'], 'entries': [],
    },
    'shed-left': {
        'box': (0, 0, 3, 2), 'valid': Q(6), 'missing': Q(0), 'outside': Q(0),
        'water': Q(4, 5), 'mean': Q(400, 3), 'holes': [],
        'entries': [('a', Q(2), Q(1, 5)), ('b', Q(2), Q(0)),
                    ('f1', Q(2), Q(3, 5))],
    },
    'shed-right': {
        'box': (2, 0, 5, 2), 'valid': Q(4), 'missing': Q(2), 'outside': Q(0),
        'water': Q(6, 5), 'mean': Q(300), 'holes': ['m'],
        'entries': [('f1', Q(2), Q(3, 5)), ('f2', Q(2), Q(3, 5))],
    },
    'west': {
        'box': (0, 0, 2, 2), 'valid': Q(4), 'missing': Q(0), 'outside': Q(0),
        'water': Q(1, 5), 'mean': Q(50), 'holes': [],
        'entries': [('a', Q(2), Q(1, 5)), ('b', Q(2), Q(0))],
    },
}

TARGETS = {
    'frag-mix': {'box': (2, 0, 5, 2), 'valid': Q(4), 'water': Q(6, 5),
                 'mean': Q(300), 'coverage': Q(2, 3), 'sources': ['f1', 'f2']},
    'outside': {'box': (5, 0, 6, 2), 'valid': Q(0), 'water': None, 'mean': None,
                'coverage': Q(0), 'sources': []},
    'west': {'box': (0, 0, 2, 2), 'valid': Q(4), 'water': Q(1, 5), 'mean': Q(50),
             'coverage': Q(1), 'sources': ['a', 'b']},
}


def source_ledger(box, source):
    """Independent geometry ledger: what the archived source says about a box."""
    box = tuple(F(v) for v in box)
    total = box_area(box)
    valid = missing = water = Q(0)
    entries, holes = [], []
    for cell in source.get('source_cells', []):
        section = overlap(box, cell['box_m'])
        if section <= 0:
            continue
        if cell['swe_mm'] is None:
            missing += section
            holes.append(cell['id'])
        else:
            depth = F(cell['swe_mm'])
            volume = section * depth / 1000
            valid += section
            water += volume
            entries.append((cell['id'], section, volume))
    return {'area': total, 'valid': valid, 'missing': missing,
            'outside': total - valid - missing, 'water': water,
            'entries': sorted(entries), 'holes': sorted(holes)}


class _Report:
    def __init__(self):
        self.failures = []

    def fail(self, category, product, ident, message):
        self.failures.append({'category': category, 'product': product,
                              'id': ident, 'message': message})


def _index(report, rows, expected, product):
    if not isinstance(rows, list):
        report.fail('product_shape', product, None, 'product is not a list')
        return {}
    index = {}
    for row in rows:
        if not isinstance(row, dict):
            report.fail('product_shape', product, None, 'row is not an object')
            continue
        ident = row.get('id')
        if not isinstance(ident, str):
            report.fail('product_shape', product, None, f'invalid row id: {ident!r}')
            continue
        if ident in index:
            report.fail('product_shape', product, ident, 'duplicate id')
        index[ident] = row
    for ident in expected:
        if ident not in index:
            report.fail('product_shape', product, ident, 'missing row')
    for ident in index:
        if ident not in expected:
            report.fail('product_shape', product, ident, 'unexpected row')
    return index


def _entries(row):
    try:
        return sorted((str(entry['cell']), F(entry['m2']), F(entry['water_m3']))
                      for entry in row['contributions'])
    except (KeyError, TypeError):
        return None


def _sorted_keys(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return sorted(value)


def _check_source(report, source):
    if not isinstance(source, dict):
        report.fail('source_provenance', 'source', None, 'source is not an object')
        return
    if source.get('survey') != 'handgrid':
        report.fail('source_provenance', 'source', None, 'unexpected survey name')
    rows = source.get('source_cells')
    if not isinstance(rows, list):
        report.fail('source_provenance', 'source', None, 'source_cells is not a list')
        return
    actual = {}
    for cell in rows:
        try:
            ident = cell['id']
            if not isinstance(ident, str) or ident in actual:
                raise KeyError('id')
            actual[ident] = (tuple(F(v) for v in cell['box_m']), F(cell['swe_mm']))
        except (KeyError, TypeError):
            report.fail('source_provenance', 'source',
                        cell.get('id') if isinstance(cell, dict) else None, 'malformed cell')
            return
    expected = {key: (cell['box'], cell['swe']) for key, cell in CELLS.items()}
    if actual != expected:
        report.fail('source_provenance', 'source', None,
                    'archived source differs from the hand fixture')


def _check_basin(report, row, expected, source):
    ident = row['id']
    ledger = source_ledger(expected['box'], source)
    for field in ('valid', 'missing', 'outside'):
        if expected[field] != ledger[field]:
            report.fail('fixture', 'basin', ident,
                        f'hand {field} {expected[field]} disagrees with geometry {ledger[field]}')
    if expected['valid'] == 0:
        if expected['water'] is not None or expected['mean'] is not None:
            report.fail('fixture', 'basin', ident, 'hand row has water without valid area')
    elif expected['water'] != ledger['water']:
        report.fail('fixture', 'basin', ident,
                    f'hand water {expected["water"]} disagrees with geometry {ledger["water"]}')
    if expected['entries'] != ledger['entries'] or expected['holes'] != ledger['holes']:
        report.fail('fixture', 'basin', ident,
                    'hand ledger disagrees with the archived source geometry')
    area = F(row.get('area_m2'))
    valid = F(row.get('valid_m2'))
    missing = F(row.get('missing_in_grid_m2'))
    outside = F(row.get('outside_grid_m2'))

    if area != ledger['area']:
        report.fail('aggregate_conservation', 'basin', ident,
                    f'area_m2 {row.get("area_m2")!r} != nominal {ledger["area"]}')
    if None in (area, valid, missing, outside) or valid + missing + outside != area:
        report.fail('aggregate_conservation', 'basin', ident,
                    'valid + missing + outside does not equal area')
    if valid != ledger['valid']:
        report.fail('aggregate_conservation', 'basin', ident,
                    f'valid_m2 {row.get("valid_m2")!r} != source geometry {ledger["valid"]}')
    if missing != ledger['missing']:
        report.fail('aggregate_conservation', 'basin', ident,
                    f'missing_in_grid_m2 != source geometry {ledger["missing"]}')
    if outside != ledger['outside']:
        report.fail('aggregate_conservation', 'basin', ident,
                    f'outside_grid_m2 != source geometry {ledger["outside"]}')

    entries = _entries(row)
    if entries is None:
        report.fail('cell_ledger', 'basin', ident, 'malformed contributions')
    else:
        if entries != sorted(expected['entries']):
            report.fail('cell_ledger', 'basin', ident,
                        f'per-cell ledger {entries} != hand ledger {sorted(expected["entries"])}')
        if valid is not None and sum(m2 for _, m2, _ in entries) != valid:
            report.fail('aggregate_conservation', 'basin', ident,
                        'contribution areas do not sum to valid_m2')

    holes = _sorted_keys(row.get('missing_cells'))
    if holes != expected['holes']:
        report.fail('cell_ledger', 'basin', ident,
                    f'missing_cells {holes!r} != {expected["holes"]!r}')

    water = F(row.get('water_m3'))
    if water is None:
        if ledger['valid'] != 0 or ledger['water'] != 0:
            report.fail('water_total', 'basin', ident,
                        'water_m3 is null but the source geometry has measured water')
    else:
        if water != ledger['water']:
            report.fail('water_total', 'basin', ident,
                        f'water_m3 {row.get("water_m3")!r} != source geometry {ledger["water"]}')
        if entries is not None and sum(v for _, _, v in entries) != water:
            report.fail('water_total', 'basin', ident,
                        'contribution volumes do not sum to water_m3')

    if expected['water'] is None:
        if water is not None or expected['valid'] != 0:
            report.fail('water_total', 'basin', ident,
                        'water_m3 must be null: the hand ledger has no valid water')
    elif water != expected['water']:
        report.fail('water_total', 'basin', ident,
                    f'water_m3 {row.get("water_m3")!r} != hand total {expected["water"]!r}')

    mean = F(row.get('valid_mean_mm'))
    if expected['mean'] is not None and mean != expected['mean']:
        report.fail('area_normalization', 'basin', ident,
                    f'valid_mean_mm {row.get("valid_mean_mm")!r} != hand mean {expected["mean"]}')
    if ledger['valid'] == 0:
        if mean is not None:
            report.fail('area_normalization', 'basin', ident,
                        'valid_mean_mm must be null when there is no valid area')
    elif water is None or mean != water * 1000 / ledger['valid']:
        report.fail('area_normalization', 'basin', ident,
                    f'valid_mean_mm {row.get("valid_mean_mm")!r} is not water_m3 * 1000 / valid_m2')

    if entries is not None:
        depths = {cell['id']: F(cell['swe_mm']) for cell in source.get('source_cells', [])}
        for cell, m2, volume in entries:
            depth = depths.get(cell)
            if depth is None:
                report.fail('source_reconciliation', 'basin', ident,
                            f'{cell} is missing or absent in the archived source')
            elif volume != m2 * depth / 1000:
                report.fail('source_reconciliation', 'basin', ident,
                            f'{cell} volume is not m2 * archived depth / 1000')


def _check_target(report, row, expected, source):
    ident = row['id']
    ledger = source_ledger(expected['box'], source)
    if expected['valid'] != ledger['valid']:
        report.fail('fixture', 'target', ident,
                    f'hand valid {expected["valid"]} disagrees with geometry {ledger["valid"]}')
    if expected['sources'] != sorted(cell for cell, _, _ in ledger['entries']):
        report.fail('fixture', 'target', ident,
                    'hand source list disagrees with the archived source geometry')
    if expected['valid'] == 0:
        if expected['water'] is not None or expected['mean'] is not None:
            report.fail('fixture', 'target', ident, 'hand row has water without valid area')
    elif expected['water'] != ledger['water']:
        report.fail('fixture', 'target', ident,
                    f'hand water {expected["water"]} disagrees with geometry {ledger["water"]}')
    try:
        box = tuple(F(v) for v in row['box_m'])
    except (KeyError, TypeError):
        box = None
    if box != expected['box']:
        report.fail('aggregate_conservation', 'target', ident,
                    f'box_m {row.get("box_m")!r} != hand box {expected["box"]}')
    valid = F(row.get('valid_m2'))
    water = F(row.get('water_m3'))
    mean = F(row.get('valid_mean_mm'))
    coverage = F(row.get('coverage'))

    if valid != ledger['valid']:
        report.fail('aggregate_conservation', 'target', ident,
                    f'valid_m2 {row.get("valid_m2")!r} != source geometry {ledger["valid"]}')
    if valid is not None and valid + ledger['missing'] + ledger['outside'] != ledger['area']:
        report.fail('aggregate_conservation', 'target', ident,
                    'valid plus uncovered area does not equal the target box area')

    sources = _sorted_keys(row.get('source_cells'))
    expected_sources = sorted(cell for cell, _, _ in ledger['entries'])
    if sources != expected_sources:
        report.fail('cell_ledger', 'target', ident,
                    f'source_cells {row.get("source_cells")!r} != {expected_sources!r}')
    if sources != expected['sources']:
        report.fail('cell_ledger', 'target', ident,
                    f'source_cells {row.get("source_cells")!r} != hand ledger {expected["sources"]!r}')

    if water is None:
        if ledger['valid'] != 0 or ledger['water'] != 0:
            report.fail('water_total', 'target', ident,
                        'water_m3 is null but the source geometry has measured water')
    elif water != ledger['water']:
        report.fail('water_total', 'target', ident,
                    f'water_m3 {row.get("water_m3")!r} != source geometry {ledger["water"]}')

    if expected['water'] is None:
        if water is not None or expected['valid'] != 0:
            report.fail('water_total', 'target', ident,
                        'water_m3 must be null: the hand ledger has no valid water')
    elif water != expected['water']:
        report.fail('water_total', 'target', ident,
                    f'water_m3 {row.get("water_m3")!r} != hand total {expected["water"]!r}')
    if mean is not None and expected['mean'] is not None and mean != expected['mean']:
        report.fail('area_normalization', 'target', ident,
                    f'valid_mean_mm {row.get("valid_mean_mm")!r} != hand mean {expected["mean"]}')
    if coverage is not None and coverage != expected['coverage']:
        report.fail('area_normalization', 'target', ident,
                    f'coverage {row.get("coverage")!r} != hand coverage {expected["coverage"]}')

    if ledger['valid'] == 0:
        if mean is not None:
            report.fail('area_normalization', 'target', ident,
                        'valid_mean_mm must be null without valid area')
        if coverage != 0:
            report.fail('area_normalization', 'target', ident,
                        'coverage must be zero without valid area')
    else:
        # Use the area basis this product reports for itself, so a coverage
        # normalised over the wrong box is named as a normalisation error
        # rather than merely as a geometry mismatch.
        basis = box_area(box) if box is not None else ledger['area']
        if valid is None or coverage != valid / basis:
            report.fail('area_normalization', 'target', ident,
                        'coverage is not valid_m2 / reported box area')
        if water is None or mean != water * 1000 / ledger['valid']:
            report.fail('area_normalization', 'target', ident,
                        'valid_mean_mm is not water_m3 * 1000 / valid_m2')


def _check_fragments(report, source, basins, targets):
    first, second = CELLS['f1'], CELLS['f2']
    if first['swe'] != second['swe']:
        report.fail('fragment_conservation', 'source', 'f1/f2',
                    'fragments do not carry the same value')
    combined_area = box_area(first['box']) + box_area(second['box'])
    combined_water = combined_area * first['swe'] / 1000
    row = basins.get('frags')
    if row is not None:
        if F(row.get('valid_m2')) != combined_area:
            report.fail('fragment_conservation', 'basin', 'frags',
                        'same-value fragments were not counted once by area')
        if F(row.get('water_m3')) != combined_water:
            report.fail('fragment_conservation', 'basin', 'frags',
                        'same-value fragments were double counted or dropped')
    target = targets.get('frag-mix')
    if target is not None and F(target.get('valid_m2')) != combined_area:
        report.fail('fragment_conservation', 'target', 'frag-mix',
                    'fragment-only area is not the sum of the two fragment areas')


def _check_zero_null(report, basins, targets):
    dry = basins.get('dry')
    if dry is not None:
        if dry.get('water_m3') is None or F(dry.get('water_m3')) != 0:
            report.fail('zero_vs_null', 'basin', 'dry',
                        'a measured zero must be 0, not null')
        if dry.get('valid_mean_mm') is None or F(dry.get('valid_mean_mm')) != 0:
            report.fail('zero_vs_null', 'basin', 'dry',
                        'a measured-zero mean must be 0, not null')
    miss = basins.get('miss')
    if miss is not None:
        if miss.get('water_m3') is not None or miss.get('valid_mean_mm') is not None:
            report.fail('zero_vs_null', 'basin', 'miss',
                        'an all-missing basin must report null water and mean')
        if F(miss.get('valid_m2')) != 0:
            report.fail('zero_vs_null', 'basin', 'miss',
                        'an all-missing basin must have zero valid area')
    for ident in ('outside',):
        target = targets.get(ident)
        if target is None:
            continue
        if target.get('water_m3') is not None or target.get('valid_mean_mm') is not None:
            report.fail('zero_vs_null', 'target', ident,
                        'a target outside the source grid must report null water and mean')
        if F(target.get('valid_m2')) != 0:
            report.fail('zero_vs_null', 'target', ident,
                        'a target outside the source grid must have zero valid area')


def _check_overlap(report, basins, source):
    left, right, domain = (basins.get(key) for key in ('shed-left', 'shed-right', 'domain'))
    if None in (left, right, domain):
        return
    left_box, right_box = BASINS['shed-left']['box'], BASINS['shed-right']['box']
    shared = overlap(left_box, right_box)
    if shared <= 0:
        report.fail('overlap_union', 'fixture', None, 'basins must overlap')
        return
    left_water = F(left.get('water_m3'))
    right_water = F(right.get('water_m3'))
    domain_water = F(domain.get('water_m3'))
    overlap_water = source_ledger((2, 0, 3, 2), source)['water']
    if None in (left_water, right_water, domain_water):
        report.fail('overlap_union', 'basin', 'shed-left/shed-right',
                    'overlapping basins must report water')
        return
    # Summing overlapping basins double counts the shared strip exactly once.
    if left_water + right_water - domain_water != overlap_water:
        report.fail('overlap_union', 'basin', 'shed-left/shed-right',
                    f'basin sum {left_water + right_water} minus domain water '
                    f'{domain_water} is not the shared-strip water {overlap_water}')
    if domain_water == left_water + right_water:
        report.fail('overlap_union', 'basin', 'domain',
                    'domain water equals the sum of overlapping basins')

    # In-grid area must follow the union too, not the basin sum.
    def inside(row):
        return F(row.get('valid_m2')) + F(row.get('missing_in_grid_m2'))

    if None not in (inside(left), inside(right), inside(domain)):
        if inside(left) + inside(right) - inside(domain) != shared:
            report.fail('overlap_union', 'basin', 'shed-left/shed-right',
                        'in-grid area exceeds the domain by something other than the shared strip')

    # Fixture trap: the two overlapping basins' nominal areas sum to exactly the
    # domain's nominal area, so an area-only reconciliation cannot expose the
    # double count.  Keep that property so the water check stays necessary.
    summed_area = box_area(left_box) + box_area(right_box)
    if summed_area != box_area(BASINS['domain']['box']):
        report.fail('overlap_union', 'fixture', None,
                    'nominal areas no longer hide the overlap; rebuild the trap')
    if box_area((0, 0, 5, 2)) >= summed_area:
        report.fail('overlap_union', 'fixture', None,
                    'the basin union must be smaller than the summed basin areas')


def check(result, source):
    """Return failures grouped by category; empty means the products reconcile."""
    report = _Report()
    _check_source(report, source)
    basins = _index(report, result.get('basins') if isinstance(result, dict) else None,
                    BASINS, 'basin')
    targets = _index(report, result.get('target_grid') if isinstance(result, dict) else None,
                     TARGETS, 'target')
    if isinstance(result, dict) and result.get('survey') != 'handgrid':
        report.fail('product_shape', 'result', None, 'survey does not match the source')
    for ident, expected in BASINS.items():
        if ident in basins:
            try:
                _check_basin(report, basins[ident], expected, source)
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                report.fail('product_shape', 'basin', ident, f'unreadable row: {exc}')
    for ident, expected in TARGETS.items():
        if ident in targets:
            try:
                _check_target(report, targets[ident], expected, source)
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                report.fail('product_shape', 'target', ident, f'unreadable row: {exc}')
    _check_fragments(report, source, basins, targets)
    _check_zero_null(report, basins, targets)
    _check_overlap(report, basins, source)
    return report.failures


def categories(result, source):
    return sorted({failure['category'] for failure in check(result, source)})


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))
