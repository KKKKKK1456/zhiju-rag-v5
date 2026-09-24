"""Bounded loopback embedding cache and generic two-view coarse selection.

Only scoring views remove PDF soft wraps. Evidence text/offsets are unchanged.
No whole-corpus build, model download, cloud endpoint or hidden score threshold.
"""
import hashlib
import json
import math
import sqlite3
import urllib.request
from pathlib import Path
from app.settings import _local_url


class _NoLocalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


class BoundedLocalVectors:
    def __init__(self,path,expected_digest,max_misses=1024,*,base_url='http://127.0.0.1:11434',model='bge-m3:latest'):
        if type(max_misses) is not int or not 1<=max_misses<=4096:raise ValueError('invalid embedding bound')
        self.base_url=_local_url(base_url,'OLLAMA_URL')
        if model!='bge-m3:latest':raise ValueError('unsupported local embedding model')
        if not expected_digest:raise ValueError('local embedding digest required')
        self.model=model
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoLocalRedirect)
        with self.opener.open(self.base_url+'/api/tags',timeout=10) as response:tags=json.load(response)
        if not any(m['name']==self.model and m['digest']==expected_digest for m in tags['models']):
            raise ValueError('local embedding model changed')
        self.digest=expected_digest;self.max_misses=max_misses;self.misses=0
        Path(path).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.cache=sqlite3.connect(path)
        self.cache.execute('CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY,value TEXT NOT NULL)')

    @staticmethod
    def validate(vector):
        if len(vector)!=1024 or any(type(x) not in (int,float) or not math.isfinite(x) for x in vector):
            raise ValueError('invalid vector')
        norm=math.sqrt(sum(x*x for x in vector))
        if norm<=0:raise ValueError('zero vector')
        return [x/norm for x in vector]

    def embed(self,texts):
        if len(texts)>4096 or any(not isinstance(t,str) or not 1<=len(t)<=24000 for t in texts):
            raise ValueError('input exceeds bounded candidate scope')
        keys=[hashlib.sha256((self.digest+'\0'+t).encode()).hexdigest() for t in texts]
        values={};text_for=dict(zip(keys,texts))
        for key in set(keys):
            hit=self.cache.execute('SELECT value FROM vectors WHERE key=?',(key,)).fetchone()
            if hit:values[key]=self.validate(json.loads(hit[0]))
        missing=list(dict.fromkeys(k for k in keys if k not in values))
        if self.misses+len(missing)>self.max_misses:raise ValueError('local embedding miss budget exceeded')
        for start in range(0,len(missing),8):
            batch=missing[start:start+8]
            req=urllib.request.Request(self.base_url+'/api/embed',data=json.dumps(dict(model=self.model,input=[text_for[k] for k in batch],truncate=False)).encode(),headers={'Content-Type':'application/json'})
            with self.opener.open(req,timeout=120) as response:result=json.load(response)
            if len(result['embeddings'])!=len(batch):raise ValueError('incomplete embedding response')
            for key,v in zip(batch,result['embeddings']):
                values[key]=self.validate(v)
                self.cache.execute('INSERT INTO vectors VALUES (?,?)',(key,json.dumps(values[key])))
            self.cache.commit();self.misses+=len(batch)
        return [values[k] for k in keys]

    def close(self):self.cache.close()


def select_two_view_units(units,queries,vectors,query_vectors,lexical_scores,scoring_view,k=16):
    if type(k) is not int or k<1 or len(queries)!=2 or len(query_vectors)!=2 or len(vectors)!=len(units):
        raise ValueError('invalid coarse inputs')
    if any(len(v)!=len(query_vectors[0]) for v in vectors+query_vectors):raise ValueError('vector dimensions differ')
    if any(not math.isfinite(x) for v in vectors+query_vectors for x in v):raise ValueError('nonfinite vector')
    source_keys=[(u['source'],u['start'],u['end']) for u in units]
    if len(set(source_keys))!=len(source_keys):raise ValueError('duplicate source units')
    scoring_units=[dict(u,text=scoring_view(u['text'])) for u in units]
    orders=[]
    for query,qv in zip(queries,query_vectors):
        dense=[sum(a*b for a,b in zip(qv,v)) for v in vectors]
        lexical=lexical_scores(scoring_view(query),scoring_units)
        if len(lexical)!=len(units) or any(not math.isfinite(s) for s in lexical):raise ValueError('invalid lexical scores')
        for scores in (dense,lexical):orders.append(sorted(range(len(units)),key=lambda i:(-scores[i],i)))
    selected=list(dict.fromkeys(i for order in orders for i in order[:k]))
    return dict(units=[units[i] for i in selected],indices=selected,coarse_orders=orders,k_per_view=k,source_modified=False)
