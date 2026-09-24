"""Isolated route-local reranking, before any global display budget.

Contains no gold, legal vocabulary, question IDs, external calls or judgments of
legal applicability. Every score is linked to an exact original source slice.
"""
import math


def make_lanes(routes, split_units, parent_limit=50, augment_original=False):
    if type(parent_limit) is not int or not 1 <= parent_limit <= 100:
        raise ValueError('invalid route budget')
    if type(augment_original) is not bool:
        raise ValueError('invalid original route flag')
    original = [r for r in routes if r['kind'] == 'original']
    if augment_original and (len(original) != 1 or original[0]['result']['status'] != 'ok'):
        raise ValueError('original retrieval must be present and successful')
    chosen = [r for r in routes if r['kind'] == 'query']
    if not chosen:
        chosen = [r for r in routes if r['kind'] == 'original']
    if not 1 <= len(chosen) <= 5:
        raise ValueError('invalid query lane count')
    parents, parent_ids, lanes = [], {}, []
    for route in chosen:
        if route['result']['status'] != 'ok':
            raise ValueError('failed retrieval is not an empty successful lane')
        chunks = route['result']['chunks'][:parent_limit]
        if augment_original and route['kind'] != 'original':
            # Keep the original question's recall path independently of rewriting.
            # Detect conflicting duplicate text instead of silently accepting it.
            combined = {}
            for chunk in chunks + original[0]['result']['chunks'][:parent_limit]:
                key = (chunk['document_id'], chunk['id'])
                if key in combined and combined[key]['content'] != chunk['content']:
                    raise ValueError('conflicting original route contents')
                combined.setdefault(key, chunk)
            chunks = list(combined.values())
        mapping = {}
        for local, chunk in enumerate(chunks):
            key = (chunk['document_id'], chunk['id'])
            if key in parent_ids:
                source = parent_ids[key]
                if parents[source]['content'] != chunk['content']:
                    raise ValueError('conflicting source contents')
            else:
                source = len(parents)
                parent_ids[key] = source
                parents.append(chunk)
            mapping[local] = source
        units = []
        seen = set()
        for unit in split_units(chunks):
            source = mapping[unit['source']]
            if unit['text'] != parents[source]['content'][unit['start']:unit['end']]:
                raise ValueError('source slice mismatch')
            key = (source, unit['start'], unit['end'])
            if key in seen:
                continue
            seen.add(key)
            units.append(dict(unit, source=source))
        lanes.append({'query': route['query'], 'issue': route.get('issue'),
                      'kind': route['kind'], 'retrieved_parents': len(route['result']['chunks']),
                      'selected_parents': len(chunks), 'augment_original': augment_original,
                      'units': units})
    return parents, lanes


def merge_scored_lanes(lanes, limit=20, reserved_per_lane=1):
    """Same normalized-mass principle, with missing lane members absent, not 0 logits."""
    if type(limit) is not int or limit < 1 or not 1 <= len(lanes) <= 5:
        raise ValueError('invalid merge budget')
    if type(reserved_per_lane) is not int or not 1 <= reserved_per_lane <= 2:
        raise ValueError('invalid per-lane reservation')
    records, orders, mass = {}, [], {}
    for li, lane in enumerate(lanes):
        units, scores = lane['units'], lane['scores']
        if len(units) != len(scores) or any(type(v) not in (float, int) or not math.isfinite(v) for v in scores):
            raise ValueError('incomplete or invalid scores')
        keys = [(u['source'], u['start'], u['end']) for u in units]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate lane source')
        ordering = sorted(range(len(units)), key=lambda i: (-scores[i], i))
        orders.append([keys[i] for i in ordering])
        maximum = max(scores, default=0)
        weights = [math.exp(v-maximum) for v in scores]
        denominator = sum(weights) or 1
        for rank, i in enumerate(ordering, 1):
            key, unit = keys[i], units[i]
            if key not in records:
                records[key] = dict(unit, issue_ranks=[None]*len(lanes), issue_scores=[None]*len(lanes))
            elif records[key]['text'] != unit['text']:
                raise ValueError('conflicting source unit')
            records[key]['issue_ranks'][li] = rank
            records[key]['issue_scores'][li] = scores[i]
            mass[key] = mass.get(key, 0) + weights[i] / denominator
    seed, owners = [], {}
    for _ in range(reserved_per_lane):
        for li, order in enumerate(orders):
            key = next((k for k in order if k not in owners), None)
            if key is not None:
                seed.append(key)
                owners[key] = li
    remaining = sorted((k for k in records if k not in owners), key=lambda k: (-mass[k], k))
    result = [dict(records[k], rank=i+1, selected_by_issue=owners.get(k),
                   relevance_mass=mass[k], judgment='retrieval_candidate_not_verified_evidence',
                   merge_method='route_local_normalized_mass',
                   reserved_per_lane=reserved_per_lane) for i, k in enumerate(seed+remaining)]
    return {'selected': result[:limit], 'all_units': result, 'coverage_status': 'unverified'}
