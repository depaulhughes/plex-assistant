import warnings

warnings.filterwarnings(
    "ignore",
    message=".*urllib3 v2 only supports OpenSSL.*",
)

import requests
import xml.etree.ElementTree as ET

from config import (
    PLEX_BASE_URL,
    PLEX_TOKEN,
    PROMETHEUS_BASE_URL,
    TAUTULLI_API_KEY,
    TAUTULLI_BASE_URL,
)


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


def build_tautulli_session_map(tautulli_activity: dict) -> dict:
    session_map = {}

    sessions = tautulli_activity.get("sessions", [])
    for s in sessions:
        session_key = str(s.get("session_key", "")).strip()
        if session_key:
            session_map[session_key] = s

    return session_map
