FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including curl for healthcheck and gosu for
# privilege dropping in the entrypoint
RUN apt-get update && apt-get install -y \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ src/
COPY config.yaml .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Create directories and non-root user (chown here covers the case where
# no volume is bind-mounted over these paths)
RUN mkdir -p /srv/vod-library /var/log/vod-crawler \
    && useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app /srv/vod-library /var/log/vod-crawler \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

# NOTE: no USER directive here — container starts as root so Unraid's
# Docker hooks (Tailscale, etc.) have root when they run. The entrypoint
# drops to appuser before launching the actual app.

# Set Python to run in unbuffered mode
ENV PYTHONUNBUFFERED=1

# Expose API port
EXPOSE 7888

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7888/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "src.main", "--scheduler"]