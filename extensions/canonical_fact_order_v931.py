"""A deterministic ordering intervention, not a claim of better target selection."""
import json

def install(solver):
    original=solver.parse_query
    def parse(query):
        p=original(query)
        def key(f):
            c=p.contexts.get(f.context_id)
            semantic={'concept':f.concept,'context':c.key if c else None,
                'unit':p.units.get(f.unit_id,f.unit_id),'value':str(f.value),
                'decimals':f.decimals,'nil':f.is_nil,'raw':f.raw_value,
                'context_id':f.context_id,'unit_id':f.unit_id}
            return json.dumps(semantic,sort_keys=True,ensure_ascii=True)
        p.facts.sort(key=key)
        for i,f in enumerate(p.facts): f.source_index=i
        return p
    solver.parse_query=parse
