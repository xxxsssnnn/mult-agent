# 长短期记忆系统使用指南（v2.0）

## 📚 概述

本系统实现了完整的长短期记忆机制，使多Agent平台能够：
- **记住最近的对话**（短期记忆）
- **总结历史对话**（长期记忆）
- **持久化存储**到数据库
- **跨会话恢复**上下文
- ✅ **零语义丢失** - 所有信息都被妥善保存（v2.0改进）

## 🚀 快速开始

### 1. 基本使用

```python
from app.memory import MemoryManager

# 创建记忆管理器
memory = MemoryManager(session_id="session-123", user_id="user-456")
await memory.initialize()

# 添加消息
await memory.add_message("user", "你好，我想学习Python")
await memory.add_message("assistant", "很好！Python是一门适合初学者的语言")

# 获取完整上下文（短期+长期）
context = await memory.get_context()
print(context)

# 保存到数据库
await memory.save_to_db()
```

### 2. Agent集成

```python
from app.agents.coder import CoderAgent

# 创建Agent
agent = CoderAgent(agent_id=uuid4(), name="MyCoder")
await agent.initialize()

# 设置记忆（按会话新建 MemoryManager）
await agent.set_memory(session_id="session-123")

# 执行任务（自动使用记忆）
result = await agent.execute_with_memory({
    "requirement": "继续刚才的代码优化",
    "language": "python"
})
```

`execute_with_memory` 自动适配各 Agent 的输入输出键并写入会话：

- **用户消息提取**：`user_input` / `requirement` / `question` / `query`；仅有 `code` 时自动加"请审查以下代码"前缀
- **助手消息提取**：`explanation` / `review` / `summary` / `output` / `answer`；仅有 `code` 时截断保存
- **记忆上下文注入**：执行前 `get_context()` 的结果（截断至 4000 字符）以 `memory_context` 键注入任务输入，Coder/Reviewer 组装 prompt 时自动带"历史会话记忆参考"段
- 使用**深拷贝**，不污染调用方传入的 dict

多个 Agent 共享同一会话记忆时，请用 `attach_memory`（不重复初始化、复用同一管理器）：

```python
manager = await agent_a.set_memory(session_id="shared-session")  # 或自行构造
await agent_b.attach_memory(manager)   # 两 Agent 写入/读取同一会话
```

### 3. Workflow 会话记忆（多 Agent 贯穿）

CodeReview / TaskPlanner 工作流支持**会话级记忆**：工作流内所有 Agent（含动态创建的子
Agent）共享同一个 MemoryManager，每一轮生成/审查都写入同一会话，后续轮次可参考历史。

两种开启方式（不开启时行为与旧版完全一致）：

```python
from app.workflows.code_review import CodeReviewWorkflow

# 方式一：构造注入（共享/测试注入同一个已构造的 manager）
workflow = CodeReviewWorkflow(coder_agent=..., reviewer_agent=...,
                              memory_manager=memory_manager)

# 方式二：execute 时通过 initial_state 配置（由工作流自动创建）
result = await workflow.execute({
    "requirement": "重构订单模块",
    "memory": {"session_id": "workflow-session-001",
               "user_id": "user-123",
               "db_session": db},   # 传 db 会话则持久化到数据库
})
# 结果 metadata.memory = {"enabled": true, "session_id": "workflow-session-001"}
```

HTTP 层开启（`POST /api/v1/workflows/code-review` 与 `/task-planner`）：

```json
{
  "requirement": "重构订单模块",
  "enable_memory": true,
  "session_id": "wf-001"
}
```

不传 `session_id` 时服务端自动生成并随结果 `metadata.memory.session_id` 返回；
下一次请求带上同一 `session_id` 即可延续会话记忆（自动做长短期记忆合并与摘要）。

## 📖 API端点

所有记忆相关的API都位于 `/api/v1/memory/` 路径下。

### 创建记忆会话

