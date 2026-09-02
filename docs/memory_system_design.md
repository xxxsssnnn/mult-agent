# 长短期记忆系统设计文档

## 1. 概述

### 1.1 背景
在多Agent协作系统中，记忆机制是实现智能对话和任务连续性的核心组件。本系统需要支持：
- **短期记忆**：保留最近N轮对话的完整上下文，用于即时响应
- **长期记忆**：对历史对话进行摘要和关键信息提取，用于长期知识积累
- **持久化存储**：将对话历史保存到数据库，支持跨会话恢复

### 1.2 目标
- 实现基于LangChain Memory模块的记忆管理机制
- 支持短期窗口记忆和长期摘要记忆的混合使用
- 提供统一的记忆接口供所有Agent使用
- 实现记忆的自动保存和加载

## 2. 架构设计

### 2.1 整体架构

```
─────────────────────────────────────────────────┐
│                  Agent Layer                     │
│  ┌──────────────┐  ┌──────────────┐              │
│  │ CoderAgent   │  │ReviewerAgent │  ...         │
│  └──────┬───────┘  └──────┬───────┘              │
│         │                 │                       │
│         └────────────────┘                       │
│                  ▼                                │
│  ┌─────────────────────────────────────────────┐ │
│  │         Memory Manager (统一接口)            │ │
│  │  ┌────────────────  ┌──────────────────┐   │ │
│  │  │ Short-term Mem │  │ Long-term Mem    │   │ │
│  │  │ (Buffer Window)│  │ (Summary + DB)   │   │ │
│  │  └────────┬───────┘  └────────┬─────────┘   │ │
│  └───────────┼──────────────────┼──────────────┘ │
│              │                  │                │
│              ▼                  ▼                │
│  ┌────────────────┐  ┌──────────────────┐       │
│  │ In-Memory Cache│  │ PostgreSQL/SQLite│       │
│  │ (Recent msgs)  │  │ (Full history)   │       │
│  └────────────────┘  └──────────────────┘       │
└─────────────────────────────────────────────────┘
```

### 2.2 核心组件

#### 2.2.1 MemoryManager（记忆管理器）
- **职责**：统一管理短期和长期记忆
- **位置**：`backend/app/memory/manager.py`
- **功能**：
  - 初始化短期和长期记忆实例
  - 提供统一的add_message、get_messages接口
  - 协调记忆的保存和加载

#### 2.2.2 ShortTermMemory（短期记忆）
- **实现**：基于 `langchain.memory.ConversationBufferWindowMemory` + 智能溢出处理
- **特性**：
  - 保留最近K轮对话（默认5轮）
  - **改进**：超出窗口的消息不会丢弃，而是返回给上层处理
  - 存储在内存中，快速访问
  - 自动检测窗口溢出并捕获被移出的消息
  - 支持回调机制通知长期记忆进行摘要
- **位置**：`backend/app/memory/short_term.py`

**关键改进**（v2.0）：
```python
# 原来：直接丢弃旧消息 ❌
# 现在：返回被移出的消息列表，供长期记忆处理 ✅
async def add_message(self, role: str, content: str) -> List[BaseMessage]:
    # 添加消息...
    if new_count > self.window_size * 2:
        evicted_messages = all_messages[:-(self.window_size * 2)]
        # 重建内存中的消息列表
        return evicted_messages  # 返回给调用者处理
    return []
```

#### 2.2.3 LongTermMemory（长期记忆）
- **实现**：基于 `langchain.memory.ConversationSummaryMemory` + 数据库持久化
- **特性**：
  - 自动总结历史对话为摘要
  - 提取关键信息和知识点
  - **改进**：接收从短期记忆移出的消息，进行智能摘要
  - 持久化到数据库的Message表
  - 支持按session_id检索
- **位置**：`backend/app/memory/long_term.py`

**关键改进**（v2.0）：
```python
# 不仅保存当前消息，还处理从短期移出的历史消息
async def add_message(self, role: str, content: str):
    # 添加到LangChain的摘要记忆
    self.memory.chat_memory.add_message(message)
    # 触发LLM生成/更新摘要
    summary_context = self.memory.load_memory_variables({})
    self.summary = summary_context.get("summary", "")
```

