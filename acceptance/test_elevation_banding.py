"""Acceptance checks for elevation-band splitting.

Run from anywhere:  python3 -I acceptance/test_elevation_banding.py
The four banding ledgers, the single-coverage rule, cross-edition conservation
and the end-to-end SQLite publish/replay flow are all asserted here.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from snowbudget.banding import banding, find_band  # noqa: E402
from snowbudget.common import decode  # noqa: E402
from snowbudget.elevation import effective_coverage, elevation_bands, elevation_schemes  # noqa: E402
from snowbudget.field import cells  # noqa: E402
from snowbudget.geometry import area, overlap, read_boxes, subtract  # noqa: E402
from snowbudget.workflow import materialize  # noqa: E402

EXAMPLES = ROOT / 'examples'


def Q(value):
    return Fraction(str(value))


def load(name):
    return decode((EXAMPLES / name).read_text(encoding='utf-8'))


def prepare(doc):
    source = cells(doc['source_cells'])
    basins = read_boxes(doc['basins'])
    bands = elevation_bands(doc['elevation_bands'])
    schemes = elevation_schemes(doc['elevation_schemes'])
    coverage = effective_coverage(schemes, doc['coverage_choice'])
    return source, basins, bands, coverage


def banding_of(doc):
    source, basins, bands, coverage = prepare(doc)
    return {row['id']: row for row in
            banding(source, basins, bands, coverage, doc['coverage_choice'])}


def basin_index(result):
    return {row['id']: row for row in result['basins']}


class GeometryTests(unittest.TestCase):
    def test_rectangle_subtraction_splits_non_aligned_cells(self):
        # vertical cut removes the middle strip; left and right remain
        parts = subtract((0, 0, 8, 4), (2, 0, 6, 4))
        self.assertEqual(sorted(parts), sorted([(0, 0, 2, 4), (6, 0, 8, 4)]))
        # corner cut leaves three rectangles whose total area is conserved
        hole = (2, 1, 6, 3)
        parts = subtract((0, 0, 8, 4), hole)
        self.assertEqual(sum(area(p) for p in parts), area((0, 0, 8, 4)) - area(hole))
        # no overlap means the original rectangle is returned untouched
        self.assertEqual(subtract((0, 0, 1, 1), (2, 2, 3, 3)), [(0, 0, 1, 1)])

    def test_secondary_cells_are_clipped_to_non_overlapping_gaps(self):
        doc = load('elevation-a.json')
        schemes = elevation_schemes(doc['elevation_schemes'])
        coverage = effective_coverage(schemes, 'scheme-a')
        # effective coverage pieces must be mutually disjoint
        for i, left in enumerate(coverage):
            for right in coverage[i + 1:]:
                self.assertEqual(overlap(left['box'], right['box']), 0)
        # scheme-b survives only where scheme-a had no evidence: the part of
        # b-right outside a-left/a-middle is x[6,8] x y[2,3]
        b_pieces = [c for c in coverage if c['scheme'] == 'scheme-b']
        self.assertEqual(len(b_pieces), 1)
        self.assertEqual(tuple(int(v) for v in b_pieces[0]['box']), (6, 2, 8, 3))
        self.assertEqual(b_pieces[0]['elevation_m'], 700)


class BandingTests(unittest.TestCase):
    def setUp(self):
        self.doc_a = load('elevation-a.json')
        self.doc_b = load('elevation-b.json')
        self.doc_gap = load('elevation-gap.json')
        self.res_a = materialize(self.doc_a)
        self.res_b = materialize(self.doc_b)
        self.rows_a = banding_of(self.doc_a)
        self.rows_b = banding_of(self.doc_b)

    def test_four_ledgers_are_separated(self):
        # gap edition, east basin: banded (x[4,6]), banded zero (z on x[4,6]),
        # no-elevation observed (x[6,7]) and unobserved (m, null SWE).
        # scheme-b patches only x[7,8]; the strip x[6,7] has no evidence at all.
        rows = banding_of(self.doc_gap)
        east = rows['east']
        low = east['bands'][0]
        self.assertEqual(Q(low['measured_m2']), 4)          # w on x[4,6], depth 250
        self.assertEqual(Q(low['water_m3']), 1)
        self.assertEqual(Q(low['zero_m2']), 2)              # z on x[4,6], depth 0
        self.assertEqual(Q(east['no_elevation_m2']), 3)     # w x[6,7] (2 m2) + z x[6,7] (1 m2)
        self.assertEqual(Q(east['no_elevation_water_m3']), Q('1/2'))  # only w carries water
        self.assertEqual(Q(east['unobserved_m2']), 4)       # m: null SWE
        west = rows['west']
        self.assertEqual(Q(west['no_elevation_m2']), 0)
        self.assertEqual(Q(west['unobserved_m2']), 4)
        # scheme-b's patch on x[7,8] is recorded as banded evidence
        patched = [f for f in east['fragments'] if f['elevation_scheme'] == 'scheme-b']
        self.assertEqual(sorted(Q(f['m2']) for f in patched), [1, 2])

    def test_reconciles_to_original_basin_totals(self):
        for doc, result in ((self.doc_a, self.res_a), (self.doc_b, self.res_b)):
            originals = basin_index(result)
            for row in banding_of(doc).values():
                base = originals[row['id']]
                recon = row['reconciliation']
                self.assertEqual(Q(recon['water_m3']), Q(base['water_m3']))
                self.assertEqual(Q(recon['valid_m2']), Q(base['valid_m2']))
                self.assertEqual(Q(recon['valid_plus_unobserved_m2']),
                                 Q(base['valid_m2']) + Q(base['missing_in_grid_m2']))
                self.assertEqual(Q(recon['basin_area_m2']), Q(base['area_m2']))
                banded_area = sum(Q(b['measured_m2']) + Q(b['zero_m2']) for b in row['bands'])
                self.assertEqual(banded_area + Q(row['no_elevation_m2']), Q(base['valid_m2']))
                banded_water = sum(Q(b['water_m3']) + Q(b['zero_water_m3']) for b in row['bands'])
                self.assertEqual(banded_water + Q(row['no_elevation_water_m3']),
                                 Q(base['water_m3']))

    def test_band_destinations_change_but_basin_totals_do_not(self):
        # east basin: scheme-a puts half the water low / half high;
        # scheme-b puts all of it high. The basin total stays 2 m3.
        east_a, east_b = self.rows_a['east'], self.rows_b['east']
        low_a, high_a = east_a['bands']
        low_b, high_b = east_b['bands']
        self.assertEqual(Q(low_a['water_m3']), 1)
        self.assertEqual(Q(high_a['water_m3']), 1)
        self.assertEqual(Q(low_b['water_m3']), 0)
        self.assertEqual(Q(high_b['water_m3']), 2)
        for rows in (self.rows_a, self.rows_b):
            self.assertEqual(Q(rows['west']['reconciliation']['water_m3']), 2)
            self.assertEqual(Q(rows['east']['reconciliation']['water_m3']), 2)
            total = sum(Q(r['reconciliation']['water_m3']) for r in rows.values())
            self.assertEqual(total, 4)

    def test_conflict_region_always_follows_one_whole_edition_coverage(self):
        # Central conflict on cell w: x[4,6] is covered by both schemes with
        # different elevations (a: 200 low, b: 700 high). Edition a must use
        # scheme-a's low evidence there, edition b scheme-b's high evidence —
        # one fixed coverage order per edition, never a per-band cherry-pick.
        # Secondary evidence survives only on non-overlapping ground: under
        # edition b, scheme-a patches x[0,2] of cell w (200 low).
        def w_fragments(rows, basin_id):
            return [f for f in rows[basin_id]['fragments'] if f['cell'] == 'w']

        # the contested strip x[4,6] lies in the east basin; its 4 m2 piece of
        # cell w must follow each edition's primary scheme, never the competitor
        east_a = w_fragments(self.rows_a, 'east')
        conflict_a = [f for f in east_a
                      if tuple(Q(v) for v in f['box_m']) == (4, 0, 6, 2)]
        self.assertEqual(len(conflict_a), 1)
        self.assertEqual((conflict_a[0]['elevation_scheme'],
                          int(Q(conflict_a[0]['elevation_m'])), conflict_a[0]['band']),
                         ('scheme-a', 200, 'low'))
        east_b = w_fragments(self.rows_b, 'east')
        conflict_b = [f for f in east_b
                      if Q(f['box_m'][0]) == 4 and int(Q(f['elevation_m'])) == 700]
        self.assertEqual(len(conflict_b), 1)
        self.assertEqual((conflict_b[0]['elevation_scheme'], conflict_b[0]['band']),
                         ('scheme-b', 'high'))
        self.assertEqual(Q(conflict_b[0]['m2']), 8)
        for rows in (self.rows_a, self.rows_b):
            all_w = (w_fragments(rows, 'west') + w_fragments(rows, 'east'))
            for frag in all_w:
                self.assertEqual(find_band(Q(frag['elevation_m']),
                                           elevation_bands(self.doc_a['elevation_bands'])),
                                 frag['band'])
            self.assertEqual(sum(Q(f['m2']) for f in all_w), 16)
        # edition b: scheme-a survives only on non-overlapping ground x[0,2]
        b_patch = [f for f in w_fragments(self.rows_b, 'west')
                   if f['elevation_scheme'] == 'scheme-a']
        self.assertEqual(len(b_patch), 1)
        self.assertEqual((int(Q(b_patch[0]['elevation_m'])), b_patch[0]['band']), (200, 'low'))
        self.assertEqual(tuple(Q(v) for v in b_patch[0]['box_m']), (0, 0, 2, 2))
        self.assertEqual(Q(b_patch[0]['m2']), 4)
        # edition a: no w fragment borrows scheme-b; scheme-b appears only on
        # the z strip where scheme-a has no cell at all
        a_borrows = [f for row in self.rows_a.values() for f in row['fragments']
                     if f['cell'] == 'w' and f['elevation_scheme'] == 'scheme-b']
        self.assertEqual(a_borrows, [])
        for rows, primary in ((self.rows_a, 'scheme-a'), (self.rows_b, 'scheme-b')):
            self.assertTrue(all(r['coverage_choice'] == primary for r in rows.values()))
            for row in rows.values():
                schemes_used = {f['elevation_scheme'] for f in row['fragments']
                                if f['elevation_scheme'] is not None}
                self.assertIn(primary, schemes_used)

    def test_patch_only_accepted_on_evidence_backed_non_overlap(self):
        # east z strip: x[4,6] is primary scheme-a, x[6,7] has no elevation
        # evidence from anyone, x[7,8] is the clipped scheme-b non-overlap patch.
        rows_gap = banding_of(self.doc_gap)
        z_frags = [f for f in rows_gap['east']['fragments'] if f['cell'] == 'z']
        patched = [f for f in z_frags if f['elevation_scheme'] == 'scheme-b']
        self.assertEqual(len(patched), 1)
        self.assertEqual(Q(patched[0]['m2']), 1)
        hole = [f for f in z_frags if f['elevation_scheme'] is None]
        self.assertEqual(len(hole), 1)
        self.assertEqual(Q(hole[0]['m2']), 1)
        self.assertIsNone(hole[0]['band'])

    def test_elevation_outside_all_bands_is_treated_as_unusable(self):
        bands = elevation_bands(self.doc_a['elevation_bands'])
        self.assertEqual(find_band(0, bands), 'low')       # lower edge included
        self.assertEqual(find_band(499, bands), 'low')
        self.assertEqual(find_band(500, bands), 'high')    # band boundary: upper band
        self.assertIsNone(find_band(2000, bands))           # upper edge excluded
        self.assertIsNone(find_band(-5, bands))

    def test_null_swe_is_never_counted_as_zero_or_water(self):
        for rows in (self.rows_a, self.rows_b, banding_of(self.doc_gap)):
            for row in rows.values():
                null_frags = [f for f in row['fragments'] if f['swe_mm'] is None]
                self.assertEqual(Q(row['unobserved_m2']),
                                 sum(Q(f['m2']) for f in null_frags))
                self.assertTrue(all(f['water_m3'] is None for f in null_frags))
                self.assertEqual(Q(row['banded_water_m3']) + Q(row['no_elevation_water_m3']),
                                 Q(row['reconciliation']['water_m3']))

    def test_source_cell_crosses_elevation_and_basin_boundaries(self):
        # cell w spans x[0,8]: one source cell is cut by the basin line x=4
        # and by both elevation grids (a boundary x=6, b boundary x=4 and x=2).
        for rows in (self.rows_a, self.rows_b):
            pieces = [f for row in rows.values() for f in row['fragments'] if f['cell'] == 'w']
            self.assertEqual(sum(Q(f['m2']) for f in pieces), 16)
            self.assertEqual(sum(Q(f['water_m3']) for f in pieces), 4)


class InputValidationTests(unittest.TestCase):
    def test_unknown_coverage_choice_is_rejected(self):
        with self.assertRaises(ValueError):
            materialize(load('elevation-invalid.json'))

    def test_partial_elevation_section_is_rejected(self):
        doc = load('elevation-a.json')
        del doc['elevation_schemes']
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_overlapping_bands_are_rejected(self):
        doc = load('elevation-a.json')
        doc['elevation_bands'][1]['span_m'] = [400, 2000]
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_null_elevation_is_rejected(self):
        doc = load('elevation-a.json')
        doc['elevation_schemes'][0]['cells'][0]['elevation_m'] = None
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_duplicate_band_and_scheme_ids_are_rejected(self):
        doc = load('elevation-a.json')
        doc['elevation_bands'][1]['id'] = 'low'
        with self.assertRaises(ValueError):
            materialize(doc)
        doc = load('elevation-a.json')
        doc['elevation_schemes'][1]['id'] = 'scheme-a'
        with self.assertRaises(ValueError):
            materialize(doc)


class CliAndArchiveTests(unittest.TestCase):
    def run_cli(self, *args):
        # -I ignores PYTHONPATH, so inject the project root from a -c launcher
        launcher = ('import sys; '
                    f'sys.path.insert(0, {str(ROOT)!r}); '
                    'from snowbudget.cli import main; raise SystemExit(main())')
        return subprocess.run([sys.executable, '-I', '-c', launcher, *args],
                              cwd=ROOT, capture_output=True, text=True)

    def test_publish_replay_history_roundtrip_for_both_editions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / 'ledger.sqlite')
            for bundle, edition in (('elevation-a.json', 'cover-a'),
                                    ('elevation-b.json', 'cover-b')):
                proc = self.run_cli('publish', f'examples/{bundle}', db, edition)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            proc = self.run_cli('history', db)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual([row['edition'] for row in json.loads(proc.stdout)],
                             ['cover-a', 'cover-b'])
            for edition in ('cover-a', 'cover-b'):
                shown = json.loads(self.run_cli('show', db, edition).stdout)
                replay = self.run_cli('replay', db, edition)
                self.assertEqual(replay.returncode, 0, replay.stderr)
                source = json.loads(self.run_cli('source', db, edition).stdout)
                self.assertEqual(source['coverage_choice'],
                                 'scheme-a' if edition == 'cover-a' else 'scheme-b')
                self.assertTrue(shown['elevation_banding'])
            # duplicate edition names stay forbidden
            dup = self.run_cli('publish', 'examples/elevation-a.json', db, 'cover-a')
            self.assertEqual(dup.returncode, 2)

    def test_legacy_bundles_keep_their_original_output_shape(self):
        for name in ('baseline.json', 'changed.json'):
            result = materialize(load(name))
            self.assertNotIn('elevation_banding', result)
            self.assertEqual(set(result), {'survey', 'target_grid', 'basins'})
        proc = self.run_cli('preview', 'examples/invalid.json')
        self.assertEqual(proc.returncode, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
