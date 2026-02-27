#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers and system dependencies
playwright install chromium
playwright install-deps chromium

# Install FFmpeg (Required for yt-dlp to merge video/audio)
# Render's Ubuntu environment allows downloading the static binary
mkdir -p bin
curl -L https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz | tar -xJ --strip-components=1 -C bin
export PATH=$PATH:$(pwd)/bin
