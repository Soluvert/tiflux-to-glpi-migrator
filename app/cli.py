from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

import typer
from loguru import logger

from .config import load_settings
from .logging_config import init_logging
from .services.analysis_service import analyze_data
from .services.discovery_service import run_discovery
from .services.export_service import export_tiflux_raw
from .services.transform_service import transform_tiflux_data
from .services.import_service import import_to_glpi
from .services.reconciliation_service import reconcile
from .utils.io import ensure_dir, read_json
from .clients.glpi_installer import (
    validate_legacy_session_permissions,
    probe_glpi_v2,
    list_glpi_itemtype_search_options,
    wait_for_glpi_and_validate_legacy_api,
)


app = typer.Typer(add_completion=False)


def _data_dir(settings) -> str:
    return settings.data_dir


@app.command("discover-tiflux")
def cmd_discover_tiflux(
    resources: Optional[str] = typer.Option(
        None,
        help="Lista separada por vírgula (ex: clients,tickets). Se omitido, usa candidatos padrão.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Aumenta logs."),
):
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"), verbose=verbose)
    ensure_dir(settings.data_dir)

    res_list = resources.split(",") if resources else None
    run_discovery(
        tiflux_base_url=settings.tiflux_base_url,
        tiflux_api_token=settings.tiflux_api_token,
        data_dir=settings.data_dir,
        resources=res_list,
        verbose=verbose,
    )
    typer.echo("Discovery concluida.")


@app.command("export-tiflux")
def cmd_export_tiflux(
    resume: bool = typer.Option(False, "--resume", help="Retoma páginas já exportadas."),
    continue_on_error: bool = typer.Option(False, "--continue-on-error", help="Continua export mesmo com falhas."),
    download_blobs: bool = typer.Option(True, "--download-blobs/--no-download-blobs", help="Baixa anexos/blobs detectados."),
):
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))
    ensure_dir(settings.data_dir)

    caps_path = os.path.join(settings.data_dir, "processed", "tiflux_api_capabilities.json")
    if not os.path.exists(caps_path):
        raise typer.BadParameter("Capacidades não encontradas. Rode `discover-tiflux` antes.")

    caps = read_json(caps_path)
    discovered_base_url = caps.get("base_url") if isinstance(caps, dict) else None
    export_tiflux_raw(
        caps=caps,
        tiflux_base_url=discovered_base_url or settings.tiflux_base_url,
        tiflux_api_token=settings.tiflux_api_token,
        data_dir=settings.data_dir,
        resume=resume,
        continue_on_error=continue_on_error,
        download_blobs=download_blobs,
    )
    typer.echo("Export concluido.")


@app.command("analyze-data")
def cmd_analyze_data():
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))
    ensure_dir(settings.data_dir)
    analyze_data(data_dir=settings.data_dir)
    typer.echo("Análise concluída.")


@app.command("full-run")
def cmd_full_run(
    resume: bool = typer.Option(False, "--resume", help="Retoma exportação (Fase 2) quando possível."),
    continue_on_error: bool = typer.Option(False, "--continue-on-error", help="Continua mesmo com falhas."),
):
    # Ordem mínima funcional: discover -> export -> analyze.
    cmd_discover_tiflux()
    cmd_export_tiflux(resume=resume, continue_on_error=continue_on_error)
    cmd_analyze_data()
    typer.echo("Full-run (Fase 1-3) concluido.")


