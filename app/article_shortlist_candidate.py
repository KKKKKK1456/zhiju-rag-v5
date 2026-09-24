"""Local coarse article shortlist; isolated from production and gold labels."""
import math
import re
from collections import Counter


def article_lexical_scores(query, units):
    def tokens(text):
        text = re.sub(r'【[^\n]*?】', ' ', text)
        chinese = [run[i:i+2] for run in re.findall(r'[\u4e00-\u9fff]+', text)
                   for i in range(len(run)-1)]
        return chinese + re.findall(r'[a-z0-9]+', text.lower())
    texts = [tokens(u['text']) for u in units]
    if not texts:
        return []
    df = Counter(t for ts in texts for t in set(ts))
    average = sum(map(len, texts)) / len(texts)
    terms = set(tokens(query))
    values = []
    for ts in texts:
        counts = Counter(ts)
        values.append(sum(math.log(1+(len(texts)-df[t]+.5)/(df[t]+.5)) *
                          counts[t]*2.2/(counts[t]+1.2*(.25+.75*len(ts)/max(average,1)))
                          for t in terms if counts[t]))
    return values


def shortlist_lane(lane, limit=64, preserve_parents=True):
    """Keep one lexical-best article per retrieved parent, then fill by BM25.

    This coarse cutoff is NOT a semantic confidence filter. Unselected original
    units remain in the caller's audit record. Source texts are never rewritten.
    """
    if type(limit) is not int or limit < 1 or type(preserve_parents) is not bool:
        raise ValueError('invalid article budget')
    units = lane['units']
    scores = article_lexical_scores(lane['query'], units)
    order = sorted(range(len(units)), key=lambda i: (-scores[i], i))
    selected, seen = [], set()
    if preserve_parents:
        parents = {u['source'] for u in units}
        if len(parents) > limit:
            raise ValueError('article budget cannot preserve all parents')
        for i in order:
            source = units[i]['source']
            if source not in seen:
                selected.append(i)
                seen.add(source)
    selected_set = set(selected)
    selected += [i for i in order if i not in selected_set][:max(0,limit-len(selected))]
    return {'units': [units[i] for i in selected], 'indices': selected,
            'lexical_scores': scores, 'total_units': len(units), 'limit': limit,
            'preserve_parents': preserve_parents}
