# --- builder ---
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip && \
    pip install --prefix=/install .

# --- runtime ---
FROM python:3.12-slim AS runtime

# Build-time environment selector. Valid values: development | staging | production.
# The matching `.env.${ENV}` template is baked into the image as `/app/.env`.
# Templates use `${VAR:-default}` so real env vars (from Secrets Manager, K8s,
# CI/CD, compose `environment:`) always win over the file default at runtime.
ARG ENV=development

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app ./app
COPY .env.${ENV} /app/.env

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