```http
POST /api/v1/memory/session
Content-Type: application/json
Authorization: Bearer <token>

{
  "title": "我的编程会话"
}
```

**响应:**
```json
{
  "success": true,
  "session_id": "abc-123-def",
  "message": "Memory session initialized successfully"
}
```

### 添加消息到记忆

```http
POST /api/v1/memory/{session_id}/message
Content-Type: application/json
Authorization: Bearer <token>

{
  "role": "user",
  "content": "帮我写一个排序算法",
  "metadata": {
    "language": "python"
  }
}
```

### 获取记忆上下文

```http
GET /api/v1/memory/{session_id}/context
Authorization: Bearer <token>
```

**响应:**
```json
{
  "success": true,
  "session_id": "abc-123-def",
  "context": "=== Conversation Summary ===\n用户请求编写排序算法...\n\n=== Recent Messages ===\nUser: 帮我写一个排序算法\nAssistant: 好的，这是一个快速排序实现...",
  "short_term_messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "long_term_summary": "用户请求编写排序算法...",
  "stats": {
    "short_term_message_count": 4,
    "long_term_has_summary": true
  }
}
```

### 获取记忆统计

```http
GET /api/v1/memory/{session_id}/stats
Authorization: Bearer <token>
```

### 删除记忆会话

```http
DELETE /api/v1/memory/{session_id}
Authorization: Bearer <token>
```

### 演示记忆功能

```http
POST /api/v1/memory/demo
Content-Type: application/json
Authorization: Bearer <token>

{
  "messages": [
    {"role": "user", "content": "第一条消息"},
    {"role": "assistant", "content": "第一条回复"},
    {"role": "user", "content": "第二条消息"}
  ]
}
```

## ️ 配置参数

在 `.env` 文件中配置记忆行为：

```env
# 短期记忆：保留最近N轮对话
MEMORY_SHORT_TERM_WINDOW_SIZE=5

# 长期记忆：每N条消息生成一次摘要
MEMORY_LONG_TERM_SUMMARY_INTERVAL=10

# 长期记忆：摘要最大长度
MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH=500

# 是否启用持久化
MEMORY_PERSISTENCE_ENABLED=True
```

##  运行演示

```bash
cd backend
python examples/memory_demo.py
```

演示内容包括：
1. ✅ 基本记忆功能
2. ✅ 带数据库持久化的记忆
3. ✅ 短期记忆窗口机制
4. ✅ Agent集成记忆

## 📊 架构说明

### 组件结构

```
app/memory/
├── __init__.py          # 模块导出
├── manager.py           # MemoryManager - 统一接口
├── short_term.py        # ShortTermMemory - 短期记忆
├── long_term.py         # LongTermMemory - 长期记忆
└── persistence.py       # MemoryPersistence - 持久化层
```

### 工作流程

```
用户输入 → Agent.execute_with_memory()
    ↓
MemoryManager.add_message()
    ↓
┌─────────────┬──────────────┐
│ 短期记忆     │ 长期记忆      │
│ (最近5轮)   │ (摘要+DB)    │
└─────────────┴──────────────┘
    ↓
MemoryPersistence.save_to_db()
    ↓
返回结果
```

## 💡 最佳实践

### 1. 会话管理

为每个独立的对话场景创建单独的session_id：

```python
# 不好的做法：所有对话共用一个session
session_id = "global-session"

# 好的做法：每次新对话创建新session
session_id = str(uuid4())  # 或从前端传入
```

### 2. 记忆清理

定期清理不需要的会话以节省存储空间：

```python
# 删除旧会话
await memory.clear()

# 或在API中调用
DELETE /api/v1/memory/{old_session_id}
```

### 3. 上下文长度控制

短期记忆窗口大小应根据LLM的token限制调整：

