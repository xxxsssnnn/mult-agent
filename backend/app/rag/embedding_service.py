"""Embedding服务 - 文本向量化"""

from typing import List, Optional
from langchain_openai import OpenAIEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings
from app.core.config import settings
import structlog

logger = structlog.get_logger(__name__)


class EmbeddingService:
    """
    Embedding服务
    
    支持多种Embedding模型：
    - OpenAI Embeddings（需要API Key）
    - HuggingFace本地模型（无需API Key）
    """
    
    def __init__(self, model_type: str = "openai", model_name: Optional[str] = None):
        """
        初始化Embedding服务
        
        Args:
            model_type: 模型类型 ('openai' 或 'huggingface')
            model_name: 具体模型名称（可选）
        """
        self.model_type = model_type
        self.model_name = model_name
        self.embeddings = None
        
        # 初始化embeddings
        self._initialize_embeddings()
        
        logger.info(
            "EmbeddingService initialized",
            model_type=model_type,
            model_name=model_name or self._get_default_model_name()
        )
    
    def _get_default_model_name(self) -> str:
        """获取默认模型名称"""
        if self.model_type == "openai":
            return "text-embedding-ada-002"
        elif self.model_type == "huggingface":
            return "sentence-transformers/all-MiniLM-L6-v2"
        else:
            return "unknown"
    
    def _initialize_embeddings(self):
        """初始化Embedding模型"""
        try:
            if self.model_type == "openai":
                self._init_openai_embeddings()
            elif self.model_type == "huggingface":
                self._init_huggingface_embeddings()
            else:
                raise ValueError(f"Unsupported model type: {self.model_type}")
        except Exception as e:
            logger.error("Failed to initialize embeddings", error=str(e))
            raise
    
    def _init_openai_embeddings(self):
        """初始化OpenAI Embeddings"""
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set, falling back to HuggingFace")
            self.model_type = "huggingface"
            self._init_huggingface_embeddings()
            return
        
        self.embeddings = OpenAIEmbeddings(
            model=self.model_name or "text-embedding-ada-002",
            openai_api_key=settings.OPENAI_API_KEY,
            chunk_size=1000  # 批量处理大小
        )
        logger.info("OpenAI Embeddings initialized")
    
    def _init_huggingface_embeddings(self):
        """初始化HuggingFace Embeddings"""
        model_name = self.model_name or "sentence-transformers/all-MiniLM-L6-v2"
        
        self.embeddings = HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={'device': 'cpu'},  # 使用CPU，如有GPU可改为'cuda'
            encode_kwargs={'normalize_embeddings': True}
        )
        logger.info(f"HuggingFace Embeddings initialized: {model_name}")
    
    async def embed_text(self, text: str) -> List[float]:
        """
        将单个文本转换为向量
        
        Args:
            text: 输入文本
            
        Returns:
            向量表示（浮点数列表）
        """
        if not self.embeddings:
            raise RuntimeError("Embeddings not initialized")
        
        try:
            embedding = self.embeddings.embed_query(text)
            logger.debug(
                "Text embedded successfully",
                text_length=len(text),
                embedding_dim=len(embedding)
            )
            return embedding
        except Exception as e:
            logger.error("Failed to embed text", error=str(e))
            raise
    
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        批量将文本转换为向量
        
        Args:
            texts: 文本列表
            
        Returns:
            向量列表
        """
        if not self.embeddings:
            raise RuntimeError("Embeddings not initialized")
        
        if not texts:
            return []
        
        try:
            embeddings = self.embeddings.embed_documents(texts)
            logger.info(
                "Texts embedded successfully",
                num_texts=len(texts),
                embedding_dim=len(embeddings[0]) if embeddings else 0
            )
            return embeddings
        except Exception as e:
            logger.error("Failed to embed texts", error=str(e))
            raise
    
    def get_embedding_dimension(self) -> int:
        """
        获取Embedding维度
        
        Returns:
            向量维度
        """
        if self.model_type == "openai":
            return 1536  # text-embedding-ada-002的维度
        elif self.model_type == "huggingface":
            # 不同模型维度不同，这里返回常见模型的维度
            if "MiniLM" in (self.model_name or ""):
                return 384
            elif "mpnet" in (self.model_name or ""):
                return 768
            else:
                return 384  # 默认值
        else:
            return 0
    
    def get_model_info(self) -> dict:
        """
        获取模型信息
        
        Returns:
            模型信息字典
        """
        return {
            "model_type": self.model_type,
            "model_name": self.model_name or self._get_default_model_name(),
            "embedding_dimension": self.get_embedding_dimension(),
            "requires_api_key": self.model_type == "openai"
        }
