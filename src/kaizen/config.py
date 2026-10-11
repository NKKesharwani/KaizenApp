from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord configuration
    DISCORD_TOKEN: str = ""
    DISCORD_GUILD_ID: int | None = None

    # OpenAI-compatible local FreeLLM API configuration
    BASE_URL: str = "http://localhost:3001/v1"
    API_KEY: str = Field(
        default="",
        validation_alias=AliasChoices(
            "API_KEY", "LLM_API_KEY", "FREELLM_API_KEY", "GEMINI_API_KEY"
        ),
    )
    MODEL: str = Field(
        default="",
        validation_alias=AliasChoices(
            "MODEL", "LLM_MODEL", "FREELLM_MODEL", "GEMINI_MODEL"
        ),
    )

    # Database
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'kaizen.db'}"

    # Web Dashboard
    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = 8001

    # Game Loop / Schedule
    DAILY_RESET_HOUR: int = 0
    DAILY_REMINDER_HOUR: int = 9
    TIMEZONE: str = "UTC"


settings = Settings()
