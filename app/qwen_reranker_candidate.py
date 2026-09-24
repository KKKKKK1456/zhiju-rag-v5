"""Isolated local instruction-aware reranker. No network, generation or verdict.

Official Qwen yes/no logit-difference scoring, with full-passage overflow windows
instead of silently truncating later source text. Scores are retrieval relevance,
not a probability that a legal rule is applicable or an answer is correct.
"""
from pathlib import Path
import hashlib
import json
import math
import time

INSTRUCTIONS={
    'generic':'Given a web search query, retrieve relevant passages that answer the query',
    'direct_rule':('Retrieve legal provisions that directly address the specific relationship and rule requested by the query. '
                   'Match the actor, action, object and requested rule, not only shared background words. '
                   'Do not prefer provisions about a different issue or requiring an additional unstated situation '
                   'over a directly relevant general provision. Retrieval does not certify legal applicability.'),
}
PREFIX=('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. '
        'Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX='<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'


def token_windows(tokens, capacity, overlap=32):
    if type(capacity) is not int or capacity<=overlap or overlap<0:
        raise ValueError('invalid passage window budget')
    if not tokens:return [[]]
    result=[];start=0
    while start<len(tokens):
        end=min(start+capacity,len(tokens));result.append(tokens[start:end])
        if end==len(tokens):break
        start=end-overlap
    return result


def encode_pairs(tokenizer,query,passages,instruction,max_length):
    """Match official full-pair tokenization; only overflow docs are windowed.

    Encoding header/document separately can change the boundary BPE token.
    Keep the common header prefix and the full-pair boundary token in the first
    document window. Short pairs exactly equal the official token sequence.
    """
    encode=lambda text:tokenizer.encode(text,add_special_tokens=False)
    prefix=encode(PREFIX);suffix=encode(SUFFIX)
    header='<Instruct>: '+INSTRUCTIONS[instruction]+'\n<Query>: '+query+'\n<Document>: '
    header_ids=encode(header)
    features=[];owners=[];counts=[]
    for i,passage in enumerate(passages):
        pair_ids=encode(header+passage)
        shared=0
        for a,b in zip(header_ids,pair_ids):
            if a!=b:break
            shared+=1
        fixed=prefix+pair_ids[:shared]
        capacity=max_length-len(fixed)-len(suffix)
        if capacity<64:raise ValueError('query exceeds budget; no silent truncation')
        windows=token_windows(pair_ids[shared:],capacity)
        counts.append(len(windows))
        for window in windows:
            ids=fixed+window+suffix
            features.append({'input_ids':ids,'attention_mask':[1]*len(ids)});owners.append(i)
    return features,owners,counts


class LocalQwenReranker:
    def __init__(self,model_path,device='cpu',max_length=512,batch_size=8,instruction='generic'):
        import torch
        from transformers import AutoTokenizer,AutoModelForCausalLM
        path=Path(model_path).resolve()
        if not path.is_dir() or not (path/'verified_manifest.json').is_file():
            raise ValueError('verified local snapshot required; no automatic download')
        if device not in ('cpu','mps') or instruction not in INSTRUCTIONS:
            raise ValueError('unsupported model configuration')
        if not 256<=max_length<=4096 or not 1<=batch_size<=16:raise ValueError('invalid budget')
        manifest=json.loads((path/'verified_manifest.json').read_text())
        if manifest.get('repo')!='Qwen/Qwen3-Reranker-0.6B':raise ValueError('wrong model manifest')
        for name,entry in manifest['files'].items():
            if Path(name).name!=name:raise ValueError('invalid manifest path')
            h=hashlib.sha256()
            with (path/name).open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
            if h.hexdigest()!=entry['sha256']:raise ValueError('snapshot checksum mismatch')
        self.torch=torch;self.device=device;self.max_length=max_length;self.batch_size=batch_size
        self.instruction=instruction;self.manifest=manifest
        self.tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False,padding_side='left')
        self.model=AutoModelForCausalLM.from_pretrained(path,local_files_only=True,trust_remote_code=False,
            use_safetensors=True,dtype=torch.float16 if device=='mps' else torch.float32,
            attn_implementation='sdpa').to(device).eval()
        torch.set_num_threads(4)
        self.yes=self.tokenizer.convert_tokens_to_ids('yes');self.no=self.tokenizer.convert_tokens_to_ids('no')
        if self.yes==self.no or self.yes is None or self.no is None:raise ValueError('missing score tokens')

    def score(self,query,passages,budget_seconds=120):
        if not isinstance(query,str) or not query.strip():raise ValueError('empty query')
        started=time.monotonic()
        features,owners,counts=encode_pairs(self.tokenizer,query,passages,self.instruction,self.max_length)
        if len(features)>4096:raise ValueError('too many windows; no partial scoring')
        scores=[-math.inf]*len(passages)
        with self.torch.inference_mode():
            for start in range(0,len(features),self.batch_size):
                if time.monotonic()-started>budget_seconds:raise TimeoutError('rerank budget expired')
                batch=self.tokenizer.pad(features[start:start+self.batch_size],padding=True,return_tensors='pt')
                logits=self.model(**{k:v.to(self.device) for k,v in batch.items()},use_cache=False,logits_to_keep=1).logits[:,-1,:]
                values=(logits[:,self.yes].float()-logits[:,self.no].float()).cpu().tolist()
                if not all(math.isfinite(v) for v in values):raise ValueError('nonfinite score')
                for j,value in enumerate(values):scores[owners[start+j]]=max(scores[owners[start+j]],value)
        return scores,{'seconds':round(time.monotonic()-started,3),'windows':len(features),'window_counts':counts,
                       'max_length':self.max_length,'batch_size':self.batch_size,'device':self.device,
                       'dtype':'float16' if self.device=='mps' else 'float32','instruction':self.instruction,
                       'score':'yes_minus_no_logit_not_legal_confidence','truncated':False}
