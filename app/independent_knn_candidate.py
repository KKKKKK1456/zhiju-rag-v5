"""Keep permission/source filters, not lexical eligibility, on dense retrieval.

Only top-level query_string MUST clauses created by the known ES adapter are
removed. Nested or unknown clauses remain untouched. This is a candidate for
isolated experiments, not automatically installed in a running service.
"""
import copy


def independent_knn_filter(query):
    if not isinstance(query, dict) or not isinstance(query.get('bool'), dict):
        raise ValueError('expected an explicit bool scope')
    result = copy.deepcopy(query)
    body = result['bool']
    must = body.get('must', [])
    if isinstance(must, dict):
        must = [must]
    if not isinstance(must, list) or any(not isinstance(c, dict) for c in must):
        raise ValueError('invalid must clauses')
    retained = [c for c in must if set(c) != {'query_string'}]
    if retained:
        body['must'] = retained
    else:
        body.pop('must', None)
    # Do not silently create a tenant-wide unscoped search.
    if not body.get('filter'):
        raise ValueError('explicit source scope required')
    return result
