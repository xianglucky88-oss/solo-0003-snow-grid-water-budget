"""Observable read-progress checks for the shared band stream."""
import unittest
from fractions import Fraction as F
from pathlib import Path

from snowbudget.common import decode, encode
from snowbudget.field import cells
from snowbudget.geometry import read_boxes
from snowbudget.streaming import BandFeed, BandedReduction, bands, reduce_banded
from snowbudget.workflow import materialize

FIXTURE = Path(__file__).parent / 'fixtures' / 'streaming.json'


def load_fixture():
    doc = decode(FIXTURE.read_text(encoding='utf-8'))
    return doc, cells(doc['source_cells']), read_boxes(doc['targets'], nonoverlap=True), read_boxes(doc['basins'])


def by_id(rows):
    return {row['id']: row for row in rows}


class BandStructureTest(unittest.TestCase):
    def setUp(self):
        _, source, _, _ = load_fixture()
        self.source = source

    def test_bands_are_ordered_non_overlapping_spatial_strips(self):
        strips = bands(self.source)
        self.assertEqual([(band.span, band.end) for band in strips],
                         [((F(0), F(2)), F(2)), ((F(2), F(4)), F(4))])
        self.assertEqual([[cell[0] for cell in band] for band in strips], [['s0a', 's0b'], ['s1n', 's1v']])
        earlier = None
        for band in strips:
            if earlier is not None:
                self.assertLessEqual(earlier.end, band.span[0])
            earlier = band

    def test_band_end_closes_its_span(self):
        for band in bands(self.source):
            self.assertEqual(band.end, band.span[1])


class ReadProgressTest(unittest.TestCase):
    def setUp(self):
        _, source, targets, basins = load_fixture()
        self.feed = BandFeed(source)
        self.reduction = BandedReduction(targets, basins)

    def test_no_rewind_and_observable_progress(self):
        self.assertEqual(self.feed.read_cells, [])
        first = self.feed.read_band()
        self.assertEqual(first.end, 2)
        self.assertEqual(self.feed.read_cells, ['s0a', 's0b'])
        self.assertEqual(self.feed.position, 1)
        self.assertFalse(self.feed.exhausted)

        tail = self.feed.read_band()
        self.assertEqual(tail.end, 4)
        self.assertEqual(self.feed.read_cells, ['s0a', 's0b', 's1n', 's1v'])
        self.assertTrue(self.feed.exhausted)
        with self.assertRaises(EOFError):
            self.feed.read_band()

    def test_one_grid_and_one_basin_finalize_while_tail_band_is_unread(self):
        first = self.feed.read_band()
        ready_grids, ready_basins = self.reduction.ingest(first)

        # Exactly one target of each family may be finalized before the tail band is read.
        self.assertEqual([row['id'] for row in ready_grids], ['early'])
        self.assertEqual([row['id'] for row in ready_basins], ['dry'])
        self.assertEqual(self.feed.read_cells, ['s0a', 's0b'])

        # Targets still exposed to tail contributions must not be handed out early.
        self.assertEqual(sorted(t.id for t in self.reduction._pending_grids), ['beyond', 'cross'])
        self.assertEqual(sorted(b.id for b in self.reduction._pending_basins), ['miss', 'wide-a', 'wide-b'])

    def test_cross_band_results_are_completed_by_later_bands(self):
        ready_grids, ready_basins = self.reduction.ingest(self.feed.read_band())
        self.assertEqual([r['id'] for r in ready_grids], ['early'])
        self.assertEqual([r['id'] for r in ready_basins], ['dry'])

        ready_grids, ready_basins = self.reduction.ingest(self.feed.read_band())
        self.assertEqual(sorted(r['id'] for r in ready_grids), ['beyond', 'cross'])
        self.assertEqual(sorted(r['id'] for r in ready_basins), ['miss', 'wide-a', 'wide-b'])
        self.assertTrue(self.feed.exhausted)

        # Nothing remains waiting once the final band boundary covers every reach.
        tail_grids, tail_basins = self.reduction.finish()
        self.assertEqual(tail_grids, [])
        self.assertEqual(tail_basins, [])

    def test_single_target_readiness_does_not_publish_the_batch(self):
        doc, source, targets, basins = load_fixture()
        feed = BandFeed(source)
        reduction = BandedReduction(targets, basins)
        collected_grids, collected_basins = [], []
        while not feed.exhausted:
            grids, basin_rows = reduction.ingest(feed.read_band())
            collected_grids.extend(grids)
            collected_basins.extend(basin_rows)
        tail_grids, tail_basins = reduction.finish()
        collected_grids.extend(tail_grids)
        collected_basins.extend(tail_basins)
        collected_grids.sort(key=lambda row: row['id'])
        collected_basins.sort(key=lambda row: row['id'])

        full_grids, full_basins = reduce_banded(source, targets, basins)
        self.assertEqual(encode(collected_grids), encode(full_grids))
        self.assertEqual(encode(collected_basins), encode(full_basins))

        # The JSON entry keeps identical fields, ordering and exact values.
        self.assertEqual(materialize(doc), {'survey': 'band-stream', 'target_grid': full_grids, 'basins': full_basins})


