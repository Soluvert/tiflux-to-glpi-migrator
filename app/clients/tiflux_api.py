from __future__ import annotations

import json
import random
import time
import datetime as dt
from typing import Any

import httpx
from loguru import logger

from ..schemas.tiflux import EndpointProbeResult


class TifluxApiClient:
    def __init__(self, *, base_url: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset_epoch: float | None = None
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_token}"},
            timeout=httpx.Timeout(30.0, connect=15.0),
        )

    @property
    def client(self) -> httpx.Client:
        return self._client

    def close(self) -> None:
        self._client.close()

    def _parse_reset_epoch(self, reset_value: str | None) -> float | None:
        if not reset_value:
            return None
        s = reset_value.strip()
        if not s:
            return None

        # Alguns provedores usam epoch seconds.
        if s.isdigit():
            try:
                return float(s)
            except ValueError:
                return None

        # Tiflux documenta datetime.
        try:
            parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.timestamp()
        except Exception:
            return None

    def _update_rate_limit_state(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("RateLimit-Remaining")
        reset = resp.headers.get("RateLimit-Reset")

        if remaining is not None and remaining.isdigit():
            self._rate_limit_remaining = int(remaining)
        else:
            self._rate_limit_remaining = None

        self._rate_limit_reset_epoch = self._parse_reset_epoch(reset)

    def _respect_rate_limit_before_request(self) -> None:
        if self._rate_limit_remaining is None or self._rate_limit_reset_epoch is None:
            return

        if self._rate_limit_remaining > 0:
            return

        now = time.time()
        wait_s = self._rate_limit_reset_epoch - now
        if wait_s > 0:
            # pequeno buffer de seguranca.
            wait_s += 0.2
            logger.warning(f"RateLimit-Remaining=0. Waiting until reset: {wait_s:.2f}s")
            time.sleep(wait_s)

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        # Rate limit oficial costuma ser 120 req/min.
        # Faz retry com backoff e respeita Retry-After em caso de 429.
        max_attempts = 8
        for attempt in range(1, max_attempts + 1):
            self._respect_rate_limit_before_request()
            try:
                resp = self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt >= max_attempts:
                    raise
                wait_s = min(10.0, 0.5 * (2 ** (attempt - 1)))
                wait_s += random.uniform(0.0, 0.3)
                logger.warning(f"Tiflux network retry attempt={attempt} wait={wait_s:.2f}s err={e}")
                time.sleep(wait_s)
                continue

            self._update_rate_limit_state(resp)

            if resp.status_code in (429, 401):
                if attempt >= max_attempts:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                if resp.status_code == 401:
                    # Tiflux retorna 401 quando rate-limit é excedido de forma persistente
                    wait_s = min(120.0, 30.0 * (2 ** (attempt - 1)))
                elif retry_after and retry_after.isdigit():
                    wait_s = float(retry_after)
                else:
                    wait_s = min(30.0, 1.0 * (2 ** (attempt - 1)))
                # fallback para RateLimit-Reset se Retry-After nao vier
                if not (retry_after and retry_after.isdigit()) and self._rate_limit_reset_epoch:
                    delta = self._rate_limit_reset_epoch - time.time()
                    if delta > 0:
                        wait_s = max(wait_s, delta)
                wait_s += random.uniform(0.0, 0.4)
                logger.warning(f"Tiflux rate-limit {resp.status_code} attempt={attempt} wait={wait_s:.2f}s")
                time.sleep(wait_s)
                continue

            resp.raise_for_status()
            return resp.json()

        raise RuntimeError("Unexpected retry loop termination")

    def get_json_with_headers(self, path: str, *, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        """Like get_json but also returns response headers (useful for x-total-items)."""
        max_attempts = 8
        for attempt in range(1, max_attempts + 1):
            self._respect_rate_limit_before_request()
            try:
                resp = self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt >= max_attempts:
                    raise
                wait_s = min(10.0, 0.5 * (2 ** (attempt - 1)))
                wait_s += random.uniform(0.0, 0.3)
                logger.warning(f"Tiflux network retry attempt={attempt} wait={wait_s:.2f}s err={e}")
                time.sleep(wait_s)
                continue

            self._update_rate_limit_state(resp)

            if resp.status_code in (429, 401):
                if attempt >= max_attempts:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                if resp.status_code == 401:
                    wait_s = min(120.0, 30.0 * (2 ** (attempt - 1)))
                elif retry_after and retry_after.isdigit():
                    wait_s = float(retry_after)
                else:
                    wait_s = min(30.0, 1.0 * (2 ** (attempt - 1)))
                if not (retry_after and retry_after.isdigit()) and self._rate_limit_reset_epoch:
                    delta = self._rate_limit_reset_epoch - time.time()
                    if delta > 0:
                        wait_s = max(wait_s, delta)
                wait_s += random.uniform(0.0, 0.4)
                logger.warning(f"Tiflux rate-limit {resp.status_code} attempt={attempt} wait={wait_s:.2f}s")
                time.sleep(wait_s)
                continue

            resp.raise_for_status()
            return resp.json(), dict(resp.headers)

        raise RuntimeError("Unexpected retry loop termination")

    def probe(self, path: str, *, params: dict[str, Any] | None = None) -> EndpointProbeResult:
        try:
            resp = self._client.get(path, params=params)
        except Exception as e:  # pragma: no cover
            logger.warning(f"Probe network error for {path}: {e}")
            return EndpointProbeResult(
                path=path,
                ok=False,
                status_code=0,
                unauthorized=False,
                forbidden=False,
                not_found=False,
                content_type=None,
                response_preview=str(e),
            )

        status = resp.status_code
        content_type = resp.headers.get("content-type")
        ok = status in (200, 201, 202)
        unauthorized = status == 401
        forbidden = status == 403
        not_found = status == 404

        preview: Any | None = None
        if content_type and "application/json" in content_type:
            try:
                data = resp.json()
                preview = data if isinstance(data, (dict, list)) else str(data)
            except json.JSONDecodeError:
                preview = resp.text[:500]
        else:
            preview = resp.text[:500] if resp.text else None

        # Reduz o tamanho para log/report.
        if isinstance(preview, (dict, list)):
            preview = preview if len(str(preview)) <= 20_000 else str(preview)[:20_000]

        return EndpointProbeResult(
            path=path,
            method="GET",
            status_code=status,
            ok=ok,
            unauthorized=unauthorized,
            forbidden=forbidden,
            not_found=not_found,
            content_type=content_type,
            response_preview=preview,
        )

