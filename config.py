import os
import warnings

from dotenv import load_dotenv
from openai import OpenAI


warnings.filterwarnings(
    "ignore",
    message=".*urllib3 v2 only supports OpenSSL.*",
)

load_dotenv()

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
