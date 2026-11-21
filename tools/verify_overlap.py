#!/usr/bin/env python3
"""Acceptance checks for the two-grid difference product.

Run from the repository root:

    python3 tools/verify_overlap.py

The script is independent of the package internals: it only drives the
documented command line (`preview`/`publish`/`show`/`replay`) and checks the
published contract.
"""
import json
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples'


def run(*args, check=True):
    proc = subprocess.run([sys.executable, '-m', 'snowbudget', *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)
    if check and proc.returncode:
        raise AssertionError(f'command failed: {args}\n{proc.stderr}')
    return proc


def preview(name):
    return json.loads(run('preview', EXAMPLES / name).stdout)['difference']


def q(value):
    return Fraction(value) if value is not None else None


def check_common(doc, label):
    common = doc['common']
    assert common['area_m2'] == 24, f'{label}: common area must be 24, got {common["area_m2"]}'
    assert q(common['water_a_m3']) == Fraction(22, 5), f'{label}: alpha common water changed'
    assert q(common['water_b_m3']) == Fraction(22, 5), f'{label}: beta common water changed'
    assert q(common['difference_m3']) == 0 and common['higher'] is None, f'{label}: basin total must tie'
    # True-zero cells remain valid common evidence; the null cell never appears.
    cells_a = {c['cell'] for c in common['contributions_a']}
    cells_b = {c['cell'] for c in common['contributions_b']}
    assert {'a-west', 'a-east', 'a-zero'} <= cells_a, f'{label}: zero cell missing on alpha side'
    assert {'b-west', 'b-mid', 'b-east', 'b-zero'} <= cells_b, f'{label}: zero cell missing on beta side'
    zero_a = next(c for c in common['contributions_a'] if c['cell'] == 'a-zero')
    zero_b = next(c for c in common['contributions_b'] if c['cell'] == 'b-zero')
    assert zero_a['m2'] == 4 and q(zero_a['water_m3']) == 0
    assert zero_b['m2'] == 4 and q(zero_b['water_m3']) == 0
    all_cells = cells_a | cells_b | {c['cell'] for c in doc['a_only']['contributions']} \
        | {c['cell'] for c in doc['b_only']['contributions']}
    assert 'b-null' not in all_cells, f'{label}: null cell must never be evidence'
    # Single-side evidence is retained separately and is absent from the common block.
    assert q(doc['a_only']['water_m3']) == Fraction(2, 5) and doc['a_only']['area_m2'] == 4
    assert q(doc['b_only']['water_m3']) == Fraction(1, 5) and doc['b_only']['area_m2'] == 4
    assert doc['a_only']['contributions'][0]['cell'] == 'a-only'
    assert doc['b_only']['contributions'][0]['cell'] == 'b-only'


def zones(doc):
    return {z['id']: z for z in doc['basins'][0]['zones']}


def main():
    left, right = preview('paired-left.json'), preview('paired-right.json')
    check_common(left, 'left')
    check_common(right, 'right')

    zl, zr = zones(left), zones(right)
    # Zone totals are computed from real overlapping geometry, not apportioned.
    assert {z['common']['area_m2'] for z in zl.values()} == {12}
    assert q(zl['c-west']['common']['difference_m3']) == Fraction(4, 5)
    assert q(zl['c-east']['common']['difference_m3']) == Fraction(-4, 5)
    assert q(zr['c-west']['common']['difference_m3']) == Fraction(-2, 5)
    assert q(zr['c-east']['common']['difference_m3']) == Fraction(2, 5)
    # The whole-basin difference (0) is never used for county thresholds:
    # identical basin totals, yet the verdicts flip.
    assert zl['c-west']['triggers'] is True and zl['c-east']['triggers'] is True
    assert zr['c-west']['triggers'] is False and zr['c-east']['triggers'] is False
    assert zl['c-west']['threshold_m3'] == '1/2'
    # Each zone keeps its own source-cell references.
    assert {c['cell'] for c in zl['c-west']['common']['contributions_a']} == {'a-west'}
    assert {c['cell'] for c in zl['c-west']['common']['contributions_b']} == {'b-west', 'b-mid'}

    # Rejection paths: zone outside basin, overlapping source cells, legacy schema guard.
    bad = run('preview', EXAMPLES / 'paired-invalid.json', check=False)
    assert bad.returncode == 2 and 'outside basin' in bad.stderr, bad.stderr
    bad_grid = run('preview', EXAMPLES / 'invalid.json', check=False)
    assert bad_grid.returncode == 2, 'legacy invalid bundle must still be rejected'

    # Archive round trip and exact replay for both schema generations.
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / 'ledger.sqlite'
        run('publish', EXAMPLES / 'paired-left.json', db, 'pair-left')
        run('publish', EXAMPLES / 'baseline.json', db, 'legacy')
        shown = json.loads(run('show', db, 'pair-left').stdout)
        assert shown['difference']['common']['difference_m3'] == 0
        run('replay', db, 'pair-left')
        run('replay', db, 'legacy')
        duplicate = run('publish', EXAMPLES / 'paired-right.json', db, 'pair-left', check=False)
        assert duplicate.returncode == 2
        history = json.loads(run('history', db).stdout)
        assert [row['edition'] for row in history] == ['pair-left', 'legacy']

    print('PASS: overlap difference product acceptance checks')


if __name__ == '__main__':
    main()
