import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./ct200.db")
    generated_store_path: Path = Path(
        os.getenv("GENERATED_STORE_PATH", "./generated_store")
    )
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or None
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))


settings = Settings()
