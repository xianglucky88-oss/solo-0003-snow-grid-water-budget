"""Source cells, occlusions and independently sourced remeasurement patches.

A source cell keeps its original SWE value plus exactly located unusable
rectangles ("occlusions"), or -- when only a percentage is known -- an
``occluded_fraction`` with no location.  Remeasurement patches carry their own
value and may overlap one another and the source grid; overlap is resolved
later by exact geometry, not here.

Input spellings are accepted leniently by intent (occlusion rectangles,
occlusion fractions/percentages, patch lists) because the bundle is an external
contract, but the geometry and the evidence are always validated strictly.
"""
from dataclasses import dataclass

from .common import number, require, unique
from .geometry import overlap, read_boxes


@dataclass(frozen=True)
class Cell:
    """One source cell: an original value and the exact parts where it is unusable."""

    id: str
    box: tuple
    swe: object
    occlusions: tuple = ()
    occluded_fraction: object = None


@dataclass(frozen=True)
class Patch:
    """One independently sourced local remeasurement over an arbitrary rectangle."""

    id: str
    box: tuple
    swe: object
    origin: object = None


BOX_KEYS = ('box_m', 'box', 'rect_m', 'rect', 'extent_m', 'bounds_m')
DEPTH_KEYS = ('swe_mm', 'swe', 'depth_mm', 'value_mm', 'swe_value_mm', 'measured_mm', 'swe_mm_value')
ORIGIN_KEYS = ('origin', 'source', 'source_id', 'agency', 'survey_line', 'flight')
CELL_KEYS = ('cell', 'source_cell', 'cell_id', 'source', 'source_id')

FRACTION_NAMES = {'occluded_fraction', 'occlusion_fraction', 'occluded_ratio',
                  'occlusion_ratio', 'occluded_percent', 'occlusion_percent',
                  'percent_occluded', 'occluded_pct', 'occlusion_pct'}
OCCLUSION_NAMES = {'occlusion', 'occlusions', 'occluded', 'occluded_boxes',
                   'occluded_rects', 'occluded_areas', 'occluded_regions',
                   'occlusion_boxes', 'occlusion_rects', 'occlusion_areas'}
OCCLUSION_WORDS = ('occlu', 'obscur', 'shadow', 'blocked', 'masked', 'mask_')
PATCH_WORDS = ('patch', 'remeasur', 'recheck', 'resurvey', 'revisit')
FRACTION_WORDS = ('fraction', 'ratio', 'percent', 'pct')


def rectangle(values, label):
    box = tuple(number(v, 'coordinate', minimum=None) for v in values)
    require(len(box) == 4 and box[0] < box[2] and box[1] < box[3], f'invalid rectangle: {label}')
    return box


def _scalar(value):
    return type(value) is int or isinstance(value, str)


def _looks_like_rectangle(value):
    return isinstance(value, (list, tuple)) and len(value) == 4 and all(_scalar(v) for v in value)


def _rectangles(value, label):
    """Normalise one occlusion/patch value into a list of boxes."""
    if isinstance(value, dict):
        for name in BOX_KEYS:
            if name in value:
                return [rectangle(value[name], label)]
        require(False, f'invalid rectangle: {label}')
    if _looks_like_rectangle(value):
        return [rectangle(value, label)]
    require(isinstance(value, (list, tuple)), f'invalid rectangle: {label}')
    boxes = []
    for position, item in enumerate(value):
        item_label = f'{label}[{position}]'
        if isinstance(item, dict):
            box = next((item[name] for name in BOX_KEYS if name in item), None)
            require(box is not None, f'invalid rectangle: {item_label}')
            boxes.append(rectangle(box, item_label))
        else:
            require(_looks_like_rectangle(item), f'invalid rectangle: {item_label}')
            boxes.append(rectangle(item, item_label))
    return boxes


def _candidate_occlusions(row, key):
    """Every non-numeric field on a cell row that names an occlusion footprint."""
    found = []
    for name, value in row.items():
        if value is None or isinstance(value, (int, float)):
            continue
        low = name.lower()
        if low in FRACTION_NAMES or any(word in low for word in FRACTION_WORDS):
            continue
        if low in OCCLUSION_NAMES or any(word in low for word in OCCLUSION_WORDS):
            found.append((name, value))
    require(len(found) <= 1, f'ambiguous occlusions: {key}')
    return found[0][1] if found else None


def _occlusion_list(row, key, box):
    value = _candidate_occlusions(row, key)
    if value is None:
        return ()
    result = []
    for position, occluded in enumerate(_rectangles(value, key)):
        label = f'{key}[{position}]'
        require(box[0] <= occluded[0] and box[1] <= occluded[1]
                and occluded[2] <= box[2] and occluded[3] <= box[3],
                f'occlusion outside source cell: {label}')
        require(not any(overlap(occluded, prior) for prior in result), f'overlapping occlusions: {label}')
        result.append(occluded)
    return tuple(result)


def _fraction(row, key):
    present = [(name, row[name]) for name in row
               if name.lower() in FRACTION_NAMES and row[name] is not None]
    require(len(present) <= 1, f'ambiguous occluded fraction: {key}')
    if not present:
        return None
    name, raw = present[0]
    value = number(raw, name, minimum=0)
    if 'percent' in name.lower() or name.lower().endswith('_pct'):
        require(value <= 100, f'{name} above maximum: {key}')
        return value / 100
    require(value <= 1, f'{name} above maximum: {key}')
    return value


