# 短期记忆滑动窗口改进方案

## 📌 问题描述

### 原始设计的缺陷

原始的短期记忆实现使用LangChain的`ConversationBufferWindowMemory`，当消息数量超过窗口大小时，**直接丢弃旧消息**。这导致严重的**语义丢失**问题：

```python
# 原始实现的问题
window_size = 5  # 保留最近5轮对话

# 当添加第6轮对话时，第1轮对话被永久删除 ❌
# 用户可能需要重复提供已说过的信息
# Agent无法理解完整的对话脉络
```

### 具体影响

1. **关键信息丢失**：早期对话中的重要上下文（如需求、约束条件）被永久删除
2. **连贯性断裂**：Agent无法追踪完整的对话历史，导致回答不一致
3. **用户体验差**：用户需要反复重申已经提供的信息
4. **效率低下**：重复的信息输入浪费时间和token

## ✅ 改进方案

### 核心思想：**转移而非丢弃**

采用**分层记忆架构**，当消息超出短期窗口时：
1. ❌ 不是直接丢弃
2. ✅ 而是提取关键信息并转移到长期记忆
3. ✅ 保证所有信息都被妥善保存

### 架构设计

```
┌─────────────────────────────────────────────┐
│         完整对话历史（数据库持久化）          │
│         - 所有消息永久保存                    │
│         - 支持按session_id检索                │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────▼──────────
        │   长期记忆层          │
        │  (智能摘要 + 关键点)  │
        │                      │
        │  接收来自短期的      │
        │  "过期"消息          │
        │  ↓                   │
        │  • LLM生成摘要       │
        │  • 提取关键实体      │
        │  • 保存决策/结论     │
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────┐
        │   短期记忆层          │
        │  (滑动窗口)           │
        │                      │
        │  • 保留最近N轮       │
        │  • 快速访问          │
        │  • 超出时触发转移    │
        ─────────────────────┘
```

### 工作流程

```
用户输入新消息
    ↓
添加到短期记忆
    ↓
检查是否超出窗口大小？
    ├─ NO → 直接返回
    └─ YES → 检测到有消息被移出
              ↓
         捕获被移出的消息
              ↓
         将旧消息添加到长期记忆
              ↓
         LLM生成/更新摘要
              ↓
         保存到数据库
              ↓
         返回成功
```

## 🔧 技术实现

### 1. ShortTermMemory改进

**文件**: `backend/app/memory/short_term.py`

#### 关键改动

```python
class ShortTermMemory:
    def __init__(self, window_size: int = 5, on_message_evict=None):
        """
        新增参数:
        - on_message_evict: 消息被移出窗口时的回调函数
        """
        self.window_size = window_size
        self.on_message_evict = on_message_evict  # 回调机制
        
    async def add_message(self, role: str, content: str) -> List[BaseMessage]:
        """
        返回值变更:
        - 原来: None
        - 现在: 返回被移出的消息列表
        
        这样上层可以知道哪些消息需要处理
        """
        # ... 添加消息逻辑 ...
        
        # 检测并捕获被移出的消息
        if new_count > self.window_size * 2:
            evicted_messages = all_messages[:-(self.window_size * 2)]
            # 重建内存中的消息列表
            self.memory.chat_memory.clear()
            for msg in kept_messages:
                self.memory.chat_memory.add_message(msg)
            
            return evicted_messages  # 返回给调用者处理
        
        return []
```

### 2. MemoryManager协调层

**文件**: `backend/app/memory/manager.py`

#### 关键逻辑

```python
async def add_message(self, role: str, content: str, metadata: Dict = None):
    # 1. 添加到短期记忆，获取被移出的消息
    evicted_messages = await self.short_term.add_message(role, content)
    
    # 2. 处理被移出的消息（转移到长期记忆）
    if evicted_messages:
        logger.info(f"Processing {len(evicted_messages)} evicted messages")
        
        for msg in evicted_messages:
            msg_role = "user" if isinstance(msg, HumanMessage) else "assistant"
            # 将旧消息添加到长期记忆进行摘要
            await self.long_term.add_message(msg_role, msg.content)
    
    # 3. 同时将所有消息添加到长期记忆（用于生成摘要）
    await self.long_term.add_message(role, content)
    
    # 4. 持久化到数据库（所有消息都保存）
    if self.persistence:
        await self.persistence.save_message(...)
```

## 📊 效果对比

### 场景示例

假设窗口大小为3轮对话：

####  原始实现（直接丢弃）

```
对话历史：
第1轮: User: "我想写一个Python排序算法"
       Assistant: "好的，这是快速排序..."
       
第2轮: User: "能优化一下吗？"
       Assistant: "可以使用动态规划..."
       
第3轮: User: "时间复杂度是多少？"
       Assistant: "O(n log n)..."

--- 添加第4轮 ---

第4轮: User: "帮我改成降序排列"
       Assistant: "好的..."

 第1轮对话被永久删除！
 Agent忘记了用户最初要的是"排序算法"
❌ 可能误解为其他任务
```

