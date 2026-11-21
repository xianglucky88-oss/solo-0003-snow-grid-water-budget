"""Acceptance tests for the hand-derived conservation oracle.

The fixture is reduced through the real CLI in a throwaway archive, then the
two archived products (target_grid and basins) are reconciled by
``tests.acceptance`` using only the archived source, the product geometry and
that product's own area basis.  No expected value is obtained by calling the
production reducer a second time.

The mutation tests make the oracle prove it can see the errors the task asks
about: an offsetting single-cell error, a wrong area normalisation, and
overlapping-basin totals mistaken for the region total.
"""
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests import acceptance  # noqa: E402

FIXTURE = ROOT / 'tests' / 'fixtures' / 'handgrid.json'


def run_cli(*args):
    return subprocess.run([sys.executable, '-m', 'snowbudget', *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)


def cli_json(*args):
    proc = run_cli(*args)
    if proc.returncode != 0:
        raise AssertionError(f'command failed ({proc.returncode}): {args}\n{proc.stderr}')
    return json.loads(proc.stdout)


class HandgridPreviewTest(unittest.TestCase):
    def test_preview_matches_hand_ledger(self):
        result = cli_json('preview', FIXTURE)
        source = acceptance.load_fixture()
        failures = acceptance.check(result, source)
        self.assertEqual(failures, [], f'unexpected findings: {failures}')

    def test_oracle_is_independent_of_reducer(self):
        text = (ROOT / 'tests' / 'acceptance.py').read_text(encoding='utf-8')
        self.assertNotIn('snowbudget', text)

    def test_invalid_fixture_is_rejected(self):
        proc = run_cli('preview', ROOT / 'examples' / 'invalid.json')
        self.assertEqual(proc.returncode, 2, proc.stderr)


def _publish_hand():
    tmp = tempfile.TemporaryDirectory()
    archive = Path(tmp.name) / 'hand.sqlite'
    published = cli_json('publish', FIXTURE, archive, 'hand')
    return tmp, archive, published


class ArchiveReadMixin:
    """Read-only archive assertions shared by the freshness and oracle tests."""

    def test_preview_and_archive_agree(self):
        result = cli_json('preview', FIXTURE)
        archived = cli_json('show', self.archive, 'hand')
        self.assertEqual(result, archived)

    def test_both_products_reconcile_with_archived_source(self):
        result = cli_json('show', self.archive, 'hand')
        source = cli_json('source', self.archive, 'hand')
        failures = acceptance.check(result, source)
        self.assertEqual(failures, [], f'unexpected findings: {failures}')
        self.assertIn('target_grid', result)
        self.assertIn('basins', result)

    def test_archived_source_is_the_fixture(self):
        archived = cli_json('source', self.archive, 'hand')
        self.assertEqual(archived, acceptance.load_fixture())

    def test_hashes_cover_normalized_source_and_result(self):
        history = {row['edition']: row for row in cli_json('history', self.archive)}
        entry = history['hand']
        source_text = json.dumps(cli_json('source', self.archive, 'hand'),
                                 ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        result_text = json.dumps(cli_json('show', self.archive, 'hand'),
                                 ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.assertEqual(hashlib.sha256(source_text.encode()).hexdigest(), entry['source_sha256'])
        self.assertEqual(hashlib.sha256(result_text.encode()).hexdigest(), entry['result_sha256'])
        self.assertEqual(hashlib.sha256(source_text.encode()).hexdigest(),
                         self.published['source_sha256'])
        self.assertEqual(hashlib.sha256(result_text.encode()).hexdigest(),
                         self.published['result_sha256'])

    def test_replay_reproduces_the_archived_edition(self):
        before = cli_json('show', self.archive, 'hand')
        replayed = cli_json('replay', self.archive, 'hand')
        self.assertEqual(replayed, before)
        self.assertEqual(cli_json('show', self.archive, 'hand'), before)

    def test_archive_rejects_a_second_edition_of_the_same_name(self):
        proc = run_cli('publish', FIXTURE, self.archive, 'hand')
        self.assertEqual(proc.returncode, 2)
        rows = [r for r in cli_json('history', self.archive) if r['edition'] == 'hand']
        self.assertEqual(len(rows), 1)

    def test_archive_is_not_created_by_read_commands(self):
        fresh = Path(self.tmp.name) / 'absent.sqlite'
        proc = run_cli('history', fresh)
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(fresh.exists())


def _rows(result):
    return {row['id']: row for row in result['basins']}, {row['id']: row for row in result['target_grid']}


def _mutate(result, edit):
    changed = copy.deepcopy(result)
    edit(changed)
    return changed


class OracleMutationTest(unittest.TestCase):
    """Each mutant must be rejected, and by the category that names the bug."""

    @classmethod
    def setUpClass(cls):
        cls.result = cli_json('preview', FIXTURE)
        cls.source = acceptance.load_fixture()

    def assert_catches(self, result, category):
        failures = acceptance.check(result, self.source)
        self.assertTrue(failures, 'mutant was not detected')
        self.assertIn(category, {f['category'] for f in failures},
                      f'{category} not among {failures}')

    def assert_not_caught(self, result, category):
        found = {f['category'] for f in acceptance.check(result, self.source)}
        self.assertNotIn(category, found,
                         f'a pure {category}-free bug must not be blamed on {category}: {found}')

    def test_offsetting_single_cell_error_is_detected(self):
        # domain: move 1/10 m3 from a to f1; total stays 7/5 m3, areas unchanged.
        def edit(result):
            sink, _ = _rows(result)
            entries = {entry['cell']: entry for entry in sink['domain']['contributions']}
            entries['a']['water_m3'] = '1/10'
            entries['f1']['water_m3'] = '7/10'
        mutant = _mutate(self.result, edit)
        self.assert_catches(mutant, 'cell_ledger')
        self.assert_catches(mutant, 'source_reconciliation')
        basins, _ = _rows(mutant)
        self.assertEqual(basins['domain']['water_m3'], '7/5')

    def test_offsetting_single_cell_error_in_target_is_detected(self):
        # A target listing an extra zero-area source cell cannot hide behind its total.
        def edit(result):
            _, targets = _rows(result)
            targets['frag-mix']['source_cells'] = ['f1', 'f2', 'a']
        self.assert_catches(_mutate(self.result, edit), 'cell_ledger')

    def test_offsetting_single_cell_area_error_is_detected(self):
        # Keep domain valid area and water totals intact but swap the area split.
        def edit(result):
            basins, _ = _rows(result)
            entries = {entry['cell']: entry for entry in basins['frags']['contributions']}
            entries['f1']['m2'] = 1
            entries['f2']['m2'] = 3
            entries['f1']['water_m3'] = '3/10'
            entries['f2']['water_m3'] = '9/10'
        self.assert_catches(_mutate(self.result, edit), 'cell_ledger')

    def test_water_total_error_is_detected(self):
        def edit(result):
            basins, _ = _rows(result)
            basins['frags']['water_m3'] = '7/5'
        self.assert_catches(_mutate(self.result, edit), 'water_total')

    def test_basin_area_normalisation_is_detected(self):
        # Mean divided by the nominal area instead of the valid area.
        def edit(result):
            basins, _ = _rows(result)
            basins['domain']['valid_mean_mm'] = str(acceptance.Q(7, 5) * 1000 / 12)
        self.assert_not_caught(_mutate(self.result, edit), 'overlap_union')
        self.assert_catches(_mutate(self.result, edit), 'area_normalization')

    def test_target_coverage_normalisation_is_detected(self):
        # Coverage divided by the valid area (always 1) instead of the box area.
        def edit(result):
            _, targets = _rows(result)
            targets['frag-mix']['coverage'] = 1
        self.assert_catches(_mutate(self.result, edit), 'area_normalization')

    def test_target_mean_normalisation_is_detected(self):
        # Mean divided by the target box area instead of the valid area.
        def edit(result):
            _, targets = _rows(result)
            targets['frag-mix']['valid_mean_mm'] = 200
        self.assert_catches(_mutate(self.result, edit), 'area_normalization')

    def test_overlapping_basin_sum_as_region_total_is_detected(self):
        def edit(result):
            basins, _ = _rows(result)
            basins['domain']['water_m3'] = '2'
            basins['domain']['valid_mean_mm'] = '250'
        self.assert_catches(_mutate(self.result, edit), 'overlap_union')

    def test_basin_outer_area_breakdown_is_detected(self):
        # Move the missing cell into "outside" while keeping the nominal area.
        def edit(result):
            basins, _ = _rows(result)
            basins['domain']['missing_in_grid_m2'] = 0
            basins['domain']['outside_grid_m2'] = 4
        self.assert_catches(_mutate(self.result, edit), 'aggregate_conservation')

    def test_fragment_double_count_is_detected(self):
        def edit(result):
            basins, _ = _rows(result)
            basins['frags']['valid_m2'] = 8
            basins['frags']['water_m3'] = '12/5'
        self.assert_catches(_mutate(self.result, edit), 'fragment_conservation')

    def test_measured_zero_reported_as_null_is_detected(self):
        def edit(result):
            basins, _ = _rows(result)
            basins['dry']['water_m3'] = None
            basins['dry']['valid_mean_mm'] = None
        self.assert_catches(_mutate(self.result, edit), 'zero_vs_null')

    def test_missing_reported_as_zero_is_detected(self):
        def edit(result):
            basins, _ = _rows(result)
            basins['miss']['water_m3'] = 0
            basins['miss']['valid_mean_mm'] = 0
        self.assert_catches(_mutate(self.result, edit), 'zero_vs_null')

    def test_target_outside_grid_reported_as_zero_is_detected(self):
        def edit(result):
            _, targets = _rows(result)
            targets['outside']['water_m3'] = 0
            targets['outside']['valid_mean_mm'] = 0
        self.assert_catches(_mutate(self.result, edit), 'zero_vs_null')

    def test_correct_products_have_no_findings(self):
        self.assertEqual(acceptance.check(self.result, self.source), [])

    def test_oracle_rejects_an_unrelated_fixture(self):
        other = cli_json('preview', ROOT / 'examples' / 'baseline.json')
        self.assertTrue(acceptance.check(other, self.source))


class ArchivedProductsTest(ArchiveReadMixin, unittest.TestCase):
    """The archived products reconcile with the archived source, and the
    same oracle also guards the archived copies rather than only the preview."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.archive, cls.published = _publish_hand()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_archived_products_reject_a_tampered_row(self):
        result = cli_json('show', self.archive, 'hand')
        source = cli_json('source', self.archive, 'hand')
        basins, _ = _rows(result)
        row = basins['shed-left']
        row['water_m3'] = '1'
        entries = {entry['cell']: entry for entry in row['contributions']}
        entries['a']['water_m3'] = '1/10'
        entries['f1']['water_m3'] = '9/10'
        failures = acceptance.check(result, source)
        self.assertIn('cell_ledger', {f['category'] for f in failures})
        self.assertIn('source_reconciliation', {f['category'] for f in failures})
        self.assertIn('water_total', {f['category'] for f in failures})

    def test_tampered_result_is_caught_by_archive_digest(self):
        tampered = Path(self.tmp.name) / 'tampered.sqlite'
        tampered.write_bytes(self.archive.read_bytes())
        with sqlite3.connect(tampered) as db:
            source, result = db.execute(
                'SELECT source, result FROM editions WHERE name=?', ('hand',)).fetchone()
            doc = json.loads(result)
            doc['basins'][0]['water_m3'] = '99'
            db.execute('UPDATE editions SET result=? WHERE name=?',
                       (json.dumps(doc, sort_keys=True, separators=(',', ':')), 'hand'))
        proc = run_cli('show', tampered, 'hand')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('digest mismatch', proc.stderr)


if __name__ == '__main__':
    unittest.main()
