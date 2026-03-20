from __future__ import annotations


def decide_entity_strategy(*, client_id: str, strategy: dict) -> dict:  # pragma: no cover
    # TODO: implementar estrategias clients_as_entities / suppliers / contacts_only.
    return {"client_id": client_id, "strategy": strategy}

