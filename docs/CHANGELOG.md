# 修改记录（CHANGELOG）

> 本文件按时间倒序记录每一次代码修改：**改了什么**、**为什么这么改**、**解决了什么问题**。
> 每条记录对应一次 git 提交，便于回溯与审计。

---

## 2026-09-03 Agents/Workflows 挂载会话记忆

**提交**：`8c84837`（配套测试 `0027016`）

**改了什么**：
- `BaseAgent` 记忆装载重写（此前 `set_memory/execute_with_memory` 存在但零调用、且输入输出键不匹配任何具体 Agent）：
  - 新增 `attach_memory(memory_manager)`：挂载**已构造**的 manager（多 Agent 共享同一会话，不重复初始化）
  - `set_memory` 保留签名，返回 manager 便于复用
  - `execute_with_memory`：**深拷贝防污染**（不再向调用方 dict 塞 `memory_context`）；`get_context()` 结果截断至 4000 字符注入 `memory_context` 键；用户消息按 `user_input/requirement/question/query` 提取（仅有 code 时加"请审查以下代码"前缀）；助手消息按 `explanation/review/summary/output/answer` 提取、code 截断兜底 —— Coder/Reviewer 的键适配补齐
- CoderAgent / ReviewerAgent 组装 prompt 时消费 `memory_context`，生成与审查轮次可参考历史会话（无记忆时行为不变）
- `BaseWorkflow` 新增 `memory_manager` 构造注入 + `ensure_memory(initial_state)`（支持 `initial_state["memory"]` = session_id/user_id/db_session 配置自动建 manager）+ `memory_info()`（结果回传会话元信息）
- CodeReviewWorkflow / TaskPlannerWorkflow：开始执行时装载记忆并 attach 到**所有**参与 Agent（TaskPlanner 对动态创建的子 Coder/Reviewer 同样挂载），节点调用改为 `execute_with_memory`；结果 `metadata.memory` 回传 `{enabled, session_id}` —— 一次工作流内多 Agent 轮次汇聚到同一会话，跨请求带上 session_id 即可延续
- HTTP：`POST /api/v1/workflows/code-review`、`/task-planner` 支持 `enable_memory` + 可选 `session_id`（不传自动生成并随 metadata 返回），复用请求 DB 会话持久化
- 文档：`MEMORY_USAGE_GUIDE.md` 新增「Workflow 会话记忆」小节并修正 execute_with_memory 键适配说明
- `backend/tests/test_workflow_memory.py`（40 项断言）：FakeMemoryManager + mock Agent **纯离线**覆盖消息键适配、上下文注入/防污染、Coder/Reviewer 实际适配、两工作流共享记忆与 metadata 回传、未启用记忆零回归

**为什么这么改**：
- 记忆模块早已具备（短/长程 + 持久化），但"接入 Agent/Workflow"只是纸面接口：Coder 产出 `code` 而 execute_with_memory 只认 `output`，workflow 从不挂载 —— 记忆实际上不可用
- Workflow 是记忆最自然的载体：多 Agent 多轮次共享会话，让"上下文连续"不再依赖每次请求把全量历史塞进 input

**解决了什么问题**：
- 记忆从"可调用的库"变成"开箱即用的会话能力"：一行 `enable_memory` + 同一 `session_id`，跨请求/跨 Agent 上下文自动衔接
- 修复输入污染与键不匹配两个潜在缺陷；未启用记忆路径与旧行为严格一致（全量回归通过）

---

## 2026-09-03 RAG RAGAS 离线评估框架

**提交**：`09526e4`（配套测试 `71990d3`）

**改了什么**：
- 新增 `backend/app/rag/evaluator.py`：RAGAS 指标评估器（可选依赖、顶部零导入）
  - 指标：`faithfulness / answer_relevancy / context_precision / context_recall`（含中文说明、默认全开、未知指标校验）
  - 样本归一化：`question/answer/contexts/ground_truth`，容错 `ground_truths` 别名与字符串→列表
  - **自动适配 ragas 两代 API**：0.1.x（HF Dataset + `metrics.base.set_llm/set_embeddings`）与 0.2.x（`EvaluationDataset/SingleTurnSample`，llm/embeddings 走 evaluate 参数）；未安装/版本不支持 → 带安装指引的 `RAGEvaluationError`，绝不拖垮主链路
  - 结构化报告：指标均值 + 逐问题明细（NaN/缺失归一为 None，均值只统计有效值）
