"""Persistent, read-only local retrieval adapter. Credentials stay in container."""
import hashlib
import json
import logging
import os
import signal
import sys
import time
import urllib.request

KB_ID = os.environ.get('RAGFLOW_DATASET_ID', '').strip()
PARAMS = {
    "dataset_ids": [KB_ID], "page": 1, "page_size": 20,
    "similarity_threshold": 0.35, "vector_similarity_weight": 0.3,
    "rerank_candidates_count": 64, "knn_top_k": 1024, "knn_num_candidates": 2048,
    "highlight": False, "include_knowledge_compilation": False,
    "use_kg": False, "toc_enhance": False, "cross_languages": [],
    "keyword": False, "rerank_id": "", "document_ids": [],
}


def emit(data):
    print("V5_RPC=" + json.dumps(data, ensure_ascii=False, default=str), flush=True)


def initialize():
    if not KB_ID:
        raise ValueError('RAGFLOW_DATASET_ID is required')
    logging.disable(logging.CRITICAL)
    from common import settings
    settings.init_settings()
    from api.db.db_models import APIToken, Document, Knowledgebase, TenantModel
    kb = Knowledgebase.get_by_id(KB_ID)
    model = TenantModel.get_by_id(kb.embd_id)
    token = (APIToken.select().where(APIToken.tenant_id == kb.tenant_id)
             .order_by(APIToken.update_time.desc()).first())
    if token is None:
        raise RuntimeError("credential unavailable")
    return kb, model, token, Document


def retrieve(message, kb, token, Document):
    question = message.get("question")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
        return {"status": "invalid", "message": "请输入 1–2000 字的问题。"}
    start = time.perf_counter()
    # Recheck document membership each request, not a stale startup allowlist.
    allowed = {d.id for d in Document.select(Document.id).where(Document.kb_id == KB_ID)}

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    request = urllib.request.Request(
        "http://127.0.0.1:9380/api/v1/retrieval",
        data=json.dumps({**PARAMS, "question": question.strip()}, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + token.token, "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(request, timeout=60) as response:
        data = json.load(response)
    if data.get("code") != 0:
        return {"status": "service_error", "message": "检索接口返回错误，不能视为知识库没有资料。",
                "error_code": data.get("code")}
    chunks = []
    for rank, raw in enumerate(data.get("data", {}).get("chunks", []), 1):
        if raw.get("document_id") not in allowed or raw.get("dataset_id") != KB_ID:
            raise ValueError("source scope mismatch")
        content = raw.get("content", "")
        chunks.append({
            "rank": rank, "id": raw.get("id"), "document_id": raw["document_id"],
            "title": raw.get("document_keyword", "未提供文件标题"),
            "content": content, "positions": raw.get("positions", []),
            "similarity": raw.get("similarity"), "keyword_similarity": raw.get("term_similarity"),
            "vector_similarity": raw.get("vector_similarity"),
            "text_sha256": hashlib.sha256(content.encode()).hexdigest(),
        })
    return {"status": "ok", "question": question.strip(), "chunks": chunks,
            "elapsed_seconds": round(time.perf_counter() - start, 3),
            "parameters": PARAMS, "dataset_id": KB_ID, "query_rewritten": False,
            "chat_model_called": False, "internal_timings": None,
            "timing_note": "总耗时包含只读权限范围检查和检索HTTP；接口内部耗时不可观测。",
            "cache_state": "uncontrolled", "answer_accuracy_verified": False}


def main():
    try:
        kb, model, token, Document = initialize()
        emit({"type": "ready", "pid": os.getpid(),
              "dataset": {"id": kb.id, "name": kb.name, "documents": kb.doc_num,
                          "chunks": kb.chunk_num, "embedding": model.model_name},
              "parameters": PARAMS})
    except Exception as exc:
        emit({"type": "fatal", "error": type(exc).__name__})
        return
    for line in sys.stdin:
        message = {}
        try:
            message = json.loads(line)
            def expire(_sig, _frame):
                raise TimeoutError("v5 deadline exceeded")
            signal.signal(signal.SIGALRM, expire)
            signal.setitimer(signal.ITIMER_REAL, 60)
            result = retrieve(message, kb, token, Document)
        except Exception as exc:
            result = {"status": "service_error", "error_type": type(exc).__name__,
                      "message": "检索未完成。已暂停新请求，避免叠加后台任务；请检查本机服务。",
                      "backend_cancellation": "unverified", "paused": True}
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        emit({"type": "result", "id": message.get("id"), "result": result})


if __name__ == "__main__":
    main()
