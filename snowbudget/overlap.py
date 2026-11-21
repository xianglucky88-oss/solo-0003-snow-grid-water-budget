"""Difference product for two misaligned grids over their real common support.

The common support is the region where BOTH forecasts carry a measured cell:
a ``null`` cell is missing evidence, while a true zero depth is valid evidence
and still participates. Every figure is integrated from the actual rectilinear
geometry with exact fractions; basin differences are never apportioned to
responsibility zones by area.
"""
from .common import number, require, scalar
from .field import cells
from .geometry import overlap, read_boxes


def _forecasts(rows):
    require(isinstance(rows, list) and len(rows) == 2, 'forecasts must contain exactly two grids')
    grids, ids = [], set()
    for row in rows:
        require(isinstance(row, dict), 'forecast must be an object')
        fid = row.get('id')
        require(isinstance(fid, str) and fid.strip() and fid not in ids, f'invalid forecast id: {fid!r}')
        ids.add(fid)
        grids.append((fid, cells(row['source_cells'])))
    return grids


def _zones(rows, basins):
    zones, boxes, ids = [], [], set()
    for row in rows:
        require(isinstance(row, dict), 'zone must be an object')
        key = row['id']
        require(isinstance(key, str) and key.strip() and key not in ids, f'invalid or duplicate zone id: {key!r}')
        ids.add(key)
        parent = row['basin']
        require(parent in basins, f'zone {key} references unknown basin: {parent}')
        box = tuple(number(v, 'coordinate', minimum=None) for v in row['box_m'])
        require(len(box) == 4 and box[0] < box[2] and box[1] < box[3], f'invalid rectangle: {key}')
        base = basins[parent]
        require(box[0] >= base[0] and box[1] >= base[1] and box[2] <= base[2] and box[3] <= base[3],
                f'zone {key} lies outside basin {parent}')
        require(not any(overlap(box, old) for old in boxes), f'overlapping responsibility zone: {key}')
        boxes.append(box)
        zones.append({'id': key, 'basin': parent, 'box': box,
                      'threshold': number(row['threshold_m3'], 'threshold')})
    return zones


class _Bucket:
    __slots__ = ('common_area', 'wa', 'wb', 'ca', 'cb', 'ao', 'bo')

    def __init__(self):
        self.common_area = self.wa = self.wb = 0
        self.ca = {}
        self.cb = {}
        self.ao = {}
        self.bo = {}


def _add(store, key, section, volume):
    if key in store:
        store[key][0] += section
        store[key][1] += volume
    else:
        store[key] = [section, volume]


def _locate(source, x0, x1, y0, y1):
    for key, box, depth in source:
        if box[0] <= x0 and x1 <= box[2] and box[1] <= y0 and y1 <= box[3]:
            return key, depth
    return None, None


def _inside(box, x0, x1, y0, y1):
    return box[0] <= x0 and x1 <= box[2] and box[1] <= y0 and y1 <= box[3]


def _contributions(store):
    return [{'cell': key, 'm2': scalar(part[0]), 'water_m3': scalar(part[1])}
            for key, part in sorted(store.items())]


def _one_sided(store):
    section = sum((part[0] for part in store.values()), 0)
    volume = sum((part[1] for part in store.values()), 0)
    return {'area_m2': scalar(section), 'water_m3': scalar(volume) if section else None,
            'contributions': _contributions(store)}


def _support(bucket, id_a, id_b):
    if bucket.common_area:
        diff = bucket.wa - bucket.wb
        higher = id_a if diff > 0 else id_b if diff < 0 else None
        common = {'area_m2': scalar(bucket.common_area),
                  'water_a_m3': scalar(bucket.wa), 'water_b_m3': scalar(bucket.wb),
                  'difference_m3': scalar(diff), 'higher': higher,
                  'contributions_a': _contributions(bucket.ca),
                  'contributions_b': _contributions(bucket.cb)}
    else:
        common = {'area_m2': 0, 'water_a_m3': None, 'water_b_m3': None,
                  'difference_m3': None, 'higher': None,
                  'contributions_a': [], 'contributions_b': []}
    return {'common': common, 'a_only': _one_sided(bucket.ao), 'b_only': _one_sided(bucket.bo)}


def compare(doc):
    """Build the difference product from a two-forecast evidence bundle."""
    require('source_cells' not in doc and 'targets' not in doc,
            'two-grid bundles use forecasts, not source_cells or targets')
    require(isinstance(doc.get('basins'), list), 'two-grid bundle requires a basins list')
    require(isinstance(doc.get('zones'), list), 'two-grid bundle requires a zones list')
    grids = _forecasts(doc['forecasts'])
    basins = read_boxes(doc['basins'])
    zones = _zones(doc['zones'], basins)

    xs, ys = set(), set()
    for _, source in grids:
        for _, box, _ in source:
            xs.update((box[0], box[2]))
            ys.update((box[1], box[3]))
    for box in list(basins.values()) + [zone['box'] for zone in zones]:
        xs.update((box[0], box[2]))
        ys.update((box[1], box[3]))
    xs, ys = sorted(xs), sorted(ys)

    whole = _Bucket()
    basin_buckets = {key: _Bucket() for key in basins}
    zone_buckets = {zone['id']: _Bucket() for zone in zones}

    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            section = (x1 - x0) * (y1 - y0)
            ak, ad = _locate(grids[0][1], x0, x1, y0, y1)
            bk, bd = _locate(grids[1][1], x0, x1, y0, y1)
            a_ok, b_ok = ad is not None, bd is not None
            targets = [whole]
            for key, box in basins.items():
                if _inside(box, x0, x1, y0, y1):
                    targets.append(basin_buckets[key])
            for zone in zones:
                if _inside(zone['box'], x0, x1, y0, y1):
                    targets.append(zone_buckets[zone['id']])
            for bucket in targets:
                if a_ok and b_ok:
                    va, vb = section * ad / 1000, section * bd / 1000
                    bucket.common_area += section
                    bucket.wa += va
                    bucket.wb += vb
                    _add(bucket.ca, ak, section, va)
                    _add(bucket.cb, bk, section, vb)
                elif a_ok:
                    _add(bucket.ao, ak, section, section * ad / 1000)
                elif b_ok:
                    _add(bucket.bo, bk, section, section * bd / 1000)

    id_a, id_b = grids[0][0], grids[1][0]
    result = {'forecast_a': id_a, 'forecast_b': id_b,
              **_support(whole, id_a, id_b), 'basins': []}
    for key in sorted(basins):
        entry = {'id': key, **_support(basin_buckets[key], id_a, id_b), 'zones': []}
        for zone in sorted((z for z in zones if z['basin'] == key), key=lambda z: z['id']):
            bucket = zone_buckets[zone['id']]
            block = _support(bucket, id_a, id_b)
            block['threshold_m3'] = scalar(zone['threshold'])
            block['triggers'] = (abs(bucket.wa - bucket.wb) >= zone['threshold']
                                 if bucket.common_area else None)
            entry['zones'].append({'id': zone['id'], **block})
        result['basins'].append(entry)
    return result
