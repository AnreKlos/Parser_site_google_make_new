"""Centralized settings — all env vars and magic numbers from one place."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    root_dir: Path = Path(__file__).parent
    data_dir: Path = Path(__file__).parent / "data"
    public_dir: Path = Path(__file__).parent / "public"

    # Database
    db_path: str = ""  # computed in model_post_init if empty
    database_url: str = ""  # computed in model_post_init for Alembic

    # ------------------------------------------------------------------
    # OpenRouter (LLM)
    # ------------------------------------------------------------------
    openrouter_api_key: str = ""
    openrouter_url: str = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_model: str = "openai/gpt-4o-mini"
    request_timeout: int = 35
    max_json_retries: int = 2

    # ------------------------------------------------------------------
    # Block visibility thresholds
    # ------------------------------------------------------------------
    min_gallery_photos: int = 3
    min_team_items: int = 1
    min_hero_photos: int = 1

    # Neuralsync (external project for site deployment, optional)
    neuralsync_root: str = ""

    def model_post_init(self, __context) -> None:
        if not self.db_path:
            object.__setattr__(self, "db_path", str(self.data_dir / "leads.db"))
        if not self.database_url:
            object.__setattr__(
                self,
                "database_url",
                f"sqlite+aiosqlite:///{self.db_path}",
            )
        if not self.neuralsync_root:
            # Fallback: sibling directory «neuralsync» in the same parent
            object.__setattr__(
                self,
                "neuralsync_root",
                str(self.root_dir.parent / "neuralsync"),
            )


settings = Settings()
