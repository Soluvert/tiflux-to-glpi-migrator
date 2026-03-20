# Contribuindo para o Tiflux GLPI Migrator

Obrigado pelo interesse em contribuir! Este documento fornece diretrizes para contribuições.

## Como Contribuir

### Reportando Bugs

1. Verifique se o bug já não foi reportado nas [Issues](https://github.com/seu-usuario/tiflux-glpi-migrator/issues)
2. Se não encontrar, crie uma nova issue incluindo:
   - Descrição clara do problema
   - Passos para reproduzir
   - Comportamento esperado vs atual
   - Logs relevantes (remova dados sensíveis!)
   - Versão do Python e sistema operacional

### Sugerindo Melhorias

Abra uma issue com a tag `enhancement` descrevendo:
- O problema que a melhoria resolve
- Como você imagina a solução
- Alternativas consideradas

### Enviando Pull Requests

1. Fork o repositório
2. Clone seu fork:
   ```bash
   git clone https://github.com/seu-usuario/tiflux-glpi-migrator.git
   ```
3. Crie uma branch para sua feature:
   ```bash
   git checkout -b feature/minha-feature
   ```
4. Faça suas alterações
5. Execute os testes:
   ```bash
   uv run pytest -v
   ```
6. Commit suas mudanças:
   ```bash
   git commit -m "feat: adiciona suporte a X"
   ```
7. Push para seu fork:
   ```bash
   git push origin feature/minha-feature
   ```
8. Abra um Pull Request

## Padrões de Código

### Estilo

- Seguimos PEP 8
- Use type hints sempre que possível
- Docstrings em português para funções públicas

### Commits

Usamos [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` nova funcionalidade
- `fix:` correção de bug
- `docs:` documentação
- `refactor:` refatoração sem mudança de comportamento
- `test:` adição/correção de testes
- `chore:` tarefas de manutenção

### Testes

- Adicione testes para novas funcionalidades
- Mantenha cobertura de testes existente
- Testes devem ser independentes e reproduzíveis

## Estrutura do Projeto

```
app/
├── clients/      # Clientes HTTP para APIs externas
├── mappers/      # Transformadores de dados
├── schemas/      # Modelos Pydantic
├── services/     # Lógica de negócio principal
├── repositories/ # Acesso a dados/persistência
└── utils/        # Funções utilitárias
```

### Adicionando Novos Recursos Tiflux

1. Adicione o recurso em `app/constants.py` > `RESOURCE_CANDIDATES`
2. Crie o modelo canônico em `app/schemas/canonical.py`
3. Implemente o mapper em `app/mappers/tiflux_to_canonical.py`
4. Implemente o mapper GLPI em `app/mappers/canonical_to_glpi.py`
5. Atualize o serviço de importação conforme necessário

## Ambiente de Desenvolvimento

```bash
# Instalar dependências
uv sync --dev

# Executar testes
uv run pytest -v

# Verificar linting
uv run ruff check .

# Formatar código
uv run ruff format .
```

## Dúvidas?

Abra uma issue com a tag `question` ou entre em contato com os mantenedores.

---

Obrigado por contribuir!