@app.command("install-glpi-hml")
def cmd_install_glpi_hml(
    timeout_seconds: int = typer.Option(180, help="Tempo maximo para esperar o GLPI ficar pronto."),
):
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))
    ensure_dir(settings.data_dir)

    compose_path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
    compose_path = os.path.abspath(compose_path)
    compose_dir = os.path.dirname(compose_path)

    logger.info("Subindo GLPI homologacao (Docker Compose)...")
    subprocess.run(
        ["docker", "compose", "-f", compose_path, "up", "-d"],
        cwd=compose_dir,
        check=False,
    )

    # Tenta instalar via CLI (nao-interativo). Se ja estiver instalado, o comando pode falhar e seguimos.
    glpi_cid = (
        subprocess.run(
            ["docker", "compose", "-f", compose_path, "ps", "-q", "glpi"],
            cwd=compose_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        .stdout.strip()
    )
    if glpi_cid:
        logger.info("Tentando instalar GLPI via bin/console db:install...")
        subprocess.run(
            [
                "docker",
                "exec",
                glpi_cid,
                "php",
                "/var/www/glpi/bin/console",
                "db:install",
                "-n",
                "-H",
                settings.glpi_db_host,
                "-P",
                str(settings.glpi_db_port),
                "-d",
                settings.glpi_db_name,
                "-u",
                settings.glpi_db_user,
                "-p",
                settings.glpi_db_password,
                "-f",
            ],
            cwd=compose_dir,
            check=False,
        )

    res = wait_for_glpi_and_validate_legacy_api(
        base_url=settings.glpi_base_url,
        init_path=settings.glpi_rest_legacy_init_path,
        user=settings.glpi_user,
        password=settings.glpi_pass,
        user_token=settings.glpi_user_token,
        app_token=settings.glpi_app_token,
        timeout_seconds=timeout_seconds,
    )

    reports_dir = os.path.join(settings.data_dir, "reports")
    ensure_dir(reports_dir)
    report_path = os.path.join(reports_dir, "glpi_bootstrap_report.md")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# GLPI bootstrap report\n\n")
        f.write(f"- ok: {res.ok}\n")
        f.write(f"- detail: {res.detail}\n")
        if res.session_token:
            f.write(f"- session_token: {res.session_token}\n")
        f.write(f"- glpi_base_url: {settings.glpi_base_url}\n")
        if res.ok and res.session_token:
            perms = validate_legacy_session_permissions(
                base_url=settings.glpi_base_url,
                session_token=res.session_token,
                app_token=settings.glpi_app_token,
            )
            f.write(f"\n## permissions\n")
            for k, v in perms.items():
                f.write(f"- {k}: {v}\n")

    if not res.ok:
        raise typer.Exit(code=1)

    typer.echo(f"GLPI homologacao pronta. Report: {report_path}")


@app.command("validate-glpi")
def cmd_validate_glpi():
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))
    ensure_dir(settings.data_dir)

    res = wait_for_glpi_and_validate_legacy_api(
        base_url=settings.glpi_base_url,
        init_path=settings.glpi_rest_legacy_init_path,
        user=settings.glpi_user,
        password=settings.glpi_pass,
        user_token=settings.glpi_user_token,
        app_token=settings.glpi_app_token,
        timeout_seconds=30,
        poll_seconds=1.5,
    )

    v2 = probe_glpi_v2(
        base_url=settings.glpi_base_url,
        v2_path=settings.glpi_rest_v2_path,
        api_token_v2=settings.glpi_api_token_v2,
    )

    reports_dir = os.path.join(settings.data_dir, "reports")
    ensure_dir(reports_dir)
    report_path = os.path.join(reports_dir, "glpi_api_mode_report.md")

    best_path = "legacy" if res.ok else ("v2" if v2.get("status") in (200, 401, 403) else "unknown")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# GLPI API mode report\n\n")
        f.write(f"- legacy initSession ok: {res.ok}\n")
        f.write(f"- legacy detail: {res.detail}\n")
        if res.session_token:
            f.write(f"- legacy session_token: {res.session_token[:12]}...(len={len(res.session_token)})\n")
        f.write(f"- v2 probe: {v2}\n\n")
        f.write(f"## Selected path\n- {best_path}\n\n")

        if not res.ok:
            f.write("## Checklist (legacy)\n")
            f.write("- Verifique `GLPI_USER`/`GLPI_PASS` ou preencha `GLPI_USER_TOKEN` no `.env`.\n")
            f.write("- Se o seu GLPI exigir, preencha `GLPI_APP_TOKEN`.\n")

        if v2.get("status") in (404, None):
            f.write("\n## Checklist (v2)\n")
            f.write("- A rota v2 parece indisponivel. Habilite API v2 no GLPI ou ajuste `GLPI_REST_V2_PATH`.\n")
            f.write("- Se precisar autenticar via OAuth, provavelmente sera necessaria uma etapa manual para criar client.\n")

    # Sonda itemtypes importantes (somente leitura).
    itemtypes = ["Ticket", "TicketFollowup", "User", "Group", "Contact", "Document", "Document_Item", "Entity", "TicketTask"]
    itemtype_report: list[dict] = []
    if res.ok and res.session_token:
        for it in itemtypes:
            r = list_glpi_itemtype_search_options(
                base_url=settings.glpi_base_url,
                session_token=res.session_token,
                app_token=settings.glpi_app_token,
                itemtype=it,
            )
            itemtype_report.append(r)

        itemtype_json_path = os.path.join(reports_dir, "glpi_itemtype_capabilities.json")
        with open(itemtype_json_path, "w", encoding="utf-8") as fjson:
            import json as _json

            fjson.write(_json.dumps(itemtype_report, ensure_ascii=True, indent=2, default=str))

    if res.ok:
        typer.echo("GLPI validado: legacy initSession OK.")
        if res.session_token:
            perms = validate_legacy_session_permissions(
                base_url=settings.glpi_base_url,
                session_token=res.session_token,
                app_token=settings.glpi_app_token,
            )
            typer.echo(f"permissions: {perms}")
        typer.echo(f"Relatorio: {report_path}")
        typer.echo(f"Itemtype caps: {os.path.join(reports_dir, 'glpi_itemtype_capabilities.json')}")
    else:
        typer.echo(f"GLPI validação falhou: {res.detail}")
        typer.echo(f"Relatorio: {report_path}")
        raise typer.Exit(code=1)


