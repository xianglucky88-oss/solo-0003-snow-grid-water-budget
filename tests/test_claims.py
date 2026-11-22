"""Exclusive declaration view tests.

Run with: python3 -m unittest discover -s tests -v
"""
import copy
import unittest
from fractions import Fraction
from pathlib import Path

from snowbudget.common import decode
from snowbudget.workflow import materialize

EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'


def F(value):
    return 0 if value is None else Fraction(str(value))


def load(name):
    return decode((EXAMPLES / name).read_text(encoding='utf-8'))


class DeclarationViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alpha_doc = load('claims_alpha.json')
        cls.bravo_doc = load('claims_bravo.json')
        cls.alpha = materialize(cls.alpha_doc)
        cls.bravo = materialize(cls.bravo_doc)

    def test_inputs_share_geometry_and_only_swap_certificate_owners(self):
        self.assertEqual(self.alpha_doc['basins'], self.bravo_doc['basins'])
        self.assertEqual(self.alpha_doc['source_cells'], self.bravo_doc['source_cells'])
        self.assertEqual([(c['id'], c['box_m']) for c in self.alpha_doc['declaration']['certificates']],
                         [(c['id'], c['box_m']) for c in self.bravo_doc['declaration']['certificates']])
        self.assertNotEqual([c['basin'] for c in self.alpha_doc['declaration']['certificates']],
                            [c['basin'] for c in self.bravo_doc['declaration']['certificates']])

    def test_swap_keeps_ordinary_basin_reports_identical(self):
        self.assertEqual(self.alpha['basins'], self.bravo['basins'])

    def test_swap_keeps_parent_totals_but_moves_exclusive_amounts(self):
        da, db = self.alpha['declaration'], self.bravo['declaration']
        self.assertEqual(da['reconciliation'], db['reconciliation'])
        self.assertEqual(da['disputed'], db['disputed'])
        self.assertEqual(da['remainder'], db['remainder'])
        self.assertEqual(da['unobserved'], db['unobserved'])
        ex_a = {row['basin']: row for row in da['exclusive']}
        ex_b = {row['basin']: row for row in db['exclusive']}
        self.assertEqual((ex_a['alpha']['valid_m2'], ex_a['alpha']['water_m3']), (3, 1))
        self.assertEqual((ex_b['bravo']['valid_m2'], ex_b['bravo']['water_m3']), (3, 1))
        self.assertEqual(ex_a['bravo']['valid_m2'], 0)
        self.assertEqual(ex_b['alpha']['valid_m2'], 0)
        self.assertEqual(sum(F(r['valid_m2']) for r in da['exclusive']),
                         sum(F(r['valid_m2']) for r in db['exclusive']))
        self.assertEqual(sum(F(r['water_m3']) for r in da['exclusive']),
                         sum(F(r['water_m3']) for r in db['exclusive']))

    def test_parts_reconcile_to_parent_report_independently_of_subtraction(self):
        for result in (self.alpha, self.bravo):
            decl = result['declaration']
            for tie in decl['reconciliation'].values():
                self.assertTrue(tie['reconciles'])
            mother = next(row for row in result['basins'] if row['id'] == 'mother')
            total_area = sum(F(row['total_m2']) for row in decl['exclusive']) + F(decl['disputed']['total_m2']) \
                + F(decl['remainder']['total_m2']) + F(decl['unobserved']['total_m2'])
            self.assertEqual(total_area, F(mother['area_m2']))
            total_water = sum(F(row['water_m3']) for row in decl['exclusive']) \
                + F(decl['disputed']['water_m3']) + F(decl['remainder']['water_m3'])
            self.assertEqual(total_water, F(mother['water_m3']))
            self.assertEqual(F(decl['unobserved']['missing_in_grid_m2']), F(mother['missing_in_grid_m2']))
            self.assertEqual(F(decl['unobserved']['outside_grid_m2']), F(mother['outside_grid_m2']))

    def test_partition_values_for_alpha_owner(self):
        decl = self.alpha['declaration']
        self.assertEqual(F(decl['disputed']['valid_m2']), 3)
        self.assertEqual(F(decl['disputed']['water_m3']), Fraction(1, 2))
        self.assertEqual(F(decl['remainder']['valid_m2']), 6)
        self.assertEqual(F(decl['remainder']['water_m3']), Fraction(3, 2))
        self.assertEqual(F(decl['unobserved']['total_m2']), 12)
        self.assertEqual(F(decl['unobserved']['missing_in_grid_m2']), 4)
        self.assertEqual(F(decl['unobserved']['outside_grid_m2']), 8)
        self.assertEqual(decl['unobserved']['water_m3'], None)
        self.assertEqual(decl['unobserved']['missing_cells'], ['m'])

    def test_bundles_without_declaration_keep_their_original_shape(self):
        for name in ('baseline.json', 'changed.json'):
            result = materialize(load(name))
            self.assertEqual(sorted(result), ['basins', 'survey', 'target_grid'])

    def test_certificates_over_unobserved_ground_never_become_exclusive(self):
        doc = copy.deepcopy(self.alpha_doc)
        doc['declaration']['certificates'] = [
            {'id': 'm1', 'basin': 'alpha', 'box_m': [0, 2, 1, 3]}]
        decl = materialize(doc)['declaration']
        self.assertEqual(decl['exclusive'][0]['valid_m2'], 0)
        self.assertEqual(decl['unobserved']['missing_in_grid_m2'], 4)

        doc = copy.deepcopy(self.alpha_doc)
        doc['declaration']['certificates'] = [
            {'id': 'o1', 'basin': 'charlie', 'box_m': [0, 4, 2, 6]}]
        decl = materialize(doc)['declaration']
        self.assertEqual(decl['exclusive'][2]['valid_m2'], 0)
        self.assertEqual(decl['unobserved']['outside_grid_m2'], 8)

    def test_no_certificates_routes_overlap_to_disputed_and_solo_ground_to_remainder(self):
        doc = copy.deepcopy(self.alpha_doc)
        doc['declaration']['certificates'] = []
        decl = materialize(doc)['declaration']
        self.assertTrue(all(row['valid_m2'] == 0 for row in decl['exclusive']))
        self.assertEqual((F(decl['disputed']['valid_m2']), F(decl['disputed']['water_m3'])),
                         (6, Fraction(3, 2)))
        self.assertEqual((F(decl['remainder']['valid_m2']), F(decl['remainder']['water_m3'])),
                         (6, Fraction(3, 2)))

    def test_fractional_certificate_coordinates_are_exact(self):
        doc = copy.deepcopy(self.alpha_doc)
        doc['declaration']['certificates'] = [
            {'id': 'f1', 'basin': 'alpha', 'box_m': ['1/2', 0, '3/2', 2]}]
        decl = materialize(doc)['declaration']
        self.assertTrue(all(tie['reconciles'] for tie in decl['reconciliation'].values()))
        self.assertEqual(decl['exclusive'][0]['valid_m2'], 2)
        self.assertEqual(decl['exclusive'][0]['water_m3'], '1/2')

    def test_invalid_declarations_are_rejected(self):
        mutations = [
            ('unknown parent', lambda d: d['declaration'].__setitem__('parent_basin', 'nope')),
            ('parent as child', lambda d: d['declaration'].__setitem__('sub_basins', ['alpha', 'mother']),
             ),
            ('duplicate child', lambda d: d['declaration'].__setitem__('sub_basins', ['alpha', 'alpha'])),
            ('unknown child', lambda d: d['declaration'].__setitem__('sub_basins', ['ghost'])),
            ('outside parent', lambda d: (
                d['declaration']['certificates'][0].__setitem__('box_m', [3, 0, 5, 1]),
                d['declaration']['certificates'][0].__setitem__('basin', 'bravo'))),
            ('outside owner', lambda d: d['declaration']['certificates'][1].__setitem__('box_m', [3, 2, 4, 3])),
            ('overlapping certificates',
             lambda d: d['declaration']['certificates'][1].__setitem__('box_m', [1, 1, 2, 2])),
            ('duplicate certificate', lambda d: d['declaration']['certificates'][1].__setitem__('id', 'c1')),
            ('degenerate certificate', lambda d: d['declaration']['certificates'][0].__setitem__('box_m', [1, 0, 1, 2])),
            ('certificates not list', lambda d: d['declaration'].__setitem__('certificates', {})),
            ('empty sub_basins', lambda d: d['declaration'].__setitem__('sub_basins', [])),
            ('missing parent', lambda d: d['declaration'].pop('parent_basin')),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                doc = copy.deepcopy(self.alpha_doc)
                mutate(doc)
                with self.assertRaises(ValueError):
                    materialize(doc)


if __name__ == '__main__':
    unittest.main()
