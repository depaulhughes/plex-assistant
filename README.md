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


3. Run the CLI locally:

pip install -r requirements.txt
python app.py


## Web UI

The project also includes a local FastAPI web UI under [web/main.py](/Users/johnnyhughes/plex-assistant-v2/web/main.py).

Install dependencies:

pip install -r requirements.txt

Run the web UI locally:

python3 -m uvicorn web.main:app --reload

Available pages:

- `/` dashboard
- `/operator`
- `/manager`
- `/history`
- `/alerts`

Useful JSON endpoints:

- `/api/state`
- `/api/health`
- `/api/history`
- `/api/alerts`


## Docker Deployment

Plex Assistant can be packaged and run as a private Docker service for NAS use.

Build the image:

docker build -t plex-assistant .

Run it directly with Docker:

docker run -d \
  --name plex-assistant \
  --restart unless-stopped \
  --env-file .env \
  -e HISTORY_LOG_PATH=/data/plex_assistant_history.jsonl \
  -e ALERT_LOG_PATH=/data/plex_assistant_alerts.jsonl \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  plex-assistant

The container runs:

python3 -m uvicorn web.main:app --host 0.0.0.0 --port 8000

No reload mode is used in Docker.


## Docker Compose

A simple Compose file is included for local/NAS-friendly deployment.

Start it:

docker compose up -d --build

Stop it:

docker compose down

The included [compose.yaml](/Users/johnnyhughes/plex-assistant-v2/compose.yaml) does the following:

- maps `8000:8000`
- loads runtime variables from `.env`
- sets `restart: unless-stopped`
- mounts `./data` to `/data` for persistent history/alert logs


## NAS / Tailscale Notes

This app is intended for private access first.

- The container binds to `0.0.0.0:8000` inside Docker.
- Map a host port such as `8000:8000` in Docker or Compose.
- Access it over your NAS LAN IP or Tailscale IP:

http://<NAS-Tailscale-IP>:8000

- No public internet exposure is assumed.
- Authentication is not included yet, so keep access private.


## Logging in Docker

History and alert logs are already configurable through environment variables:

- `HISTORY_LOG_PATH`
- `ALERT_LOG_PATH`

For container use, a good default is to mount a persistent directory and point those files into it:

- `/data/plex_assistant_history.jsonl`
- `/data/plex_assistant_alerts.jsonl`

The provided Compose file does this automatically with:

./data:/data


## Notes
- Built as part of a Plex observability + AI assistant project