- `RAGAgent.execute` 增可选 `include_full_documents`：携带模型实际看到的**全文片段**（结果中的 `retrieved_documents` 为 200 字符预览，供评估不可用）；不进语义缓存快照，默认关闭零影响
- 端到端 runner `backend/examples/rag_eval_runner.py`：自包含数据集（corpus+questions）导入隔离租户 → 逐问跑完整实时链路（自动关闭语义缓存）→ RAGAS 打分 → 写 JSON 报告；支持 `--no-rerank/--no-transform` 做 A/B 对照
- 示例数据集 `backend/examples/rag_eval_dataset.example.json`（3 篇语料 + 5 问）；可选依赖清单 `backend/requirements-eval.txt`（`ragas>=0.1.10`）
- 文档：新增 📖 `docs/RAG_EVALUATION_GUIDE.md`；FAQ Q4 更新为具体指标与开箱步骤；架构文档 v2.0 路线进度同步
- `backend/tests/test_rag_eval.py`（38 项断言）：假 ragas/datasets 注入，**离线**覆盖归一化、指标校验、报告聚合、legacy/v2 双代适配、LLM/Embedding 装配时机、未安装降级

**为什么这么改**：
- 「查询转换/重排」这类改造必须能量化收益，否则无从证明价值；RAGAS 是业界通用离线评估标准
- 平台坚持零重依赖与可离线测试：ragas 仅在评估命令真正运行时惰性加载，编排逻辑全部用假模块回归

**解决了什么问题**：
- 建立「检索质量 → 生成质量」的量化基线：context_recall 低提示调大 k/开启查询转换，context_precision 低提示开启重排
- 评估公平性：contexts 用模型真实看到的全文（非截断预览），缓存关闭保证测的是实时链路
- 开发者无需懂 ragas 内部：一份自包含数据集 + 一条命令即可出报告

---

## 2026-09-03 RAG 查询转换（LLM 多查询扩展）

**提交**：`d1bcdd0`（配套测试 `fc0d497`）

**改了什么**：
- 新增 `backend/app/rag/query_transformer.py`：检索前用 LLM 一次调用把用户问题改写成多个检索变体（**多查询扩展**）；输出恒以原文开头，保证基线召回不劣化
- 变体解析：逐行清洗（编号/项目符号/包裹引号）+ 规范化去重 + 数量封顶；与原文重复的变体丢弃；LLM 前言废话被容忍为无害变体（检索无果即无贡献）
- `RAGAgent.execute` 多段式升级：**查询转换 → 多变体逐路召回 → RRF 融合去重 → 两阶段重排 → 上下文/答案**；单变体（未启用/短查询/无 LLM）时与旧路径完全一致
- 多变体融合复用既有 RRF 常数（`RAG_HYBRID_RRF_K`），片段去重身份优先 `chunk_id`，缺失时内容摘要
- **缓存键再区分管道**：`extra` 因子叠加 `rerank|transform`，转换开/关的结果互不复用；缓存命中不触发任何 LLM 阶段；结果带 `transformation.{enabled,variants,variant_count}` 元信息
- 配置：`RAG_TRANSFORM_ENABLED(True) / NUM_VARIANTS(3) / MIN_QUERY_LEN(8)`；`capabilities` 增 `query_transformation`，`/info` 增 `query_transformation` 块与组件说明
- `backend/tests/test_rag_query_transform.py`（40 项断言）：变体解析、转换器降级矩阵、Agent 端到端（变体补漏召回被单查询漏检的文档）、缓存命中不调 LLM、管道键隔离、无 LLM 保持旧行为

**为什么这么改**：
- 单条查询只能表达一个角度，容易漏掉同一问题的其它侧面；改写后的变体各自召回可显著提升召回率
- 在已有「召回放大 → RRF → LLM 重排」链路上，查询转换是对"漏检"最直接的补强手段

**解决了什么问题**：
- 覆盖单查询漏检：原问题召不到的片段，通过侧面变体召回并 RRF 融合进候选
- 成本/风险可控：一次 LLM 调用生成全部变体；每变体召回预算按 `stage1_k/变体数` 收缩，总候选量不变，仍由重排精排把关
- 与旧版零行为差异：未启用/短查询/无 LLM 一律走单查询原路径，既有 14 套件全部保持通过

---

## 2026-09-03 RAG 两阶段重排（LLM 点级打分）

**提交**：`e0c5a8a`（配套测试 `d6ba17d`）

