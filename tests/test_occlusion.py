"""Contract tests for occlusion-aware, patch-filling reduction.

Covers the task rules:
* coverage and water follow actually evidenced space;
* patches only fill ground whose original value is unusable;
* overlapping patches never overlap-count, and conflicting ones leave a
  disputed bucket instead of a guess;
* a percentage without position is never projected onto basins;
* two source cells with equal occlusion area but different patch/basin cuts
  are validated side by side;
* the legacy whole-cell bundles keep byte-identical output.
"""
import io
import contextlib
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from snowbudget.basins import report
from snowbudget.cli import main
from snowbudget.common import decode, encode
from snowbudget.evidence import evaluate
from snowbudget.field import cells, patches
from snowbudget.geometry import read_boxes
from snowbudget.grid import regrid
from snowbudget.workflow import materialize

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / 'examples'
GOLDEN = Path(__file__).parent / 'golden'


def bundle(name):
    return decode((EXAMPLES / name).read_text(encoding='utf-8'))


def result(doc):
    return materialize(doc)


def exact(value):
    """Rebuild the exact rational a serialized scalar stands for."""
    return None if value is None else Fraction(value)


def by_id(rows):
    return {row['id']: row for row in rows}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue().strip(), err.getvalue().strip()


class LegacyContractTest(unittest.TestCase):
    """Old whole-cell bundles must not change at all."""

    def test_baseline_and_changed_are_byte_identical_goldens(self):
        for name in ('baseline', 'changed'):
            text = encode(result(bundle(f'{name}.json')))
            self.assertEqual(text, (GOLDEN / f'{name}.result.json').read_text())

    def test_occlusion_bundle_is_a_pinned_golden(self):
        text = encode(result(bundle('occlusion.json')))
        self.assertEqual(text, (GOLDEN / 'occlusion.result.json').read_text())

    def test_legacy_rows_keep_exact_field_sets(self):
        doc = result(bundle('baseline.json'))
        for row in doc['target_grid']:
            self.assertEqual(set(row), {'box_m', 'coverage', 'id', 'source_cells',
                                        'valid_m2', 'valid_mean_mm', 'water_m3'})
        for row in doc['basins']:
            self.assertEqual(set(row), {'area_m2', 'contributions', 'id', 'missing_cells',
                                        'missing_in_grid_m2', 'outside_grid_m2', 'valid_m2',
                                        'valid_mean_mm', 'water_m3'})

    def test_legacy_generators_match_materialize(self):
        doc = bundle('changed.json')
        source = cells(doc['source_cells'])
        self.assertEqual(encode(list(regrid(source, read_boxes(doc['targets'], nonoverlap=True)))),
                         encode(materialize(doc)['target_grid']))
        self.assertEqual(encode(list(report(source, read_boxes(doc['basins'])))),
                         encode(materialize(doc)['basins']))

    def test_zero_null_distinction_survives(self):
        rows = by_id(result(bundle('baseline.json'))['basins'])
        self.assertEqual((rows['dry']['water_m3'], rows['dry']['valid_mean_mm']), (0, 0))
        self.assertIsNone(rows['miss']['water_m3'])
        self.assertIsNone(rows['miss']['valid_mean_mm'])

    def test_invalid_bundle_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'overlapping grid cell: z'):
            materialize(bundle('invalid.json'))


