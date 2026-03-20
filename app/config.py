from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    tiflux_base_url: str
    tiflux_api_token: str

    glpi_base_url: str
    glpi_user: str
    glpi_pass: str

    glpi_user_token: str | None
    glpi_app_token: str | None

    glpi_db_host: str
    glpi_db_port: int
    glpi_db_name: str
    glpi_db_user: str
    glpi_db_password: str

    glpi_rest_legacy_init_path: str
    glpi_rest_v2_path: str
    glpi_api_token_v2: str | None

    data_dir: str


def load_settings(dotenv_path: str | None = None) -> Settings:
    if dotenv_path is None:
        cwd_candidate = Path(os.getcwd()) / ".env"
        pkg_candidate = Path(__file__).resolve().parents[1] / ".env"
        if cwd_candidate.exists():
            dotenv_path = str(cwd_candidate)
        else:
            dotenv_path = str(pkg_candidate)
    load_dotenv(dotenv_path=dotenv_path, override=False)

    def req(name: str) -> str:
        val = os.getenv(name)
        if not val:
            raise RuntimeError(f"Missing required env var: {name}")
        return val

    data_dir = os.getenv("MIGRATOR_DATA_DIR", "data")
    return Settings(
        # Obrigatorio para falar com o Tiflux.
        tiflux_base_url=req("TIFLUX_BASE_URL").rstrip("/"),
        tiflux_api_token=req("TIFLUX_API_TOKEN"),
        # Defaults seguros para GLPI homologacao via docker-compose.
        glpi_base_url=os.getenv("GLPI_BASE_URL", "http://localhost:8080").rstrip("/"),
        glpi_user=os.getenv("GLPI_USER", "glpi"),
        glpi_pass=os.getenv("GLPI_PASS", "glpi"),
        glpi_user_token=os.getenv("GLPI_USER_TOKEN") or None,
        glpi_app_token=os.getenv("GLPI_APP_TOKEN") or None,
        glpi_db_host=os.getenv("GLPI_DB_HOST", "db"),
        glpi_db_port=int(os.getenv("GLPI_DB_PORT", "3306")),
        glpi_db_name=os.getenv("GLPI_DB_NAME", "glpi"),
        glpi_db_user=os.getenv("GLPI_DB_USER", "glpi"),
        glpi_db_password=os.getenv("GLPI_DB_PASSWORD", "glpi"),
        glpi_rest_legacy_init_path=os.getenv("GLPI_REST_LEGACY_INIT_PATH", "/apirest.php/initSession"),
        glpi_rest_v2_path=os.getenv("GLPI_REST_V2_PATH", "/apirest.php/v2"),
        glpi_api_token_v2=os.getenv("GLPI_API_TOKEN_V2") or None,
        data_dir=data_dir,
    )

