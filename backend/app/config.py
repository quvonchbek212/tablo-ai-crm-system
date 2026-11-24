import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from sqlalchemy.engine import URL

# Ensure .env is loaded into environment variables before Settings initializes
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(_env_path, override=False)


def _active_legacy_prefix() -> Optional[str]:
    """Resolve optional backwards-compatible customer-prefixed settings."""
    active = os.getenv("ACTIVE_DB", "").strip().upper()
    if active == "LIMSDB":
        active = "LIMS"
    return active if active in {"KIMYO", "LIMS", "ATIL"} else None


def _first_nonempty(*names: Optional[str]) -> Optional[str]:
    for name in names:
        if not name:
            continue
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _first_defined(*names: Optional[str], default: Optional[str] = None) -> Optional[str]:
    for name in names:
        if name and name in os.environ:
            return os.environ[name]
    return default


_LEGACY_PREFIX = _active_legacy_prefix()
_LEGACY_DB_HOST = f"{_LEGACY_PREFIX}_DB_REMOTE_HOST" if _LEGACY_PREFIX == "KIMYO" else (
    f"{_LEGACY_PREFIX}_DB_HOST" if _LEGACY_PREFIX else None
)

class Settings(BaseSettings):
    # A project-specific prefix prevents unrelated host variables such as
    # DEBUG=release from breaking Pydantic settings validation.
    model_config = SettingsConfigDict(
        env_file=_env_path,
        env_file_encoding="utf-8",
        env_prefix="TABLOAI_",
        extra="allow",
    )

    APP_NAME: str = "TabloAI"
    APP_VERSION: str = "2.0.0"
    WORKSPACE_MODE: str = os.getenv("WORKSPACE_MODE", "demo").lower()
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").lower()
    OPENAI_API_KEY: str = _first_nonempty("OPENAI_API_KEY", "open_ai_api_key", "OPEN_AI_API_KEY") or ""
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    OPENAI_TIMEOUT: float = float(os.getenv("OPENAI_TIMEOUT", "90"))
    OPENAI_TRANSCRIBE_MODEL: str = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-transcribe")
    OPENAI_VOICE_MODEL: str = os.getenv("OPENAI_VOICE_MODEL", "gpt-realtime-2.1-mini")
    GEMINI_API_KEY: str = _first_nonempty("GEMINI_API_KEY", "gemini_api_key") or ""
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    GEMINI_TIMEOUT: float = float(os.getenv("GEMINI_TIMEOUT", "90"))
    DEBUG: bool = os.getenv("APP_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
    
    # ACTIVE_DB is kept only for backwards compatibility with older prefixed
    # deployments. New/unknown databases should use the generic variables.
    ACTIVE_DB: str = os.getenv("ACTIVE_DB", "generic").lower()

    # Generic settings take precedence; the selected legacy prefix is fallback.
    SSH_HOST: Optional[str] = _first_nonempty(
        "SSH_HOST",
        f"{_LEGACY_PREFIX}_DB_SSH_HOST" if _LEGACY_PREFIX else None,
    )
    SSH_PORT: int = int(_first_nonempty(
        "SSH_PORT",
        f"{_LEGACY_PREFIX}_DB_SSH_PORT" if _LEGACY_PREFIX else None,
    ) or "22")
    SSH_USER: Optional[str] = _first_nonempty(
        "SSH_USER",
        "SSH_USERNAME",
        f"{_LEGACY_PREFIX}_DB_SSH_USERNAME" if _LEGACY_PREFIX else None,
        f"{_LEGACY_PREFIX}_DB_SSH_USER" if _LEGACY_PREFIX else None,
    )
    # Private keys must live outside the repository and be supplied explicitly.
    SSH_PKEY_PATH: Optional[str] = _first_nonempty(
        "SSH_PKEY_PATH",
        f"{_LEGACY_PREFIX}_DB_SSH_PKEY_PATH" if _LEGACY_PREFIX else None,
    )
    SSH_PKEY_PASSPHRASE: Optional[str] = _first_defined(
        "SSH_PKEY_PASSPHRASE",
        f"{_LEGACY_PREFIX}_DB_SSH_PKEY_PASSPHRASE" if _LEGACY_PREFIX else None,
    )

    # Database settings for any PostgreSQL source. No customer/database defaults.
    DB_HOST: Optional[str] = _first_nonempty("DB_HOST", "DB_REMOTE_HOST", _LEGACY_DB_HOST)
    DB_PORT: int = int(_first_nonempty(
        "DB_PORT",
        "DB_REMOTE_PORT",
        f"{_LEGACY_PREFIX}_DB_REMOTE_PORT" if _LEGACY_PREFIX == "KIMYO" else None,
        f"{_LEGACY_PREFIX}_DB_PORT" if _LEGACY_PREFIX else None,
    ) or "5432")
    DB_NAME: Optional[str] = _first_nonempty(
        "DB_NAME",
        f"{_LEGACY_PREFIX}_DB_NAME" if _LEGACY_PREFIX else None,
    )
    DB_USER: Optional[str] = _first_nonempty(
        "DB_USER",
        f"{_LEGACY_PREFIX}_DB_USER" if _LEGACY_PREFIX else None,
    )
    DB_PASSWORD: Optional[str] = _first_defined(
        "DB_PASSWORD",
        f"{_LEGACY_PREFIX}_DB_PASSWORD" if _LEGACY_PREFIX else None,
        default="",
    )
    DB_INCLUDE_SCHEMAS: str = _first_nonempty(
        "DB_INCLUDE_SCHEMAS",
        f"{_LEGACY_PREFIX}_DB_SCHEMA" if _LEGACY_PREFIX else None,
    ) or "public"

    # Default is None unless provided in env or UI
    DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL", None)

    @property
    def effective_database_url(self) -> Optional[str]:
        # If SSH is active, the database connection URL is generated dynamically via local forward port
        if self.SSH_HOST:
            return None
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.DB_HOST and self.DB_NAME:
            return URL.create(
                drivername="postgresql+psycopg2",
                username=self.DB_USER,
                password=self.DB_PASSWORD or None,
                host=self.DB_HOST,
                port=self.DB_PORT,
                database=self.DB_NAME,
            ).render_as_string(hide_password=False)
        return None
    
    # vLLM (OpenAI-Compatible) Configuration
    VLLM_BASE_URL: str = os.getenv(
        "VLLM_BASE_URL", 
        "http://127.0.0.1:8001/v1"
    )
    VLLM_API_KEY: str = os.getenv(
        "VLLM_API_KEY", 
        ""
    )
    VLLM_MODEL: str = os.getenv("VLLM_MODEL", "deepseek-r1:32b")
    VLLM_TIMEOUT: float = float(os.getenv("VLLM_TIMEOUT", "300.0"))
    
    # Self-Healing Retry Limit for SQL Generation
    MAX_SQL_RETRIES: int = int(os.getenv("MAX_SQL_RETRIES", "3"))

    # Persistent schema metadata is refreshed instead of being trusted forever.
    SCHEMA_CACHE_TTL_SECONDS: int = int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", "3600"))
    
    # CORS Origins
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000"
    ]

settings = Settings()
