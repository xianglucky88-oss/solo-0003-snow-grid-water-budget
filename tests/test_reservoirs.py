"""Regression tests for the certified three-reservoir inference.

Run with: PYTHONPATH=. python3 -m unittest discover -s tests -v
No third-party dependency; all arithmetic stays exact (Fraction).
"""
import copy
import unittest
from fractions import Fraction

from snowbudget.common import decode
from snowbudget.lputil import linprog
from snowbudget.workflow import materialize


def load(name):
    with open(name, encoding='utf-8') as handle:
        return decode(handle.read())


class LinearProgramTests(unittest.TestCase):
    def test_known_optima(self):
        cases = [
            ([1], [[1]], [1], 1),
            ([1, 1], [[1, 1]], [1], 1),
            ([3, 2], [[2, 1], [2, 3], [3, 1]], [18, 42, 24], 33),
            ([-1, 2], [[1, 1]], [3], 6),
            ([5, 4], [[6, 4], [1, 2], [-1, 1], [0, -1]], [24, 6, 1, 2], 21),
            # x >= 1 and x <= 3 -> optimum 3 (negative-RHS row path).
            ([1], [[-1], [1]], [-1, 3], 3),
        ]
        for obj, rows, rhs, optimum in cases:
            status, x, value = linprog(obj, rows, rhs)
            self.assertEqual(status, 'optimal')
            self.assertEqual(value, optimum)
            self.assertTrue(all(v >= 0 for v in x))
            for i, row in enumerate(rows):
                self.assertLessEqual(sum(Fraction(row[j]) * x[j] for j in range(len(obj))), rhs[i])

    def test_unbounded_and_infeasible(self):
        status, _, _ = linprog([1], [[-1]], [-1])  # x >= 1, maximize x
        self.assertEqual(status, 'unbounded')
        status, _, _ = linprog([1], [[-1], [1]], [-2, 1])  # x >= 2 and x <= 1
        self.assertEqual(status, 'infeasible')


class ReservoirExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load('examples/reservoir.json')
        cls.result = materialize(cls.doc)
        cls.res = cls.result['reservoirs']

    def test_certified_bucket_balance(self):
        by_id = {q['id']: q for q in self.res['reservoirs']}
        self.assertEqual(by_id['R1']['retention_certified_m3'], '3/2')
        self.assertEqual(by_id['R1']['release_certified_m3'], '5/2')
        self.assertEqual(by_id['R2']['retention_certified_m3'], 0)
        self.assertEqual(by_id['R2']['release_certified_m3'], 0)
        self.assertEqual(by_id['R3']['retention_certified_m3'], '1/2')
        self.assertEqual(by_id['R3']['release_certified_m3'], 4)
        balance = self.res['certified_mass_balance']
        self.assertTrue(balance['identity_holds'])
        self.assertEqual(balance['measured_snow_m3'], 6)
        self.assertEqual(balance['terminal_release_m3'], 4)

    def test_measured_zero_distinct_from_unobserved(self):
        by_id = {q['id']: q for q in self.res['reservoirs']}
        r2 = by_id['R2']['inflow_certified_m3']
        self.assertEqual(r2['measured_zero_m2'], 4)   # cell z, SWE = 0
        self.assertEqual(r2['unobserved_m2'], 4)     # cell n, SWE = null
        self.assertEqual(r2['unobserved_cells'], ['n'])
        self.assertEqual(r2['local_snow_m3'], 0)     # zero is evidence, not null

    def test_shared_budget_prevents_double_spend(self):
        claims = {tuple(c['reservoirs']): c for c in self.res['overflow_claims']}
        # Solo maxima under the single south cap of 1 m3.
        self.assertEqual(claims[('R1',)]['max_extra_m3'], 1)
        self.assertEqual(claims[('R2',)]['max_extra_m3'], '1/2')
        # Simultaneous claim cannot add the two solo allowances; the shared
        # band sum binds at 1, so the common headroom drops to 1/4.
        joint = claims[('R1', 'R2')]
        self.assertTrue(joint['supported'])
        self.assertEqual(joint['max_extra_m3'], '1/4')
        spent = sum(Fraction(s['m3']) for s in joint['witness']['supplements_m3'])
        self.assertLessEqual(spent, 1)
        self.assertTrue(joint['witness']['mass_balance']['identity_holds'])

    def test_unbounded_band_does_not_deny_certified_release(self):
        r3 = next(c for c in self.res['overflow_claims'] if c['reservoirs'] == ['R3'])
        self.assertEqual(r3['max_extra_m3'], 'unbounded')
        self.assertTrue(r3['witness']['mass_balance']['identity_holds'])
        # Certified R3 release stands on its own, independent of any cap.
        by_id = {q['id']: q for q in self.res['reservoirs']}
        self.assertEqual(by_id['R3']['release_certified_m3'], 4)

    def test_zero_budget_keeps_certified_overflow_distinct(self):
        doc = copy.deepcopy(self.doc)
        doc['missing_caps_m3'] = [{'band': 'south', 'limit_m3': 0}]
        doc['overflow_claims'] = [['R1']]
        claim = materialize(doc)['reservoirs']['overflow_claims'][0]
        # No extra release can be supported without budget...
        self.assertFalse(claim['supported'])
        self.assertEqual(claim['max_extra_m3'], 0)
        # ...but the certified overflow is still reported and not denied.
        self.assertTrue(claim['already_overflows_on_evidence'])
        self.assertEqual(claim['certified_overflow']['R1'], '5/2')

    def test_witness_conservation_identity(self):
        for claim in self.res['overflow_claims']:
            if claim['witness'] is None:
                continue
            mb = claim['witness']['mass_balance']
            self.assertTrue(mb['identity_holds'])
            self.assertEqual(Fraction(mb['measured_snow_m3']) +
                             Fraction(mb['supplement_total_m3']),
                             Fraction(mb['retained_total_m3']) +
                             Fraction(mb['terminal_release_m3']))

    def test_topology_and_zones(self):
        self.assertEqual(self.res['terminal_id'], 'R3')
        self.assertEqual(self.res['topological_order'], ['R1', 'R2', 'R3'])
        boxes = [tuple(q['zone']['box_m']) for q in self.res['reservoirs']]
        # Mutually disjoint zone rectangles.
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                dx = min(a[2], b[2]) - max(a[0], b[0])
                dy = min(a[3], b[3]) - max(a[1], b[1])
                self.assertTrue(dx <= 0 or dy <= 0)


