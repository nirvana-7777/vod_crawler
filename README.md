# Ultimate VOD Crawler

A powerful **VOD (Video on Demand) crawler and library manager** for streaming providers. This tool crawls VOD catalogs from multiple providers, normalizes TV shows and movies, and provides a unified API for library management.

---

## Features

* 🎬 **Multi-provider Support** – Crawls VOD content from multiple providers (Magenta, Joyn, RTL+, Discovery DE)
* 📺 **Smart Content Classification** – Automatically detects and categorizes movies, TV shows, and episodes
* 🗂️ **Normalized Database** – Merges content from different providers into a unified catalog
* 🚀 **REST API** – Full FastAPI interface for library access and management
* ⏰ **Scheduled Crawling** – Automated daily crawls with configurable schedules
* 🔄 **Incremental Updates** – Track changes and export only modified content
* 🐳 **Multi-platform Docker Images** – Built for both `amd64` and `arm64`
* 📤 **Streaming Export** – Memory-efficient library export for large catalogs

---

# Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI Server                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐      │
│  │ /api/status │  │ /api/sync   │  │ /api/library/export │      │
│  └─────────────┘  └─────────────┘  └─────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                     Provider Crawlers                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ Magenta  │  │  Joyn    │  │  RTL+    │  │Discovery │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                      Tree Traverser                             │
│                   ┌─────────────────────┐                       │
│                   │ Content Classifier  │                       │
│                   └─────────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                      SQLite Database                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ TV Shows │  │ Episodes │  │ Movies   │  │ History  │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

---

# Quick Start

## Using Docker (Recommended)

### 1. Create a configuration file

```bash
cp config.yaml.example config.yaml

# Edit config.yaml with your settings
```

### 2. Start the application

```bash
docker-compose up -d
```

### 3. Check the status

```bash
curl http://localhost:7888/api/status
```

---

## Manual Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/ultimate-vod-crawler.git
cd ultimate-vod-crawler

# Create virtual environment
python -m venv venv

# Linux/macOS
source venv/bin/activate

# Windows
# venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run a crawl
python -m src.main --crawl

# Start the API server
python -m src.main --api
```

---

# Configuration

## `config.yaml`

```yaml
# Backend connection (the API to crawl)
backend:
  url: "http://ultimate-backend:7777"
  timeout: 30

# Library output directory
library:
  path: "/srv/vod-library"
  movies_dir: "Movies"
  tv_shows_dir: "TV Shows"
  sports_dir: "Sports"

# API server settings
api:
  host: "0.0.0.0"
  port: 7888

# Provider priority (highest first)
provider_priority:
  - "magenta2"
  - "rtlplus"
  - "joyn"
  - "discovery_de"

# Crawling settings
crawler:
  schedule: "0 3 * * *"
  concurrency: 2
  page_size: 50
  max_depth: 10
  skip_highlights: true

# Metadata settings
metadata:
  use_kodi_scrapers: true
  embed_metadata: false
  generate_nfo: true

# Logging
logging:
  level: "INFO"
  file: "/var/log/vod-crawler/crawler.log"
```

---

# API Endpoints

## Status

```http
GET /api/status
```

Returns crawler status, statistics, and synchronization progress.

---

## Library Export

```http
GET /api/library/export
GET /api/library/export/stream
```

Exports the complete library including TV shows, episodes, and movies.

### Query Parameters

| Parameter   | Description                                |
| ----------- | ------------------------------------------ |
| `since`     | ISO-8601 timestamp for incremental updates |
| `providers` | Comma-separated list of providers          |

---

## Trigger Sync

```http
POST /api/sync
```

### Request Body

```json
{
  "provider": "joyn",
  "force": false
}
```

Both fields are optional.

---

## Get Changes

```http
GET /api/changes?since=2024-01-01T00:00:00Z
```

Returns all content changes since the specified timestamp.

---

## Legacy Endpoints

```http
GET /api/library/movies?provider=joyn
GET /api/library/shows?provider=joyn
GET /api/library/shows/{show_id}/episodes?provider=joyn
```

---

# Docker

## Local Build

```bash
docker build -t ultimate-vod-crawler .

docker run -d \
  -p 7888:7888 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v /srv/vod-library:/srv/vod-library \
  ultimate-vod-crawler
```

---

## Multi-platform Build

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t nirvana777/ultimate-vod-crawler:latest \
  --push .
```

---

# GitHub Actions

The repository includes CI/CD workflows for:

* ✅ Black
* ✅ isort
* ✅ Flake8
* ✅ pytest with coverage
* ✅ Safety
* ✅ Bandit
* ✅ Multi-platform Docker builds
* ✅ Container startup validation

Docker images are automatically built when:

* Code is pushed to `main` or `master`
* A version tag (`v*`) is created
* The workflow is manually triggered

---

# Database Schema

## TV Shows

* Normalized across providers
* Shared metadata
* Cross-provider mappings

## TV Episodes

* Provider-specific
* Season and episode numbers
* Streaming configuration (manifest, DRM)
* Cast, directors, air date, metadata

## Movies

* Standalone movie entries
* Similar metadata to episodes
* Sports and highlight support

---

# Development

## Running Tests

```bash
pip install -r requirements.txt
pip install pytest pytest-cov

pytest tests/ -v --cov=src

# Run a single test
pytest tests/test_db.py -v
```

---

## Code Style

```bash
black src/
isort src/

flake8 src/ --max-line-length=100
```

---

## Adding a New Provider

1. Create a crawler inside `src/crawler/providers/`
2. Inherit from `BaseCrawler`
3. Implement the provider-specific logic
4. Add the provider to the `provider_priority` section in `config.yaml`

---

# License

This project is licensed under the MIT License. See the `LICENSE` file for details.

---

# Contributing

1. Fork the repository.
2. Create a feature branch.

```bash
git checkout -b feature/amazing-feature
```

3. Commit your changes.

```bash
git commit -m "Add amazing feature"
```

4. Push the branch.

```bash
git push origin feature/amazing-feature
```

5. Open a Pull Request.

---

# Support

If you encounter a bug or have a question, please open a GitHub Issue.
