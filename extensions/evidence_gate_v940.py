"""Hardened post-submission native-equation gate; unresolved schema means abstain.

Unlike v930 and v936, locators identify a supplied schema by xml:base and an exact
element ID. No namespace or schema filename is inferred from a local name.
"""
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit,unquote
XLINK='{http://www.w3.org/1999/xlink}'
XML='{http://www.w3.org/XML/1998/namespace}'

def assess(solver,query):
    def stop(reason,status='insufficient_evidence'):return {'status':status,'reason':reason,'answer':None}
    try:
        raw=solver.solve(query)
        if raw['diagnostics'].get('dimension_error'):return stop('frozen_solver_caught_error','runtime_error')
        p=solver.parse_query(query)
        if p.errors:return stop('parser_recovery')
        if not p.target_qname or ':' not in p.target_qname:return stop('no_explicit_target_qname')
        prefix,local=p.target_qname.split(':',1)
        namespaces={root.nsmap[prefix] for root in p.roots.values() if prefix in root.nsmap}
        if len(namespaces)!=1:return stop('namespace_unresolved')
        qname='{'+next(iter(namespaces))+'}'+local
        targets=[f for f in solver.target_facts(p) if f.concept==qname and solver._same_question_period(p,f)]
        bindings={(p.contexts[f.context_id].key,p.units.get(f.unit_id),f.value) for f in targets}
        if len(bindings)!=1:return stop('target_not_unique')
        target=targets[0]
        # Identity is (declared schema URI, element ID), not prefix spelling.
        declarations={}
        for root in p.roots.values():
            if solver.local_name(root.tag)!='schema':continue
            uri=root.get(XML+'base');ns=root.get('targetNamespace')
            if not uri or not ns:continue
            for e in root.iter():
                if solver.local_name(e.tag)=='element' and e.get('id') and e.get('name'):
                    declarations.setdefault((uri,e.get('id')),set()).add('{'+ns+'}'+e.get('name'))
        relevant=[]
        for root in p.roots.values():
            for link in root.iter():
                if solver.local_name(link.tag)!='calculationLink':continue
                locs={e.get(XLINK+'label'):e.get(XLINK+'href','') for e in link if solver.local_name(e.tag)=='loc'}
                def resolve(label):
                    href=locs.get(label,'');uri,sep,fragment=href.partition('#')
                    hits=declarations.get((uri,unquote(fragment)),set()) if sep else set()
                    return next(iter(hits)) if len(hits)==1 else None
                role=link.get(XLINK+'role','')
                for arc in link:
                    if solver.local_name(arc.tag)!='calculationArc':continue
                    parent=resolve(arc.get(XLINK+'from'));child=resolve(arc.get(XLINK+'to'))
                    # A relevant unresolved locator cannot be certified merely
                    # because the historical parser strips it to the right name.
                    href=locs.get(arc.get(XLINK+'from'),'')
                    if parent!=qname and solver._href_concept(href)!=local:continue
                    if parent!=qname or child is None:return stop('unresolved_relationship_identity')
                    if arc.get(XLINK+'arcrole')!='http://www.xbrl.org/2003/arcrole/summation-item':return stop('unsupported_arcrole')
                    try:weight=Decimal(arc.get('weight',''))
                    except InvalidOperation:return stop('unsupported_weight')
                    if not weight.is_finite() or weight==0:return stop('unsupported_weight')
                    matching=[f for f in p.facts if f.name==solver.local_name(child)]
                    if any(f.concept!=child for f in matching):return stop('local_name_collision')
                    relevant.append((role,child,weight,int(arc.get('priority','0')),arc.get('use','optional')))
        if not relevant:return stop('unresolved_relationship_identity')
        # Execute the QName-qualified relationships themselves. Never return
        # to the frozen parser that derives names from locator ID suffixes.
        roles={}
        for role,child,weight,priority,use in relevant:
            roles.setdefault(role,{}).setdefault(child,[]).append((priority,use,weight))
        values=set()
        for role,children in roles.items():
            total=Decimal(0);complete=True;active=0
            for child,arcs in children.items():
                high=max(a[0] for a in arcs);top={(a[1],a[2]) for a in arcs if a[0]==high}
                if len(top)!=1:return stop('ambiguous_effective_relationship')
                use,weight=next(iter(top))
                if use=='prohibited':continue
                if use!='optional':return stop('unsupported_relationship_use')
                active+=1
                matches={f.value for f in p.facts if f.concept==child and f.value is not None
                    and p.contexts.get(f.context_id) is not None
                    and p.contexts[f.context_id].key==p.contexts[target.context_id].key
                    and p.units.get(f.unit_id,f.unit_id)==p.units.get(target.unit_id,target.unit_id)}
                if len(matches)>1:return stop('duplicate_value_conflict')
                if not matches:complete=False;continue
                total+=weight*next(iter(matches))
            if complete and active:values.add(total)
        if len(values)!=1:return stop('no_complete_consensus')
        value=next(iter(values))
        return {'status':'consistent' if value==target.value else 'discrepancy','reason':'qualified_complete_native_consensus',
            'answer':{'extracted_value':solver.format_decimal(target.value),'calculated_value':solver.format_decimal(value)}}
    except Exception as e:return stop(type(e).__name__,'runtime_error')
