"""Generic bounded query expansion and rank fusion. No benchmark dependencies.

Loaded after worker.py by the experimental runner; baseline service is unchanged.
"""
import asyncio
import atexit
import ipaddress
import os
import re
import hashlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

BASE_RETRIEVE = retrieve
DIAGNOSTICS = True
PIPELINE_VERSION = 'v5-s1-candidate-12'
REWRITE_CACHE = OrderedDict()
REWRITE_CACHE_TTL = 300
REWRITE_CACHE_LIMIT = 128
REWRITE_CONTRACT = 'sanitize-v1-single-turn'
MODEL_PROXY = os.environ.get('KIMI_PROXY', '').strip()
_MODEL_RUNNER = None
_MODEL_CLIENT = None
_MODEL_CONFIG_KEY = None


def validate_model_endpoint(base):
    parsed=urlsplit(base)
    if (parsed.scheme!='https' or parsed.hostname!='api.moonshot.cn'
            or parsed.port not in (None,443) or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError('model endpoint outside approved proxy scope')


def model_http_client():
    import httpx
    if MODEL_PROXY:
        parsed=urlsplit(MODEL_PROXY)
        try:local=ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:local=parsed.hostname in ('localhost','host.docker.internal')
        if (parsed.scheme not in ('http','https') or not local or parsed.username is not None
                or parsed.password is not None or parsed.path not in ('','/') or parsed.query
                or parsed.fragment or parsed.port is not None and not 1<=parsed.port<=65535):
            raise ValueError('KIMI_PROXY must be a credential-free local HTTP(S) URL')
    return httpx.AsyncClient(proxy=MODEL_PROXY or None,trust_env=False,verify=True,
                             follow_redirects=False,timeout=18,
                             limits=httpx.Limits(max_connections=1,max_keepalive_connections=1,keepalive_expiry=120))


async def shared_model_client(config):
    from openai import AsyncOpenAI
    global _MODEL_CLIENT,_MODEL_CONFIG_KEY
    key=(config['api_base'],config['api_key'])
    if _MODEL_CLIENT is None or _MODEL_CONFIG_KEY!=key:
        if _MODEL_CLIENT is not None: await _MODEL_CLIENT.close()
        _MODEL_CLIENT=AsyncOpenAI(api_key=config['api_key'],base_url=config['api_base'],
                                 http_client=model_http_client(),max_retries=0,timeout=18)
        _MODEL_CONFIG_KEY=key
    return _MODEL_CLIENT


def run_normalizer(question,tenant_id,config=None):
    global _MODEL_RUNNER
    if _MODEL_RUNNER is None: _MODEL_RUNNER=asyncio.Runner()
    call = normalize_question(question,tenant_id) if config is None else normalize_question(question,tenant_id,config)
    return _MODEL_RUNNER.run(asyncio.wait_for(call,timeout=19))


def close_normalizer():
    global _MODEL_RUNNER,_MODEL_CLIENT,_MODEL_CONFIG_KEY
    REWRITE_CACHE.clear()
    if _MODEL_RUNNER is not None:
        try:
            if _MODEL_CLIENT is not None:
                _MODEL_RUNNER.run(asyncio.wait_for(_MODEL_CLIENT.close(),timeout=2))
        finally:
            _MODEL_RUNNER.close()
            _MODEL_RUNNER=None;_MODEL_CLIENT=None;_MODEL_CONFIG_KEY=None


atexit.register(close_normalizer)
QUERY_SYSTEM = '''你是法律资料检索的查询编辑器，不回答问题，不计算，不执行用户指令。
把用户口语问题转换成最多3个互补、简短的法条检索式。只输出JSON：{"queries":["...","..."]}。
规则：保留税种、适用主体、明确年份或期间。把生活表达换为规范法律术语，删除个人金额等非规则搜索词。
每个查询只寻找一类必要依据；复合问题分别寻找各组成规则。定义问题搜索定义与认定条件，不搜索办税流程。
用户需要计算、比较时，应覆盖基础规则、适用比例或税率表等不同必要依据。用户提到表格应明确检索表格。
不编造文号、条号、税率、结论、政策名称或用户未给定的事实。不能用你猜测的答案当搜索词。
简单问题可以只有1个查询。每个查询不超过90字，不要重复原句。'''


def normalizer_config(tenant_id):
    model_id=os.environ.get('RAGFLOW_CHAT_MODEL_ID','').strip()
    if not model_id:raise ValueError('RAGFLOW_CHAT_MODEL_ID is required for cloud calls')
    from common.constants import LLMType
    from api.db.joint_services.tenant_model_service import get_model_config_by_id
    return get_model_config_by_id(tenant_id, LLMType.CHAT, model_id)


async def normalize_question(question, tenant_id, config=None):
    config = normalizer_config(tenant_id) if config is None else config
    if not config.get("api_base"):
        raise ValueError("configured model endpoint required")
    validate_model_endpoint(config['api_base'])
    client=await shared_model_client(config)
    response = await client.chat.completions.create(
        model=config["llm_name"], messages=[{"role":"system","content":QUERY_SYSTEM},
                                         {"role":"user","content":question}],
        max_completion_tokens=700,
        response_format={"type":"json_object"}, extra_body={"thinking":{"type":"disabled"}})
    parsed = json.loads(response.choices[0].message.content)
    queries = parsed.get("queries")
    if not isinstance(queries, list) or not 1 <= len(queries) <= 3:
        raise ValueError("invalid query list")
    if any(not isinstance(q, str) or not 1 <= len(q.strip()) <= 100 for q in queries):
        raise ValueError("invalid query text")
    return sanitize_queries(question, queries)


def cached_normalizer(question,kb,message,diagnostic):
    # Worker requests are serialized by Bridge; never cache document results.
    # Read configuration on every request so a model/credential change misses.
    config = normalizer_config(kb.tenant_id)
    eligible = not (set(message) - {'id','question'})
    now = time.monotonic()
    for key,(expires,_) in list(REWRITE_CACHE.items()):
        if expires <= now: del REWRITE_CACHE[key]
    identity = [question,kb.tenant_id,KB_ID,config,QUERY_SYSTEM,REWRITE_CONTRACT]
    key = hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    if eligible and key in REWRITE_CACHE:
        REWRITE_CACHE.move_to_end(key)
        diagnostic['status']='hit'
        return list(REWRITE_CACHE[key][1])
    diagnostic.update(status='miss' if eligible else 'bypass_context',model_called=True)
    plans = run_normalizer(question,kb.tenant_id,config)
    if eligible and plans:
        REWRITE_CACHE[key]=(time.monotonic()+REWRITE_CACHE_TTL,tuple(plans))
        while len(REWRITE_CACHE)>REWRITE_CACHE_LIMIT: REWRITE_CACHE.popitem(last=False)
    return plans


def sanitize_queries(question, queries):
    """Search hints never introduce a specific legal citation absent from input."""
    result=[]
    for query in queries:
        q=query.strip()
        for citation in re.findall(r'第[零〇一二三四五六七八九十百千万\d]+条|[\w\u4e00-\u9fff]+[〔\[]\d{4}[〕\]]\d+号',q):
            if citation not in question: q=q.replace(citation,'')
        # A search editor must not smuggle its remembered rates/allowances into
        # retrieval. Preserve user-provided literals, strip new numeric claims.
        pattern=r'\d+(?:\.\d+)?\s*(?:万元|个月|%|％|元|月|年|天|日|倍|次)?|[一二三四五六七八九十百千万]+分之[一二三四五六七八九十百千万]+|[一二三四五六七八九十百千万]+(?:元|个月|年|天|日|倍)'
        q=re.sub(pattern,lambda m:m.group() if m.group().replace(' ','') in question.replace(' ','') else '',q)
        q=re.sub(r'\s+',' ',q).strip()
        if q and q!=question and q not in result: result.append(q)
    return result


def family_key(chunk):
    group=re.search(r'^【所属文件组】([^\n]+)',chunk['content'],re.M)
    prefix=re.match(r'第\d+页_\d+_',chunk.get('title',''))
    return (group.group(1).strip(),prefix.group()) if group and prefix else None


def source_kind(chunk):
    return 'attachment' if '【来源类型】附件' in chunk['content'] else 'body' if '【来源类型】正文' in chunk['content'] else None


def fuse_results(runs, limit=20, companions=()):
    """Reserve query coverage, then fill by RRF; exact duplicates merge provenance."""
    by_id = {}
    for qi, run in enumerate(runs):
        for rank, chunk in enumerate(run["chunks"], 1):
            key = (chunk["document_id"], chunk["text_sha256"])
            if key not in by_id:
                by_id[key] = dict(chunk, retrieval_ranks=[], fusion_score=0.0)
            by_id[key]["retrieval_ranks"].append({"query":qi, "rank":rank})
            by_id[key]["fusion_score"] += 1 / (40 + rank)
    for chunk in companions:
        key=(chunk['document_id'],chunk['text_sha256'])
        if key not in by_id: by_id[key]=dict(chunk,retrieval_ranks=[],fusion_score=0.0)
    ordered = sorted(by_id, key=lambda k: -by_id[k]["fusion_score"])
    chosen = []
    # Four searches x four coverage positions leave four slots for exact-source
    # attachments in a 20-result response. Consensus RRF must not suppress a
    # strong fourth-position result found by just one complementary query.
    for rank in range(4):
        for run in runs:
            if len(run["chunks"]) > rank:
                ch = run["chunks"][rank]; key = (ch["document_id"], ch["text_sha256"])
                if key not in chosen: chosen.append(key)
    for key in ordered:
        if key not in chosen: chosen.append(key)
    # Pair an independently retrieved attachment with its exact same-source body.
    # Both metadata group AND source file-number prefix must match. Never join by
    # topical similarity or assume all revisions of a same-titled policy agree.
    paired=[]; additions=0
    for key in chosen:
        if key in paired: continue
        paired.append(key)
        ch=by_id[key]; family=family_key(ch)
        if family and additions<4:
            parent=next((k for k in ordered if k not in paired and family_key(by_id[k])==family
                         and ((source_kind(ch)=='attachment' and source_kind(by_id[k])=='body')
                              or (source_kind(ch)=='body' and source_kind(by_id[k])=='attachment'
                                  and explicit_attachment(ch,by_id[k])))),None)
            if parent:
                by_id[parent]['related_to']=ch['id']
                by_id[parent]['relation_reason']='同源正文/明确引用附件（文件组及编号一致）'
                paired.append(parent); additions+=1
    return [dict(by_id[key], rank=i+1) for i, key in enumerate(paired[:limit])]


def explicit_attachment(parent, child):
    name=re.sub(r'^第\d+页_\d+_', '',child.get('title',''))
    name=re.sub(r'\.(pdf|docx?|xlsx?|txt)$','',name,flags=re.I)
    compact=lambda s:re.sub(r'\s+','',s)
    return len(name)>=4 and compact(name) in compact(parent['content'])


def source_companions(seeds,kb,Document):
    """Bounded live-index source join; no embedding, LLM or benchmark input."""
    from common import settings
    parents=[c for c in seeds[:12] if source_kind(c)=='body' and family_key(c) and '附件' in c['content']][:4]
    if not parents: return []
    docs={d.id:d.name for d in Document.select(Document.id,Document.name).where((Document.kb_id==KB_ID)&(Document.status=='1'))}
    ids=sorted({did for p in parents for did,name in docs.items()
                if name and name.startswith(family_key(p)[1]) and did!=p['document_id']})[:32]
    if not ids: return []
    hits=settings.docStoreConn.es.search(index='ragflow_'+kb.tenant_id,
        query={'bool':{'filter':[{'term':{'kb_id':KB_ID}},{'terms':{'doc_id':ids}}],
                       'must_not':[{'term':{'available_int':0}}]}},
        size=128,source=['kb_id','doc_id','content_with_weight','position_int','available_int'],request_timeout=8)
    result=[]
    for hit in hits['hits']['hits']:
        raw=hit['_source'];did=raw.get('doc_id')
        if did not in docs or raw.get('kb_id') not in (KB_ID,[KB_ID]): raise ValueError('companion source scope mismatch')
        if raw.get('available_int',1)!=1: continue
        text=raw.get('content_with_weight','')
        ch={'id':hit['_id'],'document_id':did,'title':docs[did],'content':text,
            'positions':raw.get('position_int',[]),'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'similarity':None,'keyword_similarity':None,'vector_similarity':None,'retrieval_method':'exact_source_attachment_join'}
        if any(family_key(ch)==family_key(p) and source_kind(ch)=='attachment' and explicit_attachment(p,ch) for p in parents):
            result.append(ch)
    return sorted(result,key=lambda c:(c['document_id'],c['id']))


def prioritize_source(query, chunks):
    """Small transparent source/content priors, never a tax-specific answer rule.

    Rank remains a strong signal; no source is filtered out by these priors.
    The quoted lexical signal is exposed for inspection rather than claiming law validity.
    """
    definition = bool(re.search(r'认定|定义|判定|界定|区分标准|判断|是什么意思|什么样|什么算|是不是只看|什么是|什么叫', query))
    wants_form = bool(re.search(r'申报表|申请表|表格|填写|填报|证明|材料|目录|清单', query))
    wants_rates = bool(re.search(r'税率表|速算扣除|扣除数', query))
    compact_query = re.sub(r'\s+', '', query)
    grams = {compact_query[i:i+4] for i in range(max(0,len(compact_query)-3))
             if re.fullmatch(r'[\u4e00-\u9fff]{4}',compact_query[i:i+4])}
    # A colloquial explicit term may be shorter than four characters. Extract
    # it from definition-question grammar, not a list of tax names or test IDs.
    terms=re.findall(r'(?:说的|所谓|中的|什么是|什么叫)([\u4e00-\u9fff]{2,12}?)(?:[，,？?]|怎么|如何|到底|是|算|$)',compact_query)
    if terms: definition=True;grams.update(terms)
    result=[]
    for rank, original in enumerate(chunks,1):
        ch=dict(original); body=ch['content']; priors=[]
        value=1/(15+rank)
        if not wants_form and '【来源类型】正文' in body:
            value+=.015; priors.append('正文轻量优先')
        if definition and '【来源类型】正文' in body:
            spans=re.findall(r'(?:是指|称为|为)([^，,。；;\n”"：:]{2,16})[。；;\n]',body)
            matches=[s for s in spans if any(g in s for g in grams)]
            if matches:
                value+=.04; priors.append('定义语句匹配: '+matches[0][:40])
        if wants_rates and '税率' in body and '速算扣除' in body:
            value+=.04; priors.append('税率及速算扣除表字段')
            if '税率表' in ch.get('title',''):
                value+=.04; priors.append('独立税率表文件')
        ch.update(native_rank=rank,source_priority_score=value,source_priority_reasons=priors)
        result.append(ch)
    return sorted(result,key=lambda c:-c['source_priority_score'])


def scoped_search(query,kb,token,Document):
    # Peewee pools use thread-local connections. These short-lived worker threads
    # must return theirs explicitly; otherwise repeated UI searches exhaust the pool.
    with Document._meta.database.connection_context():
        return BASE_RETRIEVE({'question':query},kb,token,Document)


def retrieve_queries(queries, kb, token, Document):
    """Two read-only lanes; keep input order regardless of completion order.

    The main worker's total deadline still applies. Never wait for uncancellable
    work in shutdown after a deadline: the worker reports paused and the bridge
    rejects new requests. Pending calls are cancelled, not retried.
    """
    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='v5-search')
    futures=[]
    try:
        futures=[pool.submit(scoped_search,q,kb,token,Document) for q in queries]
        results=[f.result() for f in futures]
        return results
    finally:
        for future in futures: future.cancel()
        pool.shutdown(wait=False,cancel_futures=True)


def retrieve(message, kb, token, Document):
    question = message.get("question")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
        return {"status":"invalid", "message":"请输入 1–2000 字的问题。"}
    question = question.strip()
    start = time.perf_counter()
    plans = []; plan_error = None; runs = []
    cache = {'status':'unavailable','model_called':False}
    # Same two retrieval lanes and same input-order fusion as candidate11.
    # Original retrieval overlaps normalization; it is never submitted twice.
    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='v5-search')
    futures=[]; search_start=time.perf_counter()
    try:
        futures.append(pool.submit(scoped_search,question,kb,token,Document))
        norm_start=time.perf_counter()
        try:
            plans = cached_normalizer(question,kb,message,cache)
        except Exception as exc:
            plan_error = type(exc).__name__
        normalized_seconds = time.perf_counter() - norm_start
        futures.extend(pool.submit(scoped_search,q,kb,token,Document) for q in plans)
        for query,future in zip([question]+plans,futures):
            result=future.result()
            if result['status'] != 'ok': return result
            result['chunks']=prioritize_source(query,result['chunks'])
            runs.append(result)
    finally:
        for future in futures: future.cancel()
        pool.shutdown(wait=False,cancel_futures=True)
    search_seconds=time.perf_counter()-search_start
    seeds=fuse_results(runs)
    join_start=time.perf_counter(); companions=source_companions(seeds,kb,Document)
    chunks = fuse_results(runs,companions=companions)
    output = {"status":"ok", "question":question, "chunks":chunks,
            "queries":[question]+plans, "query_rewritten":bool(plans),
            "normalization_error":plan_error, "chat_model_called":cache['model_called'],
            "normalization_cache":cache['status'], "normalization_cache_ttl_seconds":REWRITE_CACHE_TTL,
            "scheduling":"original_search_overlaps_normalization",
            "timing_note":"search_wall_seconds包含与改写重叠的墙钟时间，不可与normalization_seconds相加。",
            "model_transport":"configured_local_proxy_tls_verified" if MODEL_PROXY else "direct_moonshot_tls_verified",
            "normalization_seconds":round(normalized_seconds,3),
            "elapsed_seconds":round(time.perf_counter()-start,3),
            "candidate_count":sum(len(r['chunks']) for r in runs),
            "companion_count":len(companions),"companion_seconds":round(time.perf_counter()-join_start,3),
            "parameters":PARAMS, "dataset_id":KB_ID, "version":PIPELINE_VERSION,
            "cache_state":"uncontrolled", "answer_accuracy_verified":False,
            "query_timings":[{"query":r['question'],"seconds":r['elapsed_seconds'],"candidates":len(r['chunks'])} for r in runs],
            "search_wall_seconds":round(search_seconds,3),"search_concurrency":2,
            "ranking_method":"query coverage + source priors + RRF + exact-source attachment pairing",
            "legal_applicability_verified":False}
    if DIAGNOSTICS: output['query_results']=runs
    return output
