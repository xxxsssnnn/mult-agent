# RAG系统使用指南

**版本**: v1.0  
**创建日期**: 2026-08-04  
**状态**: ✅ 已实现

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

新增的RAG相关依赖：
- `chromadb>=0.4.22,<0.5` - 向量数据库
- `faiss-cpu>=1.7.4,<2.0` - 向量相似度搜索
- `tiktoken>=0.5.0,<0.6` - Token计数
- `pypdf>=3.17.0,<4.0` - PDF文档解析
- `python-docx>=1.1.0,<2.0` - Word文档解析

### 2. 配置环境变量（可选）

在 `.env` 文件中添加以下配置：

```bash
# RAG配置
CHROMA_HOST=localhost
CHROMA_PORT=8000

# Embedding模型选择
EMBEDDING_MODEL_TYPE=openai  # 或 huggingface

# OpenAI API Key（如果使用OpenAI Embeddings）
OPENAI_API_KEY=sk-your-api-key-here

# HuggingFace模型（如果不使用OpenAI）
HUGGINGFACE_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

### 3. 启动后端服务

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

访问API文档：http://localhost:8001/docs

## 使用方法

### 方法1：通过REST API

#### 上传文档到知识库

```bash
curl -X POST "http://localhost:8001/api/v1/rag/ingest" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" \
  -F "collection_name=my_knowledge_base"
```

支持的文件格式：
- `.pdf` - PDF文档
- `.txt` - 纯文本文件
- `.docx` - Word文档
- `.md` - Markdown文件

#### 查询知识库

```bash
curl -X POST "http://localhost:8001/api/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "query": "如何使用Python实现RAG？",
    "collection_name": "my_knowledge_base",
    "k": 3,
    "search_type": "similarity"
  }'
```

响应示例：
```json
{
  "answer": "根据文档，实现RAG需要以下步骤...",
  "sources": [
    {
      "content": "RAG的实现包括文档处理、向量化...",
      "metadata": {
        "source": "document.pdf",
        "page": 5
      },
      "score": 0.92
    }
  ],
  "context": "完整的上下文信息..."
}
```

#### 获取知识库统计

```bash
curl -X GET "http://localhost:8001/api/v1/rag/stats/my_knowledge_base" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

#### 清空知识库

```bash
curl -X DELETE "http://localhost:8001/api/v1/rag/clear/my_knowledge_base" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 方法2：通过Python代码

#### 基本用法

```python
import asyncio
from uuid import uuid4
from app.rag import RAGAgent

async def main():
    # 创建RAG Agent
    rag_agent = RAGAgent(
        agent_id=uuid4(),
        name="MyRAGAgent",
        config={
            "retrieval_k": 3,
            "search_type": "similarity",
            "collection_name": "my_kb",
            "persist_directory": "./chroma_db"
        }
    )
    
    # 初始化
    await rag_agent.initialize()
    
    # 导入文档
    result = await rag_agent.ingest_documents(["document.pdf"])
    print(f"成功导入 {result['count']} 个文档")
    
    # 查询
    answer = await rag_agent.execute({
        "user_input": "什么是RAG？"
    })
    print(f"答案：{answer['output']}")
    print(f"来源：{answer['sources']}")

asyncio.run(main())
```

#### 高级用法：自定义检索策略

```python
# 使用MMR检索（平衡相关性和多样性）
rag_agent = RAGAgent(
    agent_id=uuid4(),
    config={
        "search_type": "mmr",
        "mmr_lambda": 0.5  # 0=只考虑多样性, 1=只考虑相关性
    }
)

# 带元数据过滤的检索
results = await rag_agent.query_with_filter(
    query="Python编程",
    metadata_filter={"source": "python_guide.pdf"}
)
```

#### 批量导入文档

```python
from pathlib import Path

# 导入整个文件夹的文档
doc_folder = Path("./documents")
pdf_files = list(doc_folder.glob("*.pdf"))

