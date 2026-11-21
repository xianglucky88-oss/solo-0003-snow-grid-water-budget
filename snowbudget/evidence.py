"""Exact evidence accounting for source cells, occlusions and remeasurement patches.

Every product box is partitioned exactly once, at each source-cell, occlusion and
patch boundary.  Each atomic rectangle therefore belongs to exactly one original
cell and is covered or not by each patch, so an area is never filled, missed or
deducted twice, whatever the patches do.

Buckets stay separate everywhere:

* ``original`` -- original value still usable;
* ``patch``    -- filled by patch evidence where the original is unusable;
* ``missing``  -- in-grid area whose original is unusable and unpatched;
* ``disputed`` -- unusable area covered by patches with conflicting values;
* ``unlocated``-- area an occlusion fraction implies but cannot place;
* ``outside``  -- area beyond the source grid.

A patch is used only on unusable ground.  Over valid original ground or outside
the grid its footprint is reported as ignored, never added and never subtracted.
"""
from dataclasses import dataclass, field
from fractions import Fraction

from .geometry import area, overlap


def contains(outer, inner):
    """True when ``inner`` lies wholly inside ``outer`` (touching edges allowed)."""
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


@dataclass
class Breakdown:
    original_area: Fraction = Fraction(0)
    patch_area: Fraction = Fraction(0)
    missing_area: Fraction = Fraction(0)
    disputed_area: Fraction = Fraction(0)
    unlocated_area: Fraction = Fraction(0)
    outside_area: Fraction = Fraction(0)
    ignored_patch_area: Fraction = Fraction(0)
    original_water: Fraction = Fraction(0)
    patch_water: Fraction = Fraction(0)
    cells: dict = field(default_factory=dict)           # cell -> [area, water] from the original
    patch_evidence: dict = field(default_factory=dict)   # patch -> [area, water] actually used
    missing_cells: set = field(default_factory=set)
    filled_cells: set = field(default_factory=set)
    disputed_cells: set = field(default_factory=set)
    unlocated_cells: set = field(default_factory=set)
    disputed_patches: set = field(default_factory=set)

    @property
    def valid_area(self):
        return self.original_area + self.patch_area

    @property
    def water(self):
        return self.original_water + self.patch_water


def _state(cell):
    """How a cell's original evidence can be used.

    ``valid``     -- fully usable ground;
    ``located``   -- usable ground with exactly located unusable rectangles;
    ``unusable``  -- the whole cell's original value is unusable;
    ``unlocated`` -- a bare fraction of the cell is unusable, location unknown.
    """
    if cell.swe is None:
        return 'unusable'
    fraction = cell.occluded_fraction
    if fraction is None or fraction == 0:
        return 'located' if cell.occlusions else 'valid'
    if fraction == 1:
        return 'unusable'
    return 'unlocated'


def _axis_bounds(cells, patches, box):
    xs, ys = {box[0], box[2]}, {box[1], box[3]}

    def add(rect):
        if box[0] < rect[0] < box[2]:
            xs.add(rect[0])
        if box[0] < rect[2] < box[2]:
            xs.add(rect[2])
        if box[1] < rect[1] < box[3]:
            ys.add(rect[1])
        if box[1] < rect[3] < box[3]:
            ys.add(rect[3])

    for cell in cells:
        if overlap(cell.box, box) > 0:
            add(cell.box)
            for occluded in cell.occlusions:
                add(occluded)
    for patch in patches:
        if overlap(patch.box, box) > 0:
            add(patch.box)
    return sorted(xs), sorted(ys)


def _owner(cells, rect):
    for cell in cells:
        if contains(cell.box, rect):
            return cell
    return None


def _unusable(rect, cell, state):
    return state == 'unusable' or any(contains(occluded, rect) for occluded in cell.occlusions)


def _union_covered(patches, box):
    """Area of ``box`` covered by at least one patch, counted once."""
    if not patches:
        return Fraction(0)
    xs, ys = _axis_bounds((), patches, box)
    total = Fraction(0)
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            rect = (x0, y0, x1, y1)
            if contains(box, rect) and any(contains(patch.box, rect) for patch in patches):
                total += area(rect)
    return total