**改了什么**：
- 新增 `backend/app/rag/reranker.py`：两阶段检索的第二段 —— 第一阶段按 `min(k×系数, 上限)` 放大召回，再用 LLM 对候选**批量点级打分**（单次调用，0.0~1.0）排序并截断回 top-k
- 分数解析采用**严格逐行规则**：去掉空行后必须恰好 N 行纯数字，否则降级原序 —— 候选正文里的数字不会被误抓成错位分数
- 健壮性：未配置 LLM / LLM 调用异常 / 输出不可解析 → 一律降级原序返回，重排绝不中断检索链路；同分稳定保持召回顺序
- `RAGAgent` 装配重排：`execute` 变为 召回放大 → 重排截断 → 构建上下文 三段式；未启用重排时 stage1_k=k，**与旧行为完全一致**
- **缓存键区分管道**：`SemanticCache.make_key` 增加 `extra` 因子，是否重排的答案互不复用；结果带 `rerank.{enabled,candidates,final,scores}` 元信息
- 配置：`RAG_RERANK_ENABLED / CANDIDATE_MULTIPLIER(3) / MAX_CANDIDATES(30) / MAX_DOC_CHARS(600)`；`capabilities` 增 `reranking`，`/info` 暴露重排配置
- `backend/tests/test_rag_reranker.py`（35 项断言）：分数解析、单元重排/降级/稳定排序、Agent 端到端（放大召回→重排截断→缓存区分→ingest 失效）

**为什么这么改**：
- 混合检索的 RRF 只是"排序信号融合"，对 query 语义的细粒度相关性判断仍是弱信号（向量/词法都基于表层匹配）
- 业界标准做法是召回后用更强的重排器精排 top-k 进入 LLM 上下文 —— 直接减少噪音进上下文、提升答案质量、并省 token

**解决了什么问题**：
- 上下文噪音：进 LLM 的片段由相关性打分重排把关，而不是只信召回阶段顺序
- 与既有体系一致：零新依赖、事件失效缓存继续生效、开关默认开但无 LLM 自动旁路、不改变未启用时的任何行为

---

## 2026-09-03 RAG 混合检索（BM25+向量+RRF）+ 语义缓存

**提交**：`82972b9`（配套测试 `3cad018`）

**改了什么**：
- 默认检索策略升级为 **hybrid**：BM25 词法路 + 向量语义路 双路召回 + RRF 融合（`search_type=hybrid`，API 校验同步放开）
- 新增 `backend/app/rag/lexical.py`：自实现 Okapi BM25（零新依赖），分词同时支持中英文（英文按词、中文按单字）；按用户分区的倒排统计懒构建 + 脏标记增量重建
- 新增 `backend/app/rag/fusion.py`：`reciprocal_rank_fusion` 纯函数融合
- `backend/app/rag/vector_store.py`：切块写入时注入 `chunk_id` 作为融合对齐锚点；`add_chunks/delete_document_chunks/delete_user_collection` 同步维护词法索引；新增 `hybrid_search`；**进程重启后首次 hybrid 查询自动从 Chroma 懒重建词法索引**（避免"词法只活在重启前"）
- 新增 `backend/app/rag/cache.py`：每用户作用域语义缓存（查询→答案），LRU 容量裁剪 + TTL 兜底 + **知识库变更事件失效**（ingest 新增 / 删除文档 / 清空自动清缓存），可关闭旁路
- `backend/app/core/config.py`：新增 `RAG_LEXICAL_ENABLED / RAG_HYBRID_FETCH_MULTIPLIER / RAG_HYBRID_RRF_K` 与 `RAG_CACHE_ENABLED / RAG_CACHE_TTL_SECONDS / RAG_CACHE_MAX_ENTRIES_PER_USER`
- 能力面如实暴露：`capabilities` 含 `hybrid_search`、`semantic_cache`，`search_strategies` 首项为 `hybrid`；`execute` 返回带 `cache.hit/key` 状态
- `backend/tests/test_rag_hybrid_cache.py`（43 项断言）：RRF 融合、中英文分词、BM25 相关性/生命周期/幂等/重建、缓存命中/TTL/LRU/每用户隔离/事件失效、Agent 端到端 hybrid+缓存

**为什么这么改**：
- 纯向量相似度对"关键词精准命中、专有名词、缩写"场景召回弱（语义向量不擅词级匹配）；混合检索是当前企业级 RAG 的召回标准做法
- 上一批已引入向量库持久化，但"每次查询都重算 LLM 答案"成本高、响应慢；同查询短时重复在企业对话中很常见

**解决了什么问题**：
- 召回质量：词法精确命中 + 语义泛化互补，RRF 融合无需调权
- 成本与延迟：重复查询直接命中缓存（省去向量检索 + LLM 生成）；知识库变更即时失效，答案不陈旧
- 词法索引生命周期闭环：与向量层同步维护 + 重启后懒重建，长期可依赖

---

## 2026-09-03 RAG 文档级持久化 + 多租户文档管理 API

**提交**：`a482e58`