#### 2.2.4 MemoryPersistence（记忆持久化层）
- **职责**：负责记忆的数据库操作
- **位置**：`backend/app/memory/persistence.py`
- **功能**：
  - 保存消息到数据库
  - 加载历史消息
  - 清理过期记忆

## 3. 数据模型

### 3.1 数据库表结构

已存在的表（无需修改）：
- `conversations`：会话表
- `messages`：消息表

新增字段建议（可选）：
```python
# messages 表可添加以下字段
summary = Column(Text)  # 消息摘要（长期记忆用）
importance_score = Column(Float)  # 重要性评分（0-1）
tags = Column(JSON)  # 标签列表（用于检索）
```

### 3.2 内存数据结构

```python
class MemoryContext:
    session_id: str                    # 会话ID
    short_term_messages: List[Dict]    # 短期记忆消息列表
    long_term_summary: str             # 长期记忆摘要
    metadata: Dict                     # 元数据（创建时间、更新次数等）
```

## 4. API设计

### 4.1 MemoryManager接口

```python
class MemoryManager:
    async def initialize(self, session_id: str, user_id: Optional[str] = None):
        """初始化记忆，加载历史"""
        
    async def add_message(self, role: str, content: str, metadata: Dict = None):
        """添加消息到记忆"""
        
    async def get_context(self) -> str:
        """获取完整的记忆上下文（短期+长期）"""
        
    async def get_short_term_messages(self, limit: int = 5) -> List[Dict]:
        """获取短期记忆消息"""
        
    async def get_long_term_summary(self) -> str:
        """获取长期记忆摘要"""
        
    async def save_to_db(self):
        """持久化保存到数据库"""
        
    async def clear(self):
        """清空记忆"""
```

### 4.2 Agent集成接口

```python
class BaseAgent:
    def __init__(self, agent_id: UUID, name: str, config: Dict = None):
        self.memory_manager: Optional[MemoryManager] = None
        
    async def set_memory(self, session_id: str, user_id: str = None):
        """为Agent设置记忆"""
        
    async def execute_with_memory(self, task_input: Dict) -> Dict:
        """带记忆执行任务"""
```

## 5. 工作流程

### 5.1 消息添加流程（改进版 v2.0）

```
用户输入 → Agent.execute()
    ↓
MemoryManager.add_message(role="user", content=...)
    ↓
┌──────────────────────────────────────────────┐
│  ShortTermMemory.add_message()                │
│  • 添加到缓冲区                                │
│  • 检查是否超出窗口                            │
│  • 如果超出，捕获被移出的消息                  │
│  • 返回 evicted_messages 列表                 │
────────────────┬─────────────────────────────┘
                 ↓
         有消息被移出？
         ├─ NO → 直接继续
         └─ YES → 处理被移出的消息
                   ↓
         ┌──────────────────────────────────────┐
         │  LongTermMemory.add_message()        │
         │  • 接收被移出的历史消息               │
         │  • LLM生成/更新摘要                   │
         │  • 提取关键信息                       │
         └────────────────┬─────────────────────┘
                          ↓
         ┌──────────────────────────────────────┐
         │  MemoryPersistence.save_to_db()      │
         │  • 保存所有消息到数据库               │
         │  • 定期保存摘要                       │
         └──────────────────────────────────────┘
    ↓
返回成功（零语义丢失 ✅）
```

### 5.2 上下文获取流程

```
Agent需要上下文 → MemoryManager.get_context()
    ↓
┌──────────────────────────────────────────┐
│  1. 获取短期记忆（最近K条原始消息）         │
│  2. 获取长期记忆（历史摘要）                │
│  3. 合并：摘要 + 近期消息                  │
──────────────────────────────────────────┘
    ↓
返回格式化的上下文字符串
```

### 5.3 会话恢复流程

```
新会话开始 → MemoryManager.initialize(session_id)
    ↓
MemoryPersistence.load_history(session_id)
    ↓
┌──────────────────────────────────────────┐
│  1. 从数据库加载所有历史消息               │
│  2. 最近K条 → 短期记忆                    │
│  3. 更早的消息 → 生成摘要 → 长期记忆       │
──────────────────────────────────────────┘
    ↓
记忆准备就绪
```