await rag_agent.ingest_documents([str(f) for f in pdf_files])
```

### 方法3：运行演示脚本

```bash
cd backend
python examples/rag_demo.py
```

演示脚本会展示：
1. 基本的RAG功能
2. 文档导入流程
3. 多种查询方式
4. 知识库管理

## 最佳实践

### 1. 选择合适的Embedding模型

**OpenAI Embeddings**（推荐用于生产环境）
- ✅ 质量高，准确性好
- ✅ 速度快
- ❌ 需要API Key，有成本
- 适用场景：企业应用、高精度需求

**HuggingFace Embeddings**（推荐用于开发/测试）
- ✅ 免费，无API限制
- ✅ 本地运行，隐私性好
- ❌ 速度较慢，需要计算资源
- 适用场景：本地开发、敏感数据

### 2. 优化文本分块策略

```python
# 技术文档：较小的块
processor = DocumentProcessor(chunk_size=500, chunk_overlap=100)

# 学术论文：中等大小的块
processor = DocumentProcessor(chunk_size=1000, chunk_overlap=200)

# 法律文档：较大的块（保持上下文完整）
processor = DocumentProcessor(chunk_size=1500, chunk_overlap=300)
```

**原则**：
- 块太小：丢失上下文
- 块太大：降低检索精度
- 重叠很重要：避免关键信息被切断

### 3. 调整检索参数

```python
# 精确查询：返回少量高相关结果
config = {
    "retrieval_k": 2,
    "search_type": "similarity"
}

# 探索性查询：返回更多结果
config = {
    "retrieval_k": 5,
    "search_type": "mmr",
    "mmr_lambda": 0.7
}
```

### 4. 管理多个知识库

```python
# 为不同领域创建独立的知识库
kb_finance = VectorStoreManager(collection_name="finance")
kb_legal = VectorStoreManager(collection_name="legal")
kb_tech = VectorStoreManager(collection_name="technology")

# 根据问题类型选择知识库
if question_type == "financial":
    answer = await rag_agent.query(question, collection="finance")
elif question_type == "legal":
    answer = await rag_agent.query(question, collection="legal")
```

### 5. 定期维护知识库

```python
# 查看统计信息
stats = await rag_agent.get_knowledge_base_stats()
print(f"文档数量: {stats['document_count']}")
print(f"集合大小: {stats['collection_size']}")

# 清理过时的知识库
await rag_agent.clear_collection("outdated_kb")
```

## 故障排查

### 问题1：ChromaDB连接失败

**症状**：
```
ConnectionError: Cannot connect to ChromaDB at localhost:8000
```

**解决方案**：
1. 检查ChromaDB是否运行
2. 确认`.env`中的`CHROMA_HOST`和`CHROMA_PORT`正确
3. 如果是首次运行，确保安装了chromadb：`pip install chromadb`

### 问题2：OpenAI API调用失败

**症状**：
```
AuthenticationError: Invalid API key
```

**解决方案**：
1. 检查`.env`中是否正确设置了`OPENAI_API_KEY`
2. 切换到HuggingFace模型：`EMBEDDING_MODEL_TYPE=huggingface`
3. 验证API Key余额充足

### 问题3：文档导入后检索不到结果

**可能原因**：
1. 文本块太大或太小
2. Embedding质量不高
3. 查询语句与文档内容不匹配

**解决方案**：
1. 调整`chunk_size`和`chunk_overlap`
2. 尝试不同的Embedding模型
3. 使用更具体的查询语句
4. 增加`retrieval_k`值

### 问题4：内存占用过高

**症状**：程序运行时内存持续增长

**解决方案**：
1. 减小`chunk_size`
2. 限制单个集合的文档数量
3. 定期清理未使用的集合
4. 使用持久化模式而非内存模式

### 问题5：检索结果不相关

**解决方案**：
1. 尝试MMR搜索代替相似性搜索
2. 调整`mmr_lambda`参数
3. 优化文档预处理（去除噪声）
4. 使用更高质量的Embedding模型

## 性能优化建议

### 1. 批量操作

```python
# ❌ 低效：逐个添加文档
for doc in documents:
    await rag_agent.ingest_documents([doc])

