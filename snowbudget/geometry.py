from .common import number, require, unique


def overlap(a, b):
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0 and dy > 0 else 0


def area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def subtract(rect, hole):
    """Axis-aligned rectangle difference: the parts of rect outside hole (up to four rectangles)."""
    if overlap(rect, hole) == 0:
        return [rect]
    x0, y0, x1, y1 = rect
    hx0, hy0, hx1, hy1 = hole
    ix0, ix1 = max(x0, hx0), min(x1, hx1)
    parts = []
    if x0 < hx0:
        parts.append((x0, y0, hx0, y1))
    if hx1 < x1:
        parts.append((hx1, y0, x1, y1))
    if ix0 < ix1:  # side strips may already cover the full width; skip degenerate bands
        if y0 < hy0:
            parts.append((ix0, y0, ix1, hy0))
        if hy1 < y1:
            parts.append((ix0, hy1, ix1, y1))
    return [part for part in parts if area(part) > 0]


def read_boxes(rows, nonoverlap=False):
    result = {}
    for key, row in sorted(unique(rows).items()):
        box = tuple(number(v, 'coordinate', minimum=None) for v in row['box_m'])
        require(len(box) == 4 and box[0] < box[2] and box[1] < box[3], f'invalid rectangle: {key}')
        if nonoverlap:
            require(not any(overlap(box, b) for b in result.values()), f'overlapping grid cell: {key}')
        result[key] = box
    return result