## 6. 配置参数

### 6.1 环境变量配置

```env
# 短期记忆配置
MEMORY_SHORT_TERM_WINDOW_SIZE=5          # 保留最近N轮对话
MEMORY_SHORT_TERM_ENABLED=true           # 是否启用短期记忆

# 长期记忆配置
MEMORY_LONG_TERM_ENABLED=true            # 是否启用长期记忆
MEMORY_LONG_TERM_SUMMARY_INTERVAL=10     # 每N条消息生成一次摘要
MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH=500  # 摘要最大长度

# 持久化配置
MEMORY_PERSISTENCE_ENABLED=true          # 是否持久化到数据库
MEMORY_PERSISTENCE_BATCH_SIZE=50         # 批量保存大小
```

### 6.2 代码配置

```python
# backend/app/core/config.py
class Settings(BaseSettings):
    # Memory settings
    MEMORY_SHORT_TERM_WINDOW_SIZE: int = 5
    MEMORY_LONG_TERM_SUMMARY_INTERVAL: int = 10
    MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH: int = 500
    MEMORY_PERSISTENCE_ENABLED: bool = True
```

## 7. 性能考虑

### 7.1 优化策略（改进版 v2.0）

1. **短期记忆**：
   - 使用固定大小的deque，O(1)添加和删除
   - **改进**：手动管理窗口溢出，避免LangChain自动丢弃
   - 捕获被移出的消息并返回给上层处理
   - 避免频繁的列表切片操作

2. **长期记忆**：
   - 异步生成摘要，不阻塞主流程
   - **改进**：接收从短期移出的消息，增量更新摘要
   - 缓存摘要结果，减少重复计算
   - 批量保存消息，减少数据库IO

3. **数据库查询**：
   - 为session_id添加索引
   - 分页加载历史消息
   - 使用连接池

4. **零语义丢失保证**：
   - ✅ 所有消息都保存到数据库（永久存储）
   - ✅ 超出窗口的消息转移到长期记忆（智能摘要）
   - ✅ 短期记忆保持轻量（快速访问）
   - ✅ 完整上下文 = 长期摘要 + 短期详细对话

### 7.2 资源消耗估算

- **短期记忆**：每条消息约1KB，5条消息约5KB内存
- **长期记忆**：摘要约500字节，几乎无额外开销
- **数据库存储**：每条消息约2KB（含元数据）

## 8. 扩展性

### 8.1 未来扩展方向

1. **向量记忆**：使用向量数据库存储语义记忆，支持相似性检索
2. **知识图谱**：构建实体关系图，存储结构化知识
3. **个性化记忆**：为不同用户维护独立的记忆空间
4. **记忆压缩**：使用更高级的摘要算法压缩历史

### 8.2 插件化设计

```python
# 支持自定义记忆后端
class MemoryBackend(ABC):
    @abstractmethod
    async def save(self, messages: List[Dict]):
        pass
    
    @abstractmethod
    async def load(self, session_id: str) -> List[Dict]:
        pass

# 可实现的后端：
# - DatabaseMemoryBackend (当前)
# - VectorDBMemoryBackend (未来)
# - RedisMemoryBackend (未来)
```

## 9. 测试策略

### 9.1 单元测试

- MemoryManager初始化测试
- **短期记忆窗口滑动测试（v2.0）**：
  - 验证超出窗口的消息被正确捕获
  - 验证被移出的消息返回给调用者
  - 验证短期记忆不超过窗口限制
- 长期记忆摘要生成测试
- **语义不丢失测试（v2.0）**：
  - 添加大量消息，验证早期信息保留在长期记忆中
  - 验证完整上下文包含历史摘要和近期对话
- 持久化保存和加载测试

### 9.2 集成测试

- Agent与Memory集成测试
- 多轮对话连续性测试
- 会话恢复测试
- 并发访问测试

### 9.3 性能测试

- 大量消息添加性能
- 上下文获取延迟
- 数据库查询性能

## 10. 实施计划

### Phase 1: 基础框架（本周）
1. 创建memory目录结构
2. 实现ShortTermMemory类
3. 实现LongTermMemory类
4. 实现MemoryManager类

