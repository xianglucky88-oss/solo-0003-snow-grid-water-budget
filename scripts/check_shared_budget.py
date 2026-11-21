"""Reproducible conservation check for the shared missing-data budget.

Scenario in examples/reservoir.json: R1 and R2 are two upstream reservoirs
that both discharge into R3. The unobserved cells inside R1's zone (q) and
R2's zone (n) belong to the same source band "south", which carries a single
cap of 1 m3. The cap is one shared budget: it cannot be granted once per zone.

Run: PYTHONPATH=. python3 scripts/check_shared_budget.py
"""
from fractions import Fraction

from snowbudget.common import decode
from snowbudget.field import cells
from snowbudget.reservoirs import (parse_reservoirs, read_bands, simulate,
                                   zone_evidence)
from snowbudget.topology import read_topology
from snowbudget.workflow import materialize

BUNDLE = 'examples/reservoir.json'


def main():
    doc = decode(open(BUNDLE, encoding='utf-8').read())
    product = materialize(doc)['reservoirs']
    claims = {tuple(c['reservoirs']): c for c in product['overflow_claims']}
    solo_r1 = claims[('R1',)]['max_extra_m3']
    solo_r2 = claims[('R2',)]['max_extra_m3']
    joint = claims[('R1', 'R2')]

    source = cells(doc['source_cells'])
    bands, caps = read_bands(doc, source)
    parsed, zones, routes = parse_reservoirs(doc)
    ids = [key for key, _, _, _ in parsed]
    upstream, order, terminal, _ = read_topology(ids, routes)
    capacity = {key: cap for key, _, cap, _ in parsed}
    local = {key: zone_evidence(source, zones[zone], bands)['local_water']
             for key, zone, _, _ in parsed}

    supplements = {key: Fraction(0) for key in ids}
    for item in joint['witness']['supplements_m3']:
        supplements[item['zone']] += Fraction(item['m3'])
    spent = sum(supplements.values())

    inflows, retention, release = simulate(order, upstream, capacity, local, supplements)
    measured = sum(local.values())
    identity_lhs = measured + spent
    identity_rhs = sum(retention.values()) + release[terminal]

    print(f'solo extra release  R1={solo_r1}  R2={solo_r2}')
    print(f'simultaneous extra release (shared budget) = {joint["max_extra_m3"]}')
    print(f'witness supplements = {dict(supplements)}; total = {spent} <= cap {caps["south"]}')
    print(f'forward balance: {measured} + {spent} = {identity_lhs}; '
          f'retained {sum(retention.values())} + terminal release {release[terminal]} = {identity_rhs}')

    assert spent <= caps['south'], 'shared band budget was overspent'
    assert Fraction(joint['max_extra_m3']) < Fraction(solo_r1), 'joint claim must not pool two allowances'
    assert identity_lhs == identity_rhs, 'mass balance broken'
    print('OK: one source band keeps one budget; double spend rejected; mass balance closes.')


if __name__ == '__main__':
    main()
