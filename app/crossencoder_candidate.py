"""Unreleased local cross-encoder experiment. No model download or network path.

Unlike generative review, this returns relevance logits, not judgments of legal
applicability. Originals remain available even when their relevance rank is low.
Loaded in enhanced/precision scope by the isolated test harness only.
"""
def crossencoder_units(chunks):
    result=[]
    for ci,chunk in enumerate(chunks):
        for ui,(start,end) in enumerate(article_spans(chunk['content'])):
            result.append({'source':ci,'unit':ui,'start':start,'end':end,
                           'text':chunk['content'][start:end],
                           'input':chunk['title']+'\n'+chunk['content'][start:end]})
    return result


def apply_crossencoder_scores(chunks,units,queries,scores):
    """One ordering per query plus an inspectable combined parent ordering.

    No score threshold, deletion, invented need, answer or evidence sufficiency
    claim. Matrix has one finite numeric score for every query and original unit.
    """
    import math
    if not queries or not units or len(scores)!=len(queries):
        raise ValueError('incomplete rerank matrix')
    if units!=crossencoder_units(chunks):raise ValueError('source unit mismatch')
    if any(len(row)!=len(units) or any(type(v) not in (float,int) or not math.isfinite(v) for v in row)
           for row in scores):raise ValueError('invalid rerank score')
    lanes=[sorted(range(len(units)),key=lambda i:(-row[i],i)) for row in scores]
    # The original question is always lane zero. Per-need lanes remain separately
    # inspectable; this experiment does not claim one max score proves coverage.
    best={ci:max((scores[0][i] for i,u in enumerate(units) if u['source']==ci))
          for ci in range(len(chunks))}
    parent_order=sorted(range(len(chunks)),key=lambda ci:(-best[ci],ci))
    rows=[]
    for ci in parent_order:
        focus=[]
        for qi,lane in enumerate(lanes):
            selected=next(i for i in lane if units[i]['source']==ci)
            u=units[selected]
            focus.append(dict(u,query_index=qi,score=scores[qi][selected],
                              method='cross_encoder_relevance_not_legal_applicability'))
        rows.append(dict(chunks[ci],rank=len(rows)+1,prior_rank=chunks[ci].get('rank',ci+1),
                         rerank_score=best[ci],article_candidates=focus))
    return rows,[[dict(units[i],score=scores[qi][i],rank=j+1) for j,i in enumerate(lane)]
                 for qi,lane in enumerate(lanes)]


class LocalCrossEncoder:
    def __init__(self,model_path,device='cpu',max_length=512,batch_size=8):
        from pathlib import Path
        import torch
        from transformers import AutoModelForSequenceClassification,AutoTokenizer
        path=Path(model_path).resolve()
        if not path.is_dir() or not (path/'config.json').is_file():
            raise ValueError('approved local model directory required; no automatic download')
        if device not in ('cpu','mps'):raise ValueError('unsupported local device')
        if not 128<=max_length<=1024 or not 1<=batch_size<=16:raise ValueError('invalid batch budget')
        self.torch=torch;self.device=device;self.max_length=max_length;self.batch_size=batch_size
        self.tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
        self.model=AutoModelForSequenceClassification.from_pretrained(
            path,local_files_only=True,trust_remote_code=False,use_safetensors=True).to(device).eval()
        torch.set_num_threads(4)

    def score(self,query,passages,budget_seconds=120):
        import time
        import math
        started=time.monotonic();features=[];owners=[];window_counts=[]
        query_len=len(self.tokenizer.encode(query,add_special_tokens=False))
        if query_len+68>=self.max_length:
            raise ValueError('query exceeds rerank budget; no silent truncation')
        for i,passage in enumerate(passages):
            # Overflow windows cover the entire passage instead of discarding
            # later articles/table rows. The exact original remains untouched.
            encoded=self.tokenizer(query,passage,max_length=self.max_length,truncation='only_second',
                                   stride=32,return_overflowing_tokens=True,padding=False)
            n=len(encoded['input_ids']);window_counts.append(n)
            for wi in range(n):
                features.append({k:encoded[k][wi] for k in self.tokenizer.model_input_names if k in encoded})
                owners.append(i)
        if len(features)>4096:raise ValueError('too many rerank windows; no partial result')
        scores=[-math.inf]*len(passages)
        with self.torch.inference_mode():
            for start in range(0,len(features),self.batch_size):
                if time.monotonic()-started>budget_seconds:raise TimeoutError('rerank budget expired')
                batch=self.tokenizer.pad(features[start:start+self.batch_size],padding=True,return_tensors='pt')
                logits=self.model(**{k:v.to(self.device) for k,v in batch.items()}).logits.reshape(-1).float().cpu().tolist()
                if len(logits)!=len(features[start:start+self.batch_size]) or not all(math.isfinite(v) for v in logits):
                    raise ValueError('invalid model logits')
                for offset,value in enumerate(logits):
                    owner=owners[start+offset];scores[owner]=max(scores[owner],value)
        return scores,{'seconds':round(time.monotonic()-started,3),'windows':len(features),
                       'window_counts':window_counts,'max_length':self.max_length,
                       'batch_size':self.batch_size,'device':self.device,'truncated':False}
