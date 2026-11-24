FROM python:3.12-slim

WORKDIR /srv/backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir -e .

EXPOSE 8000
