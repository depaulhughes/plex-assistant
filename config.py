import json
import os
import warnings
import logging

from dotenv import load_dotenv
from openai import OpenAI


warnings.filterwarnings(
    "ignore",
    message=".*urllib3 v2 only supports OpenSSL.*",
)

logger = logging.getLogger("plex_assistant.config")

DOTENV_LOADED = load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

PLEX_BASE_URL = os.environ["PLEX_BASE_URL"].rstrip("/")
PLEX_TOKEN = os.environ["PLEX_TOKEN"]

TAUTULLI_BASE_URL = os.environ["TAUTULLI_BASE_URL"].rstrip("/")
TAUTULLI_API_KEY = os.environ["TAUTULLI_API_KEY"]

PROMETHEUS_BASE_URL = os.environ["PROMETHEUS_BASE_URL"].rstrip("/")

UPLOAD_LIMIT_MBPS = float(os.getenv("UPLOAD_LIMIT_MBPS", "41"))
UPLOAD_WARN_MBPS = float(os.getenv("UPLOAD_WARN_MBPS", "35"))
UPLOAD_PEAK_MBPS = float(os.getenv("UPLOAD_PEAK_MBPS", "38"))

CPU_WARN_PERCENT = float(os.getenv("CPU_WARN_PERCENT", "60"))
RAM_WARN_PERCENT = float(os.getenv("RAM_WARN_PERCENT", "85"))
IOWAIT_WARN_PERCENT = float(os.getenv("IOWAIT_WARN_PERCENT", "8"))

BURSTY_STDDEV_MBPS = float(os.getenv("BURSTY_STDDEV_MBPS", "8"))
STABLE_STDDEV_MBPS = float(os.getenv("STABLE_STDDEV_MBPS", "3"))

HIGH_BITRATE_KBPS = int(os.getenv("HIGH_BITRATE_KBPS", "15000"))
SAFE_NETWORK_KBPS = int(os.getenv("SAFE_NETWORK_KBPS", "10000"))

BANDWIDTH_BELOW_BITRATE_RATIO = float(os.getenv("BANDWIDTH_BELOW_BITRATE_RATIO", "0.8"))
NON_PLEX_UPLOAD_WARN_MBPS = float(os.getenv("NON_PLEX_UPLOAD_WARN_MBPS", "5"))

HISTORY_LOG_PATH = os.getenv("HISTORY_LOG_PATH", "plex_assistant_history.jsonl")
ALERT_LOG_PATH = os.getenv("ALERT_LOG_PATH", "plex_assistant_alerts.jsonl")
HISTORY_LOOKBACK_LIMIT = int(os.getenv("HISTORY_LOOKBACK_LIMIT", "200"))
EVENT_LOG_SNAPSHOT_MINUTES = int(os.getenv("EVENT_LOG_SNAPSHOT_MINUTES", "15"))
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "15"))
ENABLE_HISTORY_LOGGING = _env_bool("ENABLE_HISTORY_LOGGING", True)
ENABLE_ALERT_LOGGING = _env_bool("ENABLE_ALERT_LOGGING", True)
LOG_ONLY_ON_CHANGE = _env_bool("LOG_ONLY_ON_CHANGE", True)
LOG_HEALTHY_SNAPSHOTS = _env_bool("LOG_HEALTHY_SNAPSHOTS", False)
MIN_EVENT_LOG_INTERVAL_SECONDS = int(os.getenv("MIN_EVENT_LOG_INTERVAL_SECONDS", "300"))
MIN_HEALTHY_LOG_INTERVAL_SECONDS = int(os.getenv("MIN_HEALTHY_LOG_INTERVAL_SECONDS", "1800"))
ASK_STATE_CACHE_SECONDS = int(os.getenv("ASK_STATE_CACHE_SECONDS", "10"))
HOME_RECENT_INCIDENT_COOLDOWN_MINUTES = int(os.getenv("HOME_RECENT_INCIDENT_COOLDOWN_MINUTES", "15"))
HOME_RECENT_INCIDENT_STRONG_MINUTES = int(os.getenv("HOME_RECENT_INCIDENT_STRONG_MINUTES", "5"))
PLAYBACK_INSTABILITY_MEMORY_MINUTES = int(
    os.getenv("PLAYBACK_INSTABILITY_MEMORY_MINUTES", str(HOME_RECENT_INCIDENT_COOLDOWN_MINUTES))
)
PLAYBACK_INSTABILITY_HALF_LIFE_MINUTES = float(os.getenv("PLAYBACK_INSTABILITY_HALF_LIFE_MINUTES", "6"))

GRAFANA_BASE_URL = os.getenv("GRAFANA_BASE_URL", "").rstrip("/")
GRAFANA_DASHBOARD_UID = os.getenv("GRAFANA_DASHBOARD_UID", "").strip()
GRAFANA_DASHBOARD_SLUG = os.getenv("GRAFANA_DASHBOARD_SLUG", "").strip()
GRAFANA_DEFAULT_RANGE = os.getenv("GRAFANA_DEFAULT_RANGE", "1h").strip() or "1h"
GRAFANA_PUBLIC_DASHBOARD_URL = os.getenv(
    "GRAFANA_PUBLIC_DASHBOARD_URL",
    "http://100.121.121.55:3000/public-dashboards/d53728023fea418d81e38e78de6bcb5f",
).strip()


def _parse_grafana_panels():
    panels_json = os.getenv("GRAFANA_PANELS_JSON", "").strip()
    if panels_json:
        try:
            raw_panels = json.loads(panels_json)
        except json.JSONDecodeError:
            raw_panels = []
    else:
        raw_panels = []

    panels = []
    if isinstance(raw_panels, list):
        for item in raw_panels:
            if not isinstance(item, dict):
                continue
            try:
                panel_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            panels.append(
                {
                    "id": panel_id,
                    "title": str(item.get("title") or f"Panel {panel_id}"),
                    "description": str(item.get("description") or ""),
                }
            )
    if panels:
        return panels

    parsed_ids = []
    for raw_id in os.getenv("GRAFANA_PANEL_IDS", "").split(","):
        raw_id = raw_id.strip()
        if not raw_id:
            continue
        try:
            parsed_ids.append(int(raw_id))
        except ValueError:
            continue

    return [
        {
            "id": panel_id,
            "title": f"Panel {panel_id}",
            "description": "",
        }
        for panel_id in parsed_ids
    ]


GRAFANA_PANELS = _parse_grafana_panels()
