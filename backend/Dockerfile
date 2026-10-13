FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libgeos-dev libproj-dev build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY catrix ./catrix
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000
CMD ["uvicorn", "catrix.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
