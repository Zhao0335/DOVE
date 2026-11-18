"""Recompute all 120 published controls using the archived Fraction oracle."""
import argparse
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path
from paper_controlled_experiments_v929 import load, pair

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    solver = load()
    records = []
    for case in json.loads((ROOT / 'analysis/controlled_equations.json').read_text()):
        c = case['construction']
        truth = Fraction(c['a']) + c['weight_b'] * Fraction(c['b'])
        assert truth == Fraction(case['oracle_pair']['calculated_value'])
        result = solver.solve(case['query'])
        records.append(dict(id=case['id'], group=case['group'],
                            correct=pair(result) == pair(case['oracle_pair']), result=result))
    counts = Counter(r['group'] for r in records if r['correct'])
    actual = {g:counts[g] for g in sorted({r['group'] for r in records})}
    assert actual == dict(clean_complete=30, clean_partial_decoy=0,
                          corrupt_complete=30, corrupt_partial_decoy=30), actual
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(correct=actual, records=records), indent=2))
    print(actual)