class BasinProductUnchangedTests(unittest.TestCase):
    def test_basin_block_matches_baseline_contract(self):
        result = materialize(load('examples/reservoir.json'))
        basins = {b['id']: b for b in result['basins']}
        self.assertEqual(basins['all']['water_m3'], 6)
        self.assertEqual(basins['all']['valid_m2'], 12)
        self.assertEqual(basins['dry']['water_m3'], 0)       # measured zero
        self.assertIsNone(basins['miss']['water_m3'])        # unobserved

    def test_original_baseline_unchanged(self):
        result = materialize(load('examples/baseline.json'))
        self.assertNotIn('reservoirs', result)
        basins = {b['id']: b for b in result['basins']}
        self.assertEqual(basins['all']['water_m3'], 3)
        self.assertEqual(basins['cut']['water_m3'], '3/2')
        self.assertEqual(basins['dry']['water_m3'], 0)
        self.assertIsNone(basins['miss']['water_m3'])


class RejectionTests(unittest.TestCase):
    def setUp(self):
        self.base = load('examples/reservoir.json')

    def reject(self, mutate):
        doc = copy.deepcopy(self.base)
        mutate(doc)
        with self.assertRaises((ValueError, LookupError, TypeError, ArithmeticError)):
            materialize(doc)

    def test_overlapping_zones_rejected(self):
        bad = load('examples/reservoir_invalid.json')
        with self.assertRaises(ValueError):
            materialize(bad)

    def test_topology_violations_rejected(self):
        self.reject(lambda d: d['reservoirs'][2].__setitem__('outlet', 'R1'))   # cycle
        self.reject(lambda d: d['reservoirs'][0].__setitem__('outlet', 'R9'))   # unknown
        self.reject(lambda d: d['reservoirs'][0].__delitem__('outlet'))         # two terminals
        self.reject(lambda d: d['reservoirs'][2].__setitem__('outlet', 'R3'))   # self loop

    def test_band_partition_and_caps_rejected(self):
        self.reject(lambda d: d['source_bands'][1]['cells'].remove('n'))
        self.reject(lambda d: d['source_bands'][0]['cells'].append('q'))
        self.reject(lambda d: d['missing_caps_m3'].append({'band': 'x', 'limit_m3': 1}))
        self.reject(lambda d: d['missing_caps_m3'].append({'band': 'south', 'limit_m3': 2}))

    def test_claim_and_capacity_rejected(self):
        self.reject(lambda d: d.__setitem__('overflow_claims', [['R9']]))
        self.reject(lambda d: d.__setitem__('overflow_claims', [[]]))
        self.reject(lambda d: d['reservoirs'][0].__setitem__('capacity_m3', '-1'))


if __name__ == '__main__':
    unittest.main()