```python
# GPT-4可以处理更多上下文，可以增加窗口
MEMORY_SHORT_TERM_WINDOW_SIZE=10

# GPT-3.5 token较少，减小窗口
MEMORY_SHORT_TERM_WINDOW_SIZE=3
```

### 4. 错误处理

始终检查记忆操作的结果：

```python
try:
    await memory.add_message("user", content)
except Exception as e:
    logger.error(f"Failed to add message: {e}")
    # 降级处理：不使用记忆继续执行
```

### 5. 理解滑动窗口机制（v2.0重要）

**关键概念**：短期记忆的滑动窗口不会丢弃消息！

```python
# 当添加的消息超出窗口大小时：
# ❌ 不是直接丢弃
# ✅ 而是转移到长期记忆进行摘要

# 示例：窗口大小为3轮，添加第4轮时
# - 短期记忆：保留第2-4轮（最近3轮）
# - 长期记忆：接收第1轮的摘要
# - 数据库：保存所有4轮的完整记录

# 结果：零语义丢失 ✅
```

##  测试

### 单元测试

```bash
cd backend
pytest tests/test_memory.py -v
```

### API测试

使用Swagger UI测试记忆API：
1. 启动后端服务
2. 访问 http://localhost:8001/docs
3. 找到 "memory" 标签下的端点
4. 点击 "Try it out" 进行测试

## ️ 故障排查

### 问题1: 记忆没有保存

**原因**: 未提供db_session参数

**解决**:
```python
# 错误
memory = MemoryManager(session_id="xxx")

# 正确
memory = MemoryManager(session_id="xxx", db_session=db)
```

### 问题2: 长期记忆摘要为空

**原因**: 未配置OpenAI API Key

**解决**:
- 设置 `OPENAI_API_KEY` 环境变量
- 或使用Mock模式（自动启用简单摘要）

### 问题3: 短期记忆包含太多消息

**原因**: MEMORY_SHORT_TERM_WINDOW_SIZE设置过大

**解决**: 减小窗口大小
```env
MEMORY_SHORT_TERM_WINDOW_SIZE=3
```

### 问题4: 担心语义丢失（v2.0已解决）

**旧版本的担忧**: 滑动窗口会丢弃旧消息

**v2.0的解决方案**: 
- ✅ 被移出的消息不会丢弃，而是转移到长期记忆
- ✅ LLM基于完整上下文生成摘要
- ✅ 所有消息都保存到数据库
- ✅ 运行测试验证：`python tests/test_memory_improvement.py`

**验证方法**:
```python
# 添加大量消息
for i in range(20):
    await memory.add_message("user", f"Message {i}")

# 检查早期信息是否保留在摘要中
summary = await memory.get_long_term_summary()
print(summary)  # 应该包含早期对话的关键信息

# 检查统计信息
stats = await memory.get_stats()
print(f"Short-term: {stats['short_term_message_count']}")  # 应该在窗口范围内
print(f"Has summary: {stats['long_term_has_summary']}")   # 应该为True
```

## 📝 示例代码

完整的示例请参考：
- [memory_demo.py](../examples/memory_demo.py) - 交互式演示
- [memory.py](../app/api/memory.py) - API端点实现
- [manager.py](../app/memory/manager.py) - 核心管理器

##  下一步

- [ ] 添加向量记忆支持
- [ ] 实现知识图谱记忆
- [ ] 添加记忆检索API
- [ ] 支持个性化记忆空间

---

**版本**: v2.0（滑动窗口改进版）  
**最后更新**: 2026-08-18

### 主要变更（v2.0）
- ✅ **零语义丢失**：短期记忆不再丢弃旧消息，而是转移到长期记忆
- ✅ **智能摘要**：被移出的消息触发LLM生成/更新摘要
- ✅ **完整保存**：所有消息都持久化到数据库
- ✅ **测试覆盖**：新增test_memory_improvement.py验证改进效果
- ✅ **详细文档**：MEMORY_IMPROVEMENT.md说明技术方案
