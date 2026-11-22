from .common import number, require, unique


def overlap(a, b):
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0 and dy > 0 else 0


def contains(outer, inner):
    return (outer[0] <= inner[0] and outer[2] >= inner[2]
            and outer[1] <= inner[1] and outer[3] >= inner[3])


def area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def read_boxes(rows, nonoverlap=False):
    result = {}
    for key, row in sorted(unique(rows).items()):
        box = tuple(number(v, 'coordinate', minimum=None) for v in row['box_m'])
        require(len(box) == 4 and box[0] < box[2] and box[1] < box[3], f'invalid rectangle: {key}')
        if nonoverlap:
            require(not any(overlap(box, b) for b in result.values()), f'overlapping grid cell: {key}')
        result[key] = box
    return result
