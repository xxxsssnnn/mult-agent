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


settings = Settings()
