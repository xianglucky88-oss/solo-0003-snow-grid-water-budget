"""Three-reservoir offline inference from certified snow water only.

Rules implemented here:
* inflow is built solely from measured (non-null) source cells; measured zero
  and unobserved cells are reported separately and never conflated;
* responsibility zones are mutually disjoint; routing is an acyclic graph with
  at most one outlet per reservoir and exactly one terminal;
* missing-water caps are granted per source band. One band keeps one shared
  budget even when its unobserved cells touch several upstream zones, so the
  same cap can never be spent once per zone;
* certified retention/release follow a single-outlet bucket balance and are
  reported regardless of any missing-data cap;
* simultaneous overflow claims are tested by exact rational linear programs
  over the shared band budgets.
"""
from fractions import Fraction

from .common import number, require, scalar, unique
from .field import intersect
from .geometry import area
from .lputil import linprog
from .topology import read_topology, read_zones

MAX_ENUMERATED_RESERVOIRS = 10


def read_bands(doc, source):
    rows = unique(doc['source_bands'])
    band_of, covered = {}, set()
    for band_id, row in rows.items():
        require(isinstance(row.get('cells'), list) and row['cells'], f'band without cells: {band_id}')
        for cell_id in row['cells']:
            require(isinstance(cell_id, str), f'band cell id must be a string: {band_id}')
            require(cell_id not in covered, f'cell belongs to multiple source bands: {cell_id}')
            require(any(cell_id == key for key, _, _ in source), f'band references unknown cell: {cell_id}')
            covered.add(cell_id)
            band_of[cell_id] = band_id
    source_ids = {key for key, _, _ in source}
    require(covered == source_ids,
            f'source bands must partition every source cell; missing {sorted(source_ids - covered)}')
    caps = {}
    for entry in doc.get('missing_caps_m3', []):
        band_id = entry['band']
        require(band_id in rows, f'cap references unknown source band: {band_id}')
        require(band_id not in caps, f'duplicate cap for source band: {band_id}')
        caps[band_id] = number(entry['limit_m3'], 'missing cap')
    return band_of, caps


def parse_reservoirs(doc):
    rows = unique(doc['reservoirs'])
    routes, parsed, zone_rows = [], [], []
    for key, row in rows.items():
        capacity = number(row['capacity_m3'], 'capacity')
        require(capacity >= 0, f'capacity must be non-negative: {key}')
        outlet = row.get('outlet')
        require(outlet is None or isinstance(outlet, str), f'invalid outlet: {key}')
        if outlet is not None:
            routes.append({'from': key, 'to': outlet})
        zone_id = row['zone']
        require(isinstance(zone_id, str) and zone_id.strip(), f'invalid zone id: {key}')
        parsed.append((key, zone_id, capacity, outlet))
        zone_rows.append({'id': zone_id, 'box_m': row['box_m']})
    zone_ids = [z['id'] for z in zone_rows]
    require(len(set(zone_ids)) == len(zone_ids), 'each responsibility zone may serve one reservoir')
    zones = read_zones(zone_rows)
    return parsed, zones, routes


def zone_evidence(source, box, band_of):
    nominal = area(box)
    valid = missing = measured_zero = local_water = 0
    contributions, unobserved_cells = [], []
    eligible_area = {}
    for cell, section, depth in intersect(source, box):
        if depth is None:
            missing += section
            unobserved_cells.append(cell)
            record = eligible_area.setdefault(band_of[cell], {'m2': 0, 'cells': []})
            record['m2'] += section
            record['cells'].append(cell)
        else:
            valid += section
            volume = section * depth / 1000
            local_water += volume
            if depth == 0:
                measured_zero += section
            contributions.append({'cell': cell, 'band': band_of[cell], 'm2': scalar(section),
                                  'swe_mm': scalar(depth), 'water_m3': scalar(volume)})
    return {'nominal_m2': nominal, 'valid_m2': valid, 'unobserved_m2': missing,
            'measured_zero_m2': measured_zero, 'outside_grid_m2': nominal - valid - missing,
            'local_water': local_water, 'contributions': contributions,
            'unobserved_cells': unobserved_cells, 'eligible_area': eligible_area}


def simulate(order, upstream, capacities, local_water, supplements):
    """Nonlinear bucket forward run; supplements keyed by reservoir id."""
    retention, release, inflows = {}, {}, {}
    for key in order:
        inflow = local_water[key] + supplements.get(key, 0) + sum(release[p] for p in upstream[key])
        inflows[key] = inflow
        retention[key] = min(inflow, capacities[key])
        release[key] = inflow - retention[key]
    return inflows, retention, release


def certified_run(order, upstream, capacities, local_water):
    return simulate(order, upstream, capacities, local_water, {})


