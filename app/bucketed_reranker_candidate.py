"""Padding-only optimization. All official encoded token windows remain intact.

Loaded alongside qwen_reranker_candidate; caller supplies the verified local
model. No approximation, early exit, cache hit substitution or remote calls.
"""
import math
import time


def length_order(features):
    return sorted(range(len(features)), key=lambda i: (len(features[i]['input_ids']), i))


def score_bucketed(model, query, passages, encode, budget_seconds=120):
    if not isinstance(query,str) or not query.strip():
        raise ValueError('empty query')
    started=time.monotonic()
    features,owners,counts=encode(model.tokenizer,query,passages,model.instruction,model.max_length)
    if len(features)>4096:
        raise ValueError('too many windows; no partial scoring')
    order=length_order(features)
    scores=[-math.inf]*len(passages)
    padded_tokens=0
    with model.torch.inference_mode():
        for start in range(0,len(order),model.batch_size):
            if time.monotonic()-started>budget_seconds:
                raise TimeoutError('rerank budget expired')
            indices=order[start:start+model.batch_size]
            batch_features=[features[i] for i in indices]
            padded_tokens+=max(len(f['input_ids']) for f in batch_features)*len(indices)
            batch=model.tokenizer.pad(batch_features,padding=True,return_tensors='pt')
            logits=model.model(**{k:v.to(model.device) for k,v in batch.items()},
                               use_cache=False,logits_to_keep=1).logits[:,-1,:]
            values=(logits[:,model.yes].float()-logits[:,model.no].float()).cpu().tolist()
            if not all(math.isfinite(v) for v in values):
                raise ValueError('nonfinite score')
            for i,value in zip(indices,values):
                scores[owners[i]]=max(scores[owners[i]],value)
    return scores,{'seconds':round(time.monotonic()-started,3),'windows':len(features),
                   'window_counts':counts,'max_length':model.max_length,'batch_size':model.batch_size,
                   'device':model.device,'instruction':model.instruction,'truncated':False,
                   'method':'length_bucketed_exact_windows','padded_tokens':padded_tokens,
                   'score':'yes_minus_no_logit_not_legal_confidence'}
