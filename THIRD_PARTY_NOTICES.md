# 第三方与许可说明

- RAGFlow：来源 `infiniflow/ragflow`；固定基线见 `docs/BACKEND_COMPATIBILITY.md`。本包包含对 `rag/nlp/search.py` 的修改补丁和新增辅助文件。上游 Apache-2.0 许可原文见 `backend/LICENSE-APACHE-2.0`，原文件头已保留。补丁不代表上游官方版本。
- Qwen3-Reranker-0.6B：仅保存公开模型的固定 revision 与文件校验清单，不再分发权重。主动下载前请阅读原模型仓库 `Qwen/Qwen3-Reranker-0.6B` 的许可和模型说明。
- bge-m3 / Ollama、PyTorch、Transformers、Hugging Face Hub：独立依赖，需按各自项目许可安装使用；本包不复制其安装环境。
- Moonshot/Kimi：第三方云服务，朋友自行配置账号/密钥，并按服务条款和计费规则使用；不是本仓库提供的免费服务。

原创应用整体暂未附开源许可证；公开浏览与仓库所有者授权朋友体验不等于授予不受限制的再分发许可。已有第三方许可不受此说明改变。
