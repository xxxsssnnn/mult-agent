# Workflow 答案语义检索增强（执行档案向量索引）

> 版本：v1.0（2026-09-03）
> 状态：实现完成，随「归档自动索引 + `/workflows/answers/search`」一起生效

## 1. 是什么

workflow（代码审查 / 任务规划）每次执行结束都会自动归档到 `tasks` 表，但之前
只能按标题翻列表。本次增强：**归档落库成功后，自动把这次“执行答案”向量化**——
父任务复盘 + 每个子任务标题/状态/结果，写入独立 Chroma collection；之后可以用
**自然语言按用户隔离**地语义检索，例如：

- 「上次代码审查结论是什么？」
- 「上个月任务规划失败的原因」
- 「订单系统的数据库是怎么设计的」（命中某次子任务执行结果）

## 2. 如何工作

```
workflow 执行
  └─ _archive_run(..., user_id)      # 已在执行的归档链路
       ├─ tasks 表落库（父复盘 + 子任务）
       └─ 落库成功后尽力而为
            └─ WorkflowAnswerStore.index_run_async()
                 ├─ build_run_documents：1 父 + N 子 → 文本
                 ├─ EmbeddingService（复用 RAG 配置）embed
                 └─ Chroma upsert（collection=wf_answers, metadata 含 user_id）
                            ▲
GET /workflows/answers/search └─ 按 user_id + 可选 label/status 过滤的向量检索
```

要点：

- **租户隔离**：所有用户共享 collection，靠 `metadata.user_id` 过滤（`$and` 组合）。
  归档时只有执行发起人（`current_user`）会产生索引，检索也只看自己名下的。
- **文本设计**：父文档含 工作流/标题/目标/结果 + 复盘全文；子文档含 子任务标题/
  状态（中文化）/执行结果。中文状态（成功/失败/待执行…）是为了让自然语言更易命中。
- **独立落盘**：默认 `./wf_answer_db`，与 RAG 的 `chroma_db` 分开，避免同一目录
  多 `PersistentClient` 的 sqlite 锁冲突。
- **尽力而为**：索引失败只记日志，绝不阻断归档主流程；embedding/Chroma 不可用时
  检索返回 `available=false`，上层可优雅降级。

## 3. 配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `WORKFLOW_ANSWER_INDEX_ENABLED` | `True` | 总开关 |
| `WORKFLOW_ANSWER_PERSIST_DIRECTORY` | `./wf_answer_db` | Chroma 持久目录 |
| `WORKFLOW_ANSWER_COLLECTION` | `wf_answers` | collection 名 |

Embedding 模型沿用 RAG：`RAG_EMBEDDING_MODEL_TYPE` / `RAG_EMBEDDING_MODEL_NAME`。

> 注意：若 RAG 与 answers 用的是同一 embedding 配置则无需迁移；若中途更换
> embedding 模型/维度，请清空 `wf_answer_db` 目录重新建立索引。

## 4. 接口

### `GET /api/v1/workflows/answers/search`（需登录）

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | str | 是 | 自然语言问题（1~500 字符） |
| `limit` | int | 否 | 返回条数，默认 5，范围 1~20 |
| `workflow_label` | str | 否 | 工作流筛选：`任务规划` / `代码审查` |
| `status` | str | 否 | 状态筛选：`completed` / `failed` 等 |

响应：

```jsonc
{
  "success": true,
  "available": true,          // false = 语义索引未启用/后端不可用（非错误）
  "query": "上次代码审查结论",
  "count": 1,
  "results": [
    {
      "task_id": "wf-xxxxxxxxxxxx",
      "parent_task_id": null,           // 子任务条目此处为父归档 id
      "workflow_label": "代码审查",
      "title": "[代码审查] 实现用户登录",
      "status": "completed",
      "is_subtask": false,
      "content": "【代码审查 · 执行档案】\n标题：…",   // 命中的可读文本
      "similarity": 0.89,
      "created_at": "2026-09-03T10:00:00+00:00"
    }
  ]
}
```

curl 示例：

```bash
curl -s -X GET "http://127.0.0.1:8001/api/v1/workflows/answers/search?query=%E4%B8%8A%E6%AC%A1%E4%BB%A3%E7%A0%81%E5%AE%A1%E6%9F%A5%E7%BB%93%E8%AE%BA&limit=3&workflow_label=%E4%BB%A3%E7%A0%81%E5%AE%A1%E6%9F%A5" \
  -H "Authorization: Bearer <token>"
```

## 5. 运维与清理

- 索引健康/计数：Python 侧可调用
  `from app.workflows.answer_store import workflow_answer_store; workflow_answer_store.health()`
- 某次归档的索引删除：`WorkflowAnswerStore.remove_task(user_id, task_id)`（父+子一并删）
- 清空全部：直接删除 `WORKFLOW_ANSWER_PERSIST_DIRECTORY` 目录（服务重启后自动重建）

## 6. 回归测试

```bash
python tests/test_workflow_answer_search.py   # 40 项断言，离线可跑
```

覆盖：文档展开、索引/检索/跨用户隔离/过滤/删除/幂等、embedding 失败降级、
归档自动索引（user_id 透传）、检索端点鉴权与参数透传、后端不可用降级。
