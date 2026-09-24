"""Isolated UI runtime. Consent-gated answers; no eval labels or query log."""
import json
import os
import select
import subprocess
import time
from pathlib import Path
from app.integrated_retrieval_candidate import collect_recall, assemble, prepare, rank
from tools.integrated_local_worker import LocalRecallWorker
from app.grounded_answer import evidence_bundle, validate_answer
from app.answer_pipeline import apply_review
from app.followup_state import apply_update
from app.settings import load_settings, docker_python_command

ROOT=Path(__file__).resolve().parents[1]

class PipelineFailure(RuntimeError):
    def __init__(self,stage,reply):
        super().__init__('model stage failed')
        self.stage=stage
        self.error_code=reply.get('error_code',reply.get('error','unknown'))
        self.failure_kind=reply.get('failure_kind')
        self.retry_after=reply.get('retry_after')

def provider_failure_message(kind):
    if kind=='local_proxy':return '配置的本机模型代理未连接；未向模型提交问题。请检查 KIMI_PROXY 的地址和端口，知识库原文不受影响。'
    if kind=='quota':return 'Kimi API 账户额度受限，请检查可用余额、抵扣券及消费限额；不是知识库缺资料，未自动重试。'
    return 'Kimi 接口限流，本次处理未完成；不是缺资料。未自动重试，请等待额度恢复后再试。'
MODULES=('enhanced.py','precision_candidate.py','crossencoder_candidate.py','issue_ranking_candidate.py',
    'lane_first_candidate.py','shared_candidates_candidate.py','article_shortlist_candidate.py',
    'pdf_linebreak_coarse_candidate.py','local_coarse_candidate.py','query_routes_candidate.py',
    'scoped_recall_units_candidate.py','qwen_reranker_candidate.py','bucketed_reranker_candidate.py','dual_view_rank_candidate.py')


