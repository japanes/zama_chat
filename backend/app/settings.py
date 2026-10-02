"""Process settings from env / project-root .env.

Only single external things live here (Gateway URL and key, Jev model name, DB URL).
Everything people choose between and change often (LLM models, prices, which model
serves which role) is data in the DB, edited from the admin.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_json: bool = False
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://zama:zama@localhost:5442/zama"
    test_database_url: str = "postgresql+asyncpg://zama:zama@localhost:5442/zama_test"
    db_pool_size: int = 10

    # Vercel AI Gateway (LLM via OpenAI-compatible API + Jev via /v1/evaluate)
    ai_gateway_api_key: SecretStr = SecretStr("")
    ai_gateway_url: str = "https://ai-gateway.vercel.sh/v1"
    jev_model: str = "typesafe-ai/jev"
    # TODO(review): the Gateway catalog marks Jev as zdr="none"; the probe checks
    # whether requests with zeroDataRetention=true are accepted at all.
    jev_zero_data_retention: bool = True
    jev_timeout_s: float = 20.0

    # Fernet key for tokens / secrets stored in the DB
    secret_key: SecretStr = SecretStr("")

    public_api_url: str = "http://localhost:8090"
    admin_url: str = "http://localhost:3120"

    # Dialog
    lease_seconds: int = 90
    http_lease_wait_seconds: float = 30.0
    history_messages: int = 30
    turn_restart_limit: int = 2

    # Admin auth (stage 3)
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_from_name: str = "Zama Chat"
    smtp_use_tls: bool = True
    magic_link_dev_echo: bool = False
    session_days: int = 30
    magic_link_minutes: int = 15
    cookie_secure: bool = False

    # Instagram (stage 4): one Meta app for the whole system. For "Instagram API with
    # Instagram Login" these are the Instagram app id / secret from the app dashboard.
    meta_app_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    ig_webhook_verify_token: SecretStr = SecretStr("")
    ig_graph_url: str = "https://graph.instagram.com/v23.0"
    ig_oauth_url: str = "https://www.instagram.com/oauth/authorize"
    ig_token_url: str = "https://api.instagram.com/oauth/access_token"
    # TODO(review): the HUMAN_AGENT tag lets an operator answer up to 7 days after the
    # client's last message; Meta gives it only after a separate permission review
    ig_human_agent_tag: bool = False
    ig_timeout_s: float = 20.0
    # echo of an outgoing message is handled this much later, so the mid of our own
    # reply is saved first (the send job writes it after Graph API answers)
    ig_echo_delay_s: float = 5.0

    # Notifications (stage 4)
    telegram_api_url: str = "https://api.telegram.org"

    # Search (stage 5): Elasticsearch — the catalog by words; Qdrant — the catalog and the
    # knowledge base by meaning. Empty URL → that index is off (the bot searches without it).
    elasticsearch_url: str = "http://localhost:9201"
    elasticsearch_user: str = ""
    elasticsearch_password: SecretStr = SecretStr("")
    qdrant_url: str = "http://localhost:6334"
    qdrant_api_key: SecretStr = SecretStr("")
    # index / collection names start with it (one ES / Qdrant for several installs)
    search_index_prefix: str = "zama_"
    search_timeout_s: float = 10.0

    @property
    def is_dev(self) -> bool:
        return self.env != "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
