from __future__ import annotations

import base64
from typing import Any

import httpx
from loguru import logger


class GlpiLegacyApiClient:
    """Cliente REST legacy do GLPI (apirest.php)."""

    def __init__(
        self,
        *,
        base_url: str,
        user: str | None = None,
        password: str | None = None,
        user_token: str | None = None,
        app_token: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.user_token = user_token
        self.app_token = app_token
        self.session_token: str | None = None
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, connect=15.0),
        )

    def close(self) -> None:
        if self.session_token:
            try:
                self._client.get(
                    "/apirest.php/killSession",
                    headers=self._headers(),
                )
            except Exception:
                pass
        self._client.close()

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.session_token:
            h["Session-Token"] = self.session_token
        if self.app_token:
            h["App-Token"] = self.app_token
        return h

    def init_session(self) -> str:
        """Inicia sessão e retorna session_token."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.app_token:
            headers["App-Token"] = self.app_token

        if self.user_token:
            headers["Authorization"] = f"user_token {self.user_token}"
        elif self.user and self.password:
            creds = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        else:
            raise ValueError("Credentials required (user_token or user/password)")

        resp = self._client.get("/apirest.php/initSession", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        self.session_token = data.get("session_token")
        if not self.session_token:
            raise RuntimeError(f"initSession failed: {data}")
        logger.debug(f"GLPI session initialized: {self.session_token[:8]}...")

        # Enable recursive access to all sub-entities
        self._client.post(
            "/apirest.php/changeActiveEntities",
            headers=self._headers(),
            json={"entities_id": 0, "is_recursive": True},
        )

        return self.session_token

    def get_item(self, itemtype: str, item_id: int) -> dict[str, Any]:
        """Busca um item pelo ID."""
        resp = self._client.get(
            f"/apirest.php/{itemtype}/{item_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def get_items(
        self,
        itemtype: str,
        *,
        range_start: int = 0,
        range_end: int = 50,
        search_criteria: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Lista itens de um tipo."""
        params: dict[str, Any] = {
            "range": f"{range_start}-{range_end}",
        }
        if search_criteria:
            for i, crit in enumerate(search_criteria):
                for k, v in crit.items():
                    params[f"criteria[{i}][{k}]"] = v

        resp = self._client.get(
            f"/apirest.php/{itemtype}",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def search_items(
        self,
        itemtype: str,
        criteria: list[dict],
        forcedisplay: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Busca itens com critérios de pesquisa."""
        params: dict[str, Any] = {}
        for i, crit in enumerate(criteria):
            for k, v in crit.items():
                params[f"criteria[{i}][{k}]"] = v
        if forcedisplay:
            for i, fd in enumerate(forcedisplay):
                params[f"forcedisplay[{i}]"] = fd

        resp = self._client.get(
            f"/apirest.php/search/{itemtype}",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])

    def create_item(self, itemtype: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Cria um item no GLPI."""
        resp = self._client.post(
            f"/apirest.php/{itemtype}",
            headers=self._headers(),
            json={"input": payload},
        )
        resp.raise_for_status()
        result = resp.json()
        logger.debug(f"Created {itemtype}: {result}")
        return result

    def create_items(self, itemtype: str, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cria múltiplos itens no GLPI."""
        resp = self._client.post(
            f"/apirest.php/{itemtype}",
            headers=self._headers(),
            json={"input": payloads},
        )
        resp.raise_for_status()
        return resp.json()

    def update_item(self, itemtype: str, item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Atualiza um item no GLPI."""
        payload["id"] = item_id
        resp = self._client.put(
            f"/apirest.php/{itemtype}/{item_id}",
            headers=self._headers(),
            json={"input": payload},
        )
        resp.raise_for_status()
        return resp.json()

    def delete_item(self, itemtype: str, item_id: int, *, force_purge: bool = False) -> dict[str, Any]:
        """Deleta um item no GLPI."""
        params = {}
        if force_purge:
            params["force_purge"] = 1
        resp = self._client.delete(
            f"/apirest.php/{itemtype}/{item_id}",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def find_or_create_entity(self, name: str, parent_id: int = 0) -> int:
        """Busca entidade pelo nome ou cria se não existir."""
        try:
            results = self.search_items(
                "Entity",
                criteria=[
                    {"field": 1, "searchtype": "contains", "value": name},
                ],
                forcedisplay=[2],
            )
            for r in (results or []):
                found_name = (r.get("1") or "").strip().lower()
                target = name.strip().lower()
                # GLPI returns hierarchical names like "Root entity > Name"
                if found_name == target or found_name.endswith(f"> {target}"):
                    found_id = int(r.get("2") or r.get("id", 0))
                    if found_id:
                        return found_id
        except Exception as e:
            logger.warning(f"Entity search failed: {e}")

        result = self.create_item("Entity", {
            "name": name,
            "entities_id": parent_id,
        })
        return int(result.get("id", 0))

    def find_or_create_user(self, name: str, email: str | None = None) -> int:
        """Busca usuário pelo login ou cria se não existir."""
        login = name.lower().replace(" ", ".").split("@")[0][:50]
        try:
            results = self.search_items(
                "User",
                criteria=[
                    {"field": 1, "searchtype": "contains", "value": login},
                ],
                forcedisplay=[2],
            )
            if results:
                return int(results[0].get("2") or results[0].get("id", 0))
        except Exception as e:
            logger.warning(f"User search failed: {e}")

        parts = name.split()
        result = self.create_item("User", {
            "name": login,
            "realname": parts[-1] if parts else login,
            "firstname": parts[0] if len(parts) > 1 else "",
            "_useremails": [email] if email else [],
        })
        return int(result.get("id", 0))

    def find_or_create_category(
        self, name: str, entities_id: int = 0, *, parent_id: int = 0,
    ) -> int:
        """Busca categoria ITIL pelo nome (e pai) ou cria se não existir."""
        try:
            criteria = [
                {"field": 1, "searchtype": "equals", "value": name},
            ]
            if parent_id:
                criteria.append(
                    {"field": 3, "searchtype": "equals", "value": str(parent_id), "link": "AND"},
                )
            results = self.search_items(
                "ITILCategory",
                criteria=criteria,
                forcedisplay=[2],
            )
            if results:
                return int(results[0].get("2") or results[0].get("id", 0))
        except Exception as e:
            logger.warning(f"Category search failed: {e}")

        payload: dict[str, Any] = {
            "name": name,
            "entities_id": entities_id,
            "is_incident": 1,
            "is_request": 1,
            "is_recursive": 1,
        }
        if parent_id:
            payload["itilcategories_id"] = parent_id
        result = self.create_item("ITILCategory", payload)
        return int(result.get("id", 0))

    def create_ticket(self, payload: dict[str, Any]) -> int:
        """Cria um ticket no GLPI e retorna o ID."""
        result = self.create_item("Ticket", payload)
        return int(result.get("id", 0))

    def create_ticket_task(
        self,
        ticket_id: int,
        content: str,
        *,
        actiontime: int = 0,
        date: str | None = None,
        users_id_tech: int | None = None,
    ) -> int:
        """Cria um TicketTask vinculado a um ticket."""
        payload: dict[str, Any] = {
            "tickets_id": ticket_id,
            "content": content,
            "state": 2,  # Done
        }
        if actiontime:
            payload["actiontime"] = actiontime
        if date:
            payload["date"] = date
            payload["begin"] = date
        if users_id_tech:
            payload["users_id_tech"] = users_id_tech
        result = self.create_item("TicketTask", payload)
        return int(result.get("id", 0))

    def create_followup(self, ticket_id: int, content: str, *, date: str | None = None, is_private: bool = False, users_id: int | None = None) -> int:
        """Cria um ITILFollowup vinculado a um ticket."""
        payload: dict[str, Any] = {
            "items_id": ticket_id,
            "itemtype": "Ticket",
            "content": content,
            "is_private": 1 if is_private else 0,
        }
        if date:
            payload["date"] = date
            payload["date_creation"] = date
            payload["date_mod"] = date
        if users_id:
            payload["users_id"] = users_id
        result = self.create_item("ITILFollowup", payload)
        return int(result.get("id", 0))
