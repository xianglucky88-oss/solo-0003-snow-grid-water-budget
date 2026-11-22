"""Reconciliation tests for seam adjudication, retraction and legacy readings."""
import json
import subprocess
import sys
import unittest
from fractions import Fraction
from pathlib import Path

from snowbudget.common import encode
from snowbudget.geometry import area
from snowbudget.workflow import materialize

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / 'examples'


def load(name):
    return json.loads((EXAMPLES / name).read_text(encoding='utf-8'))


def preview(path):
    return subprocess.run([sys.executable, '-m', 'snowbudget', 'preview', str(path)],
                          cwd=ROOT, capture_output=True, text=True, check=False)


def classes(basin):
    return {name: entry['m2'] for name, entry in basin['classes'].items()}


class LegacyReading(unittest.TestCase):
    """Overlap-free bundles keep the original reading, including its rejection."""

    def test_overlap_free_fixtures_are_unchanged(self):
        for name in ('baseline.json', 'changed.json'):
            result = materialize(load(name))
            for grid in result['target_grid']:
                self.assertEqual(set(grid), {'id', 'box_m', 'valid_m2', 'water_m3', 'valid_mean_mm',
                                             'coverage', 'source_cells'})
            for basin in result['basins']:
                self.assertEqual(set(basin), {'id', 'area_m2', 'valid_m2', 'missing_in_grid_m2',
                                              'outside_grid_m2', 'water_m3', 'valid_mean_mm',
                                              'contributions', 'missing_cells'})

    def test_overlap_without_grades_is_still_rejected(self):
        run = preview(EXAMPLES / 'invalid.json')
        self.assertEqual(run.returncode, 2)
        self.assertIn('overlapping grid cell: z', run.stderr)
        self.assertEqual(run.stdout, '')

    def test_seam_with_one_ungraded_side_is_rejected(self):
        doc = load('seam.json')
        doc['source_cells'][0].pop('evidence')
        with self.assertRaisesRegex(ValueError, 'overlapping grid cell: west'):
            materialize(doc)

    def test_known_totals(self):
        result = materialize(load('baseline.json'))
        all_basin = next(b for b in result['basins'] if b['id'] == 'all')
        self.assertEqual(all_basin['valid_m2'], 12)
        self.assertEqual(all_basin['water_m3'], 3)
        self.assertEqual(all_basin['missing_in_grid_m2'], 4)
        self.assertEqual(all_basin['outside_grid_m2'], 4)
        cut = next(b for b in result['basins'] if b['id'] == 'cut')
        self.assertEqual(cut['water_m3'], '3/2')
        dry = next(b for b in result['basins'] if b['id'] == 'dry')
        self.assertEqual(dry['water_m3'], 0)
        miss = next(b for b in result['basins'] if b['id'] == 'miss')
        self.assertIsNone(miss['water_m3'])


class SeamAdjudication(unittest.TestCase):
    """Evidence grade picks the tile; equal grades with different depths conflict."""

    def test_footprints_partition_every_basin(self):
        for name in ('seam.json', 'withdrawn.json'):
            for basin in materialize(load(name))['basins']:
                total = sum(area(tuple(Fraction(v) for v in f['box_m']))
                            for entry in basin['classes'].values() for f in entry['footprints'])
                self.assertEqual(total, Fraction(basin['area_m2']), f'{name}/{basin["id"]}')

    def test_headline_totals_agree_with_classes(self):
        for name in ('seam.json', 'withdrawn.json'):
            for basin in materialize(load(name))['basins']:
                accepted = sum(Fraction(entry['m2']) for key, entry in basin['classes'].items()
                               if key in ('unique', 'replaceable', 'backfilled', 'resolved'))
                self.assertEqual(accepted, Fraction(basin['valid_m2']))
                parts = (Fraction(basin['valid_m2']) + Fraction(basin['missing_in_grid_m2'])
                         + Fraction(basin['retracted_m2']) + Fraction(basin['conflict_m2'])
                         + Fraction(basin['outside_grid_m2']))
                self.assertEqual(parts, Fraction(basin['area_m2']))

    def test_higher_grade_wins_the_seam(self):
        seam = next(t for t in materialize(load('seam.json'))['target_grid'] if t['id'] == 'seam')
        accepted = [f for f in seam['footprints'] if f['status'] == 'accepted']
        self.assertEqual([(f['cell'], f['evidence']) for f in accepted], [('west', 2)])
        contested = [f for f in seam['footprints'] if f['status'] == 'conflict']
        self.assertEqual([(f['cells'], f['values_mm']) for f in contested], [(['east', 'west'], [500, 250])])

    def test_conflict_is_never_averaged(self):
        for basin in materialize(load('seam.json'))['basins']:
            contested = basin['classes']['contested']
            self.assertTrue(all(f['status'] == 'conflict' for f in contested['footprints']))
            self.assertNotIn('water_m3', contested)
            for footprint in contested['footprints']:
                self.assertEqual(len(set(footprint['values_mm'])), 2)
                self.assertNotIn('swe_mm', footprint)

    def test_three_basin_classes_are_distinct(self):
        all_basin = next(b for b in materialize(load('seam.json'))['basins'] if b['id'] == 'all')
        seen = classes(all_basin)
        self.assertEqual(seen['replaceable'], 12)
        self.assertEqual(seen['contested'], 8)
        self.assertEqual(seen['no_evidence'], 8)
        self.assertEqual(seen['replaceable'] + seen['contested'] + seen['no_evidence'] + seen['unique'],
                         int(all_basin['area_m2']))
        self.assertEqual(all_basin['valid_m2'], seen['unique'] + seen['replaceable'])

    def test_totals_stay_exact(self):
        all_basin = next(b for b in materialize(load('seam.json'))['basins'] if b['id'] == 'all')
        self.assertEqual(all_basin['water_m3'], 10)
        self.assertEqual(all_basin['valid_m2'], 36)
        self.assertEqual(all_basin['conflict_m2'], 8)
        self.assertEqual(all_basin['outside_grid_m2'], 0)


