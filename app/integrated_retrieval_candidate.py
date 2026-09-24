"""One isolated stage-1 pipeline. No labels, cloud calls, or production wiring.

The caller provides a validated question-only plan and scoped read-only recall.
Identical query routes and fusion in both budgets; only shortlist size changes.
"""
import time
from app.query_routes_candidate import supplemental_routes
from app.document_scope_fallback_candidate import document_fallback_routes


def collect_recall(question, status, plan, retrieve):
    extra = supplemental_routes(question, status, plan)
    routes = [dict(query=question, kind='original', issue=None)]
    if status == 'ok':
        routes += [dict(query=i['query'], kind='query', issue=n)
                   for n, i in enumerate(plan['issues'])]
    cache = {}
    def run(route):
        scope = tuple(route.get('document_ids', []))
        key = (route['query'], scope)
        if key not in cache:
            cache[key] = retrieve(route['query'], list(scope))
        if cache[key].get('status') != 'ok':
            raise ValueError('recall incomplete, not absence of evidence')
        return dict(route, result=cache[key])
    routes = [run(r) for r in routes]
    extra = [run(r) for r in extra]
    scoped = [run(r) for r in document_fallback_routes(question, status, routes)]
    return dict(question=question, planner_status=status, routes=routes,
                extra_routes=extra, scoped_routes=scoped, request_count=len(cache))


def assemble(raw, plan, ops):
    parents, lanes = ops['make_lanes'](raw['routes'], ops['crossencoder_units'], 50, True)
    parents, lanes = ops['append_recalled_units'](parents, lanes, raw['extra_routes'], ops['crossencoder_units'], 50)
    if raw['planner_status'] != 'ok':
        parents, lanes = ops['append_scoped_units'](raw['question'], parents, lanes,
            raw['scoped_routes'], ops['crossencoder_units'], 50)
        queries = [(raw['question'], raw['question'])]
    else:
        if raw['scoped_routes'] or plan['question'] != raw['question']:
            raise ValueError('plan/source scope mismatch')
        queries = [(i['query'], i['focus']) for i in plan['issues']]
    lanes = ops['share_candidate_units'](parents, lanes)
    if [l['query'] for l in lanes] != [q[0] for q in queries]:
        raise ValueError('lane/query mismatch')
    units = lanes[0]['units']
    if len(units) > 4096:
        raise ValueError('candidate bound exceeded')
    return dict(pool=parents, units=units, queries=queries, planner_status=raw['planner_status'])


def prepare(assembled, embed, ops, width=20):
    if type(width) is not int or width not in (20, 32):
        raise ValueError('only frozen comparison widths allowed')
    start = time.monotonic()
    units = assembled['units']
    vectors = embed([u['input'] for u in units])
    lanes = []
    for i, qs in enumerate(assembled['queries']):
        # Failed-plan fallback remains 32 in BOTH arms.
        k = width if assembled['planner_status'] == 'ok' else 32
        chosen = ops['select_two_view_units'](units, list(qs), vectors, embed(list(qs)),
            ops['article_lexical_scores'], ops['pdf_cjk_scoring_view'], k)
        lanes.append(dict(issue=i, queries=list(qs), units=chosen['units']))
    return dict(pool=assembled['pool'], planner_status=assembled['planner_status'], lanes=lanes,
                coarse_seconds=time.monotonic()-start)


def rank(prepared, score, ops):
    start = time.monotonic(); ranked = []; views_all = []
    for lane in prepared['lanes']:
        queries = lane['queries'] if prepared['planner_status'] == 'ok' else lane['queries'][:1]
        views = []
        for query in queries:
            scores, timing = score(query, [u['input'] for u in lane['units']])
            views.append(dict(query=query, issue=lane['issue'], kind='query',
                              units=lane['units'], scores=scores, timing=timing))
        ranked.append(ops['fuse_issue_views'](*views) if len(views) == 2 else views[0])
        views_all.append(views)
    order = ops['merge_scored_lanes'](ranked, reserved_per_lane=2)['all_units']
    return dict(order=order, views=views_all, score_pairs=sum(len(v['units']) for vs in views_all for v in vs),
                rerank_seconds=time.monotonic()-start)
