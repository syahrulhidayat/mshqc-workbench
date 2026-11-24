FROM python:3.12-slim

ARG INSTALL_MSHQC=false

WORKDIR /srv/backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY app ./app
RUN if [ "$INSTALL_MSHQC" = "true" ]; then \
      pip install --no-cache-dir -e ".[mshqc]"; \
    else \
      pip install --no-cache-dir -e .; \
    fi

EXPOSE 8000
