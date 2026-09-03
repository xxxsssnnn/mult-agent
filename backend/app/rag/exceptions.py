"""RAG 领域异常定义

API 层将领域异常映射为明确的 HTTP 状态码，取代旧的"吞异常返回 success:false"模式：
- UnsupportedFileTypeError -> 415
- FileTooLargeError      -> 413
- EmptyDocumentError     -> 422
- DocumentNotFoundError  -> 404
- RAGBackendError        -> 503（向量库/Embedding 等后端不可用）
"""


class RAGError(Exception):
    """RAG 领域异常基类"""


class UnsupportedFileTypeError(RAGError, ValueError):
    """不支持的文件类型"""


class FileTooLargeError(RAGError, ValueError):
    """文件超出大小上限"""


class EmptyDocumentError(RAGError, ValueError):
    """文档解析后为空（无有效文本内容）"""


class DocumentNotFoundError(RAGError):
    """文档不存在或不属于当前用户"""


class RAGBackendError(RAGError, RuntimeError):
    """底层后端（向量库 / Embedding / 解析器）不可用或处理失败"""
