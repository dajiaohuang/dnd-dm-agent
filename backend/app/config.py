from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ENV = PROJECT_ROOT / ".env"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "dm_agent.db"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"


class Settings(BaseSettings):
    database_url: str = DEFAULT_DB_URL
    embedding_model: str = "BAAI/bge-m3"
    embedding_backend: str = "local_bge_m3"
    embedding_dim: int = 1024
    embedding_device: str = "auto"
    embedding_batch_size: int = 16
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    llm_model: str = "deepseek-chat"
    data_dir: Path = DEFAULT_DATA_DIR
    napcat_base_url: str = ""
    napcat_token: str = ""
    napcat_self_id: str = ""
    napcat_allowed_user_ids: str = ""
    napcat_dm_user_ids: str = ""
    napcat_campaign_id: str = "campaign_001"
    napcat_character_id: str = "char_001"
    napcat_require_group_at: bool = True
    attachment_max_bytes: int = 20 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=(PROJECT_ENV, ".env"), extra="ignore")


settings = Settings()
