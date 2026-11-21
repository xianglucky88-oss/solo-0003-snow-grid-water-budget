"""Legacy contracts: exact golden outputs, wrapper equivalence, rejection set."""
import json
import unittest
from pathlib import Path

from snowbudget.basins import report
from snowbudget.common import decode, encode
from snowbudget.field import cells
from snowbudget.geometry import read_boxes
from snowbudget.grid import regrid
from snowbudget.streaming import BandFeed, BandedReduction, reduce_banded
from snowbudget.workflow import materialize

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / 'examples'
GOLDEN = Path(__file__).parent / 'golden'


def bundle(name):
    return decode((EXAMPLES / name).read_text(encoding='utf-8'))


def parsed(doc):
    return (cells(doc['source_cells']), read_boxes(doc['targets'], nonoverlap=True),
            read_boxes(doc['basins']))


class GoldenOutputTest(unittest.TestCase):
    def test_baseline_output_is_byte_identical_to_pre_refactor_contract(self):
        text = encode(materialize(bundle('baseline.json')))
        self.assertEqual(text, (GOLDEN / 'baseline.result.json').read_text())

    def test_changed_output_is_byte_identical_to_pre_refactor_contract(self):
        text = encode(materialize(bundle('changed.json')))
        self.assertEqual(text, (GOLDEN / 'changed.result.json').read_text())

    def test_top_level_fields_and_row_shapes_are_unchanged(self):
        raw = encode(materialize(bundle('baseline.json')))
        golden = (GOLDEN / 'baseline.result.json').read_text()
        # Serialization (including member order) is covered byte-for-byte above;
        # here we pin the field sets of every row type.
        self.assertEqual(set(json.loads(raw).keys()), {'survey', 'target_grid', 'basins'})
        for row in json.loads(raw)['target_grid']:
            self.assertEqual(set(row),
                             {'box_m', 'coverage', 'id', 'source_cells', 'valid_m2', 'valid_mean_mm', 'water_m3'})
        for row in json.loads(raw)['basins']:
            self.assertEqual(set(row), {'area_m2', 'contributions', 'id', 'missing_cells',
                                        'missing_in_grid_m2', 'outside_grid_m2', 'valid_m2',
                                        'valid_mean_mm', 'water_m3'})
        self.assertEqual(raw, golden)


class WrapperEquivalenceTest(unittest.TestCase):
    def _check(self, name):
        doc = bundle(name)
        source, targets, basin_boxes = parsed(doc)
        streamed_grids, streamed_basins = reduce_banded(source, targets, basin_boxes)

        legacy_grids = list(regrid(source, targets))
        legacy_basins = list(report(source, basin_boxes))
        self.assertEqual(encode(legacy_grids), encode(streamed_grids))
        self.assertEqual(encode(legacy_basins), encode(streamed_basins))

    def test_baseline_and_changed_legacy_generators_match_streaming(self):
        self._check('baseline.json')
        self._check('changed.json')

    def test_streaming_matches_on_the_overlapping_basin_fixture(self):
        doc = decode((Path(__file__).parent / 'fixtures' / 'streaming.json').read_text(encoding='utf-8'))
        source, targets, basin_boxes = parsed(doc)
        grids, basin_rows = reduce_banded(source, targets, basin_boxes)
        self.assertEqual(encode(list(regrid(source, targets))), encode(grids))
        self.assertEqual(encode(list(report(source, basin_boxes))), encode(basin_rows))


class StreamLifecycleTest(unittest.TestCase):
    def test_target_past_source_extent_settles_only_at_finish(self):
        doc = bundle('baseline.json')
        source, targets, basin_boxes = parsed(doc)
        feed = BandFeed(source)
        reduction = BandedReduction(targets, basin_boxes)
        while not feed.exhausted:
            reduction.ingest(feed.read_band())
        self.assertEqual(reduction._pending_grids, [])
        self.assertEqual(reduction._pending_basins, [])
        self.assertEqual(reduction.finish(), ([], []))

    def test_finish_closes_targets_whose_reach_escapes_every_band(self):
        doc = bundle('baseline.json')
        source, targets, basin_boxes = parsed(doc)
        targets['far'] = (10, 10, 12, 12)
        basin_boxes['far-basin'] = (10, 10, 12, 12)
        feed = BandFeed(source)
        reduction = BandedReduction(targets, basin_boxes)
        while not feed.exhausted:
            grids, basin_rows = reduction.ingest(feed.read_band())
            self.assertNotIn('far', [r['id'] for r in grids])
            self.assertNotIn('far-basin', [r['id'] for r in basin_rows])
        self.assertEqual([t.id for t in reduction._pending_grids], ['far'])
        self.assertEqual([b.id for b in reduction._pending_basins], ['far-basin'])
        tail_grids, tail_basins = reduction.finish()
        far_grid, far_basin = tail_grids[0], tail_basins[0]
        self.assertEqual(far_grid['id'], 'far')
        self.assertIsNone(far_grid['water_m3'])
        self.assertEqual(far_grid['coverage'], 0)
        self.assertEqual(far_basin['id'], 'far-basin')
        self.assertIsNone(far_basin['water_m3'])
        self.assertEqual(far_basin['outside_grid_m2'], 4)

    def test_gap_in_source_cells_creates_three_bands(self):
        source = [('a', (0, 0, 1, 1), 100), ('b', (0, 2, 1, 3), 100), ('c', (0, 4, 1, 5), 100)]
        feed = BandFeed(source)
        self.assertEqual(len(feed), 3)
        self.assertEqual([b.end for b in [feed.read_band(), feed.read_band(), feed.read_band()]], [1, 3, 5])
        self.assertTrue(feed.exhausted)


