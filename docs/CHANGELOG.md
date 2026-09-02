# 修改记录（CHANGELOG）

> 本文件按时间倒序记录每一次代码修改：**改了什么**、**为什么这么改**、**解决了什么问题**。
> 每条记录对应一次 git 提交，便于回溯与审计。

---

## 2026-09-02 检索质量改进（词法匹配增强）

**提交**：待提交（本方向）

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