class Planner:
    def __init__(self, thinking=False, protocol='anchored', review_protocol='verdict_first', review_thinking=False):
        if protocol not in ('source_bound','anchored'):raise ValueError('invalid planner protocol')
        if review_protocol not in ('verdict_first','evidence_first','joint_evidence','separate_support'):raise ValueError('invalid review protocol')
        self.review_protocol=review_protocol;self.review_thinking=review_thinking
        self.thinking=thinking;self.protocol=protocol
        self.settings=load_settings(require_dataset=True,require_cloud=True)
        code=(ROOT/'app/worker.py').read_text().split('if __name__ == "__main__":')[0]
        for name in ('enhanced.py','issue_plan_candidate.py','anchored_issue_candidate.py','source_bound_plan.py','grounded_answer.py','answer_pipeline.py','followup_state.py','transport_preflight.py'):
            code+='\n'+(ROOT/'app'/name).read_text()
        code+='''
kb,model,token,Document=initialize()
emit(dict(type='ready',pid=os.getpid()))
runner=asyncio.Runner()
try:
    for line in sys.stdin:
        request=json.loads(line)
        MODEL_CALL_ATTEMPTS=0
        MODEL_CALL_METRICS=[]
        try:
            ANSWER_THINKING=bool(request.get('thinking',False))
            REVIEW_PROTOCOL=request.get('review_protocol','verdict_first')
            MAX_REVIEW_CALLS=request.get('max_review_calls',2)
            proxy_configured=require_model_proxy()
            if request.get('action')=='transport':
                result=dict(status='ok',local_proxy_reachable=True if proxy_configured else None,proxy_configured=proxy_configured,provider_verified=False,model_calls=0)
            elif request.get('action')=='update':
                update=runner.run(asyncio.wait_for(resolve_followup(request['packet']['previous'],request['packet']['current'],kb.tenant_id),118 if ANSWER_THINKING else 58))
                result=dict(status='ok',update=update)
            elif request.get('action')=='draft':
                draft=runner.run(asyncio.wait_for(draft_answer(request['packet'],kb.tenant_id),118 if ANSWER_THINKING else 58))
                result=dict(status='ok',draft=draft)
            elif request.get('action')=='review':
                reviewed=runner.run(asyncio.wait_for(review_answer(request['packet'],request['draft'],kb.tenant_id),240 if ANSWER_THINKING and REVIEW_PROTOCOL=='separate_support' else 118 if ANSWER_THINKING or REVIEW_PROTOCOL=='separate_support' else 58))
                result=dict(status='ok',**reviewed)
            elif request.get('action')=='answer':
                answer=runner.run(asyncio.wait_for(generate_grounded_answer(request['packet'],kb.tenant_id),48))
                result=dict(status='ok',answer=answer)
            else:
                make_plan=make_source_bound_plan if request.get('plan_protocol')=='source_bound' else make_anchored_plan
                plan=runner.run(asyncio.wait_for(make_plan(request['question'],kb.tenant_id),16))
                result=dict(status='ok',plan=plan)
        except Exception as exc:
            safe={'invalid answer envelope','invalid answer status','invalid gaps','answered shape','partial shape','insufficient shape','invalid claim','invalid claim text','uncited claim','unknown source','invalid quote','quote not in source','empty quote','invalid review','incomplete review','invalid verdict','invalid calculation review','invalid missing','invalid text','draft validation failed','review validation failed','update validation failed','model output truncated'}
            result=dict(status='error',error=type(exc).__name__,error_code=str(exc) if str(exc) in safe else type(exc).__name__)
            if isinstance(exc,ModelProxyUnavailableError):
                result.update(failure_kind='local_proxy',model_calls=0)
            plan_codes={'invalid anchored envelope','invalid anchored issue','missing request quotes','invalid request quote','invalid quote provenance','quote not uniquely present in selected clause','duplicate quote','invalid focus','new focus numeric claim','invalid plan envelope','invalid issue count','invalid issue fields','invalid issue provenance','invalid issue query','duplicate issue query','new numeric claim in query','model output truncated'}
            if str(exc)=='plan validation failed':
                code=getattr(exc,'diagnostic_code','')
                result['error_code']=code if code in plan_codes else 'plan validation failed'
            if type(exc).__name__=='RateLimitError':
                body=getattr(exc,'body',None)
                message=json.dumps(body,ensure_ascii=False).lower() if isinstance(body,(dict,list)) else ''
                result['failure_kind']=('token_rate_limit' if any(s in message for s in ('tpm','token per minute','tokens per minute')) else 'request_rate_limit' if any(s in message for s in ('rpm','requests per minute')) else 'quota' if any(s in message for s in ('insufficient','balance','quota')) else 'rate_limit')
                header=getattr(getattr(exc,'response',None),'headers',{}).get('retry-after')
                try:result['retry_after']=max(0,min(600,float(header)))
                except (TypeError,ValueError):pass
            if request.get('diagnostic') and hasattr(exc,'diagnostic_raw'):
                result.update(diagnostic_raw=exc.diagnostic_raw,diagnostic_code=exc.diagnostic_code)
        if request.get('action') in ('draft','review','update'):
            result['model_calls']=MODEL_CALL_ATTEMPTS
            result['model_call_metrics']=MODEL_CALL_METRICS
        emit(dict(id=request['id'],result=result))
finally:
    if _MODEL_CLIENT is not None:runner.run(_MODEL_CLIENT.close())
    _MODEL_CLIENT=None
    runner.close()
'''
        self.pid=None;self.sequence=0
        self.proc=subprocess.Popen(docker_python_command(self.settings,code,cloud=True),
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
        try:
            # Allow local RAGFlow imports on a cold machine. Cloud request
            # deadlines below remain unchanged.
            ready=self.read(120);self.pid=ready.get('pid')
            if ready.get('type')!='ready':raise RuntimeError('planner startup failed')
        except BaseException:self.close();raise

    def read(self,timeout):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if not select.select([self.proc.stdout],[],[],max(0,deadline-time.monotonic()))[0]:raise TimeoutError('planner deadline')
            line=self.proc.stdout.readline()
            if not line:raise RuntimeError('planner exited')
            if line.startswith('V5_RPC='):return json.loads(line[7:])
        raise TimeoutError('planner deadline')

    def plan(self,question,diagnostic=False):
        self.sequence+=1
        self.proc.stdin.write(json.dumps(dict(id=self.sequence,question=question,diagnostic=bool(diagnostic),plan_protocol=self.protocol),ensure_ascii=False)+'\n');self.proc.stdin.flush()
        reply=self.read(22)
        if reply.get('id')!=self.sequence:raise ValueError('planner correlation error')
        return reply['result']

    def close(self):
        if self.proc.poll() is not None:return
        self.proc.stdin.close()
        try:self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if type(self.pid) is int:subprocess.run(['docker','exec',self.settings.container,'kill','-TERM',str(self.pid)],capture_output=True,timeout=8)
            self.proc.terminate()

    def answer(self,packet):
        self.sequence+=1
        self.proc.stdin.write(json.dumps(dict(id=self.sequence,action='answer',packet=packet),ensure_ascii=False)+'\n');self.proc.stdin.flush()
        reply=self.read(55)
        if reply.get('id')!=self.sequence:raise ValueError('answer correlation error')
        return reply['result']

    def request(self, action, packet, draft=None, diagnostic=False, max_review_calls=2):
        if action not in ('draft','review','update','transport'):raise ValueError('unknown action')
        if type(max_review_calls) is not int or max_review_calls not in (1,2):raise ValueError('invalid review budget')
        self.sequence+=1
        thinking=action!='transport' and (self.thinking or action=='review' and self.review_thinking)
        request=dict(id=self.sequence,action=action,packet=packet,diagnostic=bool(diagnostic),thinking=thinking,review_protocol=self.review_protocol,max_review_calls=max_review_calls)
        if draft is not None:request['draft']=draft
        self.proc.stdin.write(json.dumps(request,ensure_ascii=False)+'\n');self.proc.stdin.flush()
        reply=self.read(250 if thinking and self.review_protocol=='separate_support' else 125 if thinking or self.review_protocol=='separate_support' else 65)
        if reply.get('id')!=self.sequence:raise ValueError('answer correlation error')
        return reply['result']


class Engine:
    def __init__(self,cloud_enabled=False,answer_enabled=False,answer_protocol='reviewed',planner_protocol='anchored',evidence_policy='ranked',review_protocol='verdict_first',review_thinking=False):
        if planner_protocol not in ('source_bound','anchored'):raise ValueError('invalid planner protocol')
        if evidence_policy not in ('ranked','document_round_robin'):raise ValueError('invalid evidence policy')
        self.evidence_policy=evidence_policy
        if review_protocol not in ('verdict_first','evidence_first','joint_evidence','separate_support'):raise ValueError('invalid review protocol')
        self.review_protocol=review_protocol;self.review_thinking=review_thinking
        self.planner_protocol=planner_protocol
        self.cloud_enabled=cloud_enabled;self.answer_enabled=answer_enabled;self.answer_protocol=answer_protocol;self.planner=None;self.worker=None
        self.settings=load_settings(require_dataset=True,require_digest=True)
        self.settings.cache_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
        self.ops=dict(json=json,time=time,os=os,retrieve=lambda *a:None)
        for name in MODULES:exec((ROOT/'app'/name).read_text(),self.ops)
        self.model=self.ops['LocalQwenReranker'](self.settings.qwen_model_path,self.settings.qwen_device,batch_size=8,instruction='direct_rule')
        self.worker=LocalRecallWorker()
        self.ops['score_bucketed'](self.model,'查询依据',['普通预热句子。'],self.ops['encode_pairs'])

    def search(self,question,cloud_consent,progress,answer_consent=False):
        if answer_consent and not self.answer_enabled:raise ValueError('answer permission not enabled')
        started=time.monotonic();planning=dict(status='error',error='local_only');cloud_attempts=0
        if cloud_consent:
            if not self.cloud_enabled:raise ValueError('cloud permission not enabled')
            progress('planning','仅将本次问题发送给 Kimi 拆分；不发送法规原文')
            if self.planner is None:self.planner=Planner(protocol=self.planner_protocol)
            cloud_attempts=1
            try:
                planning=self.planner.plan(question)
                if planning.get('failure_kind')=='local_proxy':cloud_attempts=0
            except Exception:
                self.planner.close();self.planner=None
                planning=dict(status='error',error='planner_transport_failure')
        plan_seconds=time.monotonic()-started;calls=0
        def retrieve(q,ids):
            nonlocal calls
            calls+=1;progress('retrieval',f'本机只读召回：第 {calls} 路'+('（文档内补召回）' if ids else ''))
            return self.worker.retrieve(q,ids)
        if self.worker is None or self.worker.broken:self.worker=LocalRecallWorker()
        recall_start=time.monotonic()
        raw=collect_recall(question,planning['status'],planning.get('plan'),retrieve)
        recall_seconds=time.monotonic()-recall_start
        progress('coarse','本机词法／向量粗筛；保留精确原文')
        assembled=assemble(raw,planning.get('plan'),self.ops)
        vectors=self.ops['BoundedLocalVectors'](self.settings.cache_path,self.settings.ollama_model_digest,4096,base_url=self.settings.ollama_url,model=self.settings.ollama_model)
        try:prepared=prepare(assembled,vectors.embed,self.ops,20)
        finally:vectors.close()
        progress('rerank','本机 Qwen 重排中；长文本可能需要更久')
        ranked=rank(prepared,lambda q,ts:self.ops['score_bucketed'](self.model,q,ts,self.ops['encode_pairs']),self.ops)
        chunks=[]
        for n,u in enumerate(ranked['order'][:20],1):
            source=prepared['pool'][u['source']]
            if u['text']!=source['content'][u['start']:u['end']]:raise ValueError('citation source mismatch')
            chunks.append(dict(rank=n,title=source['title'],document_id=source['document_id'],chunk_id=source['id'],
                start=u['start'],end=u['end'],text=u['text'],source_sha256=source['text_sha256']))
        result=dict(status='ok',version='v5 · 隔离整合版 / 2026-09-21',question=question,mode='retrieval_only',
            planning_status=planning['status'],planning_error=planning.get('error'),planning_error_code=planning.get('error_code'),cloud_attempts=cloud_attempts,
            planning_failure_kind=planning.get('failure_kind'),
            planning_contract=(planning.get('plan') or {}).get('contract'),planning_warnings=(planning.get('plan') or {}).get('warnings',[]),
            planning_plan=planning.get('plan'),
            pipeline='dual_view_width20' if planning['status']=='ok' else 'original_question_fallback_width32',
            queries=[dict(kind=r['kind'],query=r['query']) for r in raw['routes']+raw['extra_routes']+raw['scoped_routes']],
            timings=dict(total_seconds=time.monotonic()-started,planning_seconds=plan_seconds,recall_seconds=recall_seconds,
                coarse_seconds=prepared['coarse_seconds'],rerank_seconds=ranked['rerank_seconds']),
            chunks=chunks,score_pairs=ranked['score_pairs'],answer_generated=False,accuracy_verified=False)
        if answer_consent:
            progress('answer','Kimi 根据当前问题与必要法规片段生成带引用回答')
            answer_start=time.monotonic();bundle=evidence_bundle(question,chunks,policy=self.evidence_policy)
            packet=bundle['packet'];result['evidence_packing']=bundle['audit']
            result['evidence_sent_count']=len(packet['sources']);result['answer_cloud_attempts']=0
            try:
                if not packet['sources']:
                    result['answer']=dict(status='insufficient',answers=[],gaps=['本次未返回可供引用的原文，无法据此回答。'],citation_locations_verified=True,semantic_support_verified=False)
                else:
                    if self.planner is None:self.planner=Planner(protocol=self.planner_protocol,review_protocol=self.review_protocol,review_thinking=self.review_thinking)
                    self.planner.review_protocol=self.review_protocol
                    self.planner.review_thinking=self.review_thinking
                    result['answer_cloud_attempts']=1;result['cloud_attempts']+=1
                    if self.answer_protocol=='legacy':
                        reply=self.planner.answer(packet)
                        if reply.get('status')!='ok':raise ValueError('answer failed')
                        answer=reply['answer']
                        check=dict(status=answer['status'],gaps=answer['gaps'],answers=[dict(text=a['text'],citations=[c['id'] for c in a['citations']]) for a in answer['answers']])
                        result['answer']=validate_answer(check,packet)
                    else:
                        phase=time.monotonic();reply=self.planner.request('draft',packet)
                        result['timings']['draft_seconds']=time.monotonic()-phase
                        if reply.get('model_calls')==0:
                            result['answer_cloud_attempts']-=1;result['cloud_attempts']-=1
                        result.setdefault('model_call_metrics',[]).extend(dict(m,phase='draft') for m in reply.get('model_call_metrics',[]))
                        if reply.get('status')!='ok':
                            result['answer_error_code']=reply.get('error_code',reply.get('error'));raise PipelineFailure('draft',reply)
                        draft=reply['draft']
                        progress('support_check','检查结论与计算规则的证据支持；通过后本机执行运算')
                        result['answer_cloud_attempts']+=1;result['cloud_attempts']+=1
                        phase=time.monotonic();reply=self.planner.request('review',packet,draft)
                        additional=reply.get('model_calls',1)-1
                        result['answer_cloud_attempts']+=additional;result['cloud_attempts']+=additional
                        result['timings']['support_check_seconds']=time.monotonic()-phase
                        result.setdefault('model_call_metrics',[]).extend(dict(m,phase='support_check') for m in reply.get('model_call_metrics',[]))
                        if reply.get('status')!='ok':
                            result['answer_error_code']=reply.get('error_code',reply.get('error'));raise PipelineFailure('support_check',reply)
                        result['answer']=apply_review(draft,reply['review'],packet,citation_scope='packet' if self.review_protocol=='separate_support' else 'draft',coverage_only_gaps=self.review_protocol=='separate_support' and reply.get('coverage_completed') is True)
                        if self.review_protocol=='separate_support':
                            result['answer']['support_check'].update(method='separate_support_and_coverage',model_calls=reply.get('model_calls',1),coverage_completed=reply.get('coverage_completed',False))
                    result['answer_generated']=True;result['answer_protocol']=self.answer_protocol;result['review_protocol']=self.review_protocol
            except Exception as exc:
                rate=isinstance(exc,PipelineFailure) and (exc.error_code=='RateLimitError' or exc.failure_kind=='local_proxy')
                result['answer']=dict(status='error',answers=[],gaps=[provider_failure_message(exc.failure_kind) if rate else '回答生成或引用定位检查未完成；检索原文已保留，不代表知识库没有资料。'],error=exc.error_code if isinstance(exc,PipelineFailure) else type(exc).__name__)
                if isinstance(exc,PipelineFailure):result['answer'].update(failed_stage=exc.stage,failure_kind=exc.failure_kind,retry_after=exc.retry_after)
                if self.planner:self.planner.close();self.planner=None
            result['mode']='grounded_answer';result['timings']['answer_seconds']=time.monotonic()-answer_start
            result['timings']['total_seconds']=time.monotonic()-started
        return result

    def close(self):
        if self.worker:self.worker.close()
        if self.planner:self.planner.close()

    def update_question(self, previous, current):
        if self.planner is None:self.planner=Planner(protocol=self.planner_protocol)
        reply=self.planner.request('update',dict(previous=previous,current=current))
        if reply.get('status')!='ok':raise PipelineFailure('conditions',reply)
        update=reply['update']
        # Reapply the model's literal patches locally; never trust its rewritten sentence.
        return apply_update(previous,current,dict(mode='clarify',patches=[],clarification=update['clarification']) if update['status']=='clarify' else dict(mode=update['mode'],patches=update['patches'],clarification=''))
