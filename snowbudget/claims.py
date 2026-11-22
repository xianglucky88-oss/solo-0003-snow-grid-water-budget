"""Exclusive declaration view.

The ordinary basin products keep every basin independent, so overlapping basins
double count their shared ground.  This view partitions the *geometry* of one
parent basin into four declared parts using the unique ownership certificates:

exclusive   - measured ground covered by a certificate held by one sub-basin
disputed    - measured ground reached by more than one sub-basin, no certificate
remainder   - measured ground reached by at most one sub-basin, no certificate
unobserved  - missing-in-grid or outside-grid ground, which never carries water

Classification is done on geometric fragments, never by subtracting sub-basin
reports, so reassigning a certificate to another sub-basin moves exclusive
amounts while the parent totals and the ordinary basin reports stay unchanged.
"""
from .common import number, require, scalar, unique
from .field import intersect
from .geometry import area, contains, overlap


def _certificate_rows(doc):
    raw = doc.get('certificates', [])
    require(isinstance(raw, list), 'certificates must be a list')
    rows = unique(raw, field='id')
    result = {}
    for key, row in rows.items():
        require(isinstance(row, dict), f'certificate must be an object: {key}')
        owner = row.get('basin')
        require(isinstance(owner, str) and owner.strip(), f'certificate without owner: {key}')
        raw_box = row.get('box_m')
        require(isinstance(raw_box, list) and len(raw_box) == 4, f'certificate box_m must be four numbers: {key}')
        box = tuple(number(v, 'certificate coordinate', minimum=None) for v in raw_box)
        require(box[0] < box[2] and box[1] < box[3], f'certificate must have positive area: {key}')
        result[key] = (owner, box)
    return result


def _empty_bucket():
    return {'valid_m2': 0, 'water_m3': 0, 'missing_in_grid_m2': 0, 'outside_grid_m2': 0,
            'contributions': {}, 'missing_cells': set()}


def _serialize(bucket):
    contributions = [{'cell': cell, 'm2': scalar(m2), 'water_m3': scalar(water)}
                     for cell, (m2, water) in sorted(bucket['contributions'].items())]
    valid, water = bucket['valid_m2'], bucket['water_m3']
    return {'valid_m2': scalar(valid),
            'water_m3': scalar(water) if valid else None,
            'valid_mean_mm': scalar(water * 1000 / valid) if valid else None,
            'missing_in_grid_m2': scalar(bucket['missing_in_grid_m2']),
            'outside_grid_m2': scalar(bucket['outside_grid_m2']),
            'total_m2': scalar(valid + bucket['missing_in_grid_m2'] + bucket['outside_grid_m2']),
            'contributions': contributions,
            'missing_cells': sorted(bucket['missing_cells'])}