class StreamedValuesTest(unittest.TestCase):
    def setUp(self):
        doc, source, targets, basins = load_fixture()
        self.grid_rows, self.basin_rows = reduce_banded(source, targets, basins)
        self.grids, self.basins = by_id(self.grid_rows), by_id(self.basin_rows)
        self.doc = doc

    def test_early_grid_value_is_settled_from_band_one(self):
        early = self.grids['early']
        self.assertEqual(early['valid_m2'], 8)
        self.assertEqual(early['water_m3'], 1)
        self.assertEqual(early['valid_mean_mm'], 125)
        self.assertEqual(early['coverage'], 1)
        self.assertEqual(early['source_cells'], ['s0a', 's0b'])

    def test_cross_band_grid_accumulates_tail_band_only_after_waiting(self):
        cross = self.grids['cross']
        # Entirely inside band two; no contribution exists until that band is read.
        self.assertEqual(cross['valid_m2'], '1/2')
        self.assertEqual(cross['water_m3'], '1/4')
        self.assertEqual(cross['valid_mean_mm'], 500)
        self.assertEqual(cross['coverage'], '1/4')
        self.assertEqual(cross['source_cells'], ['s1v'])

    def test_outside_grid_keeps_null_contract(self):
        beyond = self.grids['beyond']
        self.assertEqual(beyond['valid_m2'], 0)
        self.assertIsNone(beyond['water_m3'])
        self.assertIsNone(beyond['valid_mean_mm'])
        self.assertEqual(beyond['coverage'], 0)
        self.assertEqual(beyond['source_cells'], [])

    def test_true_zero_is_distinct_from_missing(self):
        dry = self.basins['dry']
        self.assertEqual(dry['valid_m2'], 4)
        self.assertEqual(dry['water_m3'], 0)          # real measured zero, not null
        self.assertEqual(dry['valid_mean_mm'], 0)
        self.assertEqual(dry['missing_in_grid_m2'], 0)
        self.assertEqual(dry['outside_grid_m2'], 0)
        self.assertEqual(dry['contributions'], [{'cell': 's0b', 'm2': 4, 'water_m3': 0}])

        miss = self.basins['miss']
        self.assertEqual(miss['valid_m2'], 0)
        self.assertIsNone(miss['water_m3'])           # in-grid missing cell -> null, not zero
        self.assertIsNone(miss['valid_mean_mm'])
        self.assertEqual(miss['missing_in_grid_m2'], 4)
        self.assertEqual(miss['outside_grid_m2'], 0)
        self.assertEqual(miss['missing_cells'], ['s1n'])
        self.assertEqual(miss['contributions'], [])

    def test_overlapping_basins_keep_independent_provenance(self):
        a, b = self.basins['wide-a'], self.basins['wide-b']

        self.assertEqual(a['area_m2'], 6)
        self.assertEqual(a['valid_m2'], 4)
        self.assertEqual(a['water_m3'], 1)
        self.assertEqual(a['valid_mean_mm'], 250)
        self.assertEqual(a['missing_in_grid_m2'], 2)
        self.assertEqual(a['outside_grid_m2'], 0)
        self.assertEqual([c['cell'] for c in a['contributions']], ['s0a', 's0b', 's1v'])
        self.assertEqual({c['cell']: c for c in a['contributions']}['s0a'], {'cell': 's0a', 'm2': 2, 'water_m3': '1/2'})
        self.assertEqual({c['cell']: c for c in a['contributions']}['s0b'], {'cell': 's0b', 'm2': 1, 'water_m3': 0})
        self.assertEqual({c['cell']: c for c in a['contributions']}['s1v'], {'cell': 's1v', 'm2': 1, 'water_m3': '1/2'})
        self.assertEqual(a['missing_cells'], ['s1n'])

        self.assertEqual(b['area_m2'], 6)
        self.assertEqual(b['valid_m2'], 5)
        self.assertEqual(b['water_m3'], '5/4')
        self.assertEqual(b['valid_mean_mm'], 250)
        self.assertEqual(b['missing_in_grid_m2'], 1)
        self.assertEqual(b['outside_grid_m2'], 0)
        self.assertEqual([c['cell'] for c in b['contributions']], ['s0a', 's0b', 's1v'])
        # Shared overlap is counted per basin, and the s0a/s0b sections differ from wide-a.
        self.assertEqual({c['cell']: c for c in b['contributions']}['s0a'], {'cell': 's0a', 'm2': 1, 'water_m3': '1/4'})
        self.assertEqual({c['cell']: c for c in b['contributions']}['s0b'], {'cell': 's0b', 'm2': 2, 'water_m3': 0})
        self.assertEqual({c['cell']: c for c in b['contributions']}['s1v'], {'cell': 's1v', 'm2': 2, 'water_m3': 1})
        self.assertEqual(b['missing_cells'], ['s1n'])

    def test_outside_grid_area_is_separate_from_in_grid_missing(self):
        for basin in self.basins.values():
            nominal = F(str(basin['area_m2']))
            valid = F(str(basin['valid_m2']))
            missing = F(str(basin['missing_in_grid_m2']))
            outside = F(str(basin['outside_grid_m2']))
            self.assertEqual(nominal, valid + missing + outside)
        # wide-b reaches x=4 inside the band region: no outside area; miss likewise fully inside.
        self.assertEqual(self.basins['wide-b']['outside_grid_m2'], 0)
        self.assertEqual(self.basins['miss']['outside_grid_m2'], 0)

    def test_exact_fraction_encoding_survives(self):
        # Non-integer coordinates stay exact strings; rational water stays an exact string.
        self.assertEqual(self.doc['targets'][1]['box_m'][0], '1/2')
        self.assertEqual(self.grids['cross']['box_m'], ['1/2', 2, '5/2', 3])
        self.assertEqual(self.grids['cross']['water_m3'], '1/4')


if __name__ == '__main__':
    unittest.main()
