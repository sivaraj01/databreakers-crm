# ============================================================
# DataBreakers CRM - production image
#
# Build:  docker build -t databreakers-crm .
# Run:    docker run -p 5000:5000 --env-file .env \
#            -v crm-data:/app/instance databreakers-crm
# (Prefer `docker compose up --build` - see docker-compose.yml.)
# ============================================================
FROM python:3.12-slim

# Never write .pyc files, keep stdout/stderr unbuffered.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    PORT=5000

WORKDIR /app

# System deps: gcc only needed if a wheel is unavailable for a package;
# curl is for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get remove -y gcc \
    && apt-get autoremove -y

# Application code (static assets included; ml/ and scripts/ ship too so the
# container can train models and bootstrap the admin).
COPY app.py pytest.ini ./
COPY config.py ./
COPY app ./app
COPY ml ./ml
COPY scripts ./scripts

# The SQLite file lives in /app/instance - mount a volume there to persist it.
RUN mkdir -p /app/instance \
    && useradd --create-home --shell /usr/sbin/nologin crm \
    && chown -R crm:crm /app
USER crm

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/auth/login" -o /dev/null || exit 1

# Create schema on start (idempotent), then serve.
CMD ["sh", "-c", "python scripts/init_db.py && gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --access-logfile - 'app:create_app()'" ]
