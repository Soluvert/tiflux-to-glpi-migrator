# Tiflux to GLPI Migrator

Ferramenta de migração completa para transferir dados do **Tiflux** para o **GLPI**, com pipeline ETL robusto, auditável e idempotente.

## Funcionalidades

- **Extração automática** - Descoberta e export de dados via API Tiflux
- **Transformação inteligente** - Mapeamento configurável de campos e status
- **Importação segura** - Idempotente com suporte a retomada
- **Reconciliação** - Verificação de integridade pós-importação
- **Ambiente de homologação** - GLPI Docker integrado para testes

### O que é migrado

| Tiflux | GLPI | Status |
|--------|------|--------|
| Clientes | Entidades | ✅ |
| Tickets | Tickets | ✅ |
| Solicitantes | Usuários | ✅ |
| Técnicos | Usuários | ✅ |
| Mesas/Filas | Categorias ITIL | ✅ |
| SLA Info | Time to own/resolve | ✅ |
| Seguidores | Observadores | ✅ |
| Estágios | Campo customizado | ✅ |

## Pré-requisitos

- **Python 3.12+**
- **[uv](https://github.com/astral-sh/uv)** - Gerenciador de pacotes Python
- **Docker** e **Docker Compose** (para ambiente de homologação)
- **Credenciais Tiflux** - URL da API e token de acesso

## Instalação

```bash
# Clonar repositório
git clone https://github.com/Soluvert/tiflux-to-glpi-migrator.git
cd tiflux-to-glpi-migrator

# Instalar dependências
uv sync --dev

# Copiar arquivo de configuração
cp .env.example .env
```

## Configuração

Edite o arquivo `.env` com suas credenciais:

```env
# Tiflux (obrigatório)
TIFLUX_BASE_URL=https://api.tiflux.com
TIFLUX_API_TOKEN=seu_token_aqui

# GLPI (para importação)
GLPI_BASE_URL=http://localhost:8080
GLPI_USER=glpi
GLPI_PASS=glpi
```

### Obtendo o Token Tiflux

1. Acesse o painel administrativo do Tiflux
2. Vá em **Configurações > Integrações > API**
3. Gere um novo token de acesso
4. Copie o token para o arquivo `.env`

## Uso

### Pipeline Completo (Passo a Passo)

#### 1. Descobrir endpoints da API Tiflux

```bash
uv run python -m app.main discover-tiflux
```

Isso detecta automaticamente os recursos disponíveis na sua instância Tiflux.

#### 2. Exportar dados brutos

```bash
uv run python -m app.main export-tiflux --resume --continue-on-error
```

Os dados são salvos em `data/raw/` no formato JSON.

#### 3. Analisar qualidade dos dados

```bash
uv run python -m app.main analyze-data
```

Gera relatórios em `data/reports/` com análise de qualidade.

#### 4. Subir ambiente GLPI de homologação

```bash
# Iniciar containers
docker compose up -d

# Habilitar API REST do GLPI
uv run python -m app.main enable-glpi-api

# Validar conexão
uv run python -m app.main validate-glpi
```

O GLPI estará disponível em http://localhost:8080 (login: `glpi` / senha: `glpi`)

#### 5. Transformar dados

```bash
uv run python -m app.main transform
```

Converte dados Tiflux para o modelo canônico intermediário.

#### 6. Importar para GLPI

```bash
# Testar sem criar dados (recomendado)
uv run python -m app.main import-glpi --dry-run

# Importar de verdade
uv run python -m app.main import-glpi
```

#### 7. Verificar integridade

```bash
uv run python -m app.main reconcile
```

### Comandos Rápidos

```bash
# Executar fases 1-3 de uma vez
uv run python -m app.main full-run --resume

# Backup dos dados GLPI
uv run python -m app.main backup-glpi-data
```

### Referência Completa de Comandos

| Comando | Descrição | Flags |
|---------|-----------|-------|
| `discover-tiflux` | Descobre endpoints disponíveis na API Tiflux | `--resources` (lista csv), `--verbose` |
| `export-tiflux` | Exporta dados brutos da API Tiflux | `--resume`, `--continue-on-error`, `--download-blobs/--no-download-blobs` |
| `analyze-data` | Gera relatórios de qualidade dos dados | — |
| `full-run` | Executa discover + export + analyze de uma vez | `--resume`, `--continue-on-error` |
| `install-glpi-hml` | Sobe GLPI de homologação via Docker Compose | `--timeout-seconds` (default: 180) |
| `validate-glpi` | Valida conexão e permissões da API GLPI | — |
| `enable-glpi-api` | Habilita API REST do GLPI via SQL | — |
| `transform` | Transforma dados Tiflux → modelo canônico | — |
| `import-glpi` | Importa dados transformados no GLPI | `--dry-run`, `--skip-entities`, `--skip-users`, `--skip-categories` |
| `reconcile` | Verifica integridade dados importados vs fonte | — |
| `backup-glpi-data` | Copia volumes Docker GLPI para pasta local | — |

## Mapeamento Personalizado

Edite o arquivo `mapping.yaml` para customizar o mapeamento:

```yaml
# Estratégia para clientes
strategy:
  clients_as_entities: true      # Clientes viram Entidades GLPI

# Mapeamento de status
status_mapping:
  Opened: "New"
  Em Andamento: "In progress"
  Aguardando Retorno Cliente: "Waiting"
  Resolvido: "Solved"

# Mapeamento de prioridade (1-6 no GLPI)
priority_mapping:
  baixa: 2
  media: 3
  alta: 4
  urgente: 5

# Mesas/Desks
mesas_mapping:
  use_as: "category"   # Viram categorias ITIL
```

## Estrutura do Projeto

```
tiflux_glpi_migrator/
├── app/
│   ├── clients/          # Clientes HTTP (Tiflux, GLPI)
│   ├── mappers/          # Transformadores de dados
│   ├── schemas/          # Modelos Pydantic
│   ├── services/         # Lógica de negócio
│   └── cli.py            # Interface de linha de comando
├── data/
│   ├── raw/              # Dados brutos exportados
│   ├── processed/        # Dados transformados
│   ├── reports/          # Relatórios gerados
│   └── logs/             # Logs de execução
├── tests/                # Testes automatizados
├── .env.example          # Exemplo de configuração
├── docker-compose.yml    # GLPI de homologação
├── mapping.yaml          # Regras de mapeamento
└── pyproject.toml        # Dependências Python
```

## Idempotência e Retomada

O sistema é **idempotente** - você pode executar múltiplas vezes sem duplicar dados:

- **Export**: Páginas já baixadas são puladas (`--resume`)
- **Import**: IDs mapeados em `data/processed/id_mapping.json`
- **Reconciliação**: Compara fonte e destino para detectar divergências

### Resetar importação

Para reimportar do zero:

```bash
rm data/processed/id_mapping.json
uv run python -m app.main import-glpi
```

## Tratamento de Erros

- **Rate Limit**: Respeita headers `RateLimit-Remaining` e `RateLimit-Reset`
- **Retry automático**: 3 tentativas com backoff exponencial
- **Continue on error**: Flag `--continue-on-error` para não parar em falhas

## Desenvolvimento

```bash
# Instalar dependências de desenvolvimento
uv sync --dev

# Executar testes
uv run pytest -v

# Verificar linting
uv run ruff check .
```

## FAQ

### Como migrar para um GLPI em produção?

1. Altere `GLPI_BASE_URL` no `.env` para apontar para seu GLPI
2. Configure `GLPI_USER` e `GLPI_PASS` com credenciais de administrador
3. Execute `validate-glpi` para confirmar a conexão
4. Execute `import-glpi --dry-run` primeiro
5. Se tudo ok, execute `import-glpi`

### Os tickets existentes no GLPI serão afetados?

Não. O migrador apenas **cria** novos itens. Tickets existentes não são modificados.

### Como mapear campos customizados?

Campos não mapeados são preservados no campo `raw` do modelo canônico e podem ser acessados para mapeamentos personalizados editando os arquivos em `app/mappers/`.

### Posso migrar apenas tickets específicos?

Atualmente não há filtro por ticket. Você pode editar manualmente os arquivos em `data/raw/tickets/` antes de executar `transform`.

## Deploy no Coolify

O projeto inclui um `docker-compose.yml` pronto para deploy no Coolify.

### Variáveis de Ambiente (configurar no Coolify)

**Obrigatórias:**

| Variável | Descrição |
|----------|----------|
| `GLPI_DB_PASSWORD` | Senha do banco MySQL. Usada por todos os serviços. |
| `TIFLUX_API_TOKEN` | Token da API Tiflux (Configurações > Integrações > API). |

**Opcionais (têm defaults):**

| Variável | Default | Descrição |
|----------|---------|-----------|
| `GLPI_DB_NAME` | `glpi` | Nome do banco MySQL |
| `GLPI_DB_USER` | `glpi` | Usuário MySQL |
| `TIFLUX_BASE_URL` | `https://api.tiflux.com` | URL base da API Tiflux |
| `GLPI_USER` | `glpi` | Usuário admin do GLPI |
| `GLPI_PASS` | `glpi` | Senha admin do GLPI |

### Configuração do Domínio

- Aponte o domínio para o serviço **glpi** na porta **80**
- O migrator **não precisa de domínio** (é uma ferramenta CLI)
- O Coolify cuida do TLS/HTTPS automaticamente

### Executando Comandos no Migrator

O container `migrator` fica rodando com `sleep infinity`. Para executar comandos:

1. No Coolify, acesse o container `migrator` via **Execute Command**
2. Execute os comandos normalmente:

```bash
uv run python -m app.main discover-tiflux
uv run python -m app.main export-tiflux --resume
uv run python -m app.main full-run
```

### Serviços

| Serviço | Função | Persistente? |
|---------|--------|--------------|
| `db` | MySQL 8.0 | Sim (volume `db_data`) |
| `glpi` | GLPI helpdesk (porta 80) | Sim (volume `glpi_data`) |
| `glpi-init` | Configura API na primeira vez | Não (roda e sai) |
| `migrator` | ETL Tiflux → GLPI (comandos sob demanda) | Sim (volume `migrator_data`) |

## Limitações Conhecidas

- Anexos/documentos não são migrados automaticamente
- Histórico de comentários do ticket não é importado
- SLAs são mapeados como datas, não como regras de SLA do GLPI

## Licença

MIT License - veja [LICENSE](LICENSE) para detalhes.

## Contribuindo

Contribuições são bem-vindas! Por favor:

1. Fork o repositório
2. Crie uma branch para sua feature (`git checkout -b feature/nova-feature`)
3. Commit suas mudanças (`git commit -m 'Adiciona nova feature'`)
4. Push para a branch (`git push origin feature/nova-feature`)
5. Abra um Pull Request

## Suporte

- Abra uma [Issue](https://github.com/Soluvert/tiflux-to-glpi-migrator/issues) para bugs ou sugestões
- Para dúvidas sobre Tiflux ou GLPI, consulte suas documentações oficiais
