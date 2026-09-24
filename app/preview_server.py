"""Separate loopback-only, consent-gated, asynchronous retrieval preview."""
import argparse
import json
import secrets
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.server import Handler as BaseHandler
from app.preview_runtime import Engine, provider_failure_message
from app.release_info import RELEASE


class Jobs:
    def __init__(self,cloud_enabled=False,factory=Engine,answer_enabled=False):
        self.cloud_enabled=cloud_enabled;self.answer_enabled=answer_enabled;self.factory=factory;self.state='starting';self.current=None
        self.initialization_error=None
        self.engine=None;self.lock=threading.Lock();self.thread=threading.Thread(target=self.initialize,daemon=True);self.thread.start()

    def initialize(self):
        try:self.engine=self.factory(self.cloud_enabled,self.answer_enabled);self.state='ready'
        except Exception as exc:
            self.initialization_error=type(exc).__name__;self.state='unavailable'

    def snapshot(self):
        with self.lock:
            if self.current and self.current['state'] in ('complete','error') and time.monotonic()-self.current['updated']>900:self.current=None
            configuration={name:getattr(self.engine,name,None) for name in ('review_protocol','review_thinking','evidence_policy','planner_protocol')}
            job=json.loads(json.dumps(self.current))
            if job and job['state']=='running':
                job['elapsed_seconds']=round(time.monotonic()-job.get('started',job['updated']),1)
            return dict(state=self.state,release=dict(RELEASE),initialization_error=self.initialization_error,cloud_enabled=self.cloud_enabled,answer_enabled=self.answer_enabled,configuration=configuration,job=job)

    def submit(self,question,consent,answer_consent=False,base_id=None,context_consent=False):
        with self.lock:
            if self.state!='ready':return 503,dict(message='模型尚未就绪，请查看状态。')
            if self.current and self.current['state']=='running':return 429,dict(message='已有检索运行中；不重复排队。')
            if consent and not self.cloud_enabled:return 403,dict(message='尚未启用云端问题拆分授权。')
            if answer_consent and not self.answer_enabled:return 403,dict(message='尚未启用法规片段发送与回答授权。')
            previous=None
            if base_id is not None:
                if not context_consent or not self.cloud_enabled:return 403,dict(message='继续追问需允许将上一轮用户条件与本次问题发送给 Kimi。')
                if not self.current or self.current['id']!=base_id or self.current['state']!='complete' or time.monotonic()-self.current['updated']>900:
                    return 409,dict(message='上一轮已过期或被替换，请完整输入当前条件。')
                previous=self.current['result'].get('canonical_question',self.current['result']['question'])
            now=time.monotonic()
            job=dict(id=uuid.uuid4().hex,state='running',stage='queued',message='准备检索',started=now,updated=now)
            self.current=job
            self.thread=threading.Thread(target=self.run,args=(question,consent,job['id'],answer_consent,previous),daemon=True);self.thread.start()
            return 202,dict(id=job['id'])

    def run(self,question,consent,job_id,answer_consent=False,previous=None):
        def progress(stage,message):
            with self.lock:self.current.update(stage=stage,message=message,updated=time.monotonic())
        try:
            started=time.monotonic();update=None;effective=question;canonical=question
            if previous is not None:
                progress('conditions','只更新用户明确更正的条件；不使用助手旧答案')
                update=self.engine.update_question(previous,question)
                if update['status']=='clarify':
                    result=dict(question=question,canonical_question=previous,mode='clarification',planning_status='not_run',planning_error=None,cloud_attempts=1,chunks=[],queries=[],answer_generated=False,
                        answer=dict(status='needs_clarification',answers=[],gaps=[update['clarification']]),
                        timings=dict(total_seconds=time.monotonic()-started,planning_seconds=0,recall_seconds=0,coarse_seconds=0,rerank_seconds=0,answer_seconds=0))
                    with self.lock:self.current.update(state='complete',stage='complete',message='需要补充条件',result=result,updated=time.monotonic())
                    return
                effective=update['effective'];canonical=update['canonical']
            update_seconds=time.monotonic()-started
            result=self.engine.search(effective,consent,progress,answer_consent=answer_consent)
            result.update(canonical_question=canonical,user_message=question,
                          release=dict(RELEASE),version=RELEASE['id'])
            if update is not None:
                result['condition_update']=update;result['cloud_attempts']+=1
                result['timings']['condition_update_seconds']=update_seconds
                result['timings']['total_seconds']+=update_seconds
            with self.lock:self.current.update(state='complete',stage='complete',message='检索完成',result=result,updated=time.monotonic())
        except Exception as exc:
            with self.lock:
                failed_stage=self.current.get('stage','unknown')
                self.current.update(state='error',stage='error',failed_stage=failed_stage,
                    cloud_attempts=0 if getattr(exc,'failure_kind',None)=='local_proxy' else 1 if failed_stage=='conditions' else None,
                    message=provider_failure_message(getattr(exc,'failure_kind',None)) if getattr(exc,'error_code',None)=='RateLimitError' or getattr(exc,'failure_kind',None)=='local_proxy' else '处理未完成，不表示知识库没有资料。请检查服务后再手动重试。',error=getattr(exc,'error_code',type(exc).__name__),failure_kind=getattr(exc,'failure_kind',None),retry_after=getattr(exc,'retry_after',None),updated=time.monotonic())

    def close(self):
        self.thread.join(timeout=1)
        if self.engine and not self.thread.is_alive():self.engine.close()


