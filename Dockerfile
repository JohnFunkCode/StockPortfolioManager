# ── Stage 1: Build React frontend ─────────────────────────────────────────────
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build
# output: /frontend/dist/

# ── Stage 2: Production Python image ──────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Build-time system deps for psycopg2-binary and scipy-adjacent packages
RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caches until requirements.txt changes)
COPY requirements.txt ./
# NOTE: torch + transformers (~3 GB) are included for MCP server parity.
# If you confirm those aren't needed in the API request path, remove them
# from requirements.txt to cut image size dramatically.
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Overlay the pre-built React app so Flask can serve it at runtime
COPY --from=frontend-build /frontend/dist ./frontend/dist

# Cloud Run injects PORT; gunicorn binds to it
ENV PORT=8080
EXPOSE 8080

# 2 workers × 4 threads = 8 concurrent request slots per instance.
# Adjust --workers to match Cloud Run --concurrency / 4.
CMD exec gunicorn \
  --bind "0.0.0.0:${PORT}" \
  --workers 2 \
  --threads 4 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  "api.app:create_app()"
