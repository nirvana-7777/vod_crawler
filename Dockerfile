FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including curl for healthcheck
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ src/
COPY config.yaml .

# Create directories and set up non-root user
RUN mkdir -p /srv/vod-library /var/log/vod-crawler \
    && useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app /srv/vod-library /var/log/vod-crawler

# Switch to non-root user
USER appuser

# Set Python to run in unbuffered mode
ENV PYTHONUNBUFFERED=1

# Expose API port
EXPOSE 7888

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7888/health || exit 1

# Run the service with scheduler (blocking mode)
CMD ["python", "-m", "src.main", "--scheduler"]