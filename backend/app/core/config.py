import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "Multi-Agent Platform")
    APP_VERSION: str = os.getenv("APP_VERSION", "1.0.0")
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"
    API_V1_PREFIX: str = os.getenv("API_V1_PREFIX", "/api/v1")
    
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/multi_agent")
    DATABASE_ECHO: bool = os.getenv("DATABASE_ECHO", "False").lower() == "true"
    
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4")
    DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", "0.7"))
    
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
    
    BACKEND_CORS_ORIGINS: list = os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:3000").split(",")
    
    BCRYPT_ROUNDS: int = int(os.getenv("BCRYPT_ROUNDS", "12"))
    
    CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
    CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))
    
    # Memory settings
    MEMORY_SHORT_TERM_WINDOW_SIZE: int = int(os.getenv("MEMORY_SHORT_TERM_WINDOW_SIZE", "5"))
    MEMORY_LONG_TERM_SUMMARY_INTERVAL: int = int(os.getenv("MEMORY_LONG_TERM_SUMMARY_INTERVAL", "10"))
    MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH: int = int(os.getenv("MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH", "500"))
    MEMORY_PERSISTENCE_ENABLED: bool = os.getenv("MEMORY_PERSISTENCE_ENABLED", "True").lower() == "true"

    # --- Enterprise memory (Phase 1) ---
    # 短期记忆存储后端: auto(优先Redis,失败降级内存) / redis / memory
    MEMORY_SHORT_TERM_STORE: str = os.getenv("MEMORY_SHORT_TERM_STORE", "auto")
    # 短期记忆 Redis 键 TTL（秒）
    MEMORY_SHORT_TERM_TTL: int = int(os.getenv("MEMORY_SHORT_TERM_TTL", "7200"))
    # 是否启用异步 consolidation（记忆提取/摘要）
    MEMORY_CONSOLIDATION_ENABLED: bool = os.getenv("MEMORY_CONSOLIDATION_ENABLED", "True").lower() == "true"
    # consolidation 每批处理的消息数
    MEMORY_CONSOLIDATION_BATCH_SIZE: int = int(os.getenv("MEMORY_CONSOLIDATION_BATCH_SIZE", "5"))
    # 持久化写入失败重试次数
    MEMORY_PERSISTENCE_RETRY: int = int(os.getenv("MEMORY_PERSISTENCE_RETRY", "3"))
    # 是否在上下文中注入相关记忆条目（跨会话混合检索）
    MEMORY_RETRIEVAL_ENABLED: bool = os.getenv("MEMORY_RETRIEVAL_ENABLED", "True").lower() == "true"
    # 上下文注入的相关记忆条数上限
    MEMORY_RETRIEVAL_TOP_K: int = int(os.getenv("MEMORY_RETRIEVAL_TOP_K", "3"))
    # 记忆衰减（遗忘机制）
    MEMORY_DECAY_ENABLED: bool = os.getenv("MEMORY_DECAY_ENABLED", "True").lower() == "true"
    # 每日衰减系数（指数衰减 rate）
    MEMORY_DECAY_RATE: float = float(os.getenv("MEMORY_DECAY_RATE", "0.02"))
    # 强度低于该阈值的记忆将被归档（审计保留）
    MEMORY_DECAY_ARCHIVE_BELOW: float = float(os.getenv("MEMORY_DECAY_ARCHIVE_BELOW", "0.1"))
    # 定时衰减周期（秒，Celery beat）
    MEMORY_DECAY_INTERVAL_SECONDS: int = int(os.getenv("MEMORY_DECAY_INTERVAL_SECONDS", "21600"))
    # 向量语义检索（基于 ChromaDB，懒加载，不可用自动降级）
    MEMORY_VECTOR_ENABLED: bool = os.getenv("MEMORY_VECTOR_ENABLED", "True").lower() == "true"
    # 检索候选集规模上限（先按强度取前 N，向量命中的条目额外召回）
    MEMORY_RETRIEVAL_CANDIDATE_LIMIT: int = int(os.getenv("MEMORY_RETRIEVAL_CANDIDATE_LIMIT", "500"))
    # 单个会话 event 记忆保留上限（超出部分归档，防膨胀）
    MEMORY_EVENT_MAX_PER_SESSION: int = int(os.getenv("MEMORY_EVENT_MAX_PER_SESSION", "100"))

    # --- Enterprise RAG (Phase 1) ---
    # 是否启用 RAG 服务
    RAG_ENABLED: bool = os.getenv("RAG_ENABLED", "True").lower() == "true"
    # ChromaDB 持久化目录（本地持久化客户端）
    RAG_PERSIST_DIRECTORY: str = os.getenv("RAG_PERSIST_DIRECTORY", "./chroma_db")
    # 多租户 collection 前缀，每个用户独立 collection：rag_{user_id_hex}
    RAG_COLLECTION_PREFIX: str = os.getenv("RAG_COLLECTION_PREFIX", "rag_")
    # 文档切块参数
    RAG_CHUNK_SIZE: int = int(os.getenv("RAG_CHUNK_SIZE", "1000"))
    RAG_CHUNK_OVERLAP: int = int(os.getenv("RAG_CHUNK_OVERLAP", "200"))
    # 检索参数
    RAG_RETRIEVAL_K: int = int(os.getenv("RAG_RETRIEVAL_K", "5"))
    # 默认检索策略: hybrid(BM25+向量+RRF) / similarity / mmr / score
    RAG_SEARCH_TYPE: str = os.getenv("RAG_SEARCH_TYPE", "hybrid")
    # --- Enterprise RAG (Phase 2): 混合检索 ---
    # 词法 BM25 检索是否启用（hybrid 策略需要）
    RAG_LEXICAL_ENABLED: bool = os.getenv("RAG_LEXICAL_ENABLED", "True").lower() == "true"
    # 每路检索候选数 = k * 该系数（融合前各自放大召回）
    RAG_HYBRID_FETCH_MULTIPLIER: int = int(os.getenv("RAG_HYBRID_FETCH_MULTIPLIER", "3"))
    # RRF 常数（融合平滑系数，经典取值 60）
    RAG_HYBRID_RRF_K: int = int(os.getenv("RAG_HYBRID_RRF_K", "60"))
    # --- Enterprise RAG (Phase 2): 语义缓存 ---
    # 是否缓存 查询→答案（每用户作用域）
    RAG_CACHE_ENABLED: bool = os.getenv("RAG_CACHE_ENABLED", "True").lower() == "true"
    # 缓存 TTL 兜底（秒；知识库变更时也会事件失效）
    RAG_CACHE_TTL_SECONDS: int = int(os.getenv("RAG_CACHE_TTL_SECONDS", "3600"))
    # 每用户缓存条目上限（LRU 裁剪）
    RAG_CACHE_MAX_ENTRIES_PER_USER: int = int(os.getenv("RAG_CACHE_MAX_ENTRIES_PER_USER", "200"))
    # --- Enterprise RAG (Phase 3): 两阶段重排 ---
    # 是否启用 LLM 点级重排（第二段）。仅当同时配置了 OPENAI_API_KEY 才实际生效
    RAG_RERANK_ENABLED: bool = os.getenv("RAG_RERANK_ENABLED", "True").lower() == "true"
    # 第一阶段召回放大系数：stage1_k = min(k * 该系数, 上限)，重排后再截断到 k
    RAG_RERANK_CANDIDATE_MULTIPLIER: int = int(os.getenv("RAG_RERANK_CANDIDATE_MULTIPLIER", "3"))
    # 送入重排器候选上限（防 prompt 过长 / 成本失控）
    RAG_RERANK_MAX_CANDIDATES: int = int(os.getenv("RAG_RERANK_MAX_CANDIDATES", "30"))
    # 重排 prompt 中单文档内容截断长度
    RAG_RERANK_MAX_DOC_CHARS: int = int(os.getenv("RAG_RERANK_MAX_DOC_CHARS", "600"))
    # Embedding 后端: openai / huggingface
    RAG_EMBEDDING_MODEL_TYPE: str = os.getenv("RAG_EMBEDDING_MODEL_TYPE", "openai")
    RAG_EMBEDDING_MODEL_NAME: str = os.getenv("RAG_EMBEDDING_MODEL_NAME", "")
    # 上传限制
    RAG_MAX_FILE_SIZE_MB: int = int(os.getenv("RAG_MAX_FILE_SIZE_MB", "20"))
    RAG_ALLOWED_EXTENSIONS: list = os.getenv("RAG_ALLOWED_EXTENSIONS", ".pdf,.txt,.docx,.doc,.md").split(",")
    # 分页默认值
    RAG_DOCS_PAGE_SIZE: int = int(os.getenv("RAG_DOCS_PAGE_SIZE", "20"))


settings = Settings()