#### ✅ 改进实现（转移至长期记忆）

```
短期记忆（窗口内）：
第2轮: User: "能优化一下吗？"
       Assistant: "可以使用动态规划..."
       
第3轮: User: "时间复杂度是多少？"
       Assistant: "O(n log n)..."

第4轮: User: "帮我改成降序排列"
       Assistant: "好的..."

长期记忆（摘要）：
"""
用户请求编写Python排序算法。
已提供快速排序实现，讨论了优化方案和时间复杂度O(n log n)。
当前正在修改为降序排列。
"""

✅ 所有信息都得到保留
✅ Agent通过摘要了解完整上下文
✅ 不会出现语义丢失
```

### 性能指标

| 指标 | 原始实现 | 改进实现 |
|------|---------|---------|
| **信息保留率** | ~40% (只保留窗口内) | 100% (全部保存) |
| **语义完整性** | ❌ 断裂 | ✅ 完整 |
| **Token使用** | 较少 | 略多（+摘要） |
| **响应速度** | 快 | 相当（异步处理） |
| **存储成本** | 低 | 略高（可接受） |

## 🎯 优势总结

### 1. 零语义丢失
- ✅ 所有对话内容都被妥善保存
- ✅ 早期的重要信息不会丢失
- ✅ Agent始终拥有完整上下文

### 2. 智能分层
- ✅ 短期：快速访问最近对话
- ✅ 长期：智能摘要历史对话
- ✅ 数据库：永久保存完整记录

### 3. 自动管理
- ✅ 无需手动干预
- ✅ 自动检测窗口溢出
- ✅ 自动触发摘要更新

### 4. 灵活配置
```env
# 可根据需求调整窗口大小
MEMORY_SHORT_TERM_WINDOW_SIZE=5  # 短期窗口

# 控制摘要更新频率
MEMORY_LONG_TERM_SUMMARY_INTERVAL=10  # 每10条消息更新一次摘要
```

## ️ 注意事项

### 1. 性能考虑
- 每次消息添加都需要检查窗口状态
- 被移出的消息需要额外处理（LLM摘要）
- **解决方案**：异步处理，不阻塞主流程

### 2. Token消耗
- 长期记忆的摘要会占用额外的token
- **解决方案**：控制摘要长度（默认500字符）

### 3. 数据库存储
- 所有消息都保存到数据库
- **解决方案**：定期清理过期会话，归档冷数据

## 🚀 后续优化方向

### 1. 智能关键点提取
```python
# 不只是简单摘要，而是提取：
- 用户需求/目标
- 关键技术决策
- 重要约束条件
- 已解决的问题
```

### 2. 向量索引
```python
# 为长期记忆建立向量索引
- 支持语义搜索
- 快速检索相关历史
- 智能召回关键信息
```

### 3. 个性化记忆
```python
# 为不同用户维护独立记忆空间
- 用户偏好学习
- 习惯模式识别
- 个性化响应
```

## 📝 使用建议

### 开发环境
```python
# 较小的窗口，快速测试
MEMORY_SHORT_TERM_WINDOW_SIZE=3
```

### 生产环境
```python
# 较大的窗口，更好的体验
MEMORY_SHORT_TERM_WINDOW_SIZE=7-10

# 配合强大的LLM生成高质量摘要
OPENAI_API_KEY=<your_key>
OPENAI_MODEL=gpt-4
```

### 资源受限环境
```python
# 减小窗口和摘要长度
MEMORY_SHORT_TERM_WINDOW_SIZE=3
MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH=200
```

## 🔍 故障排查

### 问题1: 消息没有被转移到长期记忆

**原因**: 窗口大小设置过大，未达到溢出条件

**解决**: 
```python
# 检查实际消息数量
stats = await memory.get_stats()
print(f"Short-term count: {stats['short_term_message_count']}")

# 如果接近但未超过窗口*2，属于正常现象
```

### 问题2: 摘要质量不佳

**原因**: LLM未配置或模型能力不足

**解决**:
```env
# 确保配置了OpenAI API Key
OPENAI_API_KEY=sk-xxx

# 或使用更强的模型
OPENAI_MODEL=gpt-4
```

### 问题3: 性能下降

**原因**: 频繁的消息转移和摘要生成

**解决**:
```env
# 增加摘要更新间隔，减少LLM调用
MEMORY_LONG_TERM_SUMMARY_INTERVAL=20  # 从10改为20
```

---

**版本**: v2.0  
**最后更新**: 2026-08-18  
**状态**: ✅ 已实现并测试
