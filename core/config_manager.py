"""
Configuration management for per-domain settings.
Stub implementation - to be filled with actual logic.
"""

import json
from pathlib import Path

CONFIGS_DIR = Path("configs")


def load_config(domain: str) -> dict:
    """
    Load configuration for a specific domain.
    Args:
        domain: Domain name (e.g., "32status.ru")
    Returns:
        dict with configuration (selectors, settings)
    """
    config_path = CONFIGS_DIR / f"{domain}.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_config(domain: str, config: dict):
    """
    Save configuration for a specific domain.
    Args:
        domain: Domain name
        config: Configuration dictionary to save
    """
    CONFIGS_DIR.mkdir(exist_ok=True)
    config_path = CONFIGS_DIR / f"{domain}.json"
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)