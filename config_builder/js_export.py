"""Python-to-JS serialisation and validation via ``node --check``."""

import re
import subprocess
from pathlib import Path
from typing import Any


# ======================================================================
# JS literal serialisation
# ======================================================================


def slug_to_var_name(slug: str) -> str:
    """Convert a kebab-case slug to a camelCase JS variable name."""
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", slug) if p]
    if not parts:
        return "leadConfig"
    first = parts[0].lower()
    rest = [p[:1].upper() + p[1:] for p in parts[1:]]
    return f"{first}{''.join(rest)}Config"


def js_escape_string(value: str) -> str:
    """Escape a Python string for use as a single-quoted JS string literal."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\r", "\\r").replace("\n", "\\n")
    return f"'{escaped}'"


def to_js_literal(value: Any, indent: int = 0) -> str:
    """Recursively convert a Python value to a JS literal string."""
    space = "  " * indent
    next_space = "  " * (indent + 1)

    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return js_escape_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        parts = [to_js_literal(v, indent + 1) for v in value]
        return "[\n" + ",\n".join(f"{next_space}{p}" for p in parts) + f"\n{space}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for key, val in value.items():
            js_key = key if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", key) else js_escape_string(key)
            lines.append(f"{next_space}{js_key}: {to_js_literal(val, indent + 1)}")
        return "{\n" + ",\n".join(lines) + f"\n{space}}}"
    return js_escape_string(str(value))


def to_js_module(config: dict, slug: str) -> str:
    """Convert a Python config dict to an ES module string."""
    var_name = slug_to_var_name(slug)
    body = to_js_literal(config, indent=0)
    return f"export const {var_name} = {body};\n\nexport default {var_name};\n"


# ======================================================================
# Validation
# ======================================================================


def validate_js_with_node(path: Path) -> None:
    """Run ``node --check`` on *path*; raise on syntax error."""
    result = subprocess.run(
        ["node", "--check", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        raise RuntimeError(f"node --check failed for {path}:\n{output}")
