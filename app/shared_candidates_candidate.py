"""Share exact retrieved source units before per-query coarse selection.

No new retrieval, generated facts, relevance labels, or question-specific rules.
Keep each lane's original order, then append missing units in stable union order.
Sharing makes evidence eligible; it does not force it into a shortlist or verdict.
"""
import copy


def share_candidate_units(parents, lanes):
    if not 1 <= len(lanes) <= 5:
        raise ValueError('invalid lane count')
    catalog = {}
    for lane in lanes:
        if 'scores' in lane:
            raise ValueError('share before scoring, not after')
        seen = set()
        for unit in lane['units']:
            source, start, end = (unit[k] for k in ('source', 'start', 'end'))
            if (type(source) is not int or not 0 <= source < len(parents)
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(parents[source]['content'])):
                raise ValueError('invalid source location')
            if unit['text'] != parents[source]['content'][start:end]:
                raise ValueError('not an exact source slice')
            key = (source, start, end)
            if key in seen:
                raise ValueError('duplicate lane unit')
            seen.add(key)
            if key in catalog and any(catalog[key][field] != unit[field] for field in ('text', 'input')):
                raise ValueError('conflicting input for same source')
            catalog.setdefault(key, unit)
    output = []
    for lane in lanes:
        keys = {(u['source'], u['start'], u['end']) for u in lane['units']}
        expanded = copy.deepcopy(lane)
        expanded['units'] += [copy.deepcopy(u) for k, u in catalog.items() if k not in keys]
        expanded['shared_candidate_audit'] = {
            'original_unit_count': len(keys), 'shared_unit_count': len(catalog),
            'added_unit_count': len(catalog) - len(keys),
            'policy': 'all_existing_lanes_exact_source_union_before_coarse_selection'}
        output.append(expanded)
    return output