### Phase 2: 持久化层（下周）
1. 实现MemoryPersistence类
2. 集成到数据库模型
3. 添加API端点

### Phase 3: Agent集成（下周）
1. 修改BaseAgent支持记忆
2. 更新CoderAgent和ReviewerAgent
3. 更新工作流使用记忆

### Phase 4: 测试和优化（下周）
1. 编写单元测试
2. 集成测试
3. 性能优化

## 11. 依赖项

### 11.1 Python包

已在requirements.txt中：
- `langchain>=0.1.0,<0.2`
- `langchain-community>=0.0.17,<0.1`

需要确认的版本：
- LangChain Memory模块在0.1.x版本中稳定可用

### 11.2 数据库

- SQLite（开发环境）：已支持
- PostgreSQL（生产环境）：已支持

## 12. 风险与挑战

### 12.1 技术风险

1. **LangChain版本兼容性**：Memory API可能在不同版本间变化
   - 缓解：锁定版本范围，充分测试

2. **LLM摘要质量**：摘要可能丢失重要信息
   - 缓解：调整提示词，增加摘要长度
   - **改进（v2.0）**：接收从短期移出的完整消息，基于完整上下文生成摘要

3. **性能瓶颈**：大量历史消息可能导致加载缓慢
   - 缓解：分页加载，异步处理

4. **窗口溢出处理**：手动管理窗口可能引入bug
   - 缓解：充分的单元测试，验证边界情况
   - **已解决（v2.0）**：通过test_memory_improvement.py全面测试

### 12.2 业务风险

1. **隐私问题**：记忆可能包含敏感信息
   - 缓解：添加数据脱敏机制

2. **存储成本**：长期积累可能导致数据库膨胀
   - 缓解：定期清理过期记忆，归档冷数据

## 13. 示例代码

### 13.1 基本使用（改进版 v2.0）

```python
from app.memory.manager import MemoryManager

# 初始化记忆
memory = MemoryManager(session_id="session-123", user_id="user-456")
await memory.initialize()

# 添加消息（自动处理窗口溢出）
await memory.add_message("user", "帮我写一个Python函数")
await memory.add_message("assistant", "好的，请问需要什么功能？")

# ... 继续添加更多消息 ...

# 当消息超出短期窗口时：
# ✅ 被移出的消息自动转移到长期记忆
# ✅ LLM生成/更新摘要
# ✅ 所有消息保存到数据库

# 获取上下文（包含历史摘要 + 近期对话）
context = await memory.get_context()
print(context)
# 输出：
# === Conversation Summary ===
# User asked for help writing a Python function.
# Assistant requested clarification on requirements.
# (早期对话的摘要)
#
# === Recent Messages ===
# User: 最近的提问...
# Assistant: 最近的回答...
# (最近N轮的完整对话)

# 持久化
await memory.save_to_db()
```

### 13.2 Agent中使用

```python
from app.agents.coder import CoderAgent

# 创建Agent并设置记忆
agent = CoderAgent(agent_id=uuid4(), name="MyCoder")
await agent.set_memory(session_id="session-123")

# 执行任务（自动使用记忆）
result = await agent.execute_with_memory({
    "task": "继续刚才的代码"
})
```

## 14. 监控与日志

### 14.1 关键指标

- 记忆命中率
- 平均上下文长度
- 摘要生成耗时
- 数据库写入延迟

### 14.2 日志记录

```python
logger.info(
    "Memory operation completed",
    session_id=session_id,
    operation="add_message",
    short_term_count=len(short_mem),
    long_term_summary_length=len(summary)
)
```

---

**文档版本**: v2.0（滑动窗口改进版）  
**最后更新**: 2026-08-18  
**作者**: Multi-Agent Platform Team

### 主要变更（v2.0）
- ✅ 短期记忆不再丢弃旧消息，而是返回给上层处理
- ✅ 被移出的消息自动转移到长期记忆进行摘要
- ✅ 实现零语义丢失，所有信息都被妥善保存
- ✅ 新增测试脚本验证改进效果（test_memory_improvement.py）
- ✅ 详细文档说明改进方案（MEMORY_IMPROVEMENT.md）
