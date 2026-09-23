FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Run the service as an unprivileged user
RUN useradd --create-home --uid 10001 appuser

# Install dependencies first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Private data directory (uploaded files + SQLite DB), owned by appuser, mode 0700.
# Mounted as a volume at runtime so data survives container restarts.
RUN install -d -m 700 -o appuser -g appuser /data
ENV DATA_DIR=/data

USER appuser

EXPOSE 8000

# Required at runtime (no defaults on purpose): SIGNING_SECRET, API_KEYS.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
