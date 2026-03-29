import warnings

warnings.filterwarnings(
    "ignore",
    message=".*urllib3 v2 only supports OpenSSL.*",
)

import os
import sys
import json
import time
import requests
import xml.etree.ElementTree as ET
from openai import OpenAI
from dotenv import load_dotenv


load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

PLEX_BASE_URL = os.environ["PLEX_BASE_URL"].rstrip("/")
PLEX_TOKEN = os.environ["PLEX_TOKEN"]

TAUTULLI_BASE_URL = os.environ["TAUTULLI_BASE_URL"].rstrip("/")
TAUTULLI_API_KEY = os.environ["TAUTULLI_API_KEY"]

PROMETHEUS_BASE_URL = os.environ["PROMETHEUS_BASE_URL"].rstrip("/")

# === THRESHOLDS / TUNING ===

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


def prom_query_scalar(query: str) -> float:
    resp = requests.get(
        f"{PROMETHEUS_BASE_URL}/api/v1/query",
        params={"query": query},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {data}")

    results = data["data"]["result"]
    if not results:
        return 0.0
    return float(results[0]["value"][1])


def prom_query_range(query: str, start: int, end: int, step: str = "5s") -> list[float]:
    resp = requests.get(
        f"{PROMETHEUS_BASE_URL}/api/v1/query_range",
        params={
            "query": query,
            "start": start,
            "end": end,
            "step": step,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus range query failed: {data}")

    results = data["data"]["result"]
    if not results:
        return []

    values = results[0].get("values", [])
    return [float(v[1]) for v in values if len(v) > 1]


def get_plex_sessions() -> list[dict]:
    resp = requests.get(
        f"{PLEX_BASE_URL}/status/sessions",
        params={"X-Plex-Token": PLEX_TOKEN},
        timeout=10,
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    sessions = []

    for video in root.findall("Video"):
        player = video.find("Player")
        media = video.find("Media")
        part = media.find("Part") if media is not None else None
        streams = part.findall("Stream") if part is not None else []

        video_stream = next((s for s in streams if s.attrib.get("streamType") == "1"), None)
        audio_stream = next((s for s in streams if s.attrib.get("streamType") == "2"), None)
        subtitle_stream = next((s for s in streams if s.attrib.get("streamType") == "3"), None)

        sessions.append(
            {
                "title": video.attrib.get("title"),
                "year": video.attrib.get("year"),
                "type": video.attrib.get("type"),
                "session_key": video.attrib.get("sessionKey"),
                "player_product": player.attrib.get("product") if player is not None else None,
                "player_state": player.attrib.get("state") if player is not None else None,
                "decision": part.attrib.get("decision") if part is not None else None,
                "bitrate_kbps": int(video_stream.attrib.get("bitrate", "0")) if video_stream is not None else 0,
                "video_codec": video_stream.attrib.get("codec") if video_stream is not None else None,
                "audio_codec": audio_stream.attrib.get("codec") if audio_stream is not None else None,
                "subtitle_codec": subtitle_stream.attrib.get("codec") if subtitle_stream is not None else None,
                "subtitle_decision": subtitle_stream.attrib.get("decision") if subtitle_stream is not None else None,
                "container": media.attrib.get("container") if media is not None else None,
                "audio_channels": audio_stream.attrib.get("channels") if audio_stream is not None else None,
                "subtitle_format": subtitle_stream.attrib.get("format") if subtitle_stream is not None else None,
            }
        )

    return sessions


def get_tautulli_activity() -> dict:
    resp = requests.get(
        f"{TAUTULLI_BASE_URL}/api/v2",
        params={"apikey": TAUTULLI_API_KEY, "cmd": "get_activity"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("response", {}).get("result") != "success":
        raise RuntimeError(f"Tautulli API failed: {data}")

    return data["response"]["data"]


def diagnose(state: dict) -> dict:
    reasons = []
    bottleneck = "none"
    health = "healthy"

    host_cpu = state["system"]["host_cpu_percent"]
    host_ram = state["system"]["host_ram_percent"]
    plex_upload = state["system"]["plex_upload_mbps"]
    total_upload = state["system"]["total_upload_mbps"]
    iowait = state["system"]["iowait_percent"]
    transcodes = state["plex"]["transcodes"]
    plex_cpu_host = state["system"]["plex_cpu_host_percent"]

    if plex_upload > UPLOAD_WARN_MBPS and host_cpu < (CPU_WARN_PERCENT / 2):
        bottleneck = "upload"
        health = "warning"
        reasons.append("Plex upload is near your home upload ceiling.")

    if transcodes > 0 and plex_cpu_host > 25:
        bottleneck = "transcoding"
        health = "warning"
        reasons.append("Active transcodes are increasing Plex CPU load.")

    if iowait > IOWAIT_WARN_PERCENT:
        bottleneck = "disk_io"
        health = "warning"
        reasons.append("Disk wait is elevated, suggesting storage bottleneck.")

    if host_ram > RAM_WARN_PERCENT:
        bottleneck = "memory_pressure"
        health = "warning"
        reasons.append("Host RAM usage is high.")

    if total_upload - plex_upload > NON_PLEX_UPLOAD_WARN_MBPS:
        reasons.append("Non-Plex upload traffic appears to be present.")

    if not reasons:
        reasons.append("CPU, RAM, upload, and disk wait all look healthy.")

    return {
        "health": health,
        "bottleneck": bottleneck,
        "reasoning": reasons,
    }

def build_tautulli_session_map(tautulli_activity: dict) -> dict:
    session_map = {}

    sessions = tautulli_activity.get("sessions", [])
    for s in sessions:
        session_key = str(s.get("session_key", "")).strip()
        if session_key:
            session_map[session_key] = s

    return session_map


def build_state() -> dict:
    sessions = get_plex_sessions()
    tautulli_activity = get_tautulli_activity()
    tautulli_session_map = build_tautulli_session_map(tautulli_activity)
    recent_upload = get_recent_upload_analysis(window_seconds=60)

    system = {
        "host_cpu_percent": round(
            prom_query_scalar('100 * (1 - (sum(rate(node_cpu_seconds_total{mode="idle"}[1m])) / sum(rate(node_cpu_seconds_total[1m]))))'),
            2,
        ),
        "host_ram_percent": round(
            prom_query_scalar('100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))'),
            2,
        ),
        "plex_cpu_host_percent": round(
            prom_query_scalar('(sum(rate(container_cpu_usage_seconds_total{name="plex-gpu-and-music-folder"}[1m])) / scalar(max(machine_cpu_cores))) * 100'),
            2,
        ),
        "plex_ram_gib": round(
            prom_query_scalar('container_memory_working_set_bytes{name="plex-gpu-and-music-folder"} / 1024 / 1024 / 1024'),
            2,
        ),
        "plex_upload_mbps": round(
            prom_query_scalar('sum(rate(container_network_transmit_bytes_total{name="plex-gpu-and-music-folder"}[1m])) * 8 / 1000000'),
            2,
        ),
        "total_upload_mbps": round(
            prom_query_scalar('sum(rate(node_network_transmit_bytes_total{device!~"lo"}[1m])) * 8 / 1000000'),
            2,
        ),
        "iowait_percent": round(
            prom_query_scalar('sum(rate(node_cpu_seconds_total{mode="iowait"}[1m])) * 100 / scalar(count(count(node_cpu_seconds_total{mode="idle"}) by (cpu)))'),
            2,
        ),
    }

    for s in sessions:
        session_key = str(s.get("session_key", "")).strip()
        t_session = tautulli_session_map.get(session_key, {})

        s["tautulli_state"] = (t_session.get("state") or "").lower()
        s["tautulli_bandwidth_kbps"] = int(t_session.get("bandwidth", 0) or 0)
        s["tautulli_stream_container_decision"] = (t_session.get("stream_container_decision") or "").lower()
        s["tautulli_stream_video_decision"] = (t_session.get("stream_video_decision") or "").lower()
        s["tautulli_stream_audio_decision"] = (t_session.get("stream_audio_decision") or "").lower()
        s["tautulli_stream_subtitle_decision"] = (t_session.get("stream_subtitle_decision") or "").lower()
        s["tautulli_quality_profile"] = t_session.get("quality_profile")
        s["tautulli_product"] = t_session.get("product")
        s["tautulli_player"] = t_session.get("player")
    
    transcodes = sum(1 for s in sessions if (s.get("decision") or "").lower() == "transcode")
    direct_plays = sum(1 for s in sessions if (s.get("decision") or "").lower() == "directplay")
    

    state = {
    "plex": {
        "active_sessions": len(sessions),
        "transcodes": transcodes,
        "direct_plays": direct_plays,
        "sessions": sessions,
        "tautulli_activity": tautulli_activity,
            },
    "system": system,
    "history": {
        "recent_upload": recent_upload,
                },
            }

    state["facts"] = derive_facts(state)
    state["diagnosis"] = diagnose(state)
    state["structured_diagnosis"] = build_structured_diagnosis(state)
    return state

def get_recent_upload_analysis(window_seconds: int = 60) -> dict:
    end = int(time.time())
    start = end - window_seconds

    query = 'sum(rate(node_network_transmit_bytes_total{device!~"lo"}[1m])) * 8 / 1000000'
    samples = prom_query_range(query, start=start, end=end, step="5s")

    if not samples:
        return {
            "samples": [],
            "avg_upload_mbps": 0.0,
            "max_upload_mbps": 0.0,
            "above_warn_count": 0,
            "above_peak_count": 0,
            "sustained_upload_high": False,
            "brief_upload_spike": False,
            "upload_std_dev": 0.0,
            "upload_is_stable": False,
            "upload_is_bursty": False,
        }

    avg_upload = sum(samples) / len(samples)
    variance = sum((x - avg_upload) ** 2 for x in samples) / len(samples)
    std_dev = variance ** 0.5
    max_upload = max(samples)

    above_warn_count = sum(1 for x in samples if x > UPLOAD_WARN_MBPS)
    above_peak_count = sum(1 for x in samples if x > UPLOAD_PEAK_MBPS)

    sustained_upload_high = above_warn_count >= max(3, len(samples) // 2)
    brief_upload_spike = max_upload > UPLOAD_WARN_MBPS and not sustained_upload_high

    return {
        "samples": samples,
        "avg_upload_mbps": round(avg_upload, 2),
        "max_upload_mbps": round(max_upload, 2),
        "above_warn_count": above_warn_count,
        "above_peak_count": above_peak_count,
        "sustained_upload_high": sustained_upload_high,
        "brief_upload_spike": brief_upload_spike,
        "upload_std_dev": round(std_dev, 2),
        "upload_is_stable": std_dev < STABLE_STDDEV_MBPS,
        "upload_is_bursty": std_dev > BURSTY_STDDEV_MBPS,
    }


def summarize(state: dict) -> str:
    lines = []
    lines.append(f'Health: {state["diagnosis"]["health"]}')
    lines.append(f'Bottleneck: {state["diagnosis"]["bottleneck"]}')
    lines.append("")
    lines.append(
        f'System: CPU {state["system"]["host_cpu_percent"]}% | '
        f'RAM {state["system"]["host_ram_percent"]}% | '
        f'Plex upload {state["system"]["plex_upload_mbps"]} Mbps | '
        f'Total upload {state["system"]["total_upload_mbps"]} Mbps'
    )
    lines.append(
        f'Plex: {state["plex"]["active_sessions"]} session(s), '
        f'{state["plex"]["transcodes"]} transcode(s), '
        f'{state["plex"]["direct_plays"]} direct play(s)'
    )

    for s in state["plex"]["sessions"]:
        lines.append(
            f'- {s["title"]} ({s["year"]}) | {s["decision"]} | '
            f'{s["bitrate_kbps"]} kbps | video={s["video_codec"]} | '
            f'audio={s["audio_codec"]} | subtitles={s["subtitle_codec"] or "none"}'
        )

    lines.append("")
    lines.extend(f'Reason: {r}' for r in state["diagnosis"]["reasoning"])
    return "\n".join(lines)

def derive_facts(state: dict) -> dict:
    facts = {}

    plex = state["plex"]
    system = state["system"]
    history = state.get("history", {})
    recent_upload = history.get("recent_upload", {})

    # --- basic facts ---
    facts["has_sessions"] = plex["active_sessions"] > 0
    facts["has_transcodes"] = plex["transcodes"] > 0

    # --- upload facts ---
    upload_limit_mbps = UPLOAD_LIMIT_MBPS
    plex_upload = system.get("plex_upload_mbps", 0)
    total_upload = system.get("total_upload_mbps", 0)

    remaining_headroom = max(upload_limit_mbps - total_upload, 0)

    facts["estimated_upload_limit_mbps"] = upload_limit_mbps
    facts["remaining_upload_headroom_mbps"] = remaining_headroom
    facts["upload_not_bottleneck_for_current_stream"] = (
        remaining_headroom > NON_PLEX_UPLOAD_WARN_MBPS
        and not recent_upload.get("sustained_upload_high", False)
    )

    facts["upload_headroom_ok"] = total_upload < UPLOAD_WARN_MBPS
    facts["plex_stream_light"] = plex_upload < 10

    facts["recent_upload_avg_mbps"] = recent_upload.get("avg_upload_mbps", 0)
    facts["recent_upload_max_mbps"] = recent_upload.get("max_upload_mbps", 0)
    facts["sustained_upload_high"] = recent_upload.get("sustained_upload_high", False)
    facts["brief_upload_spike"] = recent_upload.get("brief_upload_spike", False)
    facts["upload_std_dev"] = recent_upload.get("upload_std_dev", 0)
    facts["upload_is_stable"] = recent_upload.get("upload_is_stable", False)
    facts["upload_is_bursty"] = recent_upload.get("upload_is_bursty", False)

    facts["non_plex_upload_mbps"] = max(total_upload - plex_upload, 0)
    facts["non_plex_upload_present"] = facts["non_plex_upload_mbps"] > NON_PLEX_UPLOAD_WARN_MBPS

    facts["plex_vs_total_ratio"] = (
        plex_upload / total_upload if total_upload > 0 else 0
    )

    facts["upload_mostly_plex"] = facts["plex_vs_total_ratio"] > 0.7

    # --- session-level facts ---
    session_facts = []

    for s in plex["sessions"]:
        subtitle_codec = (s.get("subtitle_codec") or "").lower()
        player = (s.get("player_product") or "").lower()
        bitrate = s.get("bitrate_kbps") or 0
        decision = (s.get("decision") or "").lower()
        audio_codec = (s.get("audio_codec") or "").lower()
        container = (s.get("container") or "").lower()
        tautulli_state = (s.get("tautulli_state") or "").lower()
        tautulli_bandwidth_kbps = s.get("tautulli_bandwidth_kbps") or 0
        tautulli_bandwidth_mbps = round(tautulli_bandwidth_kbps / 1000, 2) if tautulli_bandwidth_kbps else 0

        bitrate_mbps = round(bitrate / 1000, 2) if bitrate else 0

        session_facts.append({
            "title": s.get("title"),
            "is_direct_play": decision == "directplay",
            "is_transcode": decision == "transcode",
            "is_image_subtitle": any(x in subtitle_codec for x in ["pgs", "vobsub"]),
            "is_text_subtitle": any(x in subtitle_codec for x in ["srt", "ass", "ssa"]),
            "bitrate_kbps": bitrate,
            "bitrate_mbps": bitrate_mbps,
            "bitrate_high": bitrate > HIGH_BITRATE_KBPS,
            "likely_network_safe": bitrate < SAFE_NETWORK_KBPS,
            "likely_upload_issue": bitrate_mbps > remaining_headroom if bitrate else False,
            "client_is_roku": "roku" in player,
            "client_is_browser": any(x in player for x in ["web", "chrome", "browser"]),
            "client_is_mac": "mac" in player,
            "client_is_fire_tv": "fire" in player or "aft" in player,
            "client_is_apple_tv": "apple tv" in player,
            "client_is_windows": "windows" in player,

            "is_playing": tautulli_state == "playing",
            "is_paused": tautulli_state == "paused",

            # raw player state from telemetry
            "is_buffering_signal": tautulli_state == "buffering",

            # confirmed buffering: trust telemetry state directly
            "is_buffering": tautulli_state == "buffering",

            # softer indicator for sessions that look suspicious even if not currently marked buffering
            "buffering_risk": (
                decision == "directplay"
                and (
                    tautulli_bandwidth_mbps < max(bitrate_mbps * 0.8, 1)
                    or (
                        container == "mp4"
                        and (subtitle_codec == "mov_text" or audio_codec == "ac3")
                    )
                )
            ),

            "playback_state": tautulli_state or "unknown",

            "tautulli_bandwidth_kbps": tautulli_bandwidth_kbps,
            "tautulli_bandwidth_mbps": tautulli_bandwidth_mbps,

            "container_decision": (s.get("tautulli_stream_container_decision") or "").lower(),
            "video_decision": (s.get("tautulli_stream_video_decision") or "").lower(),
            "audio_decision": (s.get("tautulli_stream_audio_decision") or "").lower(),
            "subtitle_decision": (s.get("tautulli_stream_subtitle_decision") or "").lower(),

            "delivery_below_expected": (
                tautulli_bandwidth_mbps > 0
                and bitrate_mbps > 0
                and tautulli_bandwidth_mbps < bitrate_mbps * BANDWIDTH_BELOW_BITRATE_RATIO
            ),

            "looks_healthy_now": (
                decision == "directplay"
                and tautulli_state == "playing"
                and not any(x in subtitle_codec for x in ["pgs", "vobsub"])
                and bitrate < SAFE_NETWORK_KBPS
            ),

            "audio_codec": audio_codec,
            "container": container,
            "audio_channels": s.get("audio_channels"),
            "subtitle_format": s.get("subtitle_format"),

            "is_remote_session": tautulli_bandwidth_kbps > 0,
            "subtitle_is_mov_text": subtitle_codec == "mov_text" or (s.get("subtitle_format") or "").lower() == "mov_text",
            "audio_is_ac3": audio_codec == "ac3",
            "audio_is_multichannel": (s.get("audio_channels") or "") not in {"", "1", "2"},
            "container_is_mp4": container == "mp4",

            "client_name": s.get("player_product"),
            "tautulli_product_name": s.get("tautulli_product"),
            "tautulli_player_name": s.get("tautulli_player"),

            "file_trait_risk": (
            container == "mp4"
            and (
                subtitle_codec == "mov_text"
                or audio_codec == "ac3"
            )
        ),

        "client_trait_risk": (
            "mac" in player
            or "browser" in player
        ),

        "network_path_risk": (
            tautulli_state == "buffering"
            and decision == "directplay"
            and facts.get("upload_is_bursty")
            and not facts.get("sustained_upload_high")
        ),

        "compatibility_pattern": (
            tautulli_state == "buffering"
            and decision == "directplay"
            and (
                container == "mp4"
                or subtitle_codec == "mov_text"
                or audio_codec == "ac3"
            )
            and any(x in player for x in ["mac", "windows", "browser", "roku", "android", "fire", "apple tv"])
        ),

        "path_sensitivity_pattern": (
            tautulli_state == "buffering"
            and decision == "directplay"
            and facts.get("upload_is_bursty")
            and not facts.get("sustained_upload_high")
            and facts.get("remaining_upload_headroom_mbps", 0) >= 5
            and any(x in player for x in ["mac", "windows", "browser", "android", "fire", "roku", "apple tv"])
        ),

        "client_is_tv_app": any(x in player for x in ["android", "aft", "fire", "roku", "apple tv", "tv"]),
        "client_is_desktop_app": any(x in player for x in ["mac", "windows", "desktop"]),
        "client_is_mobile": any(x in player for x in ["iphone", "ipad", "android"]),
        "client_trait_risk": any(x in player for x in ["mac", "windows", "browser", "roku"]),

            
        })

    facts["session_facts"] = session_facts

    facts["buffering_signal_sessions"] = [
    sf["title"] for sf in session_facts if sf.get("is_buffering_signal")
]

    facts["buffering_sessions"] = [
        sf["title"] for sf in session_facts if sf.get("is_buffering")
    ]

    facts["buffering_risk_sessions"] = [
        sf["title"] for sf in session_facts if sf.get("buffering_risk")
    ]

    facts["buffering_signal_detected"] = len(facts["buffering_signal_sessions"]) > 0
    facts["buffering_detected"] = len(facts["buffering_sessions"]) > 0
    facts["buffering_risk_detected"] = len(facts["buffering_risk_sessions"]) > 0

    facts["paused_sessions"] = [sf["title"] for sf in session_facts if sf.get("is_paused")]
    facts["playing_sessions"] = [sf["title"] for sf in session_facts if sf.get("is_playing")]

    facts["compatibility_pattern_sessions"] = [
    sf["title"] for sf in session_facts if sf.get("compatibility_pattern")
    ]
    facts["compatibility_pattern_detected"] = len(facts["compatibility_pattern_sessions"]) > 0

    facts["path_sensitivity_sessions"] = [
        sf["title"] for sf in session_facts if sf.get("path_sensitivity_pattern")
    ]
    facts["path_sensitivity_detected"] = len(facts["path_sensitivity_sessions"]) > 0

    facts["file_trait_risk_sessions"] = [
        sf["title"] for sf in session_facts if sf.get("file_trait_risk")
    ]
    facts["file_trait_risk_detected"] = len(facts["file_trait_risk_sessions"]) > 0

    facts["client_trait_risk_sessions"] = [
        sf["title"] for sf in session_facts if sf.get("client_trait_risk")
    ]
    facts["client_trait_risk_detected"] = len(facts["client_trait_risk_sessions"]) > 0

    return facts

def diagnose_buffering(state: dict) -> list[str]:
    reasons = []

    system = state["system"]
    facts = state.get("facts", {})
    sessions = state["plex"]["sessions"]

    if facts.get("buffering_detected"):
        reasons.append(
            f'Buffering is currently confirmed for: {", ".join(facts.get("buffering_sessions", []))}.'
        )
    else:
        reasons.append("Buffering is not currently confirmed by telemetry.")

    if not sessions:
        return reasons + ["No active Plex sessions were detected."]

    if state["plex"]["transcodes"] > 0:
        reasons.append("One or more active sessions are transcoding, which increases buffering risk.")

    if system["host_cpu_percent"] > CPU_WARN_PERCENT:
        reasons.append("Host CPU is elevated, so server processing could be contributing.")

    if system["host_ram_percent"] > RAM_WARN_PERCENT:
        reasons.append("Host RAM usage is high, which could contribute to instability or contention.")

    if system["iowait_percent"] > IOWAIT_WARN_PERCENT:
        reasons.append("Disk wait is elevated, suggesting storage I/O may be slowing playback.")

    if (
        facts.get("sustained_upload_high")
        and facts.get("upload_mostly_plex")
        and facts.get("upload_is_stable")
        and facts.get("remaining_upload_headroom_mbps", 0) < 2
        and facts.get("recent_upload_avg_mbps", 0) > UPLOAD_WARN_MBPS
        ):
        reasons.append(
        f'Upload has been consistently near capacity over the last minute '
        f'(avg {facts.get("recent_upload_avg_mbps", 0)} Mbps, max {facts.get("recent_upload_max_mbps", 0)} Mbps), '
        'which strongly suggests upload saturation is a real bottleneck.'
    )

    elif facts.get("sustained_upload_high") and facts.get("non_plex_upload_present"):
        reasons.append(
            f'Upload is high (avg {facts.get("recent_upload_avg_mbps", 0)} Mbps), '
            f'but a significant portion (~{round(facts.get("non_plex_upload_mbps", 0), 2)} Mbps) '
            'is not from Plex. This suggests other network activity is contributing to the load.'
        )

    elif facts.get("upload_is_bursty"):
        reasons.append(
            f'Upload is bursty (std dev {facts.get("upload_std_dev", 0)} Mbps), '
            'which is typical for Plex streaming and not inherently a bottleneck.'
        )

    elif facts.get("brief_upload_spike"):
        reasons.append(
            f'Upload showed a brief spike (max {facts.get("recent_upload_max_mbps", 0)} Mbps), '
            'but it was not sustained and does not indicate a bottleneck.'
        )

    for s in sessions:
        title = s.get("title", "Unknown")
        decision = (s.get("decision") or "").lower()
        subtitle_codec = (s.get("subtitle_codec") or "").lower()
        player = (s.get("player_product") or "").lower()
        bitrate = s.get("bitrate_kbps", 0)
        bitrate_mbps = round((bitrate or 0) / 1000, 2)

        if subtitle_codec in {"pgs", "vobsub"}:
            reasons.append(f'{title} is using image subtitles ({subtitle_codec}), which often force transcoding on many clients.')

        if bitrate > HIGH_BITRATE_KBPS:
            reasons.append(f'{title} has a high bitrate (~{bitrate_mbps} Mbps), which requires strong network conditions.')

        if "roku" in player:
            reasons.append(f'{title} is playing on Roku, which is more likely to hit compatibility-related playback issues.')

        if decision == "directplay" and subtitle_codec == "srt":
            reasons.append(f'{title} is direct playing with SRT subtitles, which is an optimal playback path.')

    return reasons

def build_structured_diagnosis(state: dict) -> dict:
    facts = state.get("facts", {})
    system = state.get("system", {})
    sessions = facts.get("session_facts", [])

    ruled_out = []
    risk_factors = []
    confidence = "low"
    most_likely_cause = "unknown"

    buffering_confirmed = facts.get("buffering_detected", False)
    buffering_signal_detected = facts.get("buffering_signal_detected", False)
    transcodes_active = state["plex"]["transcodes"] > 0

    # --- ruled out causes ---
    if not transcodes_active:
        ruled_out.append("transcoding")

    if system.get("host_cpu_percent", 0) < CPU_WARN_PERCENT:
        ruled_out.append("cpu")

    if system.get("host_ram_percent", 0) < RAM_WARN_PERCENT:
        ruled_out.append("ram")

    if system.get("iowait_percent", 0) < IOWAIT_WARN_PERCENT:
        ruled_out.append("disk_io")

    # only rule out upload if it is clearly NOT the issue
    if (
        facts.get("remaining_upload_headroom_mbps", 0) >= 5
        and not facts.get("sustained_upload_high")
    ):
        ruled_out.append("upload_saturation")

    # --- risk factors ---
    if buffering_confirmed:
        risk_factors.append("buffering_confirmed")

    if facts.get("sustained_upload_high"):
        risk_factors.append("sustained_upload_high")

    if facts.get("upload_is_bursty"):
        risk_factors.append("bursty_upload_pattern")

    if facts.get("upload_mostly_plex"):
        risk_factors.append("upload_mostly_plex")

    if facts.get("non_plex_upload_present"):
        risk_factors.append("non_plex_upload_present")

    if facts.get("compatibility_pattern_detected"):
        risk_factors.append("compatibility_pattern_detected")

    if facts.get("path_sensitivity_detected"):
        risk_factors.append("path_sensitivity_detected")

    for sf in sessions:
        if sf.get("is_image_subtitle"):
            risk_factors.append("image_subtitles")
        if sf.get("client_is_roku"):
            risk_factors.append("roku_client")
        if sf.get("client_is_browser"):
            risk_factors.append("browser_client")
        if sf.get("delivery_below_expected"):
            risk_factors.append("delivery_throughput_issue")
        if sf.get("is_buffering"):
            risk_factors.append("session_buffering")
        if sf.get("looks_healthy_now"):
            risk_factors.append("healthy_direct_play_session")
        if sf.get("client_trait_risk"):
            risk_factors.append("sensitive_client_type")

    # --- default when no issue is confirmed ---
    if not buffering_confirmed:
        most_likely_cause = "none_detected"
        confidence = "low"

    # --- true upload saturation: strict definition ---
    if (
        buffering_confirmed
        and facts.get("sustained_upload_high")
        and facts.get("upload_mostly_plex")
        and facts.get("remaining_upload_headroom_mbps", 0) < 2
        and facts.get("recent_upload_avg_mbps", 0) > UPLOAD_WARN_MBPS
        and facts.get("upload_is_stable")
    ):
        most_likely_cause = "upload_saturation"
        confidence = "high"

    # --- delivery mismatch is stronger than generic client/network ---
    if (
        buffering_confirmed
        and any(sf.get("delivery_below_expected") for sf in sessions)
        and not transcodes_active
    ):
        most_likely_cause = "network_throughput_issue"
        confidence = "high"

    # --- file/client compatibility pattern ---
    if (
        buffering_confirmed
        and facts.get("compatibility_pattern_detected")
        and not transcodes_active
        and "upload_saturation" in ruled_out
    ):
        most_likely_cause = "client_file_compatibility_issue"
        confidence = "medium"

    # --- path sensitivity pattern: bursty remote delivery + sensitive client ---
    if (
        buffering_confirmed
        and facts.get("path_sensitivity_detected")
        and not transcodes_active
        and "upload_saturation" in ruled_out
    ):
        most_likely_cause = "client_network_path_sensitivity"
        confidence = "medium"

    # --- buffering with healthy server + no stronger pattern -> client/network ---
    if (
        buffering_confirmed
        and not transcodes_active
        and system.get("host_cpu_percent", 0) < CPU_WARN_PERCENT
        and system.get("host_ram_percent", 0) < RAM_WARN_PERCENT
        and system.get("iowait_percent", 0) < IOWAIT_WARN_PERCENT
        and most_likely_cause not in {
            "upload_saturation",
            "network_throughput_issue",
            "client_file_compatibility_issue",
            "client_network_path_sensitivity",
        }
    ):
        most_likely_cause = "client_or_network"
        confidence = "medium"

    # --- transcoding overrides most things ---
    if transcodes_active:
        most_likely_cause = "transcoding"
        confidence = "high"

    return {
    "buffering_confirmed": buffering_confirmed,
    "buffering_signal_detected": buffering_signal_detected,
    "buffering_sessions": facts.get("buffering_sessions", []),
    "buffering_signal_sessions": facts.get("buffering_signal_sessions", []),
    "buffering_risk_detected": facts.get("buffering_risk_detected", False),
    "buffering_risk_sessions": facts.get("buffering_risk_sessions", []),
    "most_likely_cause": most_likely_cause,
    "confidence": confidence,
    "ruled_out": sorted(set(ruled_out)),
    "risk_factors": sorted(set(risk_factors)),
}

def answer_question(question: str, state: dict) -> str:
    q = question.lower().strip()

    # 🔹 BUFFERING
    if "buffer" in q:
        reasons = diagnose_buffering(state)
        return "Buffering analysis:\n- " + "\n- ".join(reasons)

    # 🔹 TRANSCODING LIST
    if "transcod" in q and "why" not in q:
        transcode_titles = [
            s["title"]
            for s in state["plex"]["sessions"]
            if (s.get("decision") or "").lower() == "transcode"
        ]
        if not transcode_titles:
            return "No active transcodes are currently detected."
        return "Active transcodes:\n- " + "\n- ".join(transcode_titles)

    # 🔹 WHY TRANSCODING
    if "why" in q and "transcod" in q:
        issues = []
        for s in state["plex"]["sessions"]:
            if (s.get("decision") or "").lower() == "transcode":
                subtitle = (s.get("subtitle_codec") or "").lower()
                if subtitle in {"pgs", "vobsub"}:
                    issues.append(f'{s["title"]} is transcoding due to image-based subtitles.')
                else:
                    issues.append(f'{s["title"]} is transcoding due to client codec incompatibility.')

        if not issues:
            return "Nothing is currently transcoding."

        return "\n".join(issues)

    # 🔹 WHAT'S HAPPENING
    if "what" in q and "happening" in q:
        plex = state["plex"]
        system = state["system"]

        lines = []

        lines.append(f'{plex.get("active_sessions", 0)} active session(s)')
        lines.append(f'{plex.get("transcodes", 0)} transcode(s), {plex.get("direct_plays", 0)} direct play(s)')
        lines.append(f'CPU {system.get("host_cpu_percent", 0)}% | RAM {system.get("host_ram_percent", 0)}%')
        lines.append(f'Plex upload {system.get("plex_upload_mbps", 0)} Mbps')

        if plex["sessions"]:
            lines.append("\nActive streams:")
            for s in plex["sessions"]:
                lines.append(
                    f'- {s["title"]} | {s["decision"]} | {s["video_codec"]}/{s["audio_codec"]} | subs={s["subtitle_codec"]}'
                )

        return "\n".join(lines)

    # 🔹 UPLOAD
    if "upload" in q or "bandwidth" in q:
        return (
            f'Plex upload is {state["system"]["plex_upload_mbps"]} Mbps and total upload is '
            f'{state["system"]["total_upload_mbps"]} Mbps.'
        )

    # 🔹 HEALTH
    if "healthy" in q or "health" in q:
        return (
            f'Server health is {state["diagnosis"]["health"]}. '
            f'CPU is {state["system"]["host_cpu_percent"]}%, '
            f'RAM is {state["system"]["host_ram_percent"]}%, '
            f'iowait is {state["system"]["iowait_percent"]}%.'
        )

    return summarize(state)

def build_llm_context(state: dict) -> str:
    context = {
    "system": state["system"],
    "plex_summary": {
        "active_sessions": state["plex"]["active_sessions"],
        "transcodes": state["plex"]["transcodes"],
        "direct_plays": state["plex"]["direct_plays"],
    },
    "sessions": state["plex"]["sessions"],
    "tautulli_activity": state["plex"]["tautulli_activity"],
    "facts": state.get("facts", {}),
    "diagnosis": state["diagnosis"],
    "structured_diagnosis": state.get("structured_diagnosis", {}),
    }

    return json.dumps(context, indent=2)

def answer_with_llm(question: str, state: dict) -> str:
    if client is None:
        return "OPENAI_API_KEY is missing from your .env file."

    llm_context = build_llm_context(state)
    rule_based_answer = answer_question(question, state)

    instructions = (
    "You are a Plex observability assistant. "
    "You must use the provided telemetry, facts, and structured diagnosis as the source of truth. "
    "Structured diagnosis ALWAYS overrides rule-based analysis if there is any conflict. "
    "Do not guess or invent issues. "

    "Only say buffering is currently happening if buffering_confirmed is true. "
    "If buffering_confirmed is false but buffering_risk_detected is true, describe it as a possible or weak buffering-related pattern, not confirmed buffering. "

    "Never contradict structured diagnosis. "
    "Use rule-based analysis only as supporting evidence, not as the final conclusion. "

    "Do not treat bursty upload as a bottleneck. "
    "Only consider upload a bottleneck if it is sustained near capacity, primarily driven by Plex traffic, "
    "remaining upload headroom is extremely low, and the pattern is stable rather than bursty. "

    "If the most_likely_cause is client_file_compatibility_issue, explain that this is a pattern-based diagnosis, "
    "not absolute proof, and mention contributing factors such as Mac client, MP4 container, MOV_TEXT subtitles, or AC3 audio. "

    "If the most_likely_cause is client_network_path_sensitivity, explain that this suggests the client and remote delivery path "
    "appear less tolerant of bursty throughput or long-distance network variability, even though the server itself looks healthy. "

    "If only one session is in buffering_sessions, explicitly say that other active sessions are playing normally when supported by telemetry. "

    "When analyzing problems, clearly separate: "
    "1. what is confirmed by telemetry, "
    "2. what is ruled out, and "
    "3. what is most likely (based on structured diagnosis). "

    "If no issue is confirmed, explicitly state that the system is healthy. "
    "Be concise but specific."
)

    prompt = f"""
User question:
{question}

Rule-based analysis:
{rule_based_answer}

IMPORTANT:
- Use the telemetry, facts, and structured diagnosis as the source of truth.
- If buffering_confirmed is false, do not state that buffering is currently happening.
- If buffering_confirmed is true, say that clearly.
- Do not mention any stream unless it exists in sessions[].
- Explicitly mention ruled-out causes when helpful.
- If upload is bursty but not sustained, do not describe it as a bottleneck.
- Prefer structured_diagnosis.most_likely_cause for the final answer.
- Distinguish between a weak buffering signal and confirmed buffering.
- Only attribute buffering to sessions listed in buffering_sessions.
- Do not describe sessions in buffering_signal_sessions as definitely buffering unless they are also in buffering_sessions.

Live system context:
{llm_context}
"""

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            instructions=instructions,
            input=prompt,
            store=False,
        )
        return response.output_text
    except Exception as e:
        return (
            f"LLM mode failed: {e}\n\n"
            f"Falling back to rule-based answer:\n\n"
            f"{rule_based_answer}"
        )

if __name__ == "__main__":
    state = build_state()

    if len(sys.argv) > 1:
        args = sys.argv[1:]

        if args[0] == "--llm":
            question = " ".join(args[1:]).strip()
            if not question:
                print("Usage: python app.py --llm \"your question here\"")
            else:
                print(answer_with_llm(question, state))
        else:
            question = " ".join(args).strip()
            print(answer_question(question, state))
    else:
        print("=== HUMAN SUMMARY ===")
        print(summarize(state))
        print("\n=== RAW STATE JSON ===")
        print(json.dumps(state, indent=2))