# ReClip Plus

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/framework-Flask-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/docker-supported-blue.svg)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A high-performance, lightweight, and modern self-hosted media downloader. Forked from [averygan/reclip](https://github.com/averygan/reclip), ReClip Plus expands upon the original minimalist philosophy by introducing real-time live download progress, in-memory concurrency queuing, partial video clipping, subtitle and chapter extraction, SSRF security guards, and progressive batch metadata processing — while maintaining zero external service dependencies (no Redis, no Celery, no database).

![ReClip Preview](assets/preview-mp3.png)

---

## ✨ What's New in ReClip Plus

| Feature | Original ReClip | ReClip Plus |
|---|---|---|
| **Live Download Progress** | Static "Downloading..." state | Real-time Server-Sent Events (SSE) with %, speed, size & ETA |
| **Download Queue** | Unbounded background threads | In-memory queue with configurable concurrency (`MAX_CONCURRENT_DOWNLOADS`) |
| **Download Cancellation** | Not supported | Process-tree termination & instant cleanup of partial files |
| **Audio Extraction** | Always re-encodes to MP3 | **Original Audio (stream copy without re-encoding)** + 128/192/256/320 kbps |
| **Video Modes** | Resolution picker only | **Best Quality**, **Balanced (H.264/AAC)**, **Fastest (No Merge)**, or custom |
| **Partial Downloads** | Not supported | Time range clipping (`Start: 00:01:00`, `End: 00:02:30`) via yt-dlp cuts |
| **Subtitles & Chapters** | Not supported | Subtitle discovery, automatic captioning & chapter embedding |
| **Batch URL Fetching** | Sequential blocking loop | Controlled concurrent metadata fetching with progressive card rendering |
| **Metadata Caching** | None (hits remote extractor every time) | In-memory thread-safe LRU cache with configurable TTL (5–10 min) |
| **Security & SSRF** | None (could access localhost) | DNS resolution & RFC 1918/RFC 3927 private IP & cloud metadata protection |
| **Presets & History** | None | Client-side LocalStorage presets & searchable download history |
| **Healthcheck** | None | Machine-readable `/health` endpoint for Docker and Coolify |
| **CLI Generator** | None | "Copy yt-dlp command" for advanced terminal users |
| **Startup Behavior** | Blocks container startup updating pip | Fast zero-network boot with optional `RECLIP_UPDATE_ON_STARTUP` |

---

## 🚀 Quick Start

### Option 1: Docker (Recommended)

Run directly with Docker:

```bash
docker run -d \
  --name reclip-plus \
  -p 8899:8899 \
  -v reclip-downloads:/app/downloads \
  --restart unless-stopped \
  woffluon/reclip-plus:latest
```

Or using **Docker Compose**:

```yaml
services:
  reclip:
    image: reclip-plus:latest
    build: .
    container_name: reclip-plus
    ports:
      - "8899:8899"
    volumes:
      - reclip-downloads:/app/downloads
    environment:
      - PORT=8899
      - MAX_CONCURRENT_DOWNLOADS=2
      - YTDLP_CONCURRENT_FRAGMENTS=4
      - DOWNLOAD_TTL=3600
      - MIN_FREE_DISK_SPACE_MB=500
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8899/health"]
      interval: 30s
      timeout: 5s
      retries: 3

volumes:
  reclip-downloads:
```

Launch with:
```bash
docker compose up -d
```
Visit **http://localhost:8899**.

---

### Option 2: Self-Hosting on Coolify

ReClip Plus is natively optimized for single-container Coolify deployment:

1. In Coolify, create a new **Application** from your Git repository.
2. Select **Dockerfile** as the build pack.
3. Configure the exposed port to `8899`.
4. Add a persistent volume mapping: `/app/downloads`.
5. Coolify will automatically detect the `HEALTHCHECK` directive at `/health`.

---

### Option 3: Local Manual Installation

Prerequisites:
- Python 3.11+
- `ffmpeg` installed and in your system `PATH`
- `yt-dlp` installed

```bash
# Clone the repository
git clone https://github.com/Woffluon/reclip-plus.git
cd reclip-plus

# Set up virtual environment and run
chmod +x reclip.sh
./reclip.sh
```

On Windows:
Simply double-click **`reclip.bat`** or run:
```cmd
reclip.bat
```
*(Or manually via PowerShell):*
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
# or: python -m reclip
```

Open **http://localhost:8899** in your browser.

---

## ⚙️ Configuration & Environment Variables

All settings are configured via environment variables with safe defaults for low-resource environments:

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8899` | HTTP port the server listens on |
| `HOST` | `0.0.0.0` | Bind host IP address |
| `RECLIP_DOWNLOAD_DIR` | `downloads` | Filesystem path for temporary media storage |
| `MAX_CONCURRENT_DOWNLOADS`| `2` | Maximum concurrent yt-dlp active processes |
| `YTDLP_CONCURRENT_FRAGMENTS`| `4` | Concurrency per fragment for HLS/DASH streams |
| `MAX_PLAYLIST_ITEMS` | `50` | Maximum videos fetched from a playlist query |
| `MAX_DURATION` | `0` | Maximum video duration in seconds (0 = unlimited) |
| `DOWNLOAD_TTL` | `3600` | Retention window (seconds) before stale files are purged |
| `MIN_FREE_DISK_SPACE_MB` | `500` | Minimum free disk space required before starting jobs |
| `METADATA_CACHE_TTL` | `600` | In-memory metadata cache expiration (seconds) |
| `METADATA_CACHE_MAX_SIZE` | `500` | Maximum URLs cached in memory |
| `ENABLE_SSRF_PROTECTION` | `true` | Rejects localhost and private network addresses |
| `RECLIP_UPDATE_ON_STARTUP`| `0` | Set `1` to run `pip install -U yt-dlp` on container boot |
| `DELETE_AFTER_DOWNLOAD` | `false` | Automatically delete files shortly after browser download |

---

## 📖 Workflows & Capabilities

### 1. Single & Batch URL Downloads
Paste one or many URLs into the input box. ReClip Plus automatically sanitizes whitespace, commas, and duplicate links, displaying feedback like `5 URLs detected (1 duplicate removed)`. Click **Paste** to grab URLs directly from the clipboard.

### 2. Live SSE Progress & Cancellation
Active downloads broadcast updates through Server-Sent Events (SSE) without HTTP polling. You get real-time downloaded size, total size, speed (e.g. `4.5 MiB/s`), and ETA.
- **Cancel:** Click the Cancel button to terminate the process tree (`yt-dlp` + `ffmpeg`) and wipe partial files.
- **Retry:** If a download experiences network errors, click Retry to restart with bounded retries.

### 3. Audio & Video Strategies
- **Video Best Quality:** Highest available video + audio merged into an MP4 container.
- **Video Balanced:** High compatibility targeting H.264 / AAC streams.
- **Video Fastest:** Pre-merged single streams to avoid CPU-intensive merging.
- **Audio Original:** Direct audio stream extraction without transcoding (preserves Opus/M4A quality with near-instant download speeds).
- **MP3 128 / 192 / 256 / 320 kbps:** High-quality MP3 encoding via ffmpeg.

### 4. Partial Clips & Time Ranges
Under **Advanced Options**, set `Clip Start` (e.g. `00:01:30`) and `End` (e.g. `00:03:00`). yt-dlp downloads only the requested segment with keyframe alignment.

### 5. Playlists
When pasting a playlist link (e.g. YouTube playlist), an interactive playlist selector appears. View titles and durations, select or deselect specific items, and batch-download chosen videos.

### 6. Subtitles & Metadata Embedding
Toggle options to embed video metadata, chapter markers, thumbnail art, or subtitle tracks (with auto-generated subtitle support).

### 7. Copy yt-dlp Command
Click **Copy yt-dlp CLI command** to copy the exact command-line equivalent of your selected options, safely escaped and without internal server paths.

---

## 🔒 Security & Safe Self-Hosting

ReClip Plus is built to be safely exposed on public networks and self-hosted environments:

- **SSRF Protection:** Resolves domain names via DNS before processing and blocks RFC 1918 private IPv4 addresses (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.1`, `::1`), link-local (`169.254.0.0/16`), and cloud metadata services (`169.254.169.254`, `metadata.google.internal`).
- **No Command Injection:** Subprocesses execute strictly as argument lists without `shell=True`. No raw shell strings are executed.
- **Path Traversal Prevention:** Files are strictly locked within the download directory, filenames are aggressively sanitized against Windows reserved names (`CON`, `PRN`, `AUX`, etc.) and traversal sequences (`..`).
- **Storage Protection:** Rejects downloads if host disk space falls below `MIN_FREE_DISK_SPACE_MB`.
- **Information Leakage Prevention:** Internal server directory paths are automatically scrubbed from client-facing error messages.

---

## 🏗️ Architecture

```
reclip-plus/
├── reclip/                    # Core Python application package
│   ├── __init__.py            # Package exports (app, job_manager, Job, JobManager)
│   ├── __main__.py            # Module CLI entrypoint (`python -m reclip`)
│   ├── app.py                 # Flask web server, API endpoints & SSE streaming
│   ├── downloader.py          # yt-dlp CLI builder, format selectors & progress parsers
│   ├── jobs.py                # In-memory concurrency queue, workers & cancellation
│   ├── utils.py               # SSRF guards, filename sanitization, disk & cache
│   ├── templates/             # Server-rendered HTML templates
│   │   └── index.html         # Responsive, semantic HTML UI
│   └── static/                # Web assets
│       ├── css/app.css        # Minimalist responsive styles, dark/light theme
│       ├── js/app.js          # Vanilla JS, SSE connection, presets, localStorage
│       └── favicon.svg        # SVG favicon
├── docker/                    # Container helper scripts
│   └── docker-entrypoint.sh   # Fast boot entrypoint
├── tests/                     # Test suite (pytest)
│   ├── conftest.py            # Pytest path and environment fixtures
│   ├── test_app.py            # API route and SSE integration tests
│   ├── test_downloader.py     # Command generation and format selector tests
│   ├── test_jobs.py           # Concurrency queue and job lifecycle tests
│   └── test_utils.py          # SSRF, sanitization, cache and cleaner tests
├── assets/                    # Project media & documentation assets
├── downloads/                 # Temporary media storage (auto-cleaned, gitignored)
├── app.py                     # Root application entrypoint (Gunicorn / dev server)
├── Dockerfile                 # Multi-stage lightweight image with non-root user
├── docker-compose.yml         # Compose configuration
├── reclip.bat                 # Windows one-click launcher & auto-installer
├── reclip.sh                  # Linux/macOS launcher script
├── requirements.txt           # Minimal dependencies: Flask, yt-dlp, gunicorn
├── pyproject.toml             # Ruff linter & pytest configuration
├── .env.example               # Configuration reference template
├── CONTRIBUTING.md            # Contribution guidelines & architecture notes
└── LICENSE                    # MIT License
```

---

## 🧪 Testing

Run the test suite:

```bash
pytest -v
```

Tests cover:
- SSRF prevention & IP validation
- Filename sanitization across operating systems
- Duration & time string parsing
- Disk space limits & metadata cache eviction
- yt-dlp CLI argument generation & quoting
- Job queue concurrency & cancellation lifecycles
- Flask API endpoints & healthcheck

---

## ⚖️ Upstream Attribution & License

ReClip Plus is a fork and derivative work of [averygan/reclip](https://github.com/averygan/reclip) created by Avery Gan.

Licensed under the [MIT License](LICENSE). Both the original copyright notice and ReClip Plus contributor additions are preserved in accordance with the license.
