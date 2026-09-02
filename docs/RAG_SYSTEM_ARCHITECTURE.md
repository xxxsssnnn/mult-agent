# RAG系统技术架构文档

**版本**: v1.0  
**创建日期**: 2026-08-04  
**状态**: ✅ 已实现

## 1. 概述

### 1.1 什么是RAG

**RAG（Retrieval-Augmented Generation，检索增强生成）**是一种结合信息检索和文本生成的AI技术架构。它通过以下步骤工作：

1. **检索（Retrieval）**：从知识库中检索与用户问题相关的文档片段
2. **增强（Augmentation）**：将检索到的内容作为上下文提供给LLM
3. **生成（Generation）**：LLM基于上下文生成准确、有依据的答案

### 1.2 为什么需要RAG

传统LLM的局限性：
- ❌ **知识截止**：训练数据有时间限制，无法了解最新信息
- ❌ **幻觉问题**：可能编造不存在的事实
- ❌ **缺乏可追溯性**：无法提供答案的来源

RAG的优势：
- ✅ **实时更新**：可以随时添加新知识到知识库
- ✅ **减少幻觉**：答案基于真实文档，有据可查
- ✅ **可追溯性**：可以引用具体的文档来源
- ✅ **领域专业化**：可以针对特定领域构建专业知识库

### 1.3 应用场景

- **企业知识库问答**：员工提问，自动从公司文档中查找答案
- **产品文档搜索**：客户查询产品使用方法
- **代码库检索**：开发者询问如何实现某个功能
- **法律文档分析**：律师查询相关法律条款
- **医疗知识查询**：医生查询医学文献

## 2. 系统架构

### 2.1 整体架构图

```
─────────────────────────────────────────────────────────────┐
│                        用户层                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Web前端 / API客户端 / CLI                            │   │
│  └────────────────────┬─────────────────────────────────┘   │
└───────────────────────┼─────────────────────────────────────┘
                        │ HTTP/REST API
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                      API层 (FastAPI)                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  POST /api/v1/rag/ingest   - 上传文档                  │   │
│  │  POST /api/v1/rag/query      - 查询知识库              │   │
│  │  GET  /api/v1/rag/stats      - 统计信息                │   │
│  │  DELETE /api/v1/rag/clear    - 清空知识库              │   │
│  └────────────────────┬─────────────────────────────────┘   │
└───────────────────────┼─────────────────────────────────────┘
                        │
                        ▼
─────────────────────────────────────────────────────────────┐
│                    RAG Agent层                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  RAGAgent                                             │   │
│  │  • 协调检索和生成流程                                  │   │
│  │  • 管理文档导入                                       │   │
│  │  • 调用LLM生成答案                                    │   │
│  └──────┬───────────────┬───────────────┬──────────────┘   │
─────────┼───────────────┼───────────────┼──────────────────┘
          │               │               │
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌──────────────────┐
│  文档处理模块     │ │  向量存储模块    │ │  Embedding服务   │
│                 │ │                 │ │                  │
│ DocumentProcessor│ │ VectorStore     │ │ EmbeddingService │
│ • PDF/TXT加载    │ │ • ChromaDB      │ │ • OpenAI         │
│ • 文本分割       │ │ • 相似性搜索     │ │ • HuggingFace    │
│ • 元数据提取     │ │ • MMR检索       │ │ • 文本向量化     │
└─────────────────┘ ────────┬────────┘ └──────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  语义检索器      │
                    │  SemanticRetriever│
                    │  • 多策略检索    │
                    │  • 结果重排序    │
                    │  • 上下文构建    │
                    ─────────────────┘
```

### 2.2 核心组件

#### 2.2.1 DocumentProcessor（文档处理器）

**职责**：加载和预处理文档

**功能**：
- 支持多种文件格式（PDF、TXT、DOCX、MD）
- 智能文本分割（保持语义完整性）
- 元数据提取（来源、页码等）

**关键参数**：
```python
chunk_size: int = 1000      # 每个文本块的大小
chunk_overlap: int = 200    # 块之间的重叠
```

**工作流程**：
```
原始文档 → 加载器 → 原始文本 → 分割器 → 文本块列表
```

#### 2.2.2 EmbeddingService（Embedding服务）

**职责**：将文本转换为向量表示

**支持的模型**：
1. **OpenAI Embeddings**（需要API Key）
   - 模型：text-embedding-ada-002
   - 维度：1536
   - 优点：质量高，速度快

2. **HuggingFace Embeddings**（本地运行）
   - 模型：sentence-transformers/all-MiniLM-L6-v2
   - 维度：384
   - 优点：免费，隐私性好

**工作流程**：
```
文本 → Tokenizer → Embedding Model → 向量（浮点数数组）
```

