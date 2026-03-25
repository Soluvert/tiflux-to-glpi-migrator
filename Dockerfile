FROM python:3.12-slim

# mysql-client for post-import SQL fixes
RUN apt-get update && \
    apt-get install -y --no-install-recommends default-mysql-client curl && \
    rm -rf /var/lib/apt/lists/*

# uv – fast Python package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (layer cache)
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

# Copy application code
COPY app/ app/
COPY mapping.yaml mapping.yaml

# Install the project itself
RUN uv sync --no-dev

ENV MIGRATOR_DATA_DIR=/app/data

ENTRYPOINT ["uv", "run", "python", "-m", "app.main"]
CMD ["--help"]