**改了什么**：
- 新增模型 `RAGDocument`（表 `rag_documents`）与迁移 `0004`：文档级元数据持久化（user_id / filename / file_type / sha256 checksum / chunk_count / collection_name / status / error_message），`(user_id, checksum)` 唯一约束支撑幂等导入
- 新增 `backend/app/rag/repository.py`：文档记录仓储层（幂等查找 / 创建 / 列表 / 按用户删除），数据访问集中化，便于测试注入
- `backend/app/core/config.py`：新增 `RAG_*` 配置段（持久化目录、collection 前缀、切块/检索参数、Embedding 后端、上传大小与扩展名白名单、分页大小），消除硬编码
- `backend/app/api/rag.py` 重写：新增 `GET /documents`（分页列表）与 `DELETE /documents/{id}`（删除单文档：向量切块 + 元数据记录），`DELETE /clear` 与 `GET /stats` 改为按用户作用域；上传安全落盘（uuid 文件名防路径穿越）、扩展名白名单（415）、大小上限（413）、领域异常统一映射明确状态码
- `backend/tests/test_rag_enterprise.py`（27 项断言）：多租户隔离 / 幂等导入 / 文档管理作用域 / 强制 user_id / 跨租户检索内容级验证
- `backend/tests/test_migrations.py`：head 推进 `0004`，新增 `rag_documents` 表/索引/唯一约束断言
- `backend/examples/rag_demo.py`：适配多租户接口
- `run_tests.ps1`：纳入 `test_rag_enterprise.py`

**为什么这么改**：
- 旧实现中 `ingest` 结果只活在 Chroma 本地文件，无任何业务持久化——没有文档清单、无法做文档级删除/权限/审计，也无法幂等（同文档重复导入产生重复向量）
- 文档级管理是"能不能上生产"的红线之一：没有它，多租户隔离只解决了读取边界，管理面（删除/清空/审计）无从谈起

**解决了什么问题**：
- 文档级生命周期闭环：上传 → 列表 → 删除单文档 → 清空，全部按当前登录用户作用域生效
- 幂等导入：重复上传相同内容自动跳过（`skipped_duplicate`），杜绝知识库膨胀
- 错误语义透明：文件类型/大小/越权删除等场景返回明确 HTTP 状态码，不再"吞错返回 200"

---

## 2026-09-03 RAG 多租户隔离 + 异步安全向量库

**提交**：`3c3ac82`

**改了什么**：
- `backend/app/rag/vector_store.py` 重构：从"全局单 collection"改为**每用户独立 Chroma collection**（`rag_{user_id_hex}`，前缀见 `RAG_COLLECTION_PREFIX`）；全部同步 Chroma 调用经 `asyncio.to_thread` + 内部锁串行化；切块元数据携带 `user_id / doc_id / collection` 支持按文档整删；后端不可用统一抛 `RAGBackendError`
- `backend/app/rag/retriever.py`：所有检索方法强制携带 `user_id`，只查该用户自己的 collection（物理隔离）
- `backend/app/rag/rag_agent.py` 重构：`execute/ingest_documents/list_documents/delete_document/delete_all_documents` 全部以 `user_id` 为租户边界（缺失即拒绝）；新增 `configure_components` 支持测试注入；文档导入按 sha256 幂等并持久化元数据
- 新增 `backend/app/rag/exceptions.py`：领域异常族（UnsupportedFileTypeError / FileTooLargeError / EmptyDocumentError / DocumentNotFoundError / RAGBackendError）
- `backend/tests/test_rag_enterprise.py`（27 项断言）与本记录配套（多租户隔离与幂等部分）

**为什么这么改**：
- 旧实现所有用户共享同一个 `rag_default` collection——**任何用户都能检索到任何用户上传的文档**，检索层完全没有租户边界（安全事件级缺陷）
- 旧 `VectorStoreManager` 绑定单一 collection，同步 Chroma 调用直接阻塞 async 事件循环
- 旧 `RAGAgent.execute` 无 `user_id` 约束，"无租户上下文也可检索"是数据泄漏的根源

**解决了什么问题**：
- 多租户数据隔离从"检索时过滤"升级为"collection 物理隔离"，缺 filter 也不会串库
- 检索/写入全程不阻塞事件循环；并发操作被内部锁串行化，避免 Chroma 客户端竞态
- 未携带 `user_id` 的任何 RAG 操作被硬性拒绝，杜绝"无主检索"路径

---

## 2026-09-02 Celery worker 启动注册 memory 任务（接线修复）

**提交**：`791de3c`

**改了什么**：
- `backend/app/core/celery_app.py`：`Celery(...)` 构造函数新增 `include=["app.tasks.memory_tasks"]`，worker/beat 进程启动即导入注册 `memory.consolidate` 与 `memory.decay_memories`
- 新增 `backend/tests/test_celery_registration.py`（9 项断言）：模拟 worker 启动路径（`loader.import_default_modules()`）后任务已注册、beat_schedule 指向已注册任务且周期与 `MEMORY_DECAY_INTERVAL_SECONDS` 一致、decay 任务可靠性属性（`acks_late` / retry）不回退
- `run_tests.ps1`：纳入新套件

