from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger


@dataclass(frozen=True)
class GlpiInstallerResult:
    ok: bool
    detail: str | None = None
    session_token: str | None = None


def _init_session(
    *,
    base_url: str,
    init_path: str,
    user: str,
    password: str,
    user_token: str | None,
    app_token: str | None,
    timeout: float = 20.0,
) -> tuple[bool, str | None]:
    url = base_url.rstrip("/") + "/" + init_path.lstrip("/")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if app_token:
        headers["App-Token"] = app_token

    if user_token:
        headers["Authorization"] = f"user_token {user_token}"
    else:
        auth_raw = f"{user}:{password}".encode("utf-8")
        auth_b64 = base64.b64encode(auth_raw).decode("ascii")
        headers["Authorization"] = f"Basic {auth_b64}"

    with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
        resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return False, None
        data: Any = resp.json()
        token = data.get("session_token")
        return (token is not None and isinstance(token, str) and bool(token)), token


def wait_for_glpi_and_validate_legacy_api(
    *,
    base_url: str,
    init_path: str,
    user: str,
    password: str,
    user_token: str | None,
    app_token: str | None,
    timeout_seconds: int = 180,
    poll_seconds: float = 3.0,
) -> GlpiInstallerResult:
    deadline = time.time() + timeout_seconds
    health_url = base_url.rstrip("/") + "/"

    last_err: str | None = None
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
                r = client.get(health_url)
                if r.status_code < 200 or r.status_code >= 400:
                    last_err = f"Health status {r.status_code}"
                    time.sleep(poll_seconds)
                    continue
        except Exception as e:  # pragma: no cover
            last_err = str(e)
            time.sleep(poll_seconds)
            continue

        ok, token = _init_session(
            base_url=base_url,
            init_path=init_path,
            user=user,
            password=password,
            user_token=user_token,
            app_token=app_token,
        )
        if ok:
            logger.info("GLPI legacy initSession OK")
            return GlpiInstallerResult(ok=True, detail="legacy api initSession ok", session_token=token)

        last_err = last_err or "initSession failed"
        time.sleep(poll_seconds)

    logger.warning(f"GLPI not ready: {last_err}")
    return GlpiInstallerResult(ok=False, detail=last_err)


def validate_legacy_session_permissions(
    *,
    base_url: str,
    session_token: str,
    app_token: str | None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """
    Testa endpoints read-only para confirmar que o token tem permissao.
    """
    headers: dict[str, str] = {"Content-Type": "application/json", "Session-Token": session_token}
    if app_token:
        headers["App-Token"] = app_token

    endpoints = {
        "getMyProfiles": "/apirest.php/getMyProfiles",
        "getMyEntities": "/apirest.php/getMyEntities",
    }

    results: dict[str, Any] = {}
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout)) as client:
        for name, path in endpoints.items():
            try:
                r = client.get(path, headers=headers)
                results[name] = {"status": r.status_code, "ok": r.status_code == 200}
            except Exception as e:  # pragma: no cover
                results[name] = {"status": None, "ok": False, "error": str(e)}

    return results


def probe_glpi_v2(
    *,
    base_url: str,
    v2_path: str,
    api_token_v2: str | None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """
    Probe simples de existencia/alcance da API v2.
    Requer apenas GET; nao cria oauth client.
    """
    url = base_url.rstrip("/") + "/" + v2_path.lstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_token_v2:
        headers["Authorization"] = f"Bearer {api_token_v2}"

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            r = client.get(url, headers=headers)
        return {"url": url, "status": r.status_code, "ok": r.status_code in (200, 401, 403)}
    except Exception as e:  # pragma: no cover
        return {"url": url, "status": None, "ok": False, "error": str(e)}


def list_glpi_itemtype_search_options(
    *,
    base_url: str,
    session_token: str,
    app_token: str | None,
    itemtype: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """
    Lista search options para um itemtype do GLPI (API legacy).
    """
    headers: dict[str, str] = {"Content-Type": "application/json", "Session-Token": session_token}
    if app_token:
        headers["App-Token"] = app_token

    path = f"/apirest.php/listSearchOptions/{itemtype}"
    url = base_url.rstrip("/") + path
    with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
        try:
            r = client.get(url, headers=headers)
            ok = r.status_code == 200
            return {"itemtype": itemtype, "status": r.status_code, "ok": ok, "payload_preview": r.text[:50_000]}
        except Exception as e:  # pragma: no cover
            return {"itemtype": itemtype, "status": None, "ok": False, "error": str(e)}

