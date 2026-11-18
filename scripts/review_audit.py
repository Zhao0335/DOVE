"""Replay and inspect the four published Test fact permutations."""
import argparse
import dataclasses
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_controlled_experiments_v929 import load, pair, rows


def audit(inputs, destination):
    conditions = ['frozen', 'reverse_facts', 'shuffle_17', 'shuffle_29', 'shuffle_43']
    snapshots = {}
    for condition in conditions:
        solver = load(condition)
        records = {}
        for row in rows(inputs):
            output = solver.solve(row['query'])
            records[row['id']] = output
        snapshots[condition] = records
    changed = sorted(identifier for identifier, baseline in snapshots['frozen'].items()
                     if any(pair(snapshots[c][identifier]) != pair(baseline) for c in conditions[1:]))
    solver = load()
    inputs_by_id = {r['id']: r for r in rows(inputs)}
    details = []
    for identifier in changed:
        parsed = solver.parse_query(inputs_by_id[identifier]['query'])
        targets = [f for f in solver.target_facts(parsed) if solver._same_question_period(parsed, f)]
        groups = defaultdict(set)
        precision_intervals = defaultdict(list)
        facts = []
        for fact in targets:
            context = parsed.contexts[fact.context_id]
            unit = parsed.units.get(fact.unit_id, fact.unit_id)
            identity = (fact.concept, context.key, unit)
            groups[identity].add(str(fact.value))
            if fact.decimals is not None:
                radius = Decimal(0) if fact.decimals == 'INF' else Decimal(10) ** (-int(fact.decimals)) / 2
                precision_intervals[identity].append((fact.value-radius, fact.value+radius))
            facts.append(dict(value=str(fact.value), decimals=fact.decimals, context=dataclasses.asdict(context), unit=unit))
        outputs = {c: snapshots[c][identifier] for c in conditions}
        contexts = {o['diagnostics'].get('context_id') for o in outputs.values()}
        category = ('unequal_duplicate_target_values' if any(len(v) > 1 for v in groups.values())
                    else 'multiple_target_contexts' if len(contexts) > 1
                    else 'fixed_selected_context_changed_program')
        duplicate_intervals_overlap = None
        if category == 'unequal_duplicate_target_values':
            duplicate_intervals_overlap = all(
                bool(precision_intervals[k]) and
                max(lo for lo, hi in precision_intervals[k]) <= min(hi for lo, hi in precision_intervals[k])
                for k, values in groups.items() if len(values) > 1)
        details.append(dict(id=identifier, category=category,
                            duplicate_intervals_overlap=duplicate_intervals_overlap,
                            semantic_target_groups=len(groups), targets=facts, outputs=outputs))
    result = dict(conditions=conditions, total=len(inputs_by_id), changed_union=len(changed),
                  counts=dict(Counter(d['category'] for d in details)),
                  changed_per_condition={c: sum(pair(o) != pair(snapshots['frozen'][k])
                     for k, o in snapshots[c].items()) for c in conditions[1:]},
                  interpretation='Descriptive target/program differences. Context stability does not prove unique semantic eligibility or correctness.',
                  cases=details)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'cases'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    audit(args.test, args.out)