**为什么这么改**：
- `autodiscover_tasks(["app.tasks"])` 实际查找的是 `app.tasks.tasks` 模块（不存在）；任务真正定义在 `app/tasks/memory_tasks.py`
- 此前该模块仅被 API 进程内的 `manager.py` 延迟 import（运行期触发），**worker 进程冷启动时从未 import 过它**——`memory.*` 任务在 worker 端未注册，beat 每 6 小时投递的 `memory.decay_memories` 会因 "Received unregistered task" 永远无法执行

**解决了什么问题**：
- 定时衰减/过期归档从"代码正确但无人调度执行"变为真正可运行：beat 投递 → worker 已注册 → 执行
- 冷启动场景下 consolidation 任务同样受惠（不再依赖 API 进程恰好先触发过 import）
- 回归测试用 worker 视角固化接线，防止未来 autodiscover/include 配置回退

---

## 2026-09-02 后台批量扫描索引（迁移 0003）

**提交**：`35a5c5b`

**改了什么**：
- `backend/app/models/memory_entry.py`：`__table_args__` 新增复合索引 `ix_memory_archived_strength_updated (archived_at, strength, updated_at)`
- 新增迁移 `backend/alembic/versions/0003_add_memory_decay_scan_index.py`
- `backend/tests/test_migrations.py`：head 断言推进到 `0003`，新增后台批量扫描索引存在性检查

**为什么这么改**：
- 后台批量任务（定时衰减 + 合规过期归档）的扫描查询是 `WHERE archived_at IS NULL AND strength IS NOT NULL`，**不带 user_id 前缀**
- 现有索引全部以 `user_id` 开头（检索路径专用），批处理无法命中，只能全表扫描；归档条目随业务增长后成本线性上升

**解决了什么问题**：
- 批处理只需扫描活跃子集（`archived_at IS NULL` 前缀定位），归档数据越多收益越明显
- 与方向"批量衰减任务归档合规过期记忆"（`5d7fe2c`）配套：过期清理真正落库后，索引保证清扫本身高效

---

## 2026-09-02 批量衰减任务归档合规过期记忆

**提交**：`5d7fe2c`

**改了什么**：
- `backend/app/memory/decay.py`：`decay_memories` 在应用时间衰减前先判断 `expires_at <= now`——到期的合规记忆**直接软归档**（不衰减、不等待强度降阈值）
- 返回值新增 `expired` 字段（因过期而归档的条目数），`decayed` 改为只统计真正做了时间衰减的条目，`archived` 语义不变（低强度 + 过期归档总数）
- 归档条目统一进入向量索引清理（复用既有 `archived_ids` 路径）
- 新增 `backend/tests/test_memory_decay_expiry.py`（5 项断言：到期归档 / 未到期保留 / 无约束保留 / `expired` 计数 / 过期条目跳过衰减）
- `run_tests.ps1`：纳入新套件

**为什么这么改**：
- `expires_at`（合规保留策略）此前只在检索层被过滤——到期条目只是"不可见"，**从未有任何任务真正落库归档**，过期数据在表中永久留存，随会话与事件累积只增不减
- 原衰减只归档"强度低于阈值"的条目；一条 `strength=0.9` 的过期记忆永远不会因衰减被归档，合规到期形同虚设

**解决了什么问题**：
- 合规保留策略闭环：到期（`expires_at`）→ 批量衰减任务（Celery beat 周期路径）→ 软归档（`archived_at`，保留审计轨迹）→ 检索/向量索引全部排除
- 过期条目不再做无意义的时间衰减与 `updated_at` 扰动
- 回归测试固化该行为（含 `expired` 计数与既有多 key 返回结构向后兼容）

---

## 2026-09-02 记忆检索归档过滤复合索引（迁移 0002）

**提交**：`08e89bb`

**改了什么**：
- `backend/alembic/versions/0002_add_memory_archive_index.py`：新增迁移——在 `memory_entries` 建复合索引 `(user_id, archived_at, strength, updated_at)`
- `backend/app/models/memory_entry.py`：模型同步增加该索引（create_all 兜底路径一致）
- `backend/tests/test_migrations.py`：断言迁移版本到 `0002` 且新索引存在

**为什么这么改**：
- 检索主查询是 `WHERE user_id = ? AND archived_at IS NULL ORDER BY strength DESC, updated_at DESC LIMIT n`；旧复合索引 `(user_id, strength, updated_at)` **无法过滤归档行**——用户条目增长（event 累积、历史归档）后每次检索都要扫描含归档行的全量集合，随归档量增长性能劣化

