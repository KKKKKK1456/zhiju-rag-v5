"""Disposable current-ACL retrieval worker. No server/index edits, no chat model."""
import json
import select
import subprocess
import time
from pathlib import Path
from app.settings import load_settings, docker_python_command

ROOT = Path(__file__).resolve().parents[1]


class LocalRecallWorker:
    def __init__(self):
        self.settings=load_settings(require_dataset=True)
        code = (ROOT/'app/worker.py').read_text().split('if __name__ == "__main__":')[0]
        code += '\n' + (ROOT/'app/independent_knn_candidate.py').read_text() + '''
import asyncio, inspect, textwrap, types
kb, model, token, Document = initialize()
from common import settings
from common.constants import LLMType
from api.db.joint_services.tenant_model_service import resolve_model_config
from api.db.services.llm_service import LLMBundle
from api.db.services.knowledgebase_service import KnowledgebaseService
from rag.app.tag import label_question
embd = LLMBundle(kb.tenant_id,resolve_model_config(kb.tenant_id,LLMType.EMBEDDING,kb.embd_id))
store = settings.retriever.dataStore
original = store.search
source = textwrap.dedent(inspect.getsource(original))
needle = 'filter=bool_query.to_dict(),  # filter=_build_knn_filter_query(bool_query, vector_similarity_weight),'
if source.count(needle)!=1:raise ValueError('unexpected backend version')
ns = dict(original.__func__.__globals__,independent_knn_filter=independent_knn_filter)
exec(source.replace(needle,'filter=independent_knn_filter(bool_query.to_dict()),'),ns)
candidate = types.MethodType(ns['search'],store)
PARAMS.update(vector_similarity_weight=.8,rerank_candidates_count=128,page_size=100)
emit(dict(type='ready',pid=os.getpid(),backend_sha256=hashlib.sha256(source.encode()).hexdigest()))
async def scoped(message):
    ids = message['document_ids']
    allowed={d.id for d in Document.select(Document.id).where(Document.kb_id==KB_ID)}
    if len(ids)!=1 or not set(ids)<=allowed:raise ValueError('scope mismatch')
    store.search = candidate
    try:
        ranks=await asyncio.wait_for(settings.retriever.retrieval(message['question'],embd,[kb.tenant_id],[KB_ID],1,100,.35,.8,
            doc_ids=ids,highlight=False,rank_feature=label_question(message['question'],[kb]),
            must_not={'exists':'compile_kwd'},rerank_candidates_count=128,knn_top_k=1024,knn_num_candidates=2048),60)
        chunks=[]
        for c in ranks['chunks']:
            if c['doc_id'] not in ids or c['kb_id']!=KB_ID:raise ValueError('source scope escaped')
            content=c['content_with_weight']
            chunks.append(dict(id=c['chunk_id'],document_id=c['doc_id'],title=c['docnm_kwd'],content=content,
                text_sha256=hashlib.sha256(content.encode()).hexdigest(),similarity=c['similarity']))
        return dict(status='ok',chunks=chunks,total=ranks['total'])
    finally:store.search=original
for line in sys.stdin:
    message=json.loads(line)
    try:
        if not KnowledgebaseService.accessible(kb_id=KB_ID,user_id=token.tenant_id):raise ValueError('dataset access denied')
        result=asyncio.run(scoped(message)) if message['document_ids'] else retrieve(message,kb,token,Document)
    except Exception as exc:result=dict(status='error',error=type(exc).__name__)
    emit(dict(id=message['id'],result=result))
'''
        self.proc = subprocess.Popen(docker_python_command(self.settings,code),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.pid = None; self.count = 0; self.broken = False
        try:
            # Cold imports and local embedding initialization can exceed 50s.
            # This is a startup deadline only; retrieval stays bounded at 70s.
            self.ready = self.read(120); self.pid = self.ready.get('pid')
            if self.ready.get('type') != 'ready':raise RuntimeError('worker init failed')
        except BaseException:
            self.close(); raise

    def read(self, timeout):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            if not select.select([self.proc.stdout],[],[],max(0,end-time.monotonic()))[0]:raise TimeoutError('worker deadline')
            line=self.proc.stdout.readline()
            if not line:raise RuntimeError('worker exited')
            if line.startswith('V5_RPC='):return json.loads(line[7:])
        raise TimeoutError('worker deadline')

    def retrieve(self, question, document_ids):
        if self.broken:raise RuntimeError('worker paused after failed request')
        self.count += 1; rid = str(self.count)
        try:
            self.proc.stdin.write(json.dumps(dict(id=rid,question=question,document_ids=document_ids),ensure_ascii=False)+'\n')
            self.proc.stdin.flush(); reply=self.read(70)
            if reply.get('id')!=rid:raise ValueError('reply mismatch')
            if reply['result'].get('status')!='ok':raise RuntimeError('recall incomplete')
            return reply['result']
        except BaseException:
            self.broken=True; self.close(); raise

    def close(self):
        if self.proc.poll() is not None:return
        self.proc.stdin.close()
        try:self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if type(self.pid) is int:
                subprocess.run(['docker','exec',self.settings.container,'kill','-TERM',str(self.pid)],capture_output=True,timeout=8)
            self.proc.terminate()