class SemanticMatrixTest(unittest.TestCase):
    """The three zero-ish conditions stay distinguishable in both old contracts."""

    def setUp(self):
        self.result = materialize(bundle('baseline.json'))
        self.grids = {row['id']: row for row in self.result['target_grid']}
        self.basins = {row['id']: row for row in self.result['basins']}

    def test_true_measured_zero_is_zero_not_null(self):
        dry = self.basins['dry']
        self.assertEqual(dry['valid_m2'], 4)
        self.assertEqual(dry['water_m3'], 0)
        self.assertEqual(dry['valid_mean_mm'], 0)
        self.assertEqual(dry['contributions'], [{'cell': 'z', 'm2': 4, 'water_m3': 0}])

    def test_in_grid_missing_is_null_with_named_hole(self):
        miss = self.basins['miss']
        self.assertEqual(miss['valid_m2'], 0)
        self.assertIsNone(miss['water_m3'])
        self.assertIsNone(miss['valid_mean_mm'])
        self.assertEqual(miss['missing_in_grid_m2'], 4)
        self.assertEqual(miss['outside_grid_m2'], 0)
        self.assertEqual(miss['missing_cells'], ['m'])
        self.assertEqual(miss['contributions'], [])

    def test_outside_grid_target_is_null_with_zero_coverage(self):
        outside = self.grids['outside']
        self.assertEqual(outside['valid_m2'], 0)
        self.assertIsNone(outside['water_m3'])
        self.assertIsNone(outside['valid_mean_mm'])
        self.assertEqual(outside['coverage'], 0)
        self.assertEqual(outside['source_cells'], [])

    def test_basin_outside_area_is_tracked_separately(self):
        whole = self.basins['all']
        self.assertEqual(whole['area_m2'], 20)
        self.assertEqual(whole['valid_m2'], 12)
        self.assertEqual(whole['missing_in_grid_m2'], 4)
        self.assertEqual(whole['outside_grid_m2'], 4)
        self.assertEqual(whole['water_m3'], 3)

    def test_null_swe_is_never_treated_as_zero_in_grid_means(self):
        north = self.grids['north']
        # Only cell v (500 mm) counts; missing cell m is excluded from measured area.
        self.assertEqual(north['valid_m2'], 4)
        self.assertEqual(north['water_m3'], 2)
        self.assertEqual(north['valid_mean_mm'], 500)
        self.assertEqual(north['coverage'], '1/2')


class RejectionTest(unittest.TestCase):
    def test_shipped_invalid_bundle_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'overlapping grid cell: z'):
            materialize(bundle('invalid.json'))

    def test_overlapping_targets_are_rejected(self):
        doc = bundle('baseline.json')
        doc['targets'].append({'id': 'dup-geo', 'box_m': [0, 0, 1, 1]})
        with self.assertRaisesRegex(ValueError, 'overlapping grid cell'):
            materialize(doc)

    def test_duplicate_ids_are_rejected(self):
        doc = bundle('baseline.json')
        doc['source_cells'].append(dict(doc['source_cells'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate id'):
            materialize(doc)

    def test_negative_swe_is_rejected(self):
        doc = bundle('baseline.json')
        doc['source_cells'][0]['swe_mm'] = -1
        with self.assertRaisesRegex(ValueError, 'below minimum'):
            materialize(doc)

    def test_non_exact_numeric_input_is_rejected(self):
        doc = bundle('baseline.json')
        doc['source_cells'][0]['swe_mm'] = 1.5
        with self.assertRaisesRegex(ValueError, 'integer or exact numeric string'):
            materialize(doc)

    def test_malformed_rectangle_is_rejected(self):
        doc = bundle('baseline.json')
        doc['basins'][0]['box_m'] = [0, 0, 0, 4]
        with self.assertRaisesRegex(ValueError, 'invalid rectangle'):
            materialize(doc)

    def test_nan_constant_in_json_is_rejected(self):
        text = (EXAMPLES / 'baseline.json').read_text().replace('250', 'NaN', 1)
        with self.assertRaisesRegex(ValueError, 'invalid JSON constant'):
            decode(text)

    def test_duplicate_json_member_is_rejected(self):
        text = (EXAMPLES / 'baseline.json').read_text().replace('"survey"', '"survey"', 1)
        tampered = '{"survey":"x","survey":"y",' + text[text.index('"source_cells"'):]
        with self.assertRaisesRegex(ValueError, 'duplicate JSON member'):
            decode(tampered)

    def test_missing_required_field_is_rejected(self):
        doc = bundle('baseline.json')
        del doc['basins']
        with self.assertRaises(LookupError):
            materialize(doc)


if __name__ == '__main__':
    unittest.main()
