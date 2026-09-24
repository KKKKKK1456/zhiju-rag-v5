"""Merge independently recalled document-scoped evidence without new issues."""
import copy


def append_scoped_units(question, parents, lanes, routes, split_units, parent_limit=50):
    if type(parent_limit) is not int or not 1<=parent_limit<=100 or len(routes)>3:
        raise ValueError('invalid document recall budget')
    if len(lanes)!=1 or lanes[0].get('kind')!='original' or lanes[0]['query']!=question:
        raise ValueError('only failed-plan original lane is eligible')
    parents=copy.deepcopy(parents);lanes=copy.deepcopy(lanes)
    index={(p['document_id'],p['id']):i for i,p in enumerate(parents)}
    if len(index)!=len(parents):raise ValueError('duplicate sources')
    existing={(u['source'],u['start'],u['end']):u for u in lanes[0]['units']}
    seen=set()
    for route in routes:
        ids=route.get('document_ids')
        if route.get('kind')!='document_fallback' or route.get('query')!=question:
            raise ValueError('wrong scoped route')
        if not isinstance(ids,list) or len(ids)!=1 or ids[0] in seen:
            raise ValueError('invalid document scopes')
        seen.add(ids[0]);result=route.get('result',{})
        if result.get('status')!='ok':raise ValueError('scoped recall incomplete')
        if any(p.get('document_id')!=ids[0] for p in result['chunks']):
            raise ValueError('scoped recall escaped document')
        chunks=result['chunks'][:parent_limit];mapping={}
        for i,parent in enumerate(chunks):
            key=(parent['document_id'],parent['id'])
            if key not in index:index[key]=len(parents);parents.append(copy.deepcopy(parent))
            elif parents[index[key]]['content']!=parent['content']:raise ValueError('source collision')
            mapping[i]=index[key]
        for unit in split_units(chunks):
            source=mapping[unit['source']]
            if unit['text']!=parents[source]['content'][unit['start']:unit['end']]:raise ValueError('source slice mismatch')
            u=dict(unit,source=source);key=(source,u['start'],u['end'])
            if key in existing:
                if any(existing[key][f]!=u[f] for f in ('text','input')):raise ValueError('input collision')
            else:lanes[0]['units'].append(u);existing[key]=u
    return parents,lanes
