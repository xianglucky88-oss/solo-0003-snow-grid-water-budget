"""Split basin water across elevation bands under one fixed elevation coverage."""
from .common import scalar
from .field import intersect
from .geometry import area, subtract

NO_BAND = None


def find_band(elevation, bands):
    for band in bands:  # spans are disjoint, so a plain scan is unambiguous
        if band['lo'] <= elevation < band['hi']:
            return band['id']
    return NO_BAND


def intersection_box(a, b):
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[0] < box[2] and box[1] < box[3] else None


def cell_box(source, cell_id):
    for key, box, _ in source:
        if key == cell_id:
            return box
    raise LookupError(cell_id)


def banding(source, basins, bands, coverage, primary):
    for basin_id, basin_box in basins.items():
        fragments = []
        for cell_id, section, depth in intersect(source, basin_box):
            source_box = cell_box(source, cell_id)
            cell_in_basin = intersection_box(basin_box, source_box)
            uncovered = [cell_in_basin]
            for patch in coverage:
                next_uncovered = []
                for rect in uncovered:
                    shared = intersection_box(rect, patch['box'])
                    if shared is None:
                        next_uncovered.append(rect)
                        continue
                    next_uncovered.extend(subtract(rect, shared))
                    band = find_band(patch['elevation_m'], bands)
                    fragments.append(make_fragment(cell_id, depth, area(shared), shared, patch, band))
                uncovered = next_uncovered
            for gap_box in uncovered:
                fragments.append(make_fragment(
                    cell_id, depth, area(gap_box), gap_box,
                    {'scheme': None, 'cell': None, 'elevation_m': None}, NO_BAND))
        yield summarize(basin_id, basin_box, bands, fragments, primary)


def make_fragment(cell_id, depth, area_m2, box, patch, band):
    water = area_m2 * depth / 1000 if depth is not None else None
    return {'cell': cell_id, 'box_m': box, 'm2': area_m2, 'swe_mm': depth, 'water_m3': water,
            'elevation_m': patch['elevation_m'], 'band': band,
            'elevation_scheme': patch['scheme'], 'elevation_cell': patch['cell']}


def summarize(basin_id, basin_box, bands, fragments, primary):
    band_rows = {band['id']: {'band': band['id'], 'span_m': [band['lo'], band['hi']],
                              'measured_m2': 0, 'water_m3': 0,
                              'zero_m2': 0, 'zero_water_m3': 0}
                 for band in bands}
    no_elev_m2 = no_elev_water = 0
    unobserved_m2 = 0
    for frag in fragments:
        area_m2, depth, band = frag['m2'], frag['swe_mm'], frag['band']
        if depth is None:
            unobserved_m2 += area_m2
        elif band is NO_BAND:
            no_elev_m2 += area_m2
            no_elev_water += frag['water_m3']
        elif depth == 0:
            row = band_rows[band]
            row['zero_m2'] += area_m2
            row['zero_water_m3'] += frag['water_m3']
        else:
            row = band_rows[band]
            row['measured_m2'] += area_m2
            row['water_m3'] += frag['water_m3']
    banded_m2 = sum(r['measured_m2'] + r['zero_m2'] for r in band_rows.values())
    banded_water = sum(r['water_m3'] + r['zero_water_m3'] for r in band_rows.values())
    valid_m2 = banded_m2 + no_elev_m2
    return {
        'id': basin_id,
        'coverage_choice': primary,
        'bands': [{'band': row['band'], 'span_m': [scalar(row['span_m'][0]), scalar(row['span_m'][1])],
                   'measured_m2': scalar(row['measured_m2']),
                   'water_m3': scalar(row['water_m3']),
                   'zero_m2': scalar(row['zero_m2']),
                   'zero_water_m3': scalar(row['zero_water_m3'])}
                  for row in band_rows.values()],
        'banded_m2': scalar(banded_m2),
        'banded_water_m3': scalar(banded_water),
        'no_elevation_m2': scalar(no_elev_m2),
        'no_elevation_water_m3': scalar(no_elev_water),
        'unobserved_m2': scalar(unobserved_m2),
        'reconciliation': {
            'valid_m2': scalar(valid_m2),
            'water_m3': scalar(banded_water + no_elev_water),
            'valid_plus_unobserved_m2': scalar(valid_m2 + unobserved_m2),
            'basin_area_m2': scalar(area(basin_box)),
        },
        'fragments': [{'cell': f['cell'],
                       'box_m': [scalar(v) for v in f['box_m']],
                       'm2': scalar(f['m2']),
                       'swe_mm': None if f['swe_mm'] is None else scalar(f['swe_mm']),
                       'water_m3': None if f['water_m3'] is None else scalar(f['water_m3']),
                       'elevation_m': None if f['elevation_m'] is None else scalar(f['elevation_m']),
                       'band': f['band'], 'elevation_scheme': f['elevation_scheme'],
                       'elevation_cell': f['elevation_cell']}
                      for f in fragments],
    }
