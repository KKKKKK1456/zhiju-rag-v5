"""Equal two-view relevance fusion within one issue, never legal verification.

Normalization is within a view, so neither score offset nor duplicate query
views can create extra independent issues in the final coverage allocation.
"""
import copy
import math


def fuse_issue_views(conditioned, focused):
    views=(conditioned,focused)
    if conditioned.get('issue')!=focused.get('issue'):
        raise ValueError('views belong to different issues')
    maps=[];weights=[]
    for view in views:
        if not isinstance(view.get('query'),str) or not view['query'].strip():
            raise ValueError('missing view query')
        if view.get('kind')!='query':raise ValueError('only issue query views may be fused')
        units,scores=view['units'],view['scores']
        if len(units)!=len(scores) or any(type(s) not in (int,float) or not math.isfinite(s) for s in scores):
            raise ValueError('invalid or incomplete view scores')
        keys=[(u['source'],u['start'],u['end']) for u in units]
        if len(set(keys))!=len(keys):raise ValueError('duplicate source unit')
        maps.append(dict(zip(keys,units)))
        maximum=max(scores,default=0);values=[math.exp(s-maximum) for s in scores];den=sum(values) or 1
        weights.append(dict(zip(keys,[v/den for v in values])))
    if maps[0].keys()!=maps[1].keys():raise ValueError('view candidate sets differ')
    for key in maps[0]:
        if any(maps[0][key][f]!=maps[1][key][f] for f in ('text','input')):
            raise ValueError('source pair changed between views')
    result=copy.deepcopy(conditioned)
    result['scores']=[math.log(max(1e-300,(weights[0][key]+weights[1][key])/2)) for key in maps[0]]
    result['query_views']=[v['query'] for v in views]
    result['fusion']='equal_within_issue_normalized_relevance_mass'
    result['judgment']='retrieval_candidate_not_verified_evidence'
    result.pop('timing',None)
    return result