def _credit(table, key, gross_area, water):
    entry = table.setdefault(key, [Fraction(0), Fraction(0)])
    entry[0] += gross_area
    entry[1] += water


def evaluate(cells, patches, box):
    """Decompose one target or basin box into exact, disjoint evidence buckets."""
    parts = Breakdown()
    states = {id(cell): _state(cell) for cell in cells}
    xs, ys = _axis_bounds(cells, patches, box)
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            rect = (x0, y0, x1, y1)
            if not contains(box, rect):
                continue
            share = area(rect)
            if share <= 0:
                continue
            covering = sorted((patch for patch in patches if contains(patch.box, rect)),
                              key=lambda patch: patch.id)
            cell = _owner(cells, rect)
            if cell is None:
                parts.outside_area += share
                continue
            state = states[id(cell)]
            if state == 'unlocated':
                # A bare fraction has no coordinates; _settle_fraction handles
                # this cell once per product, so no atom may claim its ground.
                continue
            if not _unusable(rect, cell, state):
                parts.original_area += share
                volume = share * cell.swe / 1000
                parts.original_water += volume
                _credit(parts.cells, cell.id, share, volume)
                continue
            values = {patch.swe for patch in covering}
            if not values:
                parts.missing_area += share
                parts.missing_cells.add(cell.id)
            elif len(values) == 1:
                value = next(iter(values))
                volume = share * value / 1000
                parts.patch_area += share
                parts.patch_water += volume
                parts.filled_cells.add(cell.id)
                # One deterministic owner per atom so patch contributions still
                # sum to the filled area when independent patches agree.
                _credit(parts.patch_evidence, next(p for p in covering if p.swe == value).id, share, volume)
            else:
                # Conflicting independent remeasurements: the shared rectangle
                # is disputed exactly once -- not once per patch, not missing.
                parts.disputed_area += share
                parts.disputed_cells.add(cell.id)
                parts.disputed_patches.update(patch.id for patch in covering)
    for cell in cells:
        if states[id(cell)] == 'unlocated' and overlap(cell.box, box) > 0:
            _settle_fraction(parts, cell, patches, box)
    # Every patch footprint inside this product is either used to fill unusable
    # ground, held in dispute, or unused -- counted once even where patches
    # overlap, so a shared patch area can never be deducted twice.
    parts.ignored_patch_area = max(
        Fraction(0),
        _union_covered(patches, box) - parts.patch_area - parts.disputed_area)
    return parts


def _settle_fraction(parts, cell, patches, box):
    """Settle a bare occluded fraction only where it is truly located.

    The occlusion's position inside the cell is unknown.  Only a product that
    fully contains the cell fixes ``fraction * cell area`` of unusable ground
    inside itself; a product that merely clips the cell cannot say how much of
    the occlusion it holds, so its entire overlap with the cell is reported as
    unlocated.  No share of the fraction is ever projected onto a clipping
    basin, and the pieces of a split cell are never added back up as if the
    position were known.
    """
    shared = overlap(cell.box, box)
    if not contains(box, cell.box):
        parts.unlocated_area += shared
        parts.unlocated_cells.add(cell.id)
        return
    total = area(cell.box)
    occluded = cell.occluded_fraction * total
    usable = total - occluded
    if usable:
        volume = usable * cell.swe / 1000
        parts.original_area += usable
        parts.original_water += volume
        _credit(parts.cells, cell.id, usable, volume)
    if not occluded:
        return
    # An unplaced occlusion can only be certainly repaired by a patch that
    # holds the entire cell; a partial patch cannot prove it reaches it.
    whole = sorted((patch for patch in patches if contains(patch.box, cell.box)), key=lambda p: p.id)
    values = {patch.swe for patch in whole}
    if not values:
        parts.missing_area += occluded
        parts.missing_cells.add(cell.id)
    elif len(values) == 1:
        value = next(iter(values))
        volume = occluded * value / 1000
        parts.patch_area += occluded
        parts.patch_water += volume
        parts.filled_cells.add(cell.id)
        _credit(parts.patch_evidence, next(p for p in whole if p.swe == value).id, occluded, volume)
    else:
        parts.disputed_area += occluded
        parts.disputed_cells.add(cell.id)
        parts.disputed_patches.update(patch.id for patch in whole)