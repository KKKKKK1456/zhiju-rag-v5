# RAGFlow 后端兼容边界

这不是通用 RAGFlow 安装包，也不是已在另一台电脑完成端到端验证的部署。这里仅保留当前预览运行链涉及的两个检索实现改动，不包含知识库、模型权重、索引、账号、凭据或现有容器配置。

## 固定基线与后端

- 基线仓库：<https://github.com/infiniflow/ragflow>。
- 基线 commit：`b9df87c4c75a5b0d35c90d15329fc0f6f91cb73e`（本地 checkout 记录的 0.27.1 release-notes commit）。
- 兼容范围限定 **Elasticsearch**。没有验证 Infinity、OceanBase、GaussDB 或其他后端，也不承诺其他 commit/镜像 tag 兼容。
- 该 commit 与源文件来自本地 shallow checkout；本次未联网核实远端，也没有启动新的 RAGFlow 服务或提交云模型请求。
- 原 checkout 存在其他未提交改动；本包不是整个 dirty checkout 的镜像。仅安装官方相同 commit 而不应用本补丁，会缺少当前检索的精确锚点补召回和排序行为。

## 本补丁包含什么

`backend/ragflow-exact-anchor.patch` 包含：

1. 对 `rag/nlp/search.py` 的 tracked diff：查询规范化、精确锚点词法补召回、合并候选、以及存在精确锚点时的混合得分（0.4 × 原检索得分 + 0.6 × 字面覆盖）。
2. 新增完整的 `rag/nlp/exact_match.py`：通用 Unicode/空白规范化、编号及显式引号名称提取、字面覆盖率计算；该新增文件已内嵌于补丁，无须另行复制。

未包括任何真实查询、聊天记录、法规片段或 benchmark fixture。本次逐段检查了 search.py 差异及 exact_match.py 全文，并检查个人路径、UUID/长十六进制私有标识、常见凭据形式；这不替代项目级安全审查。

注意：检索补丁保留了源代码的 debug 日志，可能记录运行时问题中的精确锚点。新环境不应启用不必要的 debug 日志，也不要将服务日志提交到 GitHub。

## 在独立 checkout 应用

使用单独的新 checkout。不要直接修改正在服务的生产知识库安装。先取得上述基线，再把下面的占位路径改为这份分享包的实际位置：

```sh
cd /path/to/independent/ragflow
git checkout --detach b9df87c4c75a5b0d35c90d15329fc0f6f91cb73e
git rev-parse HEAD
git status --short
git apply --check /path/to/zhiju-rag-v5/backend/ragflow-exact-anchor.patch
git apply /path/to/zhiju-rag-v5/backend/ragflow-exact-anchor.patch
git diff --stat
git status --short
```

应用前 HEAD 必须与固定 commit 完全一致，且工作树应干净。任何 `git apply --check` 错误都应停止；不要使用强制覆盖、`--reject` 或跳过检查。成功后应只有 search.py 被修改，以及 exact_match.py 被新增。补丁没有改动 Docker/数据库/索引，也没有导入语料。

应用源码不表示正在运行的容器已经使用该源码。应按新环境自己的构建/部署流程确认容器里的 RAGFlow 代码版本；本包不沿用原机器的挂载、镜像或 compose 文件。

## 为什么还需要严格限定版本

预览中的 `tools/integrated_local_worker.py` 不是只调用稳定公开 HTTP API：它导入 RAGFlow 的内部数据库/模型服务，并在当前进程中替换 Elasticsearch 的 search 方法。

其精确匹配的原始语句位于基线 `rag/utils/es_conn.py:268`。源 checkout 中该文件未修改，基线具有所需字符串。但其他后端/版本即使 HTTP API 名称相同，也可能初始化失败。这个进程内补丁由预览应用自身执行，不是本 backend patch 的持久改动。

固定基线中已有运行所需的：

- `api.db.db_models` 及其 Knowledgebase、TenantModel、APIToken、Document 模型；
- `resolve_model_config`、`get_model_config_by_id`、`LLMBundle`、`KnowledgebaseService.accessible`、`rag.app.tag.label_question`；
- `/api/v1/retrieval` 的 `include_knowledge_compilation`、`knn_top_k`、`knn_num_candidates` 等现用参数。

本次只确认这些接口的静态形状与源 checkout 差异。朋友仍需自己的数据库初始化、知识库、文档、索引、token、模型行 ID、Ollama 模型、Python 环境与配置；不能复用原机器的标识或凭据。

## 取得自己的 CHAT 模型记录 ID

`RAGFLOW_CHAT_MODEL_ID` 对应 `tenant_model.id`，不是 `model_name`，也不是 API key。先在朋友自己的 RAGFlow 中配置好 Moonshot/Kimi 模型，再从同一知识库所属租户中选取支持 CHAT 的活动模型。

