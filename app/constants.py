from __future__ import annotations

RESOURCE_CANDIDATES: tuple[str, ...] = (
    "clients",
    "client_files",
    "contacts",
    "contracts",
    "addresses",
    "tables",
    "priorities",
    "technical_groups",
    "permission_groups",
    "tickets",
    "ticket_history",
    "stage_durations",
    "ticket_files",
    "chats",
)

# Subdir dentro de `data/raw/` para exemplos reais coletados na descoberta.
RAW_SAMPLES_DIR = "data/raw/_samples"

# Onde o manifesto (JSONL) será gravado dentro de `data_dir`.
RAW_MANIFEST_PATH = "raw/manifest.jsonl"

