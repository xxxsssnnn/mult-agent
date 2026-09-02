"""RAG功能使用示例"""

import asyncio
from uuid import uuid4
from app.rag import RAGAgent, DocumentProcessor, VectorStoreManager, EmbeddingService


async def demo_rag_basic():
    """演示基本的RAG功能"""
    print("=" * 80)
    print("演示1: 基本RAG功能")
    print("=" * 80)
    
    # 创建RAG Agent
    rag_agent = RAGAgent(
        agent_id=uuid4(),
        name="DemoRAGAgent",
        config={
            "retrieval_k": 3,
            "search_type": "similarity",
            "collection_name": "demo_collection",
            "persist_directory": "./chroma_db_demo"
        }
    )
    
    # 初始化
    await rag_agent.initialize()
    print("\n✓ RAG Agent初始化完成\n")
    
    # 检查知识库状态
    stats = await rag_agent.get_knowledge_base_stats()
    print(f"知识库统计:")
    print(f"  - 集合名称: {stats.get('collection_name')}")
    print(f"  - 文档数量: {stats.get('document_count', 0)}")
    print(f"  - Embedding模型: {stats.get('embedding_model')}")
    print(f"  - 向量维度: {stats.get('embedding_dimension')}\n")
    
    # 尝试查询（此时知识库为空）
    print("尝试查询空知识库...")
    result = await rag_agent.execute({
        "query": "什么是人工智能？",
        "k": 3
    })
    
    print(f"\n查询结果:")
    print(f"  - 成功: {result.get('success')}")
    print(f"  - 检索到的文档数: {result.get('num_retrieved', 0)}")
    if not result.get('success'):
        print(f"  - 错误: {result.get('error')}")
    
    print("\n说明: 由于知识库为空，需要先导入文档才能进行有效查询\n")


async def demo_document_processing():
    """演示文档处理功能"""
    print("=" * 80)
    print("演示2: 文档处理功能")
    print("=" * 80)
    
    # 创建文档处理器
    processor = DocumentProcessor(chunk_size=500, chunk_overlap=100)
    
    print(f"\n文档处理器配置:")
    print(f"  - 块大小: {processor.chunk_size} 字符")
    print(f"  - 重叠大小: {processor.chunk_overlap} 字符")
    print(f"  - 支持的格式: {processor.get_supported_formats()}\n")
    
    # 注意：这里只是演示API，实际使用时需要提供真实的文件路径
    print("示例代码:")
    print("""
# 处理单个文件
chunks = await processor.process_file("document.pdf")

# 处理多个文件
chunks = await processor.load_multiple_documents([
    "doc1.pdf",
    "doc2.txt",
    "doc3.docx"
])

# 处理整个目录
chunks = await processor.process_directory("./documents", file_types=['.pdf', '.txt'])
    """)
    
    print("\n✓ 文档处理器准备就绪\n")


async def demo_embedding_service():
    """演示Embedding服务"""
    print("=" * 80)
    print("演示3: Embedding服务")
    print("=" * 80)
    
    # 创建Embedding服务（使用HuggingFace，无需API Key）
    embedding_service = EmbeddingService(model_type="huggingface")
    
    model_info = embedding_service.get_model_info()
    print(f"\nEmbedding模型信息:")
    print(f"  - 模型类型: {model_info['model_type']}")
    print(f"  - 模型名称: {model_info['model_name']}")
    print(f"  - 向量维度: {model_info['embedding_dimension']}")
    print(f"  - 需要API Key: {model_info['requires_api_key']}\n")
    
    # 演示文本向量化
    sample_text = "这是一个测试文本，用于演示Embedding功能。"
    embedding = await embedding_service.embed_text(sample_text)
    
    print(f"文本向量化示例:")
    print(f"  - 输入文本长度: {len(sample_text)} 字符")
    print(f"  - 输出向量维度: {len(embedding)}")
    print(f"  - 向量前5个值: {embedding[:5]}\n")
    
    # 批量向量化
    texts = ["文本1", "文本2", "文本3"]
    embeddings = await embedding_service.embed_texts(texts)
    
    print(f"批量向量化示例:")
    print(f"  - 输入文本数量: {len(texts)}")
    print(f"  - 输出向量数量: {len(embeddings)}")
    print(f"  - 每个向量维度: {len(embeddings[0])}\n")
    
    print("✓ Embedding服务正常工作\n")