#### 2.2.3 VectorStoreManager（向量存储管理器）

**职责**：存储和检索向量化的文档

**技术选型**：ChromaDB
- 轻量级向量数据库
- 支持持久化到磁盘
- 内置相似性搜索
- Python原生支持

**核心功能**：
- 添加/删除文档
- 相似性搜索（Similarity Search）
- MMR搜索（Max Marginal Relevance）
- 元数据过滤

**数据结构**：
```python
{
    "id": "uuid",
    "embedding": [0.1, 0.2, ...],  # 向量
    "document": "文本内容",
    "metadata": {"source": "file.pdf", "page": 1}
}
```

#### 2.2.4 SemanticRetriever（语义检索器）

**职责**：执行智能检索并构建上下文

**检索策略**：
1. **Similarity Search**：基础相似性搜索
2. **MMR Search**：平衡相关性和多样性
3. **Score-based**：带分数的搜索
4. **Hybrid Search**：混合搜索（待完善）

**上下文构建**：
```
检索到的文档 → 格式化 → 上下文字符串 → 提供给LLM
```

#### 2.2.5 RAGAgent（RAG Agent）

**职责**：协调整个RAG流程

**工作流程**：
```
用户问题 → 检索相关文档 → 构建上下文 → LLM生成答案 → 返回结果
```

**关键方法**：
- `execute()`: 执行RAG查询
- `ingest_documents()`: 导入文档到知识库
- `get_knowledge_base_stats()`: 获取统计信息

## 3. 数据流

### 3.1 文档导入流程

```
1. 用户上传文件
   ↓
2. DocumentProcessor.load_document()
   - 根据文件类型选择加载器
   - 提取原始文本
   ↓
3. DocumentProcessor.split_documents()
   - 使用RecursiveCharacterTextSplitter
   - 分割成适当大小的块
   - 保留重叠部分
   ↓
4. EmbeddingService.embed_texts()
   - 批量向量化文本块
   ↓
5. VectorStoreManager.add_documents()
   - 存储向量 + 原文本 + 元数据
   - 持久化到ChromaDB
   ↓
6. 返回成功
```

### 3.2 查询流程

```
1. 用户提出问题
   ↓
2. RAGAgent.execute(query)
   ↓
3. SemanticRetriever.retrieve()
   - 问题向量化
   - 在ChromaDB中搜索相似文档
   - 返回Top-K结果
   ↓
4. RAGAgent._build_context_from_docs()
   - 格式化检索到的文档
   - 构建上下文字符串
   ↓
5. LLM生成答案
   - System Prompt: 指导LLM如何使用上下文
   - User Prompt: 问题 + 上下文
   - LLM生成答案
   ↓
6. 返回结果
   - 答案文本
   - 引用的文档列表
   - 元数据（来源、分数等）
```

## 4. API设计

### 4.1 端点列表

| 端点 | 方法 | 描述 | 认证 |
|------|------|------|------|
| `/api/v1/rag/ingest` | POST | 上传并导入文档 | ✅ |
| `/api/v1/rag/query` | POST | 查询知识库 | ✅ |
| `/api/v1/rag/stats` | GET | 获取统计信息 | ✅ |
| `/api/v1/rag/clear` | DELETE | 清空知识库 | ✅ |
| `/api/v1/rag/demo` | POST | 功能演示 | ✅ |
| `/api/v1/rag/info` | GET | 系统信息 | ✅ |

### 4.2 请求/响应示例

#### 上传文档

**请求**：
```http
POST /api/v1/rag/ingest
Content-Type: multipart/form-data
Authorization: Bearer <token>

Files: [document1.pdf, document2.txt, ...]
```

**响应**：
```json
{
  "success": true,
  "num_files": 2,
  "num_chunks": 45,
  "document_ids": ["uuid-1", "uuid-2", ...]
}
```

#### 查询知识库

**请求**：
```http
POST /api/v1/rag/query
Content-Type: application/json
Authorization: Bearer <token>

{
  "query": "什么是机器学习？",
  "k": 5,
  "search_type": "similarity"
}
```

**响应**：
```json
{
  "success": true,
  "query": "什么是机器学习？",
  "answer": "机器学习是人工智能的一个分支...",
  "retrieved_documents": [
    {
      "content": "机器学习是一种...",
      "metadata": {"source": "ai_book.pdf", "page": 10},
      "source": "ai_book.pdf"
    },
    ...
  ],
  "num_retrieved": 5,
  "context_length": 2500
}
```

## 5. 配置参数

### 5.1 环境变量

