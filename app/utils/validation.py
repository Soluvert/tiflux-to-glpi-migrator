from __future__ import annotations

import re
from typing import Any


_HTML_TAG_RE = re.compile(r"<\s*([a-zA-Z]+)[^>]*>")


def safe_json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def looks_like_html(text: str) -> bool:
    return bool(_HTML_TAG_RE.search(text))