def cell_depth(row, key):
    """Original SWE value under any of the accepted depth key spellings."""
    for name in DEPTH_KEYS:
        if name in row:
            return None if row[name] is None else number(row[name], 'SWE depth')
    require(False, f'missing SWE depth: {key}')


def _patch_key(row, label):
    names = [name for name, value in row.items()
             if isinstance(value, (list, dict))
             and any(word in name.lower() for word in PATCH_WORDS)]
    require(len(names) <= 1, f'ambiguous embedded patches: {label}')
    return names[0] if names else None


def _value(row, names, label):
    found = [name for name in names if row.get(name) is not None]
    require(len(found) <= 1, f'ambiguous value: {label}')
    return row[found[0]] if found else None


def cells(rows, extra_occlusions=()):
    """Parse source cells; a null depth means the whole cell's original value is unusable."""
    geometry = read_boxes(rows, nonoverlap=True)
    values = unique(rows)
    grouped = _group_occlusion_rows(extra_occlusions) if extra_occlusions else {}
    unknown = sorted(set(grouped) - set(values))
    require(not unknown, f'occlusion for unknown source cell: {unknown[0] if unknown else ""}')
    result = []
    for key, box in geometry.items():
        row = values[key]
        swe = cell_depth(row, key)
        occlusions = _occlusion_list(row, key, box)
        for position, extra in enumerate(grouped.get(key, ())):
            for box_index, occluded in enumerate(_rectangles(extra, f'{key}:extra[{position}]')):
                label = f'{key}:extra[{position}][{box_index}]'
                require(box[0] <= occluded[0] and box[1] <= occluded[1]
                        and occluded[2] <= box[2] and occluded[3] <= box[3],
                        f'occlusion outside source cell: {label}')
                require(not any(overlap(occluded, prior) for prior in occlusions),
                        f'overlapping occlusions: {label}')
                occlusions += (occluded,)
        fraction = _fraction(row, key)
        require(not (occlusions and fraction is not None), f'occlusion already located: {key}')
        require(fraction is None or swe is not None, f'occluded fraction needs an original value: {key}')
        result.append(Cell(key, box, swe, occlusions, fraction))
    return result


def _parse_patch(row, key, prefix=''):
    box = _value(row, BOX_KEYS, key)
    require(box is not None, f'invalid rectangle: {prefix}{key}')
    box = rectangle(box, f'{prefix}{key}')
    depth = _value(row, DEPTH_KEYS, f'{prefix}{key}')
    require(depth is not None, f'patch has no measured value: {prefix}{key}')
    origin = _value(row, ORIGIN_KEYS, f'{prefix}{key}')
    require(origin is None or (isinstance(origin, str) and origin.strip()),
            f'invalid patch origin: {prefix}{key}')
    return Patch(f'{prefix}{key}', box, number(depth, 'patch SWE depth'), origin)


def patches(rows, prefix=''):
    """Parse remeasurement patches; they may overlap each other and the source grid."""
    if isinstance(rows, dict):
        rows = [dict(value, id=key) for key, value in rows.items() if isinstance(value, dict)]
    require(isinstance(rows, list), 'patches must be a list')
    index = {}
    for position, row in enumerate(rows):
        require(isinstance(row, dict), 'patch rows must be objects')
        key = row.get('id')
        if key is None:
            key = f'patch-{position}'
            require(key not in index, f'duplicate patch id: {key}')
        require(isinstance(key, str) and key.strip(), f'invalid or duplicate id: {key!r}')
        require(key not in index, f'invalid or duplicate id: {key!r}')
        index[key] = (key, row)
    return [_parse_patch(row, key, prefix) for key, row in (index[k] for k in sorted(index))]


def _group_occlusion_rows(rows):
    grouped = {}
    for row in rows:
        require(isinstance(row, dict), 'occlusion rows must be objects')
        key = _value(row, CELL_KEYS, 'occlusion row')
        require(isinstance(key, str) and key.strip(), 'occlusion row needs a source cell')
        grouped.setdefault(key, []).append(row)
    return grouped


def _match(document, words):
    return [name for name in document
            if isinstance(document[name], (list, dict))
            and any(word in name.lower() for word in words)]


def patch_rows(doc, source_rows=()):
    """Collect top-level and per-cell remeasurement patches."""
    present = _match(doc, PATCH_WORDS)
    require(len(present) <= 1, f'ambiguous patches: {present}')
    collected = patches(doc[present[0]]) if present else []
    seen = {patch.id for patch in collected}
    for row in source_rows:
        name = _patch_key(row, row.get('id', '?'))
        if name is None:
            continue
        for patch in patches(row[name], prefix=f'{row["id"]}:'):
            require(patch.id not in seen, f'duplicate patch id: {patch.id}')
            seen.add(patch.id)
            collected.append(patch)
    return sorted(collected, key=lambda patch: patch.id)


def extra_occlusion_rows(doc):
    """Return document-level occlusion rows, if the bundle uses that layout."""
    for name in _match(doc, OCCLUSION_WORDS):
        rows = doc[name]
        if isinstance(rows, list) and any(isinstance(row, dict) and _value(row, CELL_KEYS, name) is not None
                                          for row in rows):
            return rows
    return []
