"""config_builder — сборка JSON-конфига для neuralsync + экспорт в JS."""

from config_builder.builder import build_config
from config_builder.cli import run_build, run_build_for_ui, main

__all__ = [
    "build_config",
    "main",
    "run_build",
    "run_build_for_ui",
]
