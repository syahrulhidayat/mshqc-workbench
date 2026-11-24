FROM docker.io/library/python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG INSTALL_MSHQC=false
ARG MSHQC_INSTALL_SPEC=mshqc

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir . && \
    if [ "$INSTALL_MSHQC" = "true" ]; then \
      pip install --no-cache-dir "$MSHQC_INSTALL_SPEC"; \
    fi

EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
