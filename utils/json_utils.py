"""JSON parsing utilities: safe extraction from LLM output."""

import json
import re
from typing import Any, Dict, List, Optional


def extract_json_from_text(text: str) -> Optional[Any]:
    """Extract a JSON object/array from raw text, trying multiple strategies."""
    if text is None:
        return None
    candidate = text.strip()
    if not candidate:
        return None

    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        pass

    md_match = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if md_match:
        try:
            return json.loads(md_match.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass

    obj_match = re.search(r"\{[\s\S]*\}", candidate)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    arr_match = re.search(r"\[[\s\S]*\]", candidate)
    if arr_match:
        try:
            return json.loads(arr_match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def parse_raw_reviews(raw_reviews: Optional[str]) -> List[Dict[str, Any]]:
    """Parse JSON string of reviews into a list of dicts."""
    if not raw_reviews:
        return []
    try:
        parsed = json.loads(raw_reviews)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []

    out: List[Dict[str, Any]] = []
    for i, review in enumerate(parsed):
        if not isinstance(review, dict):
            continue
        from utils.text import compact_text

        text = compact_text(
            str(review.get("text") or review.get("review") or review.get("content") or "")
        )
        if not text or len(text) < 8:
            continue
        author = compact_text(
            str(review.get("author") or review.get("author_name") or review.get("name") or "")
        )
        out.append({"index": i, "author": author, "text": text, "rating": review.get("rating")})
    return out