@app.command("transform")
def cmd_transform():
    """Transforma dados brutos do Tiflux em modelo canônico."""
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))
    ensure_dir(settings.data_dir)

    result = transform_tiflux_data(data_dir=settings.data_dir)
    typer.echo(f"Transformação concluída:")
    typer.echo(f"  - Organizations: {len(result.organizations)}")
    typer.echo(f"  - Persons: {len(result.persons)}")
    typer.echo(f"  - Queues: {len(result.queues)}")
    typer.echo(f"  - Tickets: {len(result.tickets)}")


@app.command("import-glpi")
def cmd_import_glpi(
    dry_run: bool = typer.Option(False, "--dry-run", help="Não cria nada; apenas simula o fluxo."),
    skip_entities: bool = typer.Option(False, "--skip-entities", help="Pula criação de entidades."),
    skip_users: bool = typer.Option(False, "--skip-users", help="Pula criação de usuários."),
    skip_categories: bool = typer.Option(False, "--skip-categories", help="Pula criação de categorias."),
):
    """Importa dados transformados para o GLPI."""
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))
    ensure_dir(settings.data_dir)

    stats = import_to_glpi(
        data_dir=settings.data_dir,
        glpi_base_url=settings.glpi_base_url,
        glpi_user=settings.glpi_user,
        glpi_password=settings.glpi_pass,
        glpi_user_token=settings.glpi_user_token,
        glpi_app_token=settings.glpi_app_token,
        dry_run=dry_run,
        skip_entities=skip_entities,
        skip_users=skip_users,
        skip_categories=skip_categories,
    )

    typer.echo(f"Importação concluída:")
    typer.echo(f"  - Entities: {stats.entities_created} created, {stats.entities_skipped} skipped")
    typer.echo(f"  - Users: {stats.users_created} created, {stats.users_skipped} skipped")
    typer.echo(f"  - Categories: {stats.categories_created} created, {stats.categories_skipped} skipped")
    typer.echo(f"  - Tickets: {stats.tickets_created} created, {stats.tickets_skipped} skipped, {stats.tickets_failed} failed")

    if stats.errors:
        typer.echo(f"\n{len(stats.errors)} errors occurred. Check data/reports/import_report.md")


