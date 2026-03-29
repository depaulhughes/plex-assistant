# Plex Assistant

A local observability assistant for Plex that analyzes streaming performance, buffering behavior, and system metrics.

## Features
- Detects buffering sessions
- Analyzes upload vs playback behavior
- Uses structured telemetry instead of guessing

## Setup

1. Clone the repo:

git clone https://github.com/depaulhughes/plex-assistant.git


2. Create a `.env` file:

PLEX_BASE_URL=
PLEX_TOKEN=

TAUTULLI_BASE_URL=
TAUTULLI_API_KEY=

PROMETHEUS_BASE_URL=

OPENAI_API_KEY=

UPLOAD_LIMIT_MBPS=
UPLOAD_WARN_MBPS=
UPLOAD_PEAK_MBPS=

CPU_WARN_PERCENT=
RAM_WARN_PERCENT=
IOWAIT_WARN_PERCENT=

BURSTY_STDDEV_MBPS=
STABLE_STDDEV_MBPS=

HIGH_BITRATE_KBPS=
SAFE_NETWORK_KBPS=

BANDWIDTH_BELOW_BITRATE_RATIO=
NON_PLEX_UPLOAD_WARN_MBPS=


3. Run:

pip install -r requirements.txt
python app.py


## Notes
- Built as part of a Plex observability + AI assistant project