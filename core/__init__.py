"""Core module: scraping pipeline, detection, extraction."""
from core.scraper import run_parse, ParseOptions, ParseResult, extract_data, normalize_data, build_markdown
from core.auto_detector import auto_detect
from core.config_manager import load_config, save_config
from core.block_flags import compute_block_flags

__all__ = [
    "run_parse", "ParseOptions", "ParseResult", "extract_data",
    "normalize_data", "build_markdown", "auto_detect",
    "load_config", "save_config", "compute_block_flags",
]
