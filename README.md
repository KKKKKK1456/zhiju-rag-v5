# 知据 · RAG v5

基于本地文件检索、带引用的回答、跨文件规则取值计算，以及单轮条件更正。
这是 **v5-experience-20260924.1 的脱敏源码仓库**，直接提供代码目录，不需要另行解压源码包。它不是托管在线服务，也不是自带知识库的独立安装器。

> **没有附带原作者的 7000 多份文件、索引、聊天记录、密钥或账号。**
> 使用者需准备自己的 RAGFlow、知识库和 Kimi 配置。需要相同资料时应通过私下渠道交付，不要提交进 GitHub。知识库迁移工具正在独立验证，尚未纳入此已验证基线；本仓库目前不承诺一键恢复原作者的知识库。

## 仓库目录

```text
zhiju-rag-v5/
├── app/                 # 检索、重排、证据约束、计算与多轮条件处理
├── preview/             # 本地网页界面
├── backend/             # 固定 RAGFlow 版本的最小补丁及第三方许可
├── tools/               # 启动检查、公开模型准备、源码安全检查
├── tests/               # 离线工程和界面测试
├── docs/                # 后端兼容性与验证范围
├── .env.example         # 无密钥配置模板
├── .gitignore
├── requirements.txt
├── SECURITY.md
└── THIRD_PARTY_NOTICES.md
```

## 先看这两个事实

- GitHub 地址是源码仓库，不是问答网站。安装运行后，在朋友自己的电脑打开 `http://127.0.0.1:8772/`。
- 本版仍依赖匹配版本的 RAGFlow + Elasticsearch、Ollama、Qwen 本地重排模型。后端不是任意版本通用，请先看 [后端兼容与补丁](docs/BACKEND_COMPATIBILITY.md)。

## 安装步骤

推荐环境：macOS / Linux、Python 3.12、Docker、Ollama。Apple Silicon 可用 MPS，其他机器使用 CPU（会明显更慢）；原生 Windows 与 CUDA 未验收，Windows 可自行评估 WSL2。模型与 RAGFlow 都需要较多内存和磁盘，本仓库不承诺具体最低硬件规格。

### 1. 准备后端和你自己的资料

按 [后端兼容说明](docs/BACKEND_COMPATIBILITY.md) 准备指定 RAGFlow 基线和最小补丁，使用 Elasticsearch。
在 RAGFlow 中创建自己的账户、API token 和知识库，选择 **bge-m3** 嵌入模型，上传并完成解析。
不要复制原作者数据库，也不要把原作者 API token 填给朋友。

完整回答使用 Kimi：在 RAGFlow 的模型设置中配置朋友自己的 Moonshot API key、HTTPS API 地址和支持 JSON / thinking 参数的模型。密钥保存在朋友的后端配置中，本项目不导出或打印它。

### 2. 安装本地 Python 依赖

直接克隆仓库并进入项目目录：

```bash
git clone https://github.com/KKKKKK1456/zhiju-rag-v5.git
cd zhiju-rag-v5
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

依赖文件记录参考安装的版本，不代表已在所有系统完整重装验证。若某版本不可用或与平台不兼容，不要盲目升级整个环境；保留报错并核对 Python / 平台版本。

### 3. 下载公开模型并填写本地配置

```bash
ollama pull bge-m3
python -m tools.prepare_model --download
```

Qwen 下载约 1.2 GB，只有执行这条命令才下载，运行助手不会自动下载。固定模型版本并逐文件校验 SHA-256。

编辑 `.env`：

| 配置 | 填什么 |
| --- | --- |
| `RAGFLOW_CONTAINER` | 自己的 RAGFlow 容器名，`docker ps` 可查看 |
| `RAGFLOW_DATASET_ID` | 自己已解析知识库的 ID，可从 RAGFlow 知识库地址/接口查看 |
| `RAGFLOW_CHAT_MODEL_ID` | 自己租户的 CHAT 模型记录 ID，**不是模型名称**；取法见后端说明 |
| `OLLAMA_MODEL_DIGEST` | 本机 `http://127.0.0.1:11434/api/tags` 中 `bge-m3:latest` 的完整 digest |
| `QWEN_DEVICE` | 默认 `cpu`；支持 MPS 的 Mac 可填 `mps` |
| `KIMI_PROXY` | 无代理留空；需要代理时填容器可访问的本地代理地址 |

