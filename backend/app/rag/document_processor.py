"""文档处理模块 - 加载和分割文档"""

from typing import List, Optional
from pathlib import Path
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    Docx2txtLoader,
    UnstructuredMarkdownLoader,
)
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
import structlog

logger = structlog.get_logger(__name__)


class DocumentProcessor:
    """
    文档处理器
    
    负责加载各种格式的文档并分割成适合向量化的片段
    """
    
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        """
        初始化文档处理器
        
        Args:
            chunk_size: 每个文本块的大小（字符数）
            chunk_overlap: 文本块之间的重叠大小（字符数）
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        # 初始化文本分割器
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", " ", ""]
        )
        
        logger.info(
            "DocumentProcessor initialized",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
    
    async def load_document(self, file_path: str, file_type: Optional[str] = None) -> List[Document]:
        """
        加载单个文档
        
        Args:
            file_path: 文件路径
            file_type: 文件类型（可选，如果不提供则从扩展名推断）
            
        Returns:
            文档列表
            
        Raises:
            ValueError: 不支持的文件类型
            FileNotFoundError: 文件不存在
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # 确定文件类型
        if file_type is None:
            file_type = path.suffix.lower()
        
        # 根据文件类型选择加载器
        loader = self._get_loader(file_path, file_type)
        
        try:
            documents = loader.load()
            logger.info(
                "Document loaded successfully",
                file_path=file_path,
                file_type=file_type,
                num_documents=len(documents)
            )
            return documents
        except Exception as e:
            logger.error("Failed to load document", file_path=file_path, error=str(e))
            raise
    
    def _get_loader(self, file_path: str, file_type: str):
        """
        根据文件类型获取合适的加载器
        
        Args:
            file_path: 文件路径
            file_type: 文件类型（.pdf, .txt, .docx, .md等）
            
        Returns:
            LangChain文档加载器实例
        """
        if file_type == '.pdf':
            return PyPDFLoader(file_path)
        elif file_type == '.txt':
            return TextLoader(file_path, encoding='utf-8')
        elif file_type in ['.docx', '.doc']:
            return Docx2txtLoader(file_path)
        elif file_type == '.md':
            return UnstructuredMarkdownLoader(file_path)
        else:
            # 默认尝试文本加载器
            logger.warning(f"Unknown file type {file_type}, trying TextLoader")
            return TextLoader(file_path, encoding='utf-8')
    
    async def load_multiple_documents(self, file_paths: List[str]) -> List[Document]:
        """
        加载多个文档
        
        Args:
            file_paths: 文件路径列表
            
        Returns:
            所有文档的合并列表
        """
        all_documents = []
        
        for file_path in file_paths:
            try:
                documents = await self.load_document(file_path)
                all_documents.extend(documents)
            except Exception as e:
                logger.error(f"Failed to load {file_path}: {str(e)}")
                continue
        
        logger.info(
            "Multiple documents loaded",
            total_files=len(file_paths),
            successful=len(all_documents)
        )
        
        return all_documents
    
    async def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        分割文档为小块
        
        Args:
            documents: 原始文档列表
            
        Returns:
            分割后的文档片段列表
        """
        if not documents:
            return []
        
        # 使用文本分割器分割文档
        split_docs = self.text_splitter.split_documents(documents)
        
        logger.info(
            "Documents split into chunks",
            original_count=len(documents),
            split_count=len(split_docs),
            avg_chunk_size=sum(len(doc.page_content) for doc in split_docs) // len(split_docs) if split_docs else 0
        )
        
        return split_docs
    
    async def process_file(self, file_path: str, file_type: Optional[str] = None) -> List[Document]:
        """
        完整处理流程：加载 + 分割
        
        Args:
            file_path: 文件路径
            file_type: 文件类型（可选）
            
        Returns:
            分割后的文档片段列表
        """
        # 加载文档
        documents = await self.load_document(file_path, file_type)
        
        # 分割文档
        split_docs = await self.split_documents(documents)
        
        logger.info(
            "File processing completed",
            file_path=file_path,
            num_chunks=len(split_docs)
        )
        
        return split_docs
    
    async def process_directory(self, directory_path: str, file_types: Optional[List[str]] = None) -> List[Document]:
        """
        处理目录中的所有文档
        
        Args:
            directory_path: 目录路径
            file_types: 要处理的文件类型列表（如['.pdf', '.txt']），None表示处理所有支持的类型
            
        Returns:
            所有分割后的文档片段列表
        """
        dir_path = Path(directory_path)
        
        if not dir_path.exists() or not dir_path.is_dir():
            raise ValueError(f"Invalid directory: {directory_path}")
        
        # 确定要处理的文件类型
        if file_types is None:
            file_types = ['.pdf', '.txt', '.docx', '.doc', '.md']
        
        # 收集所有符合条件的文件
        files_to_process = []
        for file_type in file_types:
            files_to_process.extend(dir_path.glob(f"**/*{file_type}"))
        
        if not files_to_process:
            logger.warning("No files found to process", directory=directory_path)
            return []
        
        # 处理所有文件
        all_chunks = []
        for file_path in files_to_process:
            try:
                chunks = await self.process_file(str(file_path))
                all_chunks.extend(chunks)
            except Exception as e:
                logger.error(f"Failed to process {file_path}: {str(e)}")
                continue
        
        logger.info(
            "Directory processing completed",
            directory=directory_path,
            total_files=len(files_to_process),
            total_chunks=len(all_chunks)
        )
        
        return all_chunks
    
    def get_supported_formats(self) -> List[str]:
        """获取支持的文件格式列表"""
        return ['.pdf', '.txt', '.docx', '.doc', '.md']