async def demo_vector_store():
    """演示向量存储功能"""
    print("=" * 80)
    print("演示4: 向量存储功能")
    print("=" * 80)
    
    # 创建Embedding服务
    embedding_service = EmbeddingService(model_type="huggingface")
    
    # 创建向量存储
    vector_store = VectorStoreManager(
        collection_name="demo_vectors",
        persist_directory="./chroma_db_demo",
        embedding_service=embedding_service
    )
    
    print(f"\n向量存储配置:")
    print(f"  - 集合名称: {vector_store.collection_name}")
    print(f"  - 持久化目录: {vector_store.persist_directory}")
    print(f"  - Embedding模型: {embedding_service.model_type}\n")
    
    # 获取统计信息
    stats = await vector_store.get_collection_stats()
    print(f"当前状态:")
    print(f"  - 文档数量: {stats['document_count']}")
    print(f"  - 向量维度: {stats['embedding_dimension']}\n")
    
    print("支持的操作:")
    for op in vector_store.get_supported_operations():
        print(f"  - {op}")
    
    print("\n示例代码:")
    print("""
# 添加文档
from langchain.schema import Document

docs = [
    Document(page_content="文档内容1", metadata={"source": "file1.pdf"}),
    Document(page_content="文档内容2", metadata={"source": "file2.pdf"})
]

ids = await vector_store.add_documents(docs)

# 相似性搜索
results = await vector_store.similarity_search("查询文本", k=5)

# MMR搜索（平衡相关性和多样性）
results = await vector_store.max_marginal_relevance_search("查询文本", k=5)

# 删除文档
await vector_store.delete_documents(ids)
    """)
    
    print("\n✓ 向量存储准备就绪\n")


async def demo_full_rag_workflow():
    """演示完整的RAG工作流程"""
    print("=" * 80)
    print("演示5: 完整RAG工作流程")
    print("=" * 80)
    
    print("\n完整的RAG工作流程包括以下步骤:\n")
    
    steps = [
        ("1. 文档准备", [
            "收集PDF、TXT、DOCX等格式的文档",
            "确保文档内容清晰、结构化"
        ]),
        ("2. 文档处理", [
            "使用DocumentProcessor加载文档",
            "自动分割成适当大小的文本块",
            "提取元数据（来源、页码等）"
        ]),
        ("3. 向量化", [
            "使用EmbeddingService将文本转换为向量",
            "支持OpenAI或HuggingFace模型",
            "批量处理提高效率"
        ]),
        ("4. 存储", [
            "将向量存入ChromaDB",
            "保存元数据和原文本",
            "支持持久化到磁盘"
        ]),
        ("5. 查询", [
            "用户提出问题",
            "问题向量化",
            "在向量数据库中搜索相似文档"
        ]),
        ("6. 检索", [
            "返回最相关的K个文档片段",
            "可选MMR策略平衡多样性和相关性",
            "构建上下文"
        ]),
        ("7. 生成", [
            "将上下文提供给LLM",
            "LLM基于上下文生成答案",
            "返回答案和引用来源"
        ])
    ]
    
    for step_name, details in steps:
        print(f"{step_name}:")
        for detail in details:
            print(f"  • {detail}")
        print()
    
    print("API端点:")
    print("  POST /api/v1/rag/ingest   - 上传并导入文档")
    print("  POST /api/v1/rag/query    - 查询知识库")
    print("  GET  /api/v1/rag/stats    - 查看统计信息")
    print("  DELETE /api/v1/rag/clear  - 清空知识库")
    print("  POST /api/v1/rag/demo     - 功能演示")
    print("  GET  /api/v1/rag/info     - 系统信息\n")


async def main():
    """运行所有演示"""
    print("\n" + "=" * 80)
    print("RAG功能演示套件")
    print("=" * 80 + "\n")
    
    try:
        # 演示1: 基本RAG功能
        await demo_rag_basic()
        
        # 演示2: 文档处理
        await demo_document_processing()
        
        # 演示3: Embedding服务
        await demo_embedding_service()
        
        # 演示4: 向量存储
        await demo_vector_store()
        
        # 演示5: 完整工作流程
        await demo_full_rag_workflow()
        
        print("=" * 80)
        print("所有演示完成！🎉")
        print("=" * 80)
        print("\n下一步:")
        print("1. 准备您的文档（PDF、TXT、DOCX等）")
        print("2. 使用 /api/v1/rag/ingest 上传文档")
        print("3. 使用 /api/v1/rag/query 查询知识库")
        print("4. 访问 http://localhost:8001/docs 查看完整API文档\n")
        
    except Exception as e:
        print(f"\n❌ 演示过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