@app.command("reconcile")
def cmd_reconcile():
    """Verifica integridade dos dados importados no GLPI."""
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))
    ensure_dir(settings.data_dir)

    result = reconcile(
        data_dir=settings.data_dir,
        glpi_base_url=settings.glpi_base_url,
        glpi_user=settings.glpi_user,
        glpi_password=settings.glpi_pass,
        glpi_user_token=settings.glpi_user_token,
        glpi_app_token=settings.glpi_app_token,
    )

    typer.echo(f"Reconciliação concluída:")
    typer.echo(f"  - Source tickets: {result.source_tickets}")
    typer.echo(f"  - GLPI tickets: {result.glpi_tickets}")
    typer.echo(f"  - Matched: {result.matched}")
    typer.echo(f"  - Missing in GLPI: {len(result.missing_in_glpi)}")
    typer.echo(f"  - Field mismatches: {len(result.field_mismatches)}")
    typer.echo(f"  - Status: {'OK' if result.ok else 'ISSUES FOUND'}")

    if not result.ok:
        typer.echo("\nVerifique data/reports/reconciliation_report.md para detalhes.")
        raise typer.Exit(code=1)


@app.command("resume")
def cmd_resume():
    typer.echo("Use `export-tiflux --resume` e `import-glpi` (quando implementado).")


@app.command("reprocess-failed")
def cmd_reprocess_failed():
    typer.echo("Comando ainda não implementado nesta iteração.")


@app.command("dry-run")
def cmd_dry_run():
    typer.echo("Comando ainda não implementado nesta iteração.")


@app.command("backup-glpi-data")
def cmd_backup_glpi_data():
    """Copia dados dos volumes Docker GLPI para pasta data/ local."""
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))

    compose_path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
    compose_path = os.path.abspath(compose_path)
    compose_dir = os.path.dirname(compose_path)

    glpi_backup_dir = os.path.join(settings.data_dir, "glpi_backup")
    db_backup_dir = os.path.join(settings.data_dir, "db_backup")
    ensure_dir(glpi_backup_dir)
    ensure_dir(db_backup_dir)

    logger.info("Copiando dados GLPI do volume Docker...")
    subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", "tiflux-glpi-migrator-glpi-data:/source:ro",
            "-v", f"{os.path.abspath(glpi_backup_dir)}:/backup",
            "alpine:latest",
            "sh", "-c", "cp -a /source/. /backup/"
        ],
        cwd=compose_dir,
        check=False,
    )

    logger.info("Exportando dump do banco MySQL...")
    db_dump_path = os.path.join(db_backup_dir, "glpi_dump.sql")
    with open(db_dump_path, "w") as f:
        subprocess.run(
            [
                "docker", "compose", "-f", compose_path,
                "exec", "-T", "db",
                "mysqldump", "-u", settings.glpi_db_user,
                f"-p{settings.glpi_db_password}", settings.glpi_db_name
            ],
            cwd=compose_dir,
            stdout=f,
            check=False,
        )

    typer.echo(f"Backup GLPI: {glpi_backup_dir}")
    typer.echo(f"Backup DB: {db_dump_path}")


@app.command("enable-glpi-api")
def cmd_enable_glpi_api():
    """Habilita a API REST do GLPI via banco de dados."""
    settings = load_settings()
    init_logging(logs_dir=os.path.join(settings.data_dir, "logs"))

    compose_path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
    compose_path = os.path.abspath(compose_path)
    compose_dir = os.path.dirname(compose_path)

    logger.info("Habilitando API no GLPI...")
    subprocess.run(
        [
            "docker", "compose", "-f", compose_path,
            "exec", "-T", "db",
            "mysql", "-u", settings.glpi_db_user,
            f"-p{settings.glpi_db_password}", settings.glpi_db_name,
            "-e",
            "UPDATE glpi_configs SET value='1' WHERE name='enable_api'; "
            "UPDATE glpi_configs SET value='1' WHERE name='enable_api_login_credentials'; "
            "INSERT INTO glpi_apiclients (name, is_active, ipv4_range_start, ipv4_range_end, entities_id, is_recursive) "
            "VALUES ('migrator', 1, INET_ATON('0.0.0.0'), INET_ATON('255.255.255.255'), 0, 1) "
            "ON DUPLICATE KEY UPDATE is_active=1;"
        ],
        cwd=compose_dir,
        check=False,
    )
    typer.echo("API GLPI habilitada.")


def main() -> None:
    app()

