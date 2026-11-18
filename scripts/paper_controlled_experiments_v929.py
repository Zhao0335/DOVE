"""Post-submission experiments; never modify the submitted solver or predictions.

Each condition starts a new isolated interpreter. Public labels are read only
by the evaluator, after the solver consumes query text alone.
"""
from __future__ import annotations
import argparse, dataclasses, hashlib, importlib, json, os, random, subprocess, sys, time, types
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runs/paper_controlled_v929'
SOURCE = ROOT / 'runs/structured_test_v5/source/finreason'
CONDITIONS = ['frozen', 'no_nested', 'no_joint', 'no_unlinked', 'no_inverse_demotion',
              'no_equality_penalties', 'half_equality_penalties', 'onehalf_equality_penalties',
              'gate_010', 'gate_050', 'reverse_facts', 'shuffle_17', 'shuffle_29', 'shuffle_43']

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p, x): Path(p).write_text(json.dumps(x, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
def rows(p): return [json.loads(x) for x in Path(p).read_text(encoding='utf-8-sig').splitlines() if x.strip()]
def pair(r): return tuple(None if r.get(k) is None else Decimal(str(r[k]).replace(',', '').replace('−','-')) for k in ('extracted_value','calculated_value'))

def load(condition='frozen', source=SOURCE, rules=None):
    package = types.ModuleType('finreason'); package.__path__ = [str(source)]
    sys.modules['finreason'] = package
    replacements = defaultdict(list)
    if condition == 'no_inverse_demotion': replacements['solver'].append(('candidate["score"] -= 1.0', 'candidate["score"] -= 0.0'))
    factors = {'no_equality_penalties':0, 'half_equality_penalties':.5, 'onehalf_equality_penalties':1.5}
    if condition in factors:
        f = factors[condition]
        replacements['solver'].append(('candidate["score"] -= .60', f'candidate["score"] -= {f*.60!r}'))
        replacements['dimensions'].append(('candidate["score"] -= 0.25', f'candidate["score"] -= {f*.25!r}'))
    if condition.startswith('gate_'):
        g = {'gate_010':'.1','gate_050':'.5'}[condition]
        replacements['solver'].append(('c["score"] >= .3', f'c["score"] >= {g}'))
        replacements['selection_dimensions'].append(('candidate.get("score", 0) < .3', f'candidate.get("score", 0) < {g}'))
    for name in ['solver','dimensions','nonnegative','selection_dimensions']:
        path = source / (name+'.py'); text = path.read_text(encoding='utf-8-sig')
        for old,new in replacements[name]:
            assert text.count(old)==1, (name,old)
            text = text.replace(old,new)
        module = types.ModuleType('finreason.'+name); module.__file__=str(path); module.__package__='finreason'
        sys.modules[module.__name__] = module
        exec(compile(text,str(path),'exec'), module.__dict__)
    s=sys.modules['finreason.solver']; d=sys.modules['finreason.dimensions']; j=sys.modules['finreason.selection_dimensions']
    sys.modules['finreason.nonnegative'].RULES_DIR = rules or ROOT/'data/rules'
    if condition=='no_nested':
        s.select_nonnegative_fact=lambda p,fs: (min(fs,key=lambda f:f.source_index), {'reason':'ablation_no_nested'})
    if condition=='no_joint': j.joint_target_candidates=lambda p:[]
    if condition=='no_unlinked':
        original=d.generate_candidates
        def generate(p,f): return [c for c in original(p,f) if c['method']!='dimension_unlinked']
        d.generate_candidates=j.generate_candidates=generate
    if condition=='reverse_facts' or condition.startswith('shuffle_'):
        original=s.parse_query
        def parse(query):
            p=original(query)
            def fingerprint():
                return sorted(json.dumps({k:str(v) for k,v in dataclasses.asdict(f).items() if k!='source_index'},sort_keys=True) for f in p.facts)
            before=fingerprint()
            if condition=='reverse_facts': p.facts.reverse()
            else:
                # Per-query deterministic seed; identical query always receives identical permutation.
                seed=int(condition.split('_')[1]) ^ int(hashlib.sha256(query.encode()).hexdigest()[:16],16)
                random.Random(seed).shuffle(p.facts)
            for i,f in enumerate(p.facts): f.source_index=i
            assert fingerprint()==before
            return p
        s.parse_query=parse
    return s

def worker(condition, dataset):
    solver=load(condition)
    records=rows(ROOT/('data/public_dev.jsonl' if dataset=='practice' else 'data/test_inputs.jsonl'))
    result=[]; start=time.perf_counter()
    for r in records:
        # The solver never receives id, dqc_id, answer, or split labels.
        try: out=solver.solve(r['query'])
        except Exception as e: out={'status':'raised','extracted_value':None,'calculated_value':None,'diagnostics':{'exception':type(e).__name__+': '+str(e)}}
        diag=out['diagnostics']; result.append({'id':r['id'], **{k:out[k] for k in ('extracted_value','calculated_value','status')},
            'method':diag.get('method'), 'context':diag.get('context_id'),
            'caught_error':diag.get('dimension_error'), 'exception':diag.get('exception')})
    save(OUT/f'{dataset}_{condition}.json',{'condition':condition,'dataset':dataset,'seconds':time.perf_counter()-start,'rows':result})

def summary():
    public=rows(ROOT/'data/public_dev.jsonl'); refs={r['id']:r for r in public}
    base=json.loads((OUT/'practice_frozen.json').read_text())['rows']
    baseline={r['id']:r for r in base}
    query_groups=defaultdict(list)
    for r in public: query_groups[hashlib.sha256(r['query'].encode()).hexdigest()].append(r)
    conflicting={r['id'] for group in query_groups.values() if len({pair(x['answer']) for x in group})>1 for r in group}
    summaries=[]
    for condition in CONDITIONS:
        raw=json.loads((OUT/f'practice_{condition}.json').read_text()); cases=[]
        for r in raw['rows']:
            ref=refs[r['id']]; actual=pair(r); gold=pair(ref['answer']); bp=pair(baseline[r['id']]); correct=actual==gold
            cases.append({'id':r['id'],'rule':ref['dqc_id'],'correct':correct,'extracted_correct':actual[0]==gold[0],
                'calculated_correct':actual[1]==gold[1], 'changed':actual!=bp,'gain':correct and bp!=gold,
                'loss':not correct and bp==gold,'status':r['status'],'method':r['method'], 'conflicting_reference':r['id'] in conflicting})
        by_rule={rule:{'n':sum(c['rule']==rule for c in cases),'correct':sum(c['correct'] and c['rule']==rule for c in cases)} for rule in sorted({c['rule'] for c in cases})}
        summaries.append({'condition':condition,'n':len(cases), **{k:sum(c[k] for c in cases) for k in ['correct','extracted_correct','calculated_correct','changed','gain','loss']},
             'by_rule':by_rule,'macro_accuracy':sum(v['correct']/v['n'] for v in by_rule.values())/len(by_rule),
             'without_conflicting_references':{'n':sum(not c['conflicting_reference'] for c in cases),'correct':sum(c['correct'] and not c['conflicting_reference'] for c in cases)},
             'statuses':dict(Counter(c['status'] for c in cases)),'seconds':raw['seconds']})
        save(OUT/f'scored_{condition}.json',cases)
    tests=[]; tb={r['id']:pair(r) for r in json.loads((OUT/'test_frozen.json').read_text())['rows']}
    final={r['id']:pair(r) for r in rows(ROOT/'submission/Task3/test_DOVE_metadata_v898/predictions.jsonl')}
    assert tb==final
    for condition in ['reverse_facts','shuffle_17','shuffle_29','shuffle_43']:
        rr=json.loads((OUT/f'test_{condition}.json').read_text())['rows']
        changed=[r for r in rr if pair(r)!=tb[r['id']]]
        tests.append({'condition':condition,'n':len(rr),'changed':len(changed),'by_new_route':dict(Counter(r['method'] for r in changed)), 'ids':[r['id'] for r in changed]})
    cross=defaultdict(Counter)
    for r in base: cross[refs[r['id']]['dqc_id']][r['method']]+=1
    result={'protocol_sha256':sha(OUT/'protocol.json'),'practice':summaries,'test_order_stability':tests,
        'test_frozen_reproduces_final_pairs':True,'practice_rule_route_crosstab':{k:dict(v) for k,v in cross.items()},
        'conflicting_identical_query_cases':sorted(conflicting), 'notes':['Practice was previously used in development: these are matched retrospective diagnostics, not fresh generalization estimates.',
        'Offline metric is exact normalized Decimal pair agreement, not the official model judge.',
        'Fact-order interventions change parsed fact order/source indices only; XML networks and semantic fact multiset are unchanged.',
        'Neither parameter selection nor prediction transfer to Test is performed. Test perturbations measure output stability only.']}
    save(ROOT/'reports/paper_controlled_experiments_v929.json',result)
    print(json.dumps({'practice':[{k:x[k] for k in ['condition','correct','changed','gain','loss']} for x in summaries], 'test_order':tests},indent=2))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--worker', choices=CONDITIONS); ap.add_argument('--dataset',choices=['practice','test']); a=ap.parse_args()
    if a.worker: worker(a.worker,a.dataset); return
    assert not OUT.exists(), 'Refuse to overwrite experiment evidence'
    OUT.mkdir(parents=True)
    protected=json.loads((ROOT/'reports/paper_final_test_evidence_v920.json').read_text())['source_sha256']
    for p,h in protected.items(): assert sha(ROOT/p)==h,p
    protocol={'created_before_execution':True,'script_sha256':sha(__file__),'conditions':CONDITIONS,
        'practice':'All 332 public cases; no exclusions; normalized exact Decimal pair scoring; labels only in scorer.',
        'test':'Frozen baseline and four fact-order interventions only; output stability, never accuracy.',
        'parameter_interventions':'Equality penalties multiplied by 0, 0.5, 1.5; joint/virtual gate 0.3 replaced with 0.1 or 0.5; all other constants unchanged.',
        'order_seeds':[17,29,43],'frozen_sources':{p.name:sha(p) for p in SOURCE.glob('*.py')},
        'input_hashes':{p:sha(ROOT/p) for p in ['data/public_dev.jsonl','data/test_inputs.jsonl']}, 'protected_hashes':protected,
        'no_paid_calls_no_competition_requests':True,'no_hyperparameter_selection':True}
    save(OUT/'protocol.json',protocol)
    for dataset, conditions in [('practice',CONDITIONS),('test',['frozen','reverse_facts','shuffle_17','shuffle_29','shuffle_43'])]:
        for condition in conditions:
            result=subprocess.run([sys.executable,'-I',str(Path(__file__).resolve()),'--worker',condition,'--dataset',dataset],cwd=OUT,capture_output=True,text=True,timeout=55)
            if result.returncode: raise RuntimeError(result.stderr)
            print(dataset,condition,'completed',flush=True)
    for p,h in protected.items(): assert sha(ROOT/p)==h,p
    summary()

if __name__=='__main__': main()
