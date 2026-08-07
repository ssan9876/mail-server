"""
Application settings, loaded from environment variables (12-factor).

Backed by pydantic-settings so every value is typed and validated at startup.
The same variable names are documented in `.env.example`.
"""
from functools import lru_cache
from typing import Literal

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# Settings that must be replaced with real secrets before a production boot.
_SECRET_SETTINGS = (
    "JWT_SECRET_KEY",
    "SECRETS_ENCRYPTION_KEY",
    "POSTGRES_PASSWORD",
    "POSTGRES_MAIL_PASSWORD",
    "ADMIN_PASSWORD",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- General ---
    ENVIRONMENT: Literal["development", "production"] = "production"
    MAIL_HOSTNAME: str = "mail.example.com"
    WEB_HOSTNAME: str = "admin.example.com"

    # --- PostgreSQL ---
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "mailserver"
    POSTGRES_USER: str = "mailserver"
    POSTGRES_PASSWORD: str = "changeme"
    POSTGRES_MAIL_USER: str = "mail_lookup"
    POSTGRES_MAIL_PASSWORD: str = "changeme"

    # --- Redis ---
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""

    # --- JWT / security ---
    JWT_SECRET_KEY: str = "changeme"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    SECRETS_ENCRYPTION_KEY: str = "changeme"

    # --- Bootstrap superadmin ---
    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = "changeme"

    # --- Cloudflare ---
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""

    # --- TLS / ACME ---
    ACME_EMAIL: str = "admin@example.com"
    ACME_STAGING: bool = False

    # --- Mail defaults ---
    DEFAULT_MAILBOX_QUOTA_MB: int = 2048
    MAILDIR_ROOT: str = "/maildata"
    # Shared volume where decrypted DKIM private keys are exported for Rspamd.
    DKIM_KEYS_PATH: str = "/dkim"

    # --- Rate limiting ---
    RATE_LIMIT_API_PER_MINUTE: int = 120
    RATE_LIMIT_LOGIN_ATTEMPTS: int = 5

    # --- CORS ---
    CORS_ORIGINS: str = ""

    # ------------------------------------------------------------------ #
    # Derived values
    # ------------------------------------------------------------------ #
    def _db_url(self, driver: str) -> str:
        """Build a DB URL with `URL.create`, which safely escapes credentials."""
        return URL.create(
            drivername=driver,
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        ).render_as_string(hide_password=False)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver) for the application."""
        return self._db_url("postgresql+asyncpg")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SYNC_DATABASE_URL(self) -> str:
        """Sync URL (psycopg2) — used for non-async tooling."""
        return self._db_url("postgresql+psycopg2")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def REDIS_URL(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @model_validator(mode="after")
    def _require_real_secrets_in_production(self) -> "Settings":
        """Refuse to boot a production deployment on placeholder credentials.

        Every secret defaults to "changeme" (and .env.example ships
        "changeme-..." values); running production with any of them left in
        place would mean forgeable JWTs, a guessable superadmin password, and
        DKIM keys encrypted with a known key.
        """
        if self.ENVIRONMENT != "production":
            return self
        weak = [
            name
            for name in _SECRET_SETTINGS
            if not getattr(self, name) or getattr(self, name).lower().startswith("changeme")
        ]
        if weak:
            raise ValueError(
                "Refusing to start in production with placeholder secrets: "
                f"{', '.join(weak)}. Generate real values (see .env.example)."
            )
        if len(self.JWT_SECRET_KEY) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters in production "
                "(generate with: openssl rand -hex 32)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


settings = get_settings()
