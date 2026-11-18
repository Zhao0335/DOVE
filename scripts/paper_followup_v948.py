"""Matched offline baselines, a 2x2 ablation, and parser/reference strata.

Run from this repository or its supplementary archive. No model or network calls.
Previously measured arms may be reused only with their protocol and byte hashes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import platform
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_controlled_experiments_v929 import load, rows, pair, sha

CONDITIONS = ['frozen', 'no_unlinked', 'no_equality_penalties', 'no_both',
              'official_full', 'official_extract', 'official_negate', 'zero']
FIELDS = ('extracted_value', 'calculated_value')


def write(path, obj):
    path = Path(path)
    with path.open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')


def block_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise RuntimeError('Network is disabled during this experiment')


def worker(condition, input_path, output):
    sys.addaudithook(block_network)
    records = rows(input_path)
    start = time.perf_counter()
    if condition.startswith('official_'):
        path = ROOT / 'vendor/official/finreason_task3/baselines/rule_baseline.py'
        spec = importlib.util.spec_from_file_location('organizer_baseline', path)
        official = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = official
        spec.loader.exec_module(official)
    elif condition != 'zero':
        solver = load('no_equality_penalties' if condition == 'no_both' else condition)
        if condition == 'no_both':
            d = sys.modules['finreason.dimensions']
            j = sys.modules['finreason.selection_dimensions']
            original = d.generate_candidates
            def generate(p, f):
                return [c for c in original(p, f) if c['method'] != 'dimension_unlinked']
            d.generate_candidates = j.generate_candidates = generate
    output_rows = []
    for record in records:
        # ID is an output join key only. Neither rule nor reference reaches inference.
        if condition.startswith('official_'):
            p = official.predict(record['query'], condition.split('_', 1)[1])
            out = {k: getattr(p, k) for k in FIELDS}
            out.update(status='ok', method=p.mechanism)
        elif condition == 'zero':
            out = dict(extracted_value='0', calculated_value='0', status='ok', method='constant')
        else:
            try:
                p = solver.solve(record['query'])
                out = {k: p[k] for k in (*FIELDS, 'status')}
                out['method'] = p['diagnostics'].get('method')
            except Exception as e:
                out = dict(extracted_value=None, calculated_value=None,
                           status='raised', method=None, exception=type(e).__name__)
        output_rows.append(dict(id=record['id'], **out))
    write(output, dict(condition=condition, rows=output_rows,
                       seconds=time.perf_counter()-start, origin='fresh_offline_execution'))


def summary(cases, condition, predicate=lambda r: True):
    selected = [r for r in cases if r['condition'] == condition and predicate(r)]
    return dict(n=len(selected), correct=sum(r['pair_correct'] for r in selected),
                extracted_correct=sum(r['extracted_correct'] for r in selected),
                calculated_correct=sum(r['calculated_correct'] for r in selected),
                gain=sum(r['gain'] for r in selected), loss=sum(r['loss'] for r in selected),
                changed=sum(r['changed'] for r in selected),
                exceptions=sum(r['status'] == 'raised' for r in selected))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--practice', type=Path, default=ROOT/'data/public_dev.jsonl')
    ap.add_argument('--out', type=Path, default=ROOT/'runs/paper_followup_v948')
    ap.add_argument('--cache', type=Path, help='Verified v929 output directory; omit for independent rerun')
    ap.add_argument('--worker', choices=CONDITIONS)
    ap.add_argument('--worker-output', type=Path)
    a = ap.parse_args()
    if a.worker:
        worker(a.worker, a.practice, a.worker_output)
        return
    sys.addaudithook(block_network)
    a.out = a.out.resolve()
    a.practice = a.practice.resolve()
    if a.out.exists():
        raise FileExistsError('Preserve prior evidence; choose a new --out directory')
    a.out.mkdir(parents=True)
    inputs = rows(a.practice)
    ids = [r['id'] for r in inputs]
    assert len(ids) == len(set(ids)) == 332
    sources = [Path(__file__), Path(__file__).with_name('paper_controlled_experiments_v929.py')]
    sources += sorted((ROOT/'runs/structured_test_v5/source/finreason').glob('*.py'))
    sources += sorted((ROOT/'data/rules').glob('*'))
    sources += sorted((ROOT/'vendor/official/finreason_task3/task3_core').glob('*.py'))
    sources += [ROOT/'vendor/official/finreason_task3/baselines/rule_baseline.py',
                ROOT/'vendor/official/finreason_task3/LICENSE']
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources if p.is_file()}
    reused = {}
    if a.cache:
        old = json.loads((a.cache/'protocol.json').read_text())
        assert sha(a.practice) == old['input_hashes']['data/public_dev.jsonl']
        for name, expected in old['frozen_sources'].items():
            assert sha(ROOT/'runs/structured_test_v5/source/finreason'/name) == expected
        for name in ['frozen', 'no_unlinked', 'no_equality_penalties']:
            path = a.cache/f'practice_{name}.json'
            reused[name] = dict(path=str(path.resolve()), sha256=sha(path))
    protocol = dict(schema_version='dove.followup.protocol/1',
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        conditions=CONDITIONS, derived_controls=['dove_extract', 'dove_negate'],
        input_sha256=sha(a.practice), source_sha256=hashes, reused=reused,
        scoring='Exact Decimal pair equality, commas and Unicode minus normalized; null is incorrect.',
        primary_denominator=332, selection='All public Practice records; no exclusions.',
        factorial='Unlinked aggregation on/off crossed with both equality penalties on/off.',
        strata=['frozen_parser_recovery', 'frozen_target_present', 'conflicting_query_reference', 'rule'],
        inference='Only query text; no IDs, rule IDs or labels supplied to prediction functions.',
        prior_exposure='Practice and single-arm results were inspected before this follow-up; retrospective.',
        protected_submission='No Test or Development predictions changed; no service requests.',
        python=platform.python_version())
    write(a.out/'protocol.json', protocol)
    outputs = {}
    for condition in CONDITIONS:
        dest = a.out/f'{condition}.json'
        if condition in reused:
            raw = json.loads(Path(reused[condition]['path']).read_text())
            raw['origin'] = 'reused_v929_saved_output'
            raw['source_sha256'] = reused[condition]['sha256']
            write(dest, raw)
        else:
            cmd = [sys.executable, '-I', str(Path(__file__).resolve()), '--worker', condition,
                   '--practice', str(a.practice), '--worker-output', str(dest)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode:
                raise RuntimeError(proc.stdout + proc.stderr)
            raw = json.loads(dest.read_text())
        assert [r['id'] for r in raw['rows']] == ids
        outputs[condition] = {r['id']: r for r in raw['rows']}
        print(condition, raw['origin'], flush=True)
    frozen = outputs['frozen']
    for name in ['dove_extract', 'dove_negate']:
        derived = []
        for record in inputs:
            r = frozen[record['id']]
            ext = r['extracted_value']
            calc = ext if name == 'dove_extract' else (str(-pair(r)[0]) if ext is not None else None)
            derived.append(dict(id=r['id'], extracted_value=ext, calculated_value=calc,
                                status=r['status'], method=name))
        write(a.out/f'{name}.json', dict(condition=name, origin='derived_from_frozen_extraction', rows=derived))
        outputs[name] = {r['id']: r for r in derived}
    solver = load()
    groups = defaultdict(list)
    for r in inputs:
        groups[r['query']].append(r)
    conflict = {r['id'] for g in groups.values() if len({pair(x['answer']) for x in g}) > 1 for r in g}
    qualities = {}
    for r in inputs:
        p = solver.parse_query(r['query'])
        qualities[r['id']] = dict(query_sha256=hashlib.sha256(r['query'].encode()).hexdigest(),
            parser_recovery=bool(p.errors), target_present=bool(solver.target_facts(p)),
            conflicting_reference=r['id'] in conflict, rule=r['dqc_id'])
    cases = []
    for condition, predicted in outputs.items():
        for r in inputs:
            o = predicted[r['id']]
            actual, gold, base = pair(o), pair(r['answer']), pair(frozen[r['id']])
            valid = all(v is not None and v.is_finite() for v in actual)
            correct = valid and actual == gold
            base_correct = base == gold
            cases.append(dict(schema_version='dove.followup.case/1', id=r['id'], condition=condition,
                prediction={k:o[k] for k in FIELDS}, status=o['status'], method=o.get('method'),
                pair_correct=correct, extracted_correct=actual[0] == gold[0],
                calculated_correct=actual[1] == gold[1], changed=actual != base,
                gain=correct and not base_correct, loss=base_correct and not correct,
                **qualities[r['id']]))
    with (a.out/'cases.jsonl').open('x', encoding='utf-8') as f:
        for r in cases:
            f.write(json.dumps(r, ensure_ascii=False)+'\n')
    by_condition = {}
    for condition in outputs:
        by_condition[condition] = dict(all=summary(cases, condition),
            without_recovery=summary(cases, condition, lambda r:not r['parser_recovery']),
            with_recovery=summary(cases, condition, lambda r:r['parser_recovery']),
            without_conflicting_reference=summary(cases, condition, lambda r:not r['conflicting_reference']),
            target_present=summary(cases, condition, lambda r:r['target_present']),
            by_rule={rule:summary(cases, condition, lambda r:r['rule'] == rule)
                     for rule in sorted({r['dqc_id'] for r in inputs})})
    loss_ids = {c: sorted(r['id'] for r in cases if r['condition'] == c and r['loss'])
                for c in ['no_unlinked', 'no_equality_penalties', 'no_both']}
    counts = {c:by_condition[c]['all']['correct'] for c in by_condition}
    interaction = counts['frozen']-counts['no_unlinked']-counts['no_equality_penalties']+counts['no_both']
    for c in outputs:
        z = by_condition[c]['all']
        assert z['correct'] == counts['frozen'] + z['gain'] - z['loss']
        assert sum(t['correct'] for t in by_condition[c]['by_rule'].values()) == z['correct']
    for p,h in hashes.items():
        assert sha(ROOT/p) == h, p
    out = dict(schema_version='dove.followup.summary/1', protocol_sha256=sha(a.out/'protocol.json'),
        conditions=by_condition, loss_ids=loss_ids,
        single_arm_loss_overlap=sorted(set(loss_ids['no_unlinked']) & set(loss_ids['no_equality_penalties'])),
        factorial_accuracy_count_contrast=interaction,
        contrast_definition='Y11-Y01-Y10+Y00 (count scale); descriptive, not a significance test.',
        case_rows=len(cases), cases_sha256=sha(a.out/'cases.jsonl'),
        baseline_source_sha256=sha(ROOT/'vendor/official/finreason_task3/baselines/rule_baseline.py'),
        noncausal_baseline_comparison='Different parsers, target selection and arithmetic; not a component ablation.',
        network_disabled=True, paid_model_calls=0, competition_posts=0,
        uncertainties=['Retrospective reused Practice set', 'No natural clean-filing specificity estimate',
                       'No per-case official Test labels', 'Parser strata are policy-defined, not random splits'])
    write(a.out/'summary.json', out)
    print(json.dumps({c:by_condition[c]['all'] for c in by_condition}, indent=2))


if __name__ == '__main__':
    main()
