# yt-dlp GUI Downloader

A simple GUI wrapper for `yt-dlp` to queue YouTube links or playlists, fetch titles, show per-item progress, and save MP3s.

## Setup

1) Install `uv` (Python package manager):
   - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows PowerShell: `irm https://astral.sh/uv/install.ps1 | iex`
2) Create a virtual env and install deps:
   ```
   uv venv
   uv sync
   ```
3) Run the app:
   ```
   uv run python main.py
   ```