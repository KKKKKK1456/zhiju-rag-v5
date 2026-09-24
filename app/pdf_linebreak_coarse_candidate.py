"""Join PDF-wrapped Chinese words in scoring views, never in source evidence.

Only a single line break between two CJK characters is removed. Paragraphs,
Latin words, numbers, punctuation, and citation offsets are retained. This is
an isolated candidate and is not enabled in the production server.
"""
import re


def pdf_cjk_scoring_view(text):
    return re.sub(r'(?<=[\u4e00-\u9fff])[ \t]*\r?\n[ \t]*(?=[\u4e00-\u9fff])', '', text)


def shortlist_pdf_lines(lane, lexical_shortlist, limit=64):
    views = [dict(unit, text=pdf_cjk_scoring_view(unit['text'])) for unit in lane['units']]
    view_lane = dict(lane, query=pdf_cjk_scoring_view(lane['query']), units=views)
    result = lexical_shortlist(view_lane, limit, False)
    return dict(result, units=[lane['units'][i] for i in result['indices']],
                scoring_view='single_cjk_pdf_linebreak_join', source_modified=False)