# ✅ 高效：批量添加
await rag_agent.ingest_documents(documents)
```

### 2. 缓存常用查询

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_cached_answer(query: str):
    return asyncio.run(rag_agent.execute({"user_input": query}))
```

### 3. 异步并发

```python
# 并发执行多个查询
queries = ["问题1", "问题2", "问题3"]
tasks = [rag_agent.execute({"user_input": q}) for q in queries]
results = await asyncio.gather(*tasks)
```

### 4. 持久化配置

```python
# 启用磁盘持久化（重启后数据不丢失）
vector_store = VectorStoreManager(
    collection_name="my_kb",
    persist_directory="./chroma_db"  # 指定持久化路径
)
```

## 安全注意事项

### 1. 敏感数据处理

- ⚠️ 不要将包含个人身份信息（PII）的文档导入知识库
- ⚠️ 对机密文档进行加密或脱敏处理
- ⚠️ 设置适当的访问控制权限

### 2. API认证

所有RAG API端点都需要有效的JWT Token：

```python
headers = {
    "Authorization": f"Bearer {user_token}",
    "Content-Type": "application/json"
}
```

### 3. 输入验证

RAG系统会自动验证：
- 文件大小（最大10MB）
- 文件类型（仅允许白名单格式）
- 查询长度（防止过长查询）

## 扩展阅读

- 📖 [RAG系统技术架构](./RAG_SYSTEM_ARCHITECTURE.md) - 详细的架构设计
- 📖 [记忆系统设计](./memory_system_design.md) - 长短期记忆实现
- 📖 [API文档](http://localhost:8001/docs) - FastAPI自动生成的API文档

## 常见问题 (FAQ)

### Q1: RAG和传统搜索引擎有什么区别？

**A**: RAG基于语义理解，能理解问题的含义，而不仅仅是关键词匹配。例如：
- 传统搜索："Python 列表 添加" → 匹配包含这些词的文档
- RAG："如何向列表中添加元素？" → 理解意图，找到相关答案

### Q2: 可以导入多少文档？

**A**: 取决于硬件配置：
- 小型部署（8GB RAM）：数千个文档
- 中型部署（16GB RAM）：数万个文档
- 大型部署（32GB+ RAM）：数十万个文档

### Q3: 支持中文吗？

**A**: ✅ 完全支持！
- OpenAI模型：原生支持多语言
- HuggingFace模型：使用多语言版本（如`paraphrase-multilingual-MiniLM-L12-v2`）

### Q4: 如何评估RAG系统的效果？

**A**: 可以使用以下指标：
1. **检索准确率**：检索到的文档是否相关
2. **答案质量**：生成的答案是否准确、完整
3. **响应时间**：端到端的延迟
4. **用户满意度**：实际用户的反馈

### Q5: RAG可以和记忆系统一起使用吗？

**A**: ✅ 当然可以！两者是互补的：
- **记忆系统**：保存对话历史，维持上下文
- **RAG系统**：提供外部知识，增强回答能力

```python
# 结合使用
agent.set_memory(session_id="session_123")
rag_result = await rag_agent.execute({
    "user_input": "公司的报销政策是什么？"
})
# RAG提供知识，记忆维持对话连贯性
```

## 更新日志

### v1.0 (2026-08-04)
- ✅ 初始版本发布
- ✅ 支持PDF、TXT、DOCX、MD格式
- ✅ ChromaDB向量存储
- ✅ OpenAI和HuggingFace Embedding
- ✅ REST API接口
- ✅ Python SDK
- ✅ 完整的文档和示例