**解决了什么问题**：
- 活跃条目检索路径（用户 + 未归档 + 强度/时间排序）被索引完整覆盖，检索成本只与活跃条目数相关
- 已有库通过 `alembic upgrade head` 自动应用；全新库 0001→0002 顺序建表
- 迁移回归测试固化（版本号 + 索引存在性）

---

## 2026-09-02 删除会话时归档其记忆条目（孤儿数据防护）

**提交**：`6ab3f11`

**改了什么**：
- `backend/app/memory/persistence.py`：`delete_conversation` 在删除会话及其消息的同时，**软归档该会话产生的全部记忆条目**（`archived_at` 标记，保留审计轨迹）
- `backend/tests/test_e2e_memory.py`：新增 2 项检查（删除会话接口 + 删除后该会话记忆不再被检索）

**为什么这么改**：
- `GET /entries` 与语义检索均按 `user_id` 过滤，不区分会话；旧实现删除会话只删 `conversations` 与 `messages`，**该会话沉淀的 MemoryEntry 全部残留**
- 用户删除会话后，其记忆（如咖啡偏好、个人信息）依然被跨会话检索召回 → 隐私与一致性问题

**解决了什么问题**：
- 删除会话后该会话的记忆条目不再参与任何检索（已归档，检索过滤 `archived_at IS NULL`）
- 保留审计轨迹（软删除，与 `DELETE /entries` 行为一致）
- `manager.clear()` 清空记忆的语义随之完整（会话级清理包含其记忆条目）

---

## 2026-09-02 e2e 补充：长期摘要跨请求可见性验证

**提交**：`b96dcce`

**改了什么**：
- `backend/tests/test_e2e_memory.py`：新增 2 项检查
  - `GET /api/v1/memory/{sid}/context` 端点可用性
  - `long_term_summary` 跨请求可见且含会话消息内容（咖啡/吉他）

**为什么这么改**：
- 审计摘要链路时确认 `initialize → load_summary(metadata_["summary"]) → long_term.set_summary` 与 `consolidate → metadata_["summary"]` 是同一条路径，闭环完整
- 但该闭环此前**无任何测试固化**：若未来 `load_summary` 读错字段或 consolidate 不再写回，摘要跨请求丢失将静默发生

**解决了什么问题**：
- 用真实 HTTP 链路固化「consolidation 摘要落库 → 跨请求加载可见」的契约，防止回归
- 补上 `GET /context` 端点本身的 e2e 覆盖

---

## 2026-09-02 测试基建：强制退出码与一键回归

**提交**：`a8763ee`

**改了什么**：
- `backend/tests/test_memory_improvement.py`：
  - `main()` 增加**强制退出码**：任何一项失败（`test_no_semantic_loss` 返回 False / 窗口测试异常）时 `sys.exit(1)`
  - `test_window_overflow_detection` 返回 `bool`，纳入结果
- 新增 `run_tests.ps1`：一键运行全部 9 个记忆测试套件，任一失败整体退出码非 0（可用于 CI）

**为什么这么改**：
- `test_memory_improvement` 是打印式测试：语义检查失败时只打印提示，**进程仍以 0 退出**——本次 mock 摘要 bug（关键信息全部丢失）就是在这种静默下长期未被发现的
- 回归验证此前依赖手工逐条命令，无法保证全量覆盖与失败可见性

**解决了什么问题**：
- 任何测试失败都会产生非 0 退出码，CI/回归脚本可可靠捕获
- `run_tests.ps1` 一键全量回归，缺 venv 时给出明确提示

---

## 2026-09-02 启发式记忆提取：过滤一次性指令与提问

**提交**：`d5a6bf8`

**改了什么**：
- `backend/app/memory/extractor.py`：
  - 新增 `_HEURISTIC_COMMAND_MARKERS`（帮我/请你/能否/请解释/帮我写…）与 `_HEURISTIC_QUESTION_PREFIX`（为什么/怎么/如何/请/介绍/解释…）两类信号
  - 启发式提取前先判定 `_is_command_or_question`，命中则跳过该消息
- `backend/tests/test_memory_extractor_heuristic.py`：新增 9 项提取质量回归测试

**为什么这么改**：
- 无 LLM 时启发式提取只按关键词匹配，**指令与问题也会命中偏好/事实关键词**：
  - 「请使用 Redis 做缓存」→ 命中 fact（使用/redis）→ 被当长期事实
  - 「为什么 Python 比 Java 快」→ 命中 fact（python）→ 被当事实
  - 「我希望你能帮我部署项目」→ 命中 preference（希望）→ 被当用户偏好
