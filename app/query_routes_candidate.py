"""Question-only supplementary recall, not a fabricated successful issue plan.

Focused views must participate in retrieval, not only later reranking. When a
planner fails, keep the whole question and additionally search bounded verbatim
sentence spans. No legal vocabulary, gold labels, invented facts or retry.
"""
import re


def supplemental_routes(question,planner_status,plan=None):
    if not isinstance(question,str) or not 1<=len(question.strip())<=2000:
        raise ValueError('invalid original question')
    if planner_status not in ('ok','error'):raise ValueError('unknown planner status')
    routes=[];seen={question}
    if planner_status=='ok':
        if not isinstance(plan,dict) or plan.get('question')!=question or not 1<=len(plan.get('issues',[]))<=5:
            raise ValueError('plan scope mismatch')
        seen.update(i['query'] for i in plan['issues'])
        for i,issue in enumerate(plan['issues']):
            q=issue.get('focus')
            if not isinstance(q,str) or not 1<=len(q.strip())<=50:raise ValueError('invalid focus')
            if q not in seen:
                routes.append(dict(query=q,kind='focus',issue=i));seen.add(q)
    else:
        for match in re.finditer(r'[^。！？!?\n]+[。！？!?]?',question):
            q=match.group()
            if q.strip() and q not in seen:
                routes.append(dict(query=q,kind='verbatim_fallback',issue=None,start=match.start(),end=match.end()))
                seen.add(q)
            if len(routes)==4:break
    return routes


def append_recalled_units(parents,lanes,extra_routes,split_units,parent_limit=50):
    import copy
    if type(parent_limit) is not int or not 1<=parent_limit<=100 or not 1<=len(lanes)<=5:
        raise ValueError('invalid recall budget')
    parents=copy.deepcopy(parents);lanes=copy.deepcopy(lanes)
    index={(p['document_id'],p['id']):i for i,p in enumerate(parents)}
    if len(index)!=len(parents):raise ValueError('duplicate parent source')
    additions={}
    for route in extra_routes:
        if route.get('kind') not in ('focus','verbatim_fallback') or route.get('result',{}).get('status')!='ok':
            raise ValueError('invalid extra recall route')
        chunks=route['result']['chunks'][:parent_limit];mapping={}
        for i,parent in enumerate(chunks):
            key=(parent['document_id'],parent['id'])
            if key not in index:index[key]=len(parents);parents.append(copy.deepcopy(parent))
            elif parents[index[key]]['content']!=parent['content']:raise ValueError('source collision')
            mapping[i]=index[key]
        for unit in split_units(chunks):
            source=mapping[unit['source']]
            if unit['text']!=parents[source]['content'][unit['start']:unit['end']]:raise ValueError('source slice mismatch')
            u=dict(unit,source=source);key=(source,u['start'],u['end'])
            if key in additions and additions[key]['input']!=u['input']:raise ValueError('scoring input collision')
            additions[key]=u
    for lane in lanes:
        existing={(u['source'],u['start'],u['end']):u for u in lane['units']}
        for key,unit in additions.items():
            if key in existing:
                if any(existing[key][f]!=unit[f] for f in ('text','input')):raise ValueError('lane input collision')
            else:lane['units'].append(copy.deepcopy(unit))
    return parents,lanes