```env
# RAG配置
CHROMA_HOST="localhost"           # ChromaDB主机
CHROMA_PORT=8000                  # ChromaDB端口

# Embedding配置
EMBEDDING_MODEL_TYPE="huggingface"  # 或 "openai"
EMBEDDING_MODEL_NAME=""             # 自定义模型名称

# 文档处理配置
DOCUMENT_CHUNK_SIZE=1000          # 文本块大小
DOCUMENT_CHUNK_OVERLAP=200        # 重叠大小

# 检索配置
RETRIEVAL_K=5                     # 默认返回结果数
SEARCH_TYPE="similarity"          # 搜索类型
```

### 5.2 代码配置

```python
# RAG Agent配置
rag_config = {
    "retrieval_k": 5,              # 检索结果数量
    "search_type": "similarity",   # 搜索策略
    "collection_name": "rag_default",  # 集合名称
    "persist_directory": "./chroma_db"  # 持久化目录
}

# Document Processor配置
processor = DocumentProcessor(
    chunk_size=1000,
    chunk_overlap=200
)

# Embedding Service配置
embedding = EmbeddingService(
    model_type="huggingface",  # 或 "openai"
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)
```

## 6. 性能优化

### 6.1 索引优化

1. **批量处理**：
   - 文档向量化时批量处理，减少API调用次数
   - 建议批次大小：100-1000条

2. **缓存机制**：
   - 缓存频繁查询的结果
   - 缓存常用文档的向量

3. **并行处理**：
   - 文档加载和向量化可以并行
   - 使用asyncio提高并发性能

### 6.2 检索优化

1. **选择合适的K值**：
   - 太小：可能遗漏相关信息
   - 太大：引入噪声，增加LLM负担
   - 建议：3-7之间

2. **使用MMR策略**：
   - 平衡相关性和多样性
   - 避免返回过于相似的结果

3. **元数据过滤**：
   - 在检索前过滤不相关的文档
   - 减少搜索空间

### 6.3 存储优化

1. **定期清理**：
   - 删除过期或不需要的文档
   - 压缩ChromaDB数据库

2. **分区存储**：
   - 按主题或部门创建不同的集合
   - 提高检索效率

## 7. 最佳实践

### 7.1 文档准备

1. **格式规范**：
   - 使用清晰的PDF或TXT格式
   - 避免扫描件（需要OCR）
   - 确保文本可复制

2. **结构化内容**：
   - 使用标题、段落分隔
   - 添加目录和索引
   - 包含元数据（作者、日期等）

3. **质量控制**：
   - 去除无关内容（广告、页眉页脚）
   - 修正拼写错误
   - 统一术语

### 7.2 查询优化

1. **问题表述**：
   - 使用清晰、具体的问题
   - 包含关键词
   - 避免模糊表述

2. **迭代查询**：
   - 如果第一次结果不理想，调整问题重新查询
   - 使用不同的关键词

3. **利用元数据**：
   - 如果知道文档来源，可以指定过滤条件
   - 缩小搜索范围

### 7.3 系统维护

1. **定期更新**：
   - 及时添加新文档
   - 删除过时内容
   - 更新 embeddings（如果模型升级）

2. **监控性能**：
   - 跟踪查询响应时间
   - 监控检索准确率
   - 收集用户反馈

3. **备份数据**：
   - 定期备份ChromaDB
   - 保存原始文档
   - 记录导入日志

## 8. 故障排查

### 8.1 常见问题

#### 问题1: 导入文档失败

**原因**：文件格式不支持或损坏

**解决**：
```python
# 检查文件格式
print(processor.get_supported_formats())

# 尝试单独加载每个文件
for file in files:
    try:
        chunks = await processor.process_file(file)
    except Exception as e:
        print(f"Failed to process {file}: {e}")
```

#### 问题2: 查询结果为空

**原因**：知识库为空或查询词不匹配

**解决**：
```python
# 检查知识库状态
stats = await rag_agent.get_knowledge_base_stats()
print(f"Document count: {stats['document_count']}")

# 尝试更通用的查询词
result = await rag_agent.execute({"query": "通用关键词"})
```

#### 问题3: 答案不准确

**原因**：检索到的文档不相关或LLM理解有误

**解决**：
```python
# 增加检索数量
result = await rag_agent.execute({
    "query": question,
    "k": 10  # 从5增加到10
})

# 检查检索到的文档
for doc in result['retrieved_documents']:
    print(doc['content'][:200])
```

#### 问题4: 响应速度慢

**原因**：文档过多或Embedding计算慢

**解决**：
```python
# 使用更快的Embedding模型
embedding = EmbeddingService(model_type="huggingface")

# 减小chunk_size
processor = DocumentProcessor(chunk_size=500)

# 使用MMR减少返回数量
result = await retriever.retrieve(query, k=3, search_type="mmr")
```

