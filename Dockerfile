FROM python:3.11-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt


# Create final image
FROM python:3.11-slim AS final

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    MAX_WORKERS=4 \
    TZ=UTC \
    JOBS_DIR=/data/jobs \
    DB_PATH=/data/db/jobs.db

# Create a non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install LaTeX and required dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-full \
    tini \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create persistent directories
RUN mkdir -p /data/jobs /data/db /app/temp && \
    chown -R appuser:appuser /app /data

# Copy Python wheels from builder stage
COPY --from=builder /app/wheels /wheels

# Install Python dependencies
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application code
COPY . /app/

# Create entrypoint script that properly handles environment variables
RUN echo '#!/bin/sh\n\
exec uvicorn main:app --host 0.0.0.0 --port $PORT --workers $MAX_WORKERS --log-level info\n\
' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Switch to non-root user
USER appuser

# Expose the service port
EXPOSE 8000

# Use tini as init
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run the application with proper signal handling
CMD ["/app/entrypoint.sh"]