def evaluate_claim(claim, order, upstream, capacities, local_water, pairs, caps,
                   certified_retention):
    """Maximize the extra release delta supportable for every claimant.

    Delta is the spill beyond the already-certified release, so a reservoir
    that overflows on evidence alone (delta >= 0) is never denied by a
    missing-data discussion.
    """
    n_vars = len(pairs)
    objective = [0] * n_vars + [1]
    band_of_pair = [band for band, _ in pairs]
    zone_of_pair = [zone for _, zone in pairs]
    claim_set = set(claim)
    free = [key for key in order if key not in claim_set]
    # Certified free capacity that supplements must fill before any new spill.
    headroom = {key: capacities[key] - certified_retention[key] for key in order}
    best = {'delta': Fraction(0), 'status': 'optimal', 'regime': None, 'x': None}
    trials = []

    for mask in range(2 ** len(free)):
        spilling = set(claim_set)
        for bit, key in enumerate(free):
            if mask >> bit & 1:
                spilling.add(key)
        rows, rhs = [], []
        # One shared budget per source band: every zone drawing from a band
        # appears in the same sum, never as separate per-zone allowances.
        for band_id in sorted(set(band_of_pair)):
            if band_id in caps:
                rows.append([1 if band_of_pair[j] == band_id else 0 for j in range(n_vars)] + [0])
                rhs.append(caps[band_id])

        # Inflow increment d_k(x) relative to the certified state. Local
        # supplements enter directly; increments through a spilling parent
        # propagate along the topology.
        delta_coeff, const = {}, {}
        for key in order:
            v = {j: Fraction(1) for j in range(n_vars) if zone_of_pair[j] == key}
            c = local_water[key]
            for parent in upstream[key]:
                if parent in spilling:
                    for j, weight in delta_coeff[parent].items():
                        v[j] = v.get(j, 0) + weight
                    c -= capacities[parent]
            delta_coeff[key], const[key] = v, c

        for key in order:
            v, c = delta_coeff[key], const[key]
            if key in claim_set:
                # New spill = inflow increment minus the certified free
                # capacity that must be filled first:
                #   d_k(x) - headroom_k >= delta
                #     <=> -v.x + delta <= -headroom_k.
                rows.append([-v.get(j, 0) for j in range(n_vars)] + [1])
                rhs.append(-headroom[key])
                # Spilling-branch guard: total inflow c + v.x >= cap.
                rows.append([-v.get(j, 0) for j in range(n_vars)] + [0])
                rhs.append(c - capacities[key])
            elif key in spilling:
                rows.append([-v.get(j, 0) for j in range(n_vars)] + [0])
                rhs.append(c - capacities[key])
            else:  # holding branch: c + v.x <= cap
                rows.append([v.get(j, 0) for j in range(n_vars)] + [0])
                rhs.append(capacities[key] - c)

        status, x, value = linprog(objective, rows, rhs)
        regime = {'spilling': sorted(spilling), 'holding': sorted(set(order) - spilling),
                  'status': status,
                  'max_extra_m3': 'unbounded' if status == 'unbounded'
                  else (scalar(value) if status == 'optimal' else None)}
        trials.append(regime)
        if status == 'infeasible':
            continue
        if status == 'unbounded':
            # Cap delta at 1 to obtain a finite, forward-checkable witness.
            bounded = rows + [[0] * n_vars + [1]]
            status2, x2, value2 = linprog(objective, bounded, rhs + [1])
            require(status2 == 'optimal' and value2 == 1, f'witness construction failed for {claim}')
            best.update(status='unbounded', regime=regime, x=x2, delta=1)
            break
        if best['x'] is None or value > best['delta']:
            best.update(status='optimal', regime=regime, x=x, delta=value)
    best['trials'] = trials
    return best


