import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    langchain_tracing_v2: bool = True
    langchain_api_key: str | None = None
    langchain_project: str = "search_agent"

    postgres_dsn: str = "postgresql://postgres:123456@localhost:5432/search_agent"

    bge_embedder_path: str = "D:/hf_models/BAAI/bge-m3"
    bge_reranker_path: str = "D:/hf_models/BAAI/bge-reranker-v2-m3"

    metaso_api_key: str = ""

    memory_retrieval_limit: int = 3
    memory_min_score: float = 0.7

    # Circuit breaker
    cb_llm_failure_threshold: int = 3
    cb_llm_recovery_timeout: int = 30
    cb_scraper_failure_threshold: int = 5
    cb_scraper_recovery_timeout: int = 60

    log_level: str = "DEBUG"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()

# 注入 os.environ 供 LangSmith / LangChain auto-instrumentation 检测
for _key, _val in {
    "LANGCHAIN_TRACING_V2": str(settings.langchain_tracing_v2).lower(),
    "LANGCHAIN_API_KEY": settings.langchain_api_key or "",
    "LANGCHAIN_PROJECT": settings.langchain_project,
}.items():
    if _val:
        os.environ.setdefault(_key, _val)
