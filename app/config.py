"""إعدادات المشروع — يقرأ من ملف .env"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str
    admin_chat_id: int
    bot_username: str = "legal_consultation_bot"
    telegram_runtime_enabled: bool = True

    # OpenRouter (Kimi 2.5)
    openrouter_api_key: str = ""
    openrouter_model: str = "moonshotai/kimi-k2"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # Ollama
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_request_timeout_seconds: int = 240

    # MLX Local
    mlx_local_python_bin: str = "/Users/majd/Desktop/codex/qlora-m3-ultra/.venv/bin/python"
    mlx_local_model_path: str = "/Users/majd/Desktop/codex/qlora-m3-ultra/models/gemma-4-e2b-it-4bit"
    mlx_local_default_adapter_path: str = "/Users/majd/Desktop/codex/qlora-m3-ultra/adapters/gemma4-e2b-legal-modes-v3-resume-v1"
    mlx_local_routing_policy_path: str = "./deployment/mode_adapter_routing_v2.json"
    mlx_local_budget_policy_path: str = "./data/benchmarks/legal_modes_v1/generation_budget_policy.json"
    mlx_local_prompt_templates_dir: str = "./data/benchmarks/legal_modes_v1/prompt_templates"
    mlx_local_runner_script_path: str = "./scripts/mlx_local_generate.py"
    mlx_local_timeout_seconds: int = 480

    # OpenAI (Embeddings)
    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"
    embedding_batch_size: int = 64

    # Generation runtime defaults
    generation_provider_default: str = "openrouter"
    generation_temperature: float = 0.2
    generation_max_tokens: int = 1500

    # RAG
    similarity_threshold: float = 0.28
    top_k_results: int = 6
    chunk_size: int = 900
    chunk_overlap: int = 180
    knowledge_dir: str = "./documents/knowledge"
    structured_chunks_path: str = "./data/structured/chunks.jsonl"
    documents_sync_enabled: bool = True
    documents_sync_interval_seconds: int = 3
    official_sync_enabled: bool = True
    official_sync_interval_seconds: int = 86400
    official_sync_request_timeout_seconds: int = 120

    # Server
    webhook_url: str = ""
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    instance_label: str = "codex-legal-rag"
    admin_panel_enabled: bool = True
    admin_panel_password: str = ""
    runtime_settings_path: str = "./data/runtime/runtime_settings.json"

    # ChromaDB
    chroma_persist_dir: str = "./data/chromadb"
    chroma_collection: str = "saudi_legal_consultations"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
