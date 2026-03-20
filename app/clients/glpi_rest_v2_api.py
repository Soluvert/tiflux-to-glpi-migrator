from __future__ import annotations

from typing import Any

import httpx


class GlpiRestV2ApiClient:
    """
    Cliente REST v2 do GLPI (apirest.php/v2/...).
    Nesta iteração, serve como base para evoluir a importação com idempotencia.
    """

    def __init__(self, *, base_url: str, api_token: str | None):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        headers = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=httpx.Timeout(30.0, connect=15.0))

    def close(self) -> None:
        self._client.close()

    def healthcheck(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

