"""Single-pass reduction over a no-rewind stream of non-overlapping spatial bands.

Targets (a mutually non-overlapping grid plus mutually overlapping basins) are
known up front. Source cells arrive band by band; every band declares the sweep
axis coordinate up to which the region is fully covered (``end``). A band that
has been read can never be revisited, so each target may only be finalized once
its own reach along the sweep axis cannot meet any later band.
"""
from .basins import BasinTarget
from .grid import GridTarget

Y_AXIS = 1


class Band:
    """One delivery: the sweep-axis span, its closing boundary, and its cells."""

    __slots__ = ('span', 'end', 'cells')

    def __init__(self, span, end, cells):
        self.span = span
        self.end = end
        self.cells = cells

    def __iter__(self):
        return iter(self.cells)


def bands(source, axis=Y_AXIS):
    """Group non-overlapping source cells into ordered, non-overlapping bands.

    Cells whose intervals meet on the sweep axis join the same band; a gap
    closes the current band. Bands never overlap and are returned in delivery
    order, each carrying the boundary (its high sweep coordinate) beyond which
    no cell it contains can contribute.
    """
    ordered = sorted(source, key=lambda item: item[1][axis])
    groups, current, low, high = [], [], None, None
    for item in ordered:
        start = item[1][axis]
        stop = item[1][axis + 2]
        if current and high <= start:
            groups.append(Band((low, high), high, current))
            current, low = [], start
        if not current:
            low = start
        current.append(item)
        high = max(high if high is not None else stop, stop)
    if current:
        groups.append(Band((low, high), high, current))
    return groups


class BandFeed:
    """Delivers bands once, in order; reading past the end is an error.

    ``read_cells`` exposes every source cell handed out so tests can observe
    read progress without rewinding.
    """

    def __init__(self, source, axis=Y_AXIS):
        self._bands = bands(source, axis)
        self.axis = axis
        self.read_cells = []
        self.position = 0

    def __len__(self):
        return len(self._bands)

    @property
    def exhausted(self):
        return self.position >= len(self._bands)

    def read_band(self):
        if self.exhausted:
            raise EOFError('band stream is exhausted; bands cannot be rewound')
        band = self._bands[self.position]
        self.position += 1
        self.read_cells.extend(cell[0] for cell in band.cells)
        return band


class BandedReduction:
    """Drives both target families over one shared band stream.

    Targets whose sweep-axis reach is covered by the latest band boundary are
    finalized immediately. Single-target readiness never publishes the batch:
    callers collect the per-band readiness and publish only what they choose.
    """

    def __init__(self, target_boxes, basin_boxes, axis=Y_AXIS):
        self.axis = axis
        self.grids = [GridTarget(key, box) for key, box in target_boxes.items()]
        self.basins = [BasinTarget(key, box) for key, box in basin_boxes.items()]
        self._pending_grids = list(self.grids)
        self._pending_basins = list(self.basins)
        self.frontier = None

    def ingest(self, band):
        for source_cell in band.cells:
            for target in self._pending_grids:
                target.accept(source_cell)
            for basin in self._pending_basins:
                basin.accept(source_cell)
        self.frontier = band.end if self.frontier is None else max(self.frontier, band.end)
        return self._close(self._pending_grids), self._close(self._pending_basins)

    def finish(self):
        """Finalize every target still waiting when the stream ends."""
        grids = [target.finalize() for target in self._pending_grids]
        basin_rows = [basin.finalize() for basin in self._pending_basins]
        self._pending_grids, self._pending_basins = [], []
        return grids, basin_rows

    def _close(self, pending):
        ready, waiting = [], []
        for target in pending:
            if target.box[self.axis + 2] <= self.frontier:
                ready.append(target.finalize())
            else:
                waiting.append(target)
        pending[:] = waiting
        return ready


def reduce_banded(source, target_boxes, basin_boxes, axis=Y_AXIS):
    """Whole-batch convenience driver preserving the legacy materialized order."""
    feed = BandFeed(source, axis)
    reduction = BandedReduction(target_boxes, basin_boxes, axis)
    grid_rows, basin_rows = [], []
    while not feed.exhausted:
        ready_grids, ready_basins = reduction.ingest(feed.read_band())
        grid_rows.extend(ready_grids)
        basin_rows.extend(ready_basins)
    tail_grids, tail_basins = reduction.finish()
    grid_rows.extend(tail_grids)
    basin_rows.extend(tail_basins)
    grid_rows.sort(key=lambda row: row['id'])
    basin_rows.sort(key=lambda row: row['id'])
    return grid_rows, basin_rows