### 8.2 日志分析

启用详细日志：
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

关键日志：
- `Document loaded successfully`：文档加载成功
- `Documents split into chunks`：文档分割完成
- `Texts embedded successfully`：向量化完成
- `Documents added to vector store`：存入向量数据库
- `Similarity search completed`：检索完成

## 9. 扩展方向

### 9.1 高级检索

1. **混合检索**：
   - 结合BM25（关键词）和向量检索
   - 提高召回率

2. **重排序（Re-ranking）**：
   - 使用Cross-Encoder对初步结果重新排序
   - 提高精度

3. **查询扩展**：
   - 自动扩展同义词和相关词
   - 提高召回率

### 9.2 多模态RAG

1. **图像检索**：
   - 使用CLIP等模型处理图像
   - 支持图文混合查询

2. **音频/视频**：
   - 转录为文本后索引
   - 支持语音查询

### 9.3 个性化RAG

1. **用户偏好学习**：
   - 记录用户的查询历史
   - 调整检索策略

2. **权限控制**：
   - 不同用户看到不同的文档
   - 敏感信息隔离

### 9.4 评估系统

1. **检索评估**：
   - Recall@K
   - MRR (Mean Reciprocal Rank)
   - NDCG

2. **生成评估**：
   - 答案准确性
   - 引用正确性
   - 用户满意度

## 10. 与其他模块集成

### 10.1 与记忆系统集成

RAG可以作为长期记忆的补充：
- **记忆系统**：存储对话历史和个人偏好
- **RAG系统**：存储外部知识和文档

两者结合可以提供更全面的上下文。

### 10.2 与Agent系统集成

RAGAgent可以作为其他Agent的工具：
```python
# CoderAgent可以使用RAG查询代码文档
coder_result = await coder_agent.execute({
    "task": "实现用户认证",
    "context": await rag_agent.execute({
        "query": "JWT认证最佳实践"
    })['answer']
})
```

### 10.3 与工作流集成

在工作流中使用RAG：
```python
# 任务规划工作流中可以查询相关知识
workflow_state["knowledge"] = await rag_agent.execute({
    "query": f"如何实现{task_description}"
})
```

## 11. 安全考虑

### 11.1 数据安全

1. **访问控制**：
   - 所有RAG API都需要认证
   - 基于角色的权限管理

2. **数据加密**：
   - 传输层使用HTTPS
   - 存储层加密敏感文档

3. **审计日志**：
   - 记录所有文档导入操作
   - 记录所有查询操作

### 11.2 内容安全

1. **文档审核**：
   - 导入前检查文档内容
   - 过滤敏感信息

2. **查询过滤**：
   - 检测恶意查询
   - 限制查询频率

3. **输出过滤**：
   - 检查生成的答案
   - 防止泄露敏感信息

## 12. 部署指南

### 12.1 开发环境

```bash
# 安装依赖
cd backend
pip install -r requirements.txt

# 启动后端
python -m uvicorn app.main:app --reload --port 8001

# 测试RAG功能
python examples/rag_demo.py
```

### 12.2 生产环境

1. **ChromaDB部署**：
   ```docker-compose
   version: '3'
   services:
     chromadb:
       image: chromadb/chroma:latest
       ports:
         - "8000:8000"
       volumes:
         - ./chroma_data:/chroma/chroma
   ```

2. **环境变量**：
   ```env
   CHROMA_HOST=chromadb
   CHROMA_PORT=8000
   OPENAI_API_KEY=sk-xxx
   EMBEDDING_MODEL_TYPE=openai
   ```

3. **资源要求**：
   - CPU: 4核以上
   - RAM: 8GB以上
   - 磁盘: 取决于文档数量（每1000个文档约100MB）

### 12.3 监控

1. **指标监控**：
   - 查询QPS
   - 平均响应时间
   - 检索准确率

2. **告警**：
   - 响应时间超过阈值
   - 错误率超过阈值
   - 磁盘空间不足

## 13. 版本历史

### v1.0 (2026-08-18)
- ✅ 初始版本发布
- ✅ 支持PDF、TXT、DOCX、MD格式
- ✅ ChromaDB向量存储
- ✅ OpenAI和HuggingFace Embedding
- ✅ 相似性搜索和MMR搜索
- ✅ 完整的REST API
- ✅ 文档和示例代码

### 计划中的v2.0
- [ ] 混合检索（BM25 + 向量）
- [ ] Cross-Encoder重排序
- [ ] 多模态支持（图像、音频）
- [ ] 查询扩展
- [ ] 评估框架

---

**文档版本**: v1.0  
**最后更新**: 2026-08-18  
**作者**: Multi-Agent Platform Team  
**状态**: ✅ 已实现并测试通过
