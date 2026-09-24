"""Bounded within-document recall after optional planning fails.

Use the unchanged question and document IDs observed in its current original
retrieval. No guessed legal rules, historical allowlist, or case-specific IDs.
"""
def document_fallback_routes(question,planner_status,routes,max_documents=3):
    if planner_status not in ('ok','error') or type(max_documents) is not int or not 1<=max_documents<=3:
        raise ValueError('invalid fallback configuration')
    if planner_status=='ok':return []
    original=[r for r in routes if r.get('kind')=='original']
    if len(original)!=1 or original[0].get('query')!=question or original[0].get('result',{}).get('status')!='ok':
        raise ValueError('missing original successful recall')
    output=[];seen=set()
    for parent in original[0]['result']['chunks']:
        text=parent['content'];doc=parent['document_id']
        if '【来源类型】正文' not in text or '【法律效力证据】' in text or doc in seen:continue
        output.append(dict(query=question,kind='document_fallback',issue=None,document_ids=[doc],origin_chunk_id=parent['id']))
        seen.add(doc)
        if len(output)==max_documents:break
    return output