- 这些一次性指令/提问沉淀后长期污染记忆库，检索时反复召回无关内容

**解决了什么问题**：
- 指令与提问不再误提取为长期记忆，记忆库只沉淀真实偏好/事实
- 真实偏好（「我喜欢用空格缩进」）与事实（「项目采用 FastAPI」）提取不受影响
- LLM 调用失败降级到启发式路径时同样受益

---

## 2026-09-02 mock 模式长期记忆摘要质量（无 LLM 降级路径）

**提交**：`9ee9947`

**改了什么**：
- `backend/app/memory/long_term.py`：无 LLM 的 mock 模式摘要从「占位符」改为**增量拼接真实消息内容**（`role: content` 换行累积），受 `max_summary_length` 限制截断
- `backend/tests/test_memory_summary_mock.py`：新增 8 项摘要质量回归测试
- 修复后 `test_memory_improvement.py::test_no_semantic_loss` 的 4 项语义断言（学习Python / 数据类型 / 列表元组 / 学生管理）全部由「丢失」变为「已保留」

**为什么这么改**：
- 旧实现每次 `add_message` 把摘要覆盖为 `"[Mock Summary] Recent N messages"`：
  - 摘要**不含任何消息内容**，长期记忆对检索/展示零信息量
  - `set_summary(旧摘要)` 后调用 `add_message` 会把旧摘要**覆盖**，增量累积失效——每次整合只反映最近一个批次，历史脉络全部丢失
- `test_no_semantic_loss`（既有测试）的语义完整性断言此前实际全部失败，但因打印式测试无 exit code 校验而未被发现

**解决了什么问题**：
- 无 LLM 环境（本地/生产降级）下长期记忆摘要保留真实消息内容
- 增量整合（旧摘要 + 新批次）正确累积，不丢历史
- 摘要长度有界（`max_summary_length` 截断）

---

## 2026-09-02 短期记忆窗口恢复幂等（跨请求/跨 worker）

**提交**：`a694c4c`

**改了什么**：
- `backend/app/memory/manager.py`：`initialize` 恢复短期记忆窗口前先检查 store 是否已有数据；**仅当 store 为空时**才从 DB 重建窗口
- `backend/tests/test_memory_short_term_restore.py`：新增 3 项恢复幂等回归测试

**为什么这么改**：
- `initialize` 每请求执行，旧实现无条件把 DB 最近窗口**追加**进 store
- 进程内 store 每次请求新建（空），追加合理；但 **Redis store 跨请求/跨 worker 持久**——每请求重复追加相同历史，窗口无限重复累积、顺序错乱（实测窗口变 `[hi, hello, hi, hello]`，真实场景下旧消息反复入窗、新消息排不到前面，并伴随多 worker 各自恢复加剧膨胀）

**解决了什么问题**：
- 持久化 store（Redis 生产模式）下短期记忆窗口不再重复累积、顺序保持正确
- 多 uvicorn worker 场景下窗口内容一致（同一 DB 权威快照），避免各 worker 各自膨胀
- 进程内 store（空）仍按原逻辑从 DB 恢复，本地/测试行为不变

---

## 2026-09-02 检索质量改进（词法匹配增强）

**提交**：`d382810`

**改了什么**：
1. `backend/app/memory/retriever.py`：
   - 中文分词从「贪婪两字片段」改为「滑动窗口 bigram」（步长 1）
   - 新增中英文停用词过滤
   - `entity` 字段参与相关性匹配（content 与 entity 取较高者）
2. `backend/tests/test_memory_retrieval_quality.py`：新增检索质量回归测试

**为什么这么改**：
- 原分词 `re.findall(r"[\u4e00-\u9fa5]{2}")` 只取**不重叠**的二字片段，导致 bigram 错位：内容「用户喜欢喝咖啡」只能切出「喝咖」而丢「咖啡」，查询「咖啡」时词法相关度为 0，相关记忆无法被召回
- 查询句中的停用词（the/of/please/这个/想要…）会放大分母，稀释真正关键词的命中率
- 记忆条目的 `entity`（实体字段）此前不参与匹配，按实体提问（如「peter 的偏好」）时即使实体字段命中也无分

**解决了什么问题**：
- 中文错位 bigram 的漏召回（语义相同但写法相邻的记忆）
- 长查询/口语化查询时关键词被停用词稀释导致的排序失真
- 实体维度查询的命中率

---

## 2026-09-02 并发安全整合触发与幂等写入（fbeb1dc）

**提交**：`fbeb1dc`

