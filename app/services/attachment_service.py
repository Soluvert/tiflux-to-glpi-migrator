from __future__ import annotations

import mimetypes
import os
import re
from collections.abc import Iterable
from typing import Any

import httpx
from loguru import logger

from ..utils.hashing import sha256_bytes
from ..utils.io import ensure_dir


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def _looks_like_blob_url(s: str) -> bool:
    low = s.lower()
    return "blob" in low or "attachment" in low or "/files/" in low or low.startswith("http")


def download_blobs_from_payload(
    *,
    http_client: httpx.Client,
    payload: Any,
    tiflux_base_url: str,
    blobs_dir: str,
    resource: str,
) -> int:
    ensure_dir(blobs_dir)
    urls: list[str] = []
    for s in _iter_strings(payload):
        if not _looks_like_blob_url(s):
            continue
        if _URL_RE.match(s):
            urls.append(s)
        elif s.startswith("/"):
            urls.append(tiflux_base_url.rstrip("/") + s)
    urls = list(dict.fromkeys(urls))

    downloaded = 0
    for url in urls[:200]:  # limit para nao explodir em APIs grandes
        try:
            resp = http_client.get(url)
            if resp.status_code != 200:
                continue
            content_type = resp.headers.get("content-type") or ""
            ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
            if not ext:
                # fallback por padrao de URL
                m = re.search(r"\.([a-zA-Z0-9]{2,5})(?:\?|$)", url)
                ext = ("." + m.group(1).lower()) if m else ""
            name = sha256_bytes(resp.content)[:16] + ext
            path = os.path.join(blobs_dir, name)
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(resp.content)
                downloaded += 1
        except Exception as e:  # pragma: no cover
            logger.warning(f"Blob download failed ({resource}): {e}")
    return downloaded

