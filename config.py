import os
import warnings

from dotenv import load_dotenv
from openai import OpenAI


warnings.filterwarnings(
    "ignore",
    message=".*urllib3 v2 only supports OpenSSL.*",
)

load_dotenv()


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
