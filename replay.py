"""Standalone, offline replay CLI. This file is copied into the local bundle."""
import argparse, hashlib, importlib, importlib.util, json, platform, sys, types
from decimal import Decimal
from pathlib import Path

BASE=Path(__file__).resolve().parent
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def records(p): return [json.loads(x) for x in Path(p).read_text(encoding='utf-8-sig').splitlines() if x.strip()]
def pair(r): return tuple(None if r.get(k) is None else Decimal(str(r[k]).replace(',','')) for k in ['extracted_value','calculated_value'])

def main():
    ap=argparse.ArgumentParser(description='Offline frozen DOVE replay; no downloads or competition uploads.')
    ap.add_argument('--input',required=True); ap.add_argument('--output',required=True);ap.add_argument('--reference')
    ap.add_argument('--mode',choices=['frozen','canonical','evidence-gate'],default='frozen');a=ap.parse_args()
    out=Path(a.output).resolve();report=out.with_suffix('.report.json')
    assert not out.exists() and not report.exists(),'Refuse to overwrite results'
    manifest=json.loads((BASE/'MANIFEST.json').read_text())
    for relative,h in manifest['files'].items(): assert digest(BASE/relative)==h,relative
    def audit(event,args):
        if event in ['socket.connect','socket.getaddrinfo']: raise RuntimeError('Network disabled for offline replay')
    sys.addaudithook(audit)
    package=types.ModuleType('finreason');package.__path__=[str(BASE/'runs/structured_test_v5/source/finreason')];sys.modules['finreason']=package
    solver=importlib.import_module('finreason.solver');n=importlib.import_module('finreason.nonnegative');n.RULES_DIR=BASE/'data/rules'
    gate=None
    if a.mode!='frozen':
        filename='canonical_fact_order_v931.py' if a.mode=='canonical' else 'evidence_gate_v940.py'
        spec=importlib.util.spec_from_file_location('extension',BASE/'extensions'/filename)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        if a.mode=='canonical': mod.install(solver)
        else: gate=mod.assess
    inputs=records(a.input);assert len({r['id'] for r in inputs})==len(inputs)
    results=[]
    for r in inputs:
        if gate:
            o=gate(solver,r['query']); answer=o['answer'] or {'extracted_value':None,'calculated_value':None}
            results.append({'id':r['id'],**answer,'status':o['status'],'reason':o['reason']})
        else:
            try: o=solver.solve(r['query'])
            except Exception as e: o={'extracted_value':None,'calculated_value':None,'status':'runtime_error','diagnostics':{'exception':type(e).__name__}}
            results.append({'id':r['id'],**{k:o[k] for k in ['extracted_value','calculated_value','status']}})
    mismatches=None
    if a.reference:
        refs=records(a.reference);reference={r['id']:pair(r) for r in refs}
        assert len(reference)==len(refs) and set(reference)=={r['id'] for r in results}
        mismatches=[r['id'] for r in results if pair(r)!=reference[r['id']]]
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in results),encoding='utf-8')
    import lxml,openpyxl
    loaded={k:str(Path(v.__file__).resolve()) for k,v in sys.modules.items() if k.startswith('finreason.')}
    assert all(Path(p).is_relative_to(BASE) for p in loaded.values())
    info={'cases':len(results),'mode':a.mode,'reference_pair_mismatches':mismatches,
        'input_sha256':digest(a.input),'output_sha256':digest(out),'manifest_sha256':digest(BASE/'MANIFEST.json'),
        'python':platform.python_version(),'lxml':lxml.__version__,'openpyxl':openpyxl.__version__,
        'module_paths':loaded,'network_disabled':True,'null_values_not_coerced_to_zero':True}
    report.write_text(json.dumps(info,indent=2)+'\n',encoding='utf-8');print(json.dumps(info,indent=2))
    if mismatches: sys.exit(2)

if __name__=='__main__':main()