以下只读示例按固定基线的真实 ORM 字段核对：`api/db/db_models.py` 中的 Knowledgebase、TenantModelProvider、TenantModelInstance、TenantModel，以及 `common/constants.py` 的 CHAT 位标记。它不是声称存在某个未验证的 REST API；尚未在朋友的新环境执行。

将命令中的 `YOUR_DATASET_ID`、容器名和容器内路径替换为朋友自己的配置。仅在其本人管理的容器上运行，不要在共享租户环境中据此枚举他人模型。

```bash
docker exec -i -w /ragflow \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/ragflow \
  -e RAGFLOW_DATASET_ID=YOUR_DATASET_ID \
  ragflow-server /ragflow/.venv/bin/python - <<'PY'
import json
import logging
import os
import sys

logging.disable(logging.CRITICAL)
try:
    from api.db.db_models import (
        DB, Knowledgebase, TenantModel, TenantModelProvider, TenantModelInstance,
    )
    from common.constants import ActiveStatusEnum, ModelTypeBinary

    with DB.connection_context():
        kb = (Knowledgebase.select(Knowledgebase.tenant_id)
              .where(Knowledgebase.id == os.environ['RAGFLOW_DATASET_ID']).get())
        providers = (TenantModelProvider.select(TenantModelProvider.id)
                     .where(TenantModelProvider.tenant_id == kb.tenant_id))
        instances = (TenantModelInstance.select(TenantModelInstance.id)
                     .where(TenantModelInstance.provider_id.in_(providers),
                            TenantModelInstance.status == ActiveStatusEnum.ACTIVE.value))
        query = (TenantModel.select(TenantModel.id, TenantModel.model_name)
                 .where(TenantModel.provider_id.in_(providers),
                        TenantModel.instance_id.in_(instances),
                        TenantModel.status == ActiveStatusEnum.ACTIVE.value,
                        TenantModel.model_type.bin_and(ModelTypeBinary.CHAT.value) > 0))
        rows = [{'id': row.id, 'name': row.model_name} for row in query]
except Exception:
    print('Could not list CHAT models; check your own dataset and backend version.', file=sys.stderr)
    sys.exit(1)
print(json.dumps(rows, ensure_ascii=False, indent=2))
PY
```

此查询只输出 ID 和模型名称；没有选择 `api_key`、instance `extra` 或完整模型配置，也不调用云端或修改数据库。选择已经配置为 Moonshot/Kimi 的那一项，将其 `id` 填入 `.env`。如同名模型不止一项或输出为空，应回到自己的 RAGFlow 模型配置核实，不要随机挑选，不要把数据库或含密钥的完整配置贴到 GitHub。

## 没有导出的其他 dirty 改动

| 原 checkout 中的改动 | 此预览运行包为何不包含 |
| --- | --- |
| `api/apps/restful_apis/chunk_api.py` | 差异为批量语料导入端点；当前预览只检索，不调用这些导入端点。 |
| `api/db/services/dialog_service.py`、`rag/advanced_rag/**` | 为 RAGFlow 原生对话/agent harness 路径；本预览使用自身规划、答案及审查管线，未调用该原生路径。 |
| `docker/.env`、`docker/docker-compose-*.yml` | 原机器的部署/连接/挂载配置，不是可共享的运行默认值；朋友必须单独配置。 |
| `ragflow_deps/download_deps.py` | 依赖下载/准备工具，不属于该预览检索调用链。 |
| `test/unit_test/**` 的本地改动 | 原后端或 agent 开发测试，不属于运行源码；没有将其中 fixture 纳入分享包。 |

“不包含”只针对当前 preview 运行链，不代表这些改动对于原安装的其他功能不重要。

## 验证范围

本次在临时独立目录重建基线 search.py，检查补丁能够干净应用、应用结果与已审查源文件逐字节一致，并完成 Python AST 语法检查。未修改原 checkout，未构建新镜像、未更改数据库、未运行模型，也未验证新环境的答案质量或速度。

下一步至少应在新环境验证：容器源码版本、Elasticsearch 后端、应用启动、纯本地检索、引用偏移/来源检查、权限范围、模型和 embedding digest，以及明确逐次许可后的云端流程。历史评测结果不能当作新部署验收。

## 许可与归属

上游 Apache License 2.0 原文保留于 `backend/LICENSE-APACHE-2.0`。search.py 和 exact_match.py 既有版权/许可头均保留；此说明不是对整个分享项目重新授权，也不新增 MIT 许可声明。

本地源 checkout 未发现上游 NOTICE 文件，因此没有虚构 NOTICE。若之后采用的上游分发版本包含 NOTICE 或附加许可证，需一并保留。补丁是本地检索实现的修改记录，不是上游发布或官方兼容背书。
