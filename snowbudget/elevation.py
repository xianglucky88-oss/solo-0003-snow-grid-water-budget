"""Elevation bands and non-aligned elevation coverages.

A release names exactly one primary elevation scheme. Secondary schemes may
only extend the effective coverage into places the primary (and earlier
extensions) do not cover; each secondary cell is geometrically clipped against
the coverage already in force. Conflicting elevations on the same location are
therefore never mixed band by band — one coherent coverage order is fixed for
the whole edition before any banding happens.
"""
from .common import disjoint, number, require, span
from .geometry import read_boxes, subtract


def elevation_bands(rows):
    require(isinstance(rows, list) and rows, 'elevation_bands must be a non-empty list')
    bands = []
    seen = set()
    for row in rows:
        band_id = row['id']
        require(isinstance(band_id, str) and band_id.strip() and band_id not in seen,
                f'invalid or duplicate elevation band: {band_id!r}')
        seen.add(band_id)
        lo, hi = span(row['span_m'], 'elevation span')
        bands.append({'id': band_id, 'lo': lo, 'hi': hi})
    disjoint([(b['lo'], b['hi']) for b in bands], 'elevation bands')
    return bands


def elevation_schemes(rows):
    require(isinstance(rows, list) and rows, 'elevation_schemes must be a non-empty list')
    schemes = {}
    for row in rows:
        name = row['id']
        require(isinstance(name, str) and name.strip() and name not in schemes,
                f'invalid or duplicate elevation scheme: {name!r}')
        cells = []
        seen_cells = set()
        for cell_row in row['cells']:
            key = cell_row['id']
            require(isinstance(key, str) and key.strip() and key not in seen_cells,
                    f'invalid or duplicate elevation cell: {key!r}')
            seen_cells.add(key)
            box = read_boxes([{'id': key, 'box_m': cell_row['box_m']}])[key]
            elevation = number(cell_row['elevation_m'], 'elevation_m', minimum=None)
            cells.append({'id': key, 'box': box, 'elevation_m': elevation})
        require(cells, f'elevation scheme has no cells: {name}')
        schemes[name] = cells
    return schemes


def effective_coverage(schemes, primary):
    """Fix one whole-edition coverage: primary first, secondary schemes only fill gaps."""
    require(primary in schemes, f'coverage_choice names an unknown scheme: {primary}')
    ordered = [primary] + sorted(name for name in schemes if name != primary)
    coverage = []
    for name in ordered:
        for cell in schemes[name]:
            pieces = [cell['box']]
            for accepted in coverage:
                pieces = [part for rect in pieces for part in subtract(rect, accepted['box'])]
            for part in pieces:
                coverage.append({'box': part, 'elevation_m': cell['elevation_m'],
                                 'scheme': name, 'cell': cell['id']})
    return coverage