class LocalRetraction(unittest.TestCase):
    """One same-grade retraction resolves its own footprint and nothing else."""

    def setUp(self):
        self.before = materialize(load('seam.json'))
        self.after = materialize(load('withdrawn.json'))

    def test_only_the_retracted_seam_changes(self):
        changed = set()
        for old, new in zip(self.before['target_grid'], self.after['target_grid']):
            if encode(old) != encode(new):
                changed.add(old['id'])
        self.assertEqual(changed, {'seam'})

    def test_unique_and_other_seams_are_untouched(self):
        def slices(result, ident):
            target = next(t for t in result['target_grid'] if t['id'] == ident)
            return [f for f in target['footprints'] if f['box_m'][:2] != [3, 0]]
        for ident in ('west-half', 'east-side', 'rest'):
            self.assertEqual(slices(self.before, ident), slices(self.after, ident))
        old = next(b for b in self.before['basins'] if b['id'] == 'all')['classes']['unique']['footprints']
        new = next(b for b in self.after['basins'] if b['id'] == 'all')['classes']['unique']['footprints']
        self.assertEqual(old, new)

    def test_same_grade_conflict_resolves_to_the_surviving_tile(self):
        seam = next(t for t in self.after['target_grid'] if t['id'] == 'seam')
        self.assertEqual(seam['conflict_m2'], 0)
        self.assertEqual(seam['water_m3'], '5/2')
        accepted = {(tuple(f['box_m']), f['cell'], f['class']) for f in seam['footprints']
                    if f['status'] == 'accepted'}
        self.assertEqual(accepted, {((2, 0, 3, 4), 'base', 'backfilled'), ((3, 0, 4, 4), 'east', 'resolved')})

    def test_lower_grade_backfills_the_withdrawn_strip(self):
        all_basin = next(b for b in self.after['basins'] if b['id'] == 'all')
        self.assertEqual(all_basin['classes']['backfilled']['m2'], 4)
        self.assertEqual(all_basin['classes']['backfilled']['water_m3'], '1/2')
        self.assertEqual(all_basin['classes']['resolved']['m2'], 4)
        self.assertEqual(all_basin['classes']['resolved']['water_m3'], 2)

    def test_untouched_conflict_remains(self):
        all_basin = next(b for b in self.after['basins'] if b['id'] == 'all')
        self.assertEqual(all_basin['conflict_m2'], 4)
        self.assertEqual(all_basin['conflict_cells'], ['dup', 'pair'])
        self.assertEqual(all_basin['valid_m2'], 40)
        self.assertEqual(all_basin['water_m3'], '23/2')


class RetractionValidation(unittest.TestCase):

    def test_retraction_must_sit_inside_its_tile(self):
        doc = load('withdrawn.json')
        doc['retractions'] = [{'id': 'west', 'box_m': [3, 0, 9, 4]}]
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_retraction_must_name_a_known_tile(self):
        doc = load('withdrawn.json')
        doc['retractions'] = [{'id': 'ghost', 'box_m': [0, 0, 1, 1]}]
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_whole_tile_retraction_is_supported(self):
        doc = load('withdrawn.json')
        doc['retractions'] = [{'id': 'west'}]
        result = materialize(doc)
        all_basin = next(b for b in result['basins'] if b['id'] == 'all')
        # base (grade 1) backfills the strip the equal-grade east cannot claim; east resolves the rest.
        self.assertEqual(all_basin['classes']['backfilled']['m2'], 4)
        self.assertEqual(all_basin['classes']['resolved']['m2'], 4)


class ArchiveRoundTrip(unittest.TestCase):

    def test_publish_replay_and_source(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'ledger.sqlite'
            publish = subprocess.run([sys.executable, '-m', 'snowbudget', 'publish',
                                      str(EXAMPLES / 'withdrawn.json'), str(archive), 'revised'],
                                     cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(publish.returncode, 0, publish.stderr)
            for command in ('replay', 'show', 'source', 'history'):
                args = [sys.executable, '-m', 'snowbudget', command, str(archive)]
                if command != 'history':
                    args.append('revised')
                run = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertTrue(run.stdout.strip())


if __name__ == '__main__':
    unittest.main()