class EqualOcclusionContrastTest(unittest.TestCase):
    """Cells a and b each lose exactly 1 m2; only the cut positions differ.

    Cell ``a``'s occlusion sits in the *patch* and *grid* but a basin boundary
    at x=1/2 splits it.  Cell ``b``'s occlusion is cut by the *patch* edge at
    x=5/2 while its basin boundary at x=9/4 falls elsewhere.  Coverage must
    follow the exact overlap each product has with the evidence, never a
    proportional share of the cell.
    """

    def setUp(self):
        self.doc = result(bundle('occlusion.json'))
        self.grids = by_id(self.doc['target_grid'])
        self.basins = by_id(self.doc['basins'])

    def test_equal_occlusion_areas_in_both_cells(self):
        for cell in cells(bundle('occlusion.json')['source_cells']):
            if cell.id in ('a', 'b'):
                occluded = sum((b[2] - b[0]) * (b[3] - b[1]) for b in cell.occlusions)
                self.assertEqual(occluded, 1)

    def test_full_grid_cells_cover_all_evidenced_ground(self):
        # a: 3 m2 original + 1 m2 patch under the occlusion + 1 m2 patch pa
        #    over already-valid ground (ignored, not double counted).
        self.assertEqual(self.grids['a']['valid_m2'], 4)
        self.assertEqual(self.grids['a']['original_m2'], 3)
        self.assertEqual(self.grids['a']['patch_m2'], 1)
        self.assertEqual(self.grids['a']['unused_patch_m2'], 1)
        self.assertEqual(self.grids['a']['missing_m2'], 0)
        self.assertEqual(self.grids['a']['coverage'], 1)
        # b: patch pb1 covers x<5/2 of the occlusion; the rest is missing.
        self.assertEqual(self.grids['b']['original_m2'], 3)
        self.assertEqual(exact(self.grids['b']['patch_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.grids['b']['missing_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.grids['b']['coverage']), Fraction(7, 8))

    def test_basin_boundary_through_occlusion_differs_from_patch_boundary(self):
        # In a the basin boundary at x=1/2 splits the whole occlusion and the
        # whole-cell patch pa together: each basin sees half of both.
        self.assertEqual(exact(self.basins['a-west']['patch_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.basins['a-east']['patch_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.basins['a-west']['missing_in_grid_m2']), 0)
        self.assertEqual(exact(self.basins['a-east']['missing_in_grid_m2']), 0)
        # In b the patch edge (x=5/2) and the basin boundary (x=11/4) disagree:
        # b-west holds the patched half plus a sliver of bare hole, while
        # b-east holds only the unpatched tail. Patch evidence is never extended
        # past its own edge to cover the basin.
        self.assertEqual(exact(self.basins['b-west']['patch_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.basins['b-west']['missing_in_grid_m2']), Fraction(1, 4))
        self.assertEqual(exact(self.basins['b-east']['patch_m2']), 0)
        self.assertEqual(exact(self.basins['b-east']['missing_in_grid_m2']), Fraction(1, 4))
        # Equal occluded ground, different basin-visible evidence.
        self.assertNotEqual(exact(self.basins['a-east']['missing_in_grid_m2']),
                            exact(self.basins['b-east']['missing_in_grid_m2']))

    def test_basin_water_is_exact_fractions(self):
        self.assertEqual((self.basins['a-west']['valid_m2'], exact(self.basins['a-west']['water_m3'])),
                         (1, Fraction(3, 20)))
        self.assertEqual(exact(self.basins['b-west']['water_m3']), Fraction(9, 40))
        self.assertEqual(exact(self.basins['a-west']['water_m3']) + exact(self.basins['a-east']['water_m3']), Fraction(1, 2))
        self.assertEqual(exact(self.basins['b-west']['water_m3']) + exact(self.basins['b-east']['water_m3']),
                         Fraction(9, 20))
        # Basin pieces always add back to the whole cell's evidenced water.
        self.assertEqual(exact(self.grids['b']['water_m3']), Fraction(9, 20))


class PatchRulesTest(unittest.TestCase):
    def setUp(self):
        self.doc = result(bundle('occlusion.json'))
        self.grids = by_id(self.doc['target_grid'])
        self.basins = by_id(self.doc['basins'])

    def test_patch_over_valid_ground_is_ignored_not_added(self):
        # pa (200 mm) sits over cell a's still-valid left column; only the
        # occluded half is filled.  The valid half keeps the 100 mm original.
        self.assertEqual(exact(self.basins['a-west']['original_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.basins['a-west']['patch_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.basins['a-west']['unused_patch_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.basins['a-west']['valid_m2']), 1)

    def test_one_patch_straddling_occlusion_and_valid_ground_fills_only_the_hole(self):
        doc = {'survey': 'x',
               'source_cells': [{'id': 'a', 'box_m': [0, 0, 2, 2], 'swe_mm': 100,
                                 'occlusions': [{'box_m': [0, 0, 1, 2]}]}],
               'patches': [{'id': 'p', 'box_m': [0, 0, 2, 2], 'swe_mm': 200}],
               'targets': [{'id': 't', 'box_m': [0, 0, 2, 2]}],
               'basins': [{'id': 'b', 'box_m': [1, 0, 2, 2]}]}
        doc = materialize(doc)
        grid = doc['target_grid'][0]
        # 2 m2 of the patch repairs the occluded half; the other 2 m2 sits over
        # still-valid 100 mm ground and is reported unused, not substituted.
        self.assertEqual((exact(grid['original_m2']), exact(grid['patch_m2']), exact(grid['unused_patch_m2'])),
                         (2, 2, 2))
        self.assertEqual(exact(grid['water_m3']), Fraction(3, 5))
        # The valid-only basin sees the whole patch as unused.
        self.assertEqual(exact(doc['basins'][0]['patch_m2']), 0)
        self.assertEqual(exact(doc['basins'][0]['unused_patch_m2']), 2)

    def test_patch_outside_the_grid_fills_nothing(self):
        outside = self.grids['out']
        self.assertEqual(outside['valid_m2'], 0)
        self.assertIsNone(outside['water_m3'])
        self.assertEqual(outside['unused_patch_m2'], 1)
        self.assertEqual(self.basins['out']['outside_grid_m2'], 1)
        self.assertEqual(self.basins['out']['unused_patch_m2'], 1)

    def test_identical_patches_are_used_once(self):
        # pb1 and pb2 are byte-identical 300 mm patches over the same half-occlusion.
        self.assertEqual(exact(self.grids['b']['patch_m2']), Fraction(1, 2))
        self.assertEqual(exact(self.grids['b']['water_m3']), Fraction(9, 20))
        self.assertEqual(self.grids['b']['disputed_m2'], 0)
        # Only one owner is credited so patch evidence sums without duplication.
        self.assertEqual([(row['patch'], exact(row['m2']), exact(row['water_m3']))
                          for row in self.basins['b-west']['patch_contributions']],
                         [('pb1', Fraction(1, 2), Fraction(3, 20))])

    def test_conflicting_patches_leave_one_disputed_region(self):
        # pc1 (100 mm) covers the whole occlusion of c; pc2 (200 mm) covers its
        # right half.  That half is disputed once -- not twice, not counted as
        # filled by both.
        self.assertEqual(exact(self.grids['c']['disputed_m2']), 1)
        self.assertEqual(exact(self.grids['c']['patch_m2']), 1)
        self.assertEqual(self.grids['c']['valid_m2'], 3)
        self.assertEqual(self.grids['c']['missing_m2'], 0)
        self.assertEqual(self.grids['c']['disputed_patches'], ['pc1', 'pc2'])
        self.assertEqual(exact(self.basins['north']['disputed_m2']), 1)
        self.assertEqual(self.basins['north']['disputed_cells'], ['c'])

    def test_patch_without_value_rejected(self):
        doc = bundle('occlusion.json')
        del doc['patches'][0]['swe_mm']
        with self.assertRaisesRegex(ValueError, 'patch has no measured value: pa'):
            materialize(doc)

    def test_majority_agreement_is_still_a_conflict(self):
        doc = {'survey': 'x',
               'source_cells': [{'id': 's', 'box_m': [0, 0, 1, 1], 'swe_mm': None}],
               'patches': [{'id': 'p1', 'box_m': [0, 0, 1, 1], 'swe_mm': 100},
                           {'id': 'p2', 'box_m': [0, 0, 1, 1], 'swe_mm': 100},
                           {'id': 'p3', 'box_m': [0, 0, 1, 1], 'swe_mm': 200}],
               'targets': [{'id': 't', 'box_m': [0, 0, 1, 1]}], 'basins': []}
        grid = materialize(doc)['target_grid'][0]
        self.assertEqual((exact(grid['patch_m2']), exact(grid['disputed_m2'])), (0, 1))
        self.assertEqual(grid['disputed_patches'], ['p1', 'p2', 'p3'])

    def test_one_patch_spanning_two_cells_fills_both(self):
        doc = {'survey': 'x',
               'source_cells': [{'id': 'a', 'box_m': [0, 0, 1, 1], 'swe_mm': None},
                                {'id': 'b', 'box_m': [1, 0, 2, 1], 'swe_mm': None}],
               'patches': [{'id': 'p', 'box_m': [0, 0, 2, 1], 'swe_mm': 100}],
               'targets': [{'id': 't', 'box_m': [0, 0, 2, 1]}],
               'basins': [{'id': 'b', 'box_m': [1, 0, 2, 1]}]}
        doc = materialize(doc)
        self.assertEqual(exact(doc['target_grid'][0]['patch_m2']), 2)
        self.assertEqual(exact(doc['basins'][0]['patch_m2']), 1)
        self.assertEqual(doc['basins'][0]['patch_contributions'],
                         [{'patch': 'p', 'm2': 1, 'water_m3': '1/10'}])

    def test_conflict_region_is_the_intersection_only(self):
        doc = {'survey': 'x',
               'source_cells': [{'id': 's', 'box_m': [0, 0, 2, 1], 'swe_mm': None}],
               'patches': [{'id': 'wide', 'box_m': [0, 0, 2, 1], 'swe_mm': 100},
                           {'id': 'narrow', 'box_m': [0, 0, 1, 1], 'swe_mm': 300}],
               'targets': [{'id': 't', 'box_m': [0, 0, 2, 1]}], 'basins': []}
        grid = materialize(doc)['target_grid'][0]
        # Only the shared 1 m2 is disputed; the remaining 1 m2 follows the
        # sole patch rather than being thrown away with the conflict.
        self.assertEqual((exact(grid['patch_m2']), exact(grid['disputed_m2'])), (1, 1))
        self.assertEqual(exact(grid['valid_m2']), 1)

    def test_patch_rect_must_be_valid(self):
        doc = bundle('occlusion.json')
        doc['patches'][0]['box_m'] = [0, 0, 0, 2]
        with self.assertRaisesRegex(ValueError, 'invalid rectangle: pa'):
            materialize(doc)


class OcclusionInputTest(unittest.TestCase):
    def test_occlusion_must_stay_inside_its_cell(self):
        doc = bundle('occlusion.json')
        doc['source_cells'][0]['occlusions'][0]['box_m'] = [-1, 0, 1, 1]
        with self.assertRaisesRegex(ValueError, 'occlusion outside source cell'):
            materialize(doc)

    def test_overlapping_occlusions_in_one_cell_rejected(self):
        doc = bundle('occlusion.json')
        doc['source_cells'][0]['occlusions'].append({'box_m': [0, 0, 1, 1]})
        with self.assertRaisesRegex(ValueError, 'overlapping occlusions'):
            materialize(doc)

    def test_located_and_percentage_occlusion_in_same_cell_rejected(self):
        doc = bundle('occlusion.json')
        doc['source_cells'][0]['occluded_fraction'] = '1/2'
        with self.assertRaisesRegex(ValueError, 'occlusion already located: a'):
            materialize(doc)

    def test_percent_spellings_preserved_exactly(self):
        for field, value, expected in (('occluded_fraction', '1/4', Fraction(1, 4)),
                                       ('occluded_percent', 25, Fraction(1, 4)),
                                       ('occlusion_fraction', '1/10', Fraction(1, 10))):
            doc = {'survey': 'x',
                   'source_cells': [{'id': 's', 'box_m': [0, 0, 2, 2], 'swe_mm': 100, field: value}],
                   'targets': [{'id': 't', 'box_m': [0, 0, 2, 2]}],
                   'basins': [{'id': 'b', 'box_m': [0, 0, 2, 2]}]}
            grid = materialize(doc)['target_grid'][0]
            self.assertEqual(exact(grid['original_m2']), 4 - expected * 4, field)
            self.assertEqual(exact(grid['missing_m2']), expected * 4, field)

    def test_occlusion_rows_can_be_a_document_level_list(self):
        doc = {'survey': 'x',
               'source_cells': [{'id': 's', 'box_m': [0, 0, 2, 2], 'swe_mm': 100}],
               'occlusion_rows': [{'cell': 's', 'box_m': [0, 0, 1, 1]}],
               'patches': [{'id': 'p', 'box_m': [0, 0, 1, 1], 'swe_mm': 200}],
               'targets': [{'id': 't', 'box_m': [0, 0, 2, 2]}],
               'basins': [{'id': 'b', 'box_m': [0, 0, 2, 2]}]}
        grid = materialize(doc)['target_grid'][0]
        self.assertEqual((exact(grid['original_m2']), exact(grid['patch_m2'])), (3, 1))

    def test_occlusion_row_for_unknown_cell_rejected(self):
        doc = {'survey': 'x',
               'source_cells': [{'id': 's', 'box_m': [0, 0, 2, 2], 'swe_mm': 100}],
               'occlusion_rows': [{'cell': 'ghost', 'box_m': [0, 0, 1, 1]}],
               'targets': [], 'basins': []}
        with self.assertRaisesRegex(ValueError, 'occlusion for unknown source cell: ghost'):
            materialize(doc)


class PercentageWithoutPositionTest(unittest.TestCase):
    """An occlusion percentage must never be projected onto basins."""

    def _doc(self):
        return {
            'survey': 'pct',
            'source_cells': [{'id': 'd', 'box_m': [0, 0, 2, 2], 'swe_mm': 40,
                              'occluded_fraction': '1/2'}],
            'targets': [{'id': 'whole', 'box_m': [0, 0, 2, 2]}],
            'basins': [
                {'id': 'whole', 'box_m': [0, 0, 2, 2]},
                {'id': 'left', 'box_m': [0, 0, 1, 2]},
                {'id': 'right', 'box_m': [1, 0, 2, 2]},
                {'id': 'quarter', 'box_m': [0, 0, '1/2', 1]},
            ],
        }

    def test_whole_cell_is_settled_exactly(self):
        doc = materialize(self._doc())
        basin = by_id(doc['basins'])['whole']
        self.assertEqual((exact(basin['original_m2']), exact(basin['missing_in_grid_m2'])), (2, 2))
        self.assertEqual(exact(basin['water_m3']), Fraction(2, 25))
        self.assertEqual(basin['unlocated_m2'], 0)

    def test_partial_basin_does_not_guess_a_share(self):
        doc = materialize(self._doc())
        for key in ('left', 'right', 'quarter'):
            basin = by_id(doc['basins'])[key]
            self.assertEqual(exact(basin['unlocated_m2']), exact(basin['area_m2']), key)
            self.assertEqual(exact(basin['valid_m2']), 0, key)
            self.assertEqual(exact(basin['missing_in_grid_m2']), 0, key)
            self.assertIsNone(basin['water_m3'], key)
            self.assertEqual(basin['unlocated_cells'], ['d'], key)

    def test_shares_do_not_impersonate_total(self):
        doc = materialize(self._doc())
        basins = by_id(doc['basins'])
        # The two halves together cannot be added up to the whole-cell answer
        # as if the occlusion position were known.
        self.assertEqual(exact(basins['left']['valid_m2']) + exact(basins['right']['valid_m2']), 0)

    def test_percentage_with_patches_still_uses_only_whole_cell_patches(self):
        doc = self._doc()
        doc['patches'] = [{'id': 'half', 'box_m': [0, 0, 1, 2], 'swe_mm': 100},
                          {'id': 'full', 'box_m': [0, 0, 2, 2], 'swe_mm': 80}]
        rows = by_id(materialize(doc)['basins'])
        # The whole-cell patch fills the unplaced occlusion; the half patch
        # cannot prove it reaches it, so it is unused, never added over valid
        # ground and never split between the mosaic basins.
        self.assertEqual(exact(rows['whole']['patch_m2']), 2)
        self.assertEqual(exact(rows['whole']['unused_patch_m2']), 2)
        self.assertEqual(exact(rows['whole']['water_m3']), Fraction(2, 25) + Fraction(4, 25))
        self.assertEqual(exact(rows['left']['unlocated_m2']), 2)
        self.assertEqual(exact(rows['right']['unlocated_m2']), 2)
        self.assertEqual(exact(rows['quarter']['unlocated_m2']), Fraction(1, 2))


class AccountingIdentityTest(unittest.TestCase):
    """Every product's buckets must partition its box, with no double counting."""

    def test_buckets_partition_each_product(self):
        doc = bundle('occlusion.json')
        source, remeasured = cells(doc['source_cells']), patches(doc['patches'])
        boxes = list(read_boxes(doc['targets'], nonoverlap=True).items())
        boxes += list(read_boxes(doc['basins']).items())
        for key, box in boxes:
            parts = evaluate(source, remeasured, box)
            total = (parts.original_area + parts.patch_area + parts.missing_area
                     + parts.disputed_area + parts.unlocated_area + parts.outside_area)
            self.assertEqual(total, (box[2] - box[0]) * (box[3] - box[1]), key)

    def test_water_is_area_times_depth_and_means_agree(self):
        doc = result(bundle('occlusion.json'))
        for row in doc['target_grid'] + doc['basins']:
            if row['valid_m2']:
                self.assertEqual(exact(row['valid_mean_mm']) * exact(row['valid_m2']),
                                 exact(row['water_m3']) * 1000, row['id'])

    def test_overlapping_patches_deducted_once(self):
        source = cells([{'id': 'c', 'box_m': [0, 0, 1, 1], 'swe_mm': None}])
        remeasured = patches([{'id': 'p1', 'box_m': [0, 0, 1, 1], 'swe_mm': 100},
                              {'id': 'p2', 'box_m': [0, 0, 1, 1], 'swe_mm': 100}])
        parts = evaluate(source, remeasured, (0, 0, 1, 1))
        self.assertEqual((parts.patch_area, parts.ignored_patch_area), (1, 0))


class CommandLineTest(unittest.TestCase):
    def test_occlusion_bundle_previews_publishes_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / 'ledger.sqlite'
            code, preview, _ = run('preview', str(EXAMPLES / 'occlusion.json'))
            self.assertEqual(code, 0)
            code, receipt, _ = run('publish', str(EXAMPLES / 'occlusion.json'), str(ledger), 'occ')
            self.assertEqual(code, 0)
            self.assertEqual(set(decode(receipt)), {'edition', 'source_sha256', 'result_sha256'})
            self.assertEqual(run('show', str(ledger), 'occ')[1], preview)
            self.assertEqual(run('replay', str(ledger), 'occ')[0], 0)

    def test_legacy_editions_still_publish_after_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / 'ledger.sqlite'
            self.assertEqual(run('publish', str(EXAMPLES / 'baseline.json'), str(ledger), 'old')[0], 0)
            self.assertEqual(run('publish', str(EXAMPLES / 'occlusion.json'), str(ledger), 'new')[0], 0)
            self.assertEqual(run('replay', str(ledger), 'old')[0], 0)
            self.assertEqual(run('replay', str(ledger), 'new')[0], 0)

    def test_invalid_occlusion_bundle_exits_two_without_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = bundle('occlusion.json')
            doc['source_cells'][0]['occlusions'][0]['box_m'] = [-1, 0, 1, 1]
            bad = Path(tmp) / 'bad.json'
            bad.write_text(json.dumps(doc), encoding='utf-8')
            ledger = Path(tmp) / 'ledger.sqlite'
            code, _, err = run('publish', str(bad), str(ledger), 'bad')
            self.assertEqual(code, 2)
            self.assertIn('occlusion outside source cell', err)
            self.assertFalse(ledger.exists())


if __name__ == '__main__':
    unittest.main()
