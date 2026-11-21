"""Exact rational linear programming for tiny synthetic feasibility checks.

Two-phase simplex with Bland's rule, using Fraction throughout so that
overflow thresholds (strict inequalities) are decided without floating error.
No third-party dependency; problem sizes here are a handful of variables.

Each A.x <= b row with negative RHS is multiplied by -1 first, so every row
has a non-negative RHS before slack / surplus + artificial variables are
introduced. The objective row keeps z - c_B.T, hence reduced costs are the
negated objective coefficients and Bland's rule takes the first negative one.
"""
from fractions import Fraction


def linprog(objective, rows, rhs):
    """Maximize objective . x subject to rows . x <= rhs and x >= 0.

    Returns (status, x, value) with status in
    {'optimal', 'unbounded', 'infeasible'}. For 'unbounded', x is a feasible
    point (with the returned finite value), not the infinite supremum.
    """
    n = len(objective)
    c = [Fraction(v) for v in objective]
    a = [[Fraction(v) for v in row] for row in rows]
    b = [Fraction(v) for v in rhs]
    if not a:
        if any(v > 0 for v in c):
            j = next(j for j, v in enumerate(c) if v > 0)
            x = [Fraction(0)] * n
            x[j] = 1
            return 'unbounded', x, c[j]
        return 'optimal', [Fraction(0)] * n, Fraction(0)

    # Normalize RHS signs once: multiplying a <= row by -1 keeps the same
    # feasible set and guarantees a clean starting basis.
    for i, value in enumerate(b):
        if value < 0:
            b[i] = -value
            a[i] = [-v for v in a[i]]

    m = len(a)
    # After RHS normalization a row needs surplus + artificial variables only
    # when it originated from a negative-RHS constraint. Non-negative rows
    # always start with a clean slack basic variable.
    needs_artificial = [value < 0 for value in rhs]
    extra = sum(2 if needs_artificial[i] else 1 for i in range(m))
    width = n + extra
    rhs_col = width
    tableau, basis = [], []
    col = n
    artificial_cols = set()
    for i in range(m):
        t = a[i] + [Fraction(0)] * extra + [b[i]]
        if needs_artificial[i]:
            t[col] = -1  # surplus
            col += 1
            t[col] = 1  # artificial, the starting basic variable
            artificial_cols.add(col)
            basis.append(col)
            col += 1
        else:
            t[col] = 1  # slack
            basis.append(col)
            col += 1
        tableau.append(t)

    def pivot(obj_row, entering):
        candidates = ((t[rhs_col] / t[entering], basis[r], r)
                      for r, t in enumerate(tableau) if t[entering] > 0)
        _, _, pivot_row = min(candidates)
        pivot = tableau[pivot_row]
        divisor = pivot[entering]
        for j in range(width + 1):
            pivot[j] /= divisor
        for r, t in enumerate(tableau + [obj_row]):
            if r == pivot_row or t[entering] == 0:
                continue
            factor = t[entering]
            for j in range(width + 1):
                t[j] -= factor * pivot[j]
        basis[pivot_row] = entering

    def canonicalize(obj_row, coeffs):
        for j in range(width):
            obj_row[j] = -Fraction(coeffs[j])
        obj_row[rhs_col] = Fraction(0)
        for i, base in enumerate(basis):
            factor = obj_row[base]
            if factor != 0:
                for j in range(width + 1):
                    obj_row[j] -= factor * tableau[i][j]

    def optimize(obj_row, phase):
        while True:
            entering = next((j for j in range(width)
                             if (phase == 1 or j not in artificial_cols) and obj_row[j] < 0), None)
            if entering is None:
                return 'optimal'
            if not any(t[entering] > 0 for t in tableau):
                # Standard simplex: a negative reduced cost with no positive
                # column entry is an unbounded improving ray. In phase I such a
                # ray cannot drive artificials out, so it means infeasible.
                return 'infeasible' if phase == 1 else 'unbounded'
            pivot(obj_row, entering)

    if artificial_cols:
        # Phase I minimizes the artificial sum: maximize -w with objective
        # coefficient -1 on every artificial column.
        phase_coeffs = [Fraction(-1) if j in artificial_cols else Fraction(0)
                        for j in range(width)]
        phase_one = [Fraction(0)] * (width + 1)
        canonicalize(phase_one, phase_coeffs)
        phase_status = optimize(phase_one, 1)
        # The phase-I objective stores max(-sum w): 0 means every artificial
        # variable was driven out; a strictly negative value means infeasible.
        if phase_status == 'unbounded' or phase_one[rhs_col] < 0:
            return 'infeasible', None, None
        # Pivot any still-basic artificial variable out, then drop all
        # artificial columns.
        for i in sorted((i for i, col_id in enumerate(basis) if col_id in artificial_cols), reverse=True):
            entering = next((j for j in range(width)
                             if j not in artificial_cols and tableau[i][j] != 0), None)
            if entering is None:
                del tableau[i]
                del basis[i]
            else:
                pivot(phase_one, entering)
        keep = [j for j in range(width) if j not in artificial_cols]
        tableau = [[t[j] for j in keep] + [t[rhs_col]] for t in tableau]
        basis = [sum(1 for j in keep if j <= col_id) - 1 for col_id in basis]
        width = len(keep)
        rhs_col = width
        artificial_cols = set()

    phase_two = [Fraction(0)] * (width + 1)
    coeffs = c + [Fraction(0)] * (width - n)
    canonicalize(phase_two, coeffs)
    status = optimize(phase_two, 2)

    x = [Fraction(0)] * n
    for i, base in enumerate(basis):
        if base < n:
            x[base] = tableau[i][rhs_col]
    return status, x, phase_two[rhs_col]
