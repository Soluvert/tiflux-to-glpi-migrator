from __future__ import annotations

import re

_TAG_RE = re.compile(r"<\s*[a-zA-Z]+[^>]*>")


def contains_html(text: str) -> bool:
    return bool(_TAG_RE.search(text))

