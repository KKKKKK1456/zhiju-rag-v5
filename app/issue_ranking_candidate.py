"""Isolated, source-preserving per-issue ranking. Not legal sufficiency checking.

No question-specific terms, article identifiers, reference labels or model calls.
Different query logits are not comparable; merge ordinal ranks, never max logits.
"""
import math


def merge_issue_rankings(units, scores, limit=20):
    """Round-robin *unique* candidates, with all issue links retained.

    Each lane gets one previously unselected unit per round where possible.
    A shared first unit must not consume the next lane's opportunity. Empty lanes
    stay explicitly empty; a candidate link is not a certification of relevance.
    All units remain in the full order for audit; limit only selects the view.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError('invalid display limit')
    if not isinstance(scores, list) or not 1 <= len(scores) <= 6:
        raise ValueError('invalid lane count')
    refs = [(u['source'], u['unit']) for u in units]
    if len(set(refs)) != len(refs):
        raise ValueError('duplicate source unit')
    for row in scores:
        if (not isinstance(row, list) or len(row) != len(units)
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)):
            raise ValueError('invalid score matrix')
    lanes = [sorted(range(len(units)), key=lambda i: (-row[i], i)) for row in scores]
    ranks = [{i: n + 1 for n, i in enumerate(lane)} for lane in lanes]
    cursors = [0] * len(lanes)
    order, seen = [], set()
    while len(order) < len(units):
        for lane_id, lane in enumerate(lanes):
            while cursors[lane_id] < len(lane) and lane[cursors[lane_id]] in seen:
                cursors[lane_id] += 1
            if cursors[lane_id] < len(lane):
                index = lane[cursors[lane_id]]
                cursors[lane_id] += 1
                seen.add(index)
                order.append((index, lane_id))
    merged = [dict(units[index], rank=n + 1, selected_by_issue=owner,
                   issue_ranks=[r[index] for r in ranks],
                   issue_scores=[row[index] for row in scores],
                   judgment='retrieval_candidate_not_verified_evidence')
              for n, (index, owner) in enumerate(order)]
    return {'selected': merged[:limit], 'all_units': merged,
            'lanes': [[dict(units[i], rank=n + 1, score=scores[j][i])
                       for n, i in enumerate(lane)] for j, lane in enumerate(lanes)],
            'coverage_status': 'unverified'}


def parent_views(chunks, ranked_units):
    """Project ranked exact excerpts onto original parents, never lose originals."""
    seen, result = {}, []
    for unit in ranked_units:
        source = unit['source']
        if type(source) is not int or not 0 <= source < len(chunks):
            raise ValueError('invalid source')
        chunk = chunks[source]
        start, end = unit['start'], unit['end']
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(chunk['content'])
                or unit['text'] != chunk['content'][start:end]):
            raise ValueError('source excerpt mismatch')
        if source not in seen:
            seen[source] = len(result)
            result.append(dict(chunk, rank=len(result) + 1,
                               prior_rank=chunk.get('rank', source + 1), article_candidates=[]))
        result[seen[source]]['article_candidates'].append(dict(unit))
    for source, chunk in enumerate(chunks):
        if source not in seen:
            result.append(dict(chunk, rank=len(result) + 1,
                               prior_rank=chunk.get('rank', source + 1), article_candidates=[]))
    return result


def merge_issue_mass(units, scores, limit=20):
    """Diagnostic alternative: per-lane normalized relevance mass, not confidence.

    Reserve one unique result per issue, then order by summed normalized mass.
    Unlike raw max logits this is invariant to a constant score shift per query.
    Normalized model relevance is NOT a probability of legal correctness.
    """
    baseline = merge_issue_rankings(units, scores, limit)
    if not units:
        return baseline
    mass = [0.0] * len(units)
    for row in scores:
        maximum = max(row)
        values = [math.exp(value - maximum) for value in row]
        total = sum(values)
        for i, value in enumerate(values):
            mass[i] += value / total
    by_ref = {(u['source'], u['unit']): i for i, u in enumerate(units)}
    # Initial coverage uses the same deterministic unique turns as round robin.
    seed = [by_ref[(u['source'], u['unit'])] for u in baseline['all_units'][:len(scores)]]
    order = seed + [i for i in sorted(range(len(units)), key=lambda i: (-mass[i], i)) if i not in seed]
    metadata = {(u['source'], u['unit']): u for u in baseline['all_units']}
    rows = [dict(metadata[(units[i]['source'], units[i]['unit'])], rank=n+1,
                 relevance_mass=mass[i], merge_method='normalized_relevance_not_calibrated_confidence')
            for n, i in enumerate(order)]
    return dict(baseline, selected=rows[:limit], all_units=rows)