宿主机 Ollama 与 RAGFlow 所配置的嵌入模型必须一致，不能用不同模型的向量混排。
`.env` 不执行 shell 命令、不做变量展开；不要把 API key 或密码放进去。

### 4. 检查与启动

```bash
python -m tools.run check --cloud
python -m tools.run serve --cloud
```

终端保持运行，页面显示就绪后打开 <http://127.0.0.1:8772/>。
检查命令只检查本地前置条件，不调用 Kimi，不代表端到端问答已经通过。

只想本地检索、不开放云端选项：

```bash
python -m tools.run serve
```

按 `Ctrl-C` 停止本项目，不删除知识库，不停止 Docker。不安装开机自启。

## 怎么体验

1. 用自然语言提问，写明主体、年份、金额和适用条件。
2. 完整问答须阅读并勾选页面两项许可：问题拆分、必要依据发送给 Kimi。许可每题清空，不默认发送。
3. 答案下可展开引用原文、计算步骤；缺条件、缺依据、服务故障与计算不通过会分别提示。
4. 更正金额/期间时勾选“继续追问最近完成的一题”；换话题用“新问题”。
5. 后端只保留最近一题，15 分钟有效；页面历史最多 20 条，刷新后不保留。多标签页共享一份任务，不是多用户隔离服务。

## 隐私与共享边界

- GitHub 仓库只包含应用源码、界面、测试、配置模板、公开模型校验清单、后端最小补丁和说明。
- 不含 API key、数据库、原文、索引、向量缓存、日志、运行结果、原作者绝对路径和私有数据集/模型 ID。
- 查询和引用在界面许可后发给 **Moonshot/Kimi**。每次回答最多 20 条 / 12000 字符证据，不发送整库；仍可能包含敏感资料，请自行判断许可。
- 本地嵌入请求也会把候选片段交给你自己配置的本地 Ollama；不配置公网 Ollama。
- 导出结果可能含问题和原文，不要误提交。`.gitignore` 是辅助措施，不替代发布前审查。
- 仅绑定回环地址，没有多用户认证/权限隔离；**不要把端口通过公网穿透或反向代理公开给朋友**。朋友应各自运行，或另行设计安全部署。

## 验证与已知限制

```bash
python -m unittest discover -s tests -v
python -m tools.package_release --check
```

这些是离线工程与协议测试，不是全库法律准确率。它们验证引用边界、中文数值计算、配置、前端安全与脱敏检查。
参考版的历史回归不能当作朋友新环境的通过证明。完整新环境安装、同数据回归与云端回答还需单独验证。
当前支持有依据的跨文档规则取值与计算，但不保证任意复杂案例都成功；完整回答可能较慢。

常见错误：

- **网页拒绝连接**：启动终端已关闭，或服务未启动。重新运行 `tools.run serve`。
- **初始化失败**：检查容器、ES 后端、知识库、模型路径和版本；不要盲目删库重导。
- **unexpected backend version**：不是库里没资料，是 RAGFlow 源码版本/后端不匹配。
- **model digest / checksum mismatch**：模型变更或文件不完整；核对版本，不跳过校验。
- **Kimi 限流或余额不足**：检查朋友自己的账户；本项目不自动充值、不隐式重试。
- **答案缺依据**：检查是否成功解析、原文是否完整、问题年份及多文件规则是否召回齐全。

许可说明见 [第三方说明](THIRD_PARTY_NOTICES.md)。公开展示源码不等于已经选定开放源代码许可证；原创部分暂未附整体开源许可证。RAGFlow 补丁包含的上游代码按其 Apache-2.0 许可保留声明。
