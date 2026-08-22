FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY specguard ./specguard
COPY fixtures/*.pdf ./fixtures/
RUN uv sync --frozen --no-dev

EXPOSE 8080

CMD ["uv", "run", "--no-sync", "uvicorn", "specguard.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