def reservoir_product(doc, source):
    band_of, caps = read_bands(doc, source)
    parsed, zones, routes = parse_reservoirs(doc)
    ids = [key for key, _, _, _ in parsed]
    require(len(ids) <= MAX_ENUMERATED_RESERVOIRS, 'too many reservoirs for exact enumeration')
    upstream, order, terminal, outlet = read_topology(ids, routes)
    capacities = {key: cap for key, _, cap, _ in parsed}

    evidence, local_water = {}, {}
    for key, zone_id, _, _ in parsed:
        facts = zone_evidence(source, zones[zone_id], band_of)
        evidence[key] = facts
        local_water[key] = facts['local_water']
    inflows, retention, release = certified_run(order, upstream, capacities, local_water)

    # One supplement variable per (source band, zone) pair that actually has
    # unobserved cells; the band-level sum enforces the single shared budget.
    pairs = []
    for key in ids:
        for band_id in sorted(evidence[key]['eligible_area']):
            pairs.append((band_id, key))
    all_bands = sorted(set(band_of.values()))

    measured_total = sum(local_water.values())
    certified_balance = {
        'measured_snow_m3': scalar(measured_total),
        'retained_total_m3': scalar(sum(retention.values())),
        'terminal_id': terminal,
        'terminal_release_m3': scalar(release[terminal]),
        'identity_holds': measured_total == sum(retention.values()) + release[terminal]}

    reservoir_reports = []
    for key, zone_id, cap, _ in parsed:
        facts = evidence[key]
        eligible = [{'band': band_id,
                     'unobserved_m2': scalar(detail['m2']),
                     'unobserved_cells': detail['cells'],
                     'cap_m3': scalar(caps[band_id]) if band_id in caps else None}
                    for band_id, detail in sorted(facts['eligible_area'].items())]
        reservoir_reports.append({
            'id': key, 'outlet': outlet.get(key),
            'zone': {'id': zone_id, 'box_m': [scalar(v) for v in zones[zone_id]],
                     'area_m2': scalar(facts['nominal_m2'])},
            'start_capacity_m3': scalar(cap),
            'inflow_certified_m3': {
                'local_snow_m3': scalar(local_water[key]),
                'from_upstream_m3': scalar(sum(release[p] for p in upstream[key])),
                'total_m3': scalar(inflows[key]),
                'contributions': facts['contributions'],
                'measured_zero_m2': scalar(facts['measured_zero_m2']),
                'unobserved_m2': scalar(facts['unobserved_m2']),
                'unobserved_cells': facts['unobserved_cells'],
                'outside_grid_m2': scalar(facts['outside_grid_m2'])},
            'retention_certified_m3': scalar(retention[key]),
            'release_certified_m3': scalar(release[key]),
            'missing_budget': {'eligible_bands': eligible,
                               'capped_bands': [b for b in all_bands if b in caps],
                               'unbounded_bands': [b for b in all_bands if b not in caps]}})

    claims_out = []
    for claim in doc.get('overflow_claims', []):
        require(isinstance(claim, list) and claim and len(set(claim)) == len(claim),
                'overflow claim must be a non-empty list of distinct reservoir ids')
        require(all(key in capacities for key in claim), f'claim references unknown reservoir: {claim}')
        best = evaluate_claim(claim, order, upstream, capacities, local_water,
                              pairs, caps, retention)
        x = best['x']
        supplements = {key: Fraction(0) for key in ids}
        supplement_rows = []
        for j, (band_id, zone_id) in enumerate(pairs):
            if x is not None and x[j] > 0:
                supplements[zone_id] += x[j]
                supplement_rows.append({'band': band_id, 'zone': zone_id, 'm3': scalar(x[j])})
        supported = best['status'] == 'unbounded' or best['delta'] > 0
        witness = None
        if supported:
            w_in, w_ret, w_rel = simulate(order, upstream, capacities, local_water, supplements)
            for key in claim:
                extra = w_rel[key] - release[key]
                require(extra >= best['delta'], f'witness fails claimant {key}: {claim}')
            supplement_total = sum(supplements.values())
            witness = {
                'target_extra_m3': scalar(best['delta']),
                'supplements_m3': supplement_rows,
                'supplement_total_m3': scalar(supplement_total),
                'simulation': [{'reservoir': key, 'inflow_m3': scalar(w_in[key]),
                                'retention_m3': scalar(w_ret[key]),
                                'release_m3': scalar(w_rel[key])} for key in order],
                'mass_balance': {
                    'measured_snow_m3': scalar(measured_total),
                    'supplement_total_m3': scalar(supplement_total),
                    'retained_total_m3': scalar(sum(w_ret.values())),
                    'terminal_release_m3': scalar(w_rel[terminal]),
                    'identity_holds': measured_total + supplement_total ==
                    sum(w_ret.values()) + w_rel[terminal]}}
        claims_out.append({'reservoirs': claim, 'supported': supported,
                           'max_extra_m3': 'unbounded' if best['status'] == 'unbounded'
                           else scalar(best['delta']),
                           'certified_overflow': {key: scalar(release[key]) for key in claim},
                           'already_overflows_on_evidence': all(release[key] > 0 for key in claim),
                           'binding_regime': best['regime'], 'regime_trials': best['trials'],
                           'witness': witness})

    return {'reservoir_count': len(ids), 'terminal_id': terminal, 'topological_order': order,
            'source_band_caps_m3': {band: scalar(limit) for band, limit in sorted(caps.items())},
            'certified_mass_balance': certified_balance,
            'reservoirs': reservoir_reports, 'overflow_claims': claims_out}
