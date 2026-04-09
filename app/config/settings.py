from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    classifier: Literal["keybert", "ollama"] = "keybert"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    request_timeout: int = 15
    max_redirects: int = 5
    max_retries: int = 3

    class Config:
        env_file = ".env"


settings = Settings()