**改了什么**：
1. `backend/app/memory/persistence.py`：
   - `save_pending_consolidation` 从「整体覆盖」改为**按 (role, content) 去重合并**
   - 新增**模块级共享锁池**（每 session 一个 `asyncio.Lock`，跨实例共享）
   - 新增 `claim_pending_consolidation()`：**原子领取并清空**待整合批次
2. `backend/app/memory/manager.py`：
   - 触发 consolidation 时改为「先合并落库 → 原子领取 → 执行」；失败则**批次回队**
   - `_trigger_consolidation` / `_consolidate_inline` 返回成功状态
3. `backend/app/memory/consolidation.py`：`save_event_entries` 跳过已存在的相同内容条目（幂等）
4. `backend/tests/test_memory_concurrency.py`：新增 6 项并发/幂等回归测试

**为什么这么改**：
- `MemoryPersistence` 每请求重建，旧实现把整个批次从旧快照**覆盖**写回，两个并发请求互相覆盖 → 消息静默丢失（lost update）
- 并发请求各自基于旧快照判断达阈值 → 同一批消息被整合两次 → 重复记忆条目
- 触发前就清空批次，consolidation 失败则消息直接消失

**解决了什么问题**：
- 并发请求下待整合批次不再丢消息（丢失的 pending 会导致记忆永远不沉淀）
- 同一批消息只会被一个请求领取并整合，杜绝重复条目
- 整合失败时批次回队，后续请求自动重试，消息不丢
- 跨进程 / Celery 重试等场景下重复执行也不会产生重复 event 条目

---

## 2026-09-01 跨请求 consolidation 与 e2e 覆盖（16fae24）

**提交**：`16fae24`

**改了什么**：
1. `backend/app/memory/manager.py`：consolidation 批次从「仅进程内存」改为**持久化在会话 `metadata_`**，请求结束前写回、重建时恢复
2. `backend/app/memory/persistence.py`：新增 `load_pending_consolidation` / `save_pending_consolidation`
3. `backend/requirements.txt`：补齐依赖
4. `backend/tests/test_e2e_memory.py`：新增真实 HTTP 端到端回归测试（16 项）

**为什么这么改**：
- 每请求新建 `MemoryManager`，待整合批次只在进程内存中累积；同一会话的跨请求消息永远无法凑满批次阈值 → 记忆在真实 HTTP 场景下从未沉淀
- 曾遇到的根因：bcrypt 版本导致环境启动失败、chromadb 缺失导致向量库初始化失败、SQLAlchemy JSON 列 in-place 修改不触发 dirty → 这些一并修复并纳入 e2e 覆盖

**解决了什么问题**：
- 真实部署（每个请求独立 manager 实例）下，跨请求消息可累积到批次阈值并正常沉淀记忆
- 启动/环境类问题通过 e2e 测试固化，防止回归

---

## 2026-08 数据库方言兼容与迁移优先启动（2356137）

**提交**：`2356137`

**改了什么**：
- 模型列类型改为方言兼容写法（SQLite/PostgreSQL/MySQL 均可建表）
- 启动流程改为「迁移优先」：自动执行 Alembic 迁移后再起服务
- 新增 `backend/tests/test_migrations.py` 迁移回归测试

**为什么这么改**：原模型类型（如 `String` 长度、`DateTime` 时区等）在非 SQLite 方言下无法建表，且启动时依赖手工执行迁移。

**解决了什么问题**：任意支持的数据库方言可直接建表启动，迁移状态可回归验证。

---

## 2026-08 检索规模保护与 event 上限（690a036）

**提交**：`690a036`

**改了什么**：
- `backend/app/memory/retriever.py`：候选集增加规模上限（按强度取前 N，向量命中额外召回）
- `backend/app/memory/consolidation.py`：单会话 event 记忆保留上限（超出归档）
- `backend/app/core/config.py`：新增 `MEMORY_RETRIEVAL_CANDIDATE_LIMIT` / `MEMORY_EVENT_MAX_PER_SESSION`

**为什么这么改**：大量历史记忆时全量打分性能不可控；event 溯源条目无限增长导致检索污染与存储膨胀。

**解决了什么问题**：检索延迟有界，event 条目膨胀可控。

---

## 2026-08 基于 ChromaDB 的向量语义检索（1281418）

**提交**：`1281418`

**改了什么**：
- 新增 `backend/app/memory/vector_store.py`：ChromaDB 记忆向量索引（懒加载、失败自动降级、用户隔离）
- `backend/app/memory/retriever.py`：混合打分加入向量相似度分量（0.3），向量不可用时自动降级

**为什么这么改**：纯词法相关度无法覆盖同义改写/语义近似，需要向量语义检索补齐召回。

**解决了什么问题**：语义相关的记忆（即使无关键词重叠）也能被召回，检索质量显著提升；无向量基础设施时优雅降级，不影响主流程。