def declaration_view(source, basins, doc):
    """Return the four-way exclusive declaration partition for one parent."""
    require(isinstance(doc, dict), 'declaration must be an object')
    parent_id = doc.get('parent_basin')
    require(isinstance(parent_id, str) and parent_id in basins, f'unknown parent_basin: {parent_id!r}')
    raw_children = doc.get('sub_basins', [])
    require(isinstance(raw_children, list) and raw_children, 'sub_basins must be a non-empty list')
    child_ids = []
    for child in raw_children:
        require(isinstance(child, str) and child in basins and child != parent_id,
                f'sub_basin must be another declared basin: {child!r}')
        require(child not in child_ids, f'duplicate sub_basin: {child}')
        child_ids.append(child)

    certificates = _certificate_rows(doc)
    for key, (owner, box) in certificates.items():
        require(owner in child_ids, f'certificate {key} owner {owner!r} is not a sub_basin')
        require(contains(basins[parent_id], box), f'certificate {key} falls outside parent basin')
        require(contains(basins[owner], box), f'certificate {key} falls outside its owner basin')
    for key, (_, box) in certificates.items():
        require(not any(overlap(box, other) for other_key, (_, other) in certificates.items()
                        if other_key != key), f'overlapping certificate: {key}')

    parent = basins[parent_id]
    child_boxes = [basins[key] for key in child_ids]
    x_edges = [parent[0], parent[2]] + [v for b in child_boxes for v in (b[0], b[2])]
    y_edges = [parent[1], parent[3]] + [v for b in child_boxes for v in (b[1], b[3])]
    for _, box in certificates.values():
        x_edges += [box[0], box[2]]
        y_edges += [box[1], box[3]]
    for _, extent, _ in source:
        x_edges += [extent[0], extent[2]]
        y_edges += [extent[1], extent[3]]
    xs, ys = sorted(set(x_edges)), sorted(set(y_edges))

    exclusive = {key: _empty_bucket() for key in child_ids}
    certs_by_child = {key: [] for key in child_ids}
    for key, (owner, _) in certificates.items():
        certs_by_child[owner].append(key)
    disputed, remainder, unobserved = _empty_bucket(), _empty_bucket(), _empty_bucket()

    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            fragment = (x0, y0, x1, y1)
            if not contains(parent, fragment):
                continue
            section = area(fragment)
            measured = next(((cell, depth) for cell, extent, depth in source
                             if contains(extent, fragment)), None)
            if measured is None or measured[1] is None:
                bucket = unobserved
                if measured is None:
                    bucket['outside_grid_m2'] += section
                else:
                    bucket['missing_in_grid_m2'] += section
                    bucket['missing_cells'].add(measured[0])
                continue
            cell, depth = measured
            owner = next((cert_owner for cert_id, (cert_owner, cert_box) in certificates.items()
                          if contains(cert_box, fragment)), None)
            if owner is not None:
                bucket = exclusive[owner]
            elif sum(1 for child in child_boxes if contains(child, fragment)) > 1:
                bucket = disputed
            else:
                bucket = remainder
            bucket['valid_m2'] += section
            bucket['water_m3'] += section * depth / 1000
            m2, water = bucket['contributions'].get(cell, (0, 0))
            bucket['contributions'][cell] = (m2 + section, water + section * depth / 1000)

    # Independent geometric totals for the parent, recomputed from source cells.
    parent_valid = parent_missing = parent_water = 0
    for cell, section, depth in intersect(source, parent):
        if depth is None:
            parent_missing += section
        else:
            parent_valid += section
            parent_water += section * depth / 1000
    parent_outside = area(parent) - parent_valid - parent_missing

    observed_buckets = list(exclusive.values()) + [disputed, remainder]
    sum_valid = sum(b['valid_m2'] for b in observed_buckets)
    sum_water = sum(b['water_m3'] for b in observed_buckets)
    sum_missing = unobserved['missing_in_grid_m2']
    sum_outside = unobserved['outside_grid_m2']
    require(sum_valid == parent_valid, 'declaration partition valid area does not reconcile')
    require(sum_water == parent_water, 'declaration partition water does not reconcile')
    require(sum_missing == parent_missing, 'declaration partition missing area does not reconcile')
    require(sum_outside == parent_outside, 'declaration partition outside area does not reconcile')
    require(sum_valid + sum_missing + sum_outside == area(parent),
            'declaration partition total area does not reconcile')

    def tieback(label, parts, total):
        return {'part_sum': scalar(parts), label: scalar(total), 'reconciles': parts == total}

    return {
        'parent_basin': parent_id,
        'sub_basins': child_ids,
        'exclusive': [dict({'basin': key, 'certificates': sorted(certs_by_child[key])},
                           **_serialize(exclusive[key])) for key in child_ids],
        'disputed': _serialize(disputed),
        'remainder': _serialize(remainder),
        'unobserved': _serialize(unobserved),
        'reconciliation': {
            'area_m2': tieback('parent_m2', sum_valid + sum_missing + sum_outside, area(parent)),
            'valid_m2': tieback('parent_valid_m2', sum_valid, parent_valid),
            'water_m3': tieback('parent_water_m3', sum_water, parent_water),
            'missing_in_grid_m2': tieback('parent_missing_m2', sum_missing, parent_missing),
            'outside_grid_m2': tieback('parent_outside_m2', sum_outside, parent_outside),
        },
    }
