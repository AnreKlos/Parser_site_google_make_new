"""Bridge configuration from environment variables."""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bridge_port: int = 8765
    bridge_host: str = "127.0.0.1"


bridge_settings = BridgeSettings()

BRIDGE_HOST = bridge_settings.bridge_host
BRIDGE_PORT = bridge_settings.bridge_port