class Handler(BaseHandler):
    def do_GET(self):
        if not self.safe_host():return self.send(403,dict(message='Local host only'))
        path=urlsplit(self.path).path
        if path=='/api/status':
            if not self.authenticated():return self.send(403,dict(message='Local session required'))
            return self.send(200,self.server.jobs.snapshot())
        files={'/':('index.html','text/html; charset=utf-8'),'/preview.js':('preview.js','text/javascript; charset=utf-8'),'/preview.css':('preview.css','text/css; charset=utf-8')}
        if path not in files:return self.send(404,dict(message='Not found'))
        name,mime=files[path];body=(ROOT/'preview'/name).read_bytes()
        if path=='/':body=body.replace(b'__V5_SESSION__',self.server.session.encode())
        return self.send(200,body,mime)

    def do_POST(self):
        if not self.safe_host() or not self.authenticated() or self.headers.get('Origin')!='http://'+self.server.authority:
            return self.send(403,dict(message='Same-origin local session required'))
        if self.path!='/api/search':return self.send(404,dict(message='Not found'))
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(415,dict(message='JSON required'))
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 1<=size<=16000:raise ValueError()
            data=json.loads(self.rfile.read(size));q=data.get('question');consent=data.get('cloud_consent',False);answer_consent=data.get('answer_consent',False);base_id=data.get('base_id');context_consent=data.get('context_consent',False)
            if set(data)-{'question','cloud_consent','answer_consent','base_id','context_consent'} or not isinstance(q,str) or not 1<=len(q.strip())<=2000 or type(consent) is not bool or type(answer_consent) is not bool or type(context_consent) is not bool or base_id is not None and (not isinstance(base_id,str) or len(base_id)>64):raise ValueError()
        except (ValueError,AttributeError,TypeError):return self.send(400,dict(message='请输入 1–2000 字的问题。'))
        code,body=self.server.jobs.submit(q.strip(),consent,answer_consent,base_id,context_consent);self.send(code,body)


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8770);p.add_argument('--cloud-approval-reference');p.add_argument('--answer-approval-reference');p.add_argument('--legacy-answer',action='store_true');p.add_argument('--planner-protocol',choices=['source_bound','anchored'],default='anchored');p.add_argument('--evidence-policy',choices=['ranked','document_round_robin'],default='ranked');p.add_argument('--review-protocol',choices=['verdict_first','evidence_first','joint_evidence','separate_support'],default='verdict_first');p.add_argument('--review-thinking',action='store_true');args=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.authority=f'127.0.0.1:{server.server_port}'
    server.session=secrets.token_urlsafe(32);server.jobs=Jobs(bool(args.cloud_approval_reference),factory=lambda c,a:Engine(c,a,'legacy' if args.legacy_answer else 'reviewed',args.planner_protocol,args.evidence_policy,args.review_protocol,args.review_thinking),answer_enabled=bool(args.answer_approval_reference))
    print('Preview listening on http://'+server.authority,flush=True)
    try:server.serve_forever()
    finally:server.server_close();server.jobs.close()


if __name__=='__main__':main()
