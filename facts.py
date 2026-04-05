import time

from clients import prom_query_range
from config import (
    BANDWIDTH_BELOW_BITRATE_RATIO,
    BURSTY_STDDEV_MBPS,
    HIGH_BITRATE_KBPS,
    NON_PLEX_UPLOAD_WARN_MBPS,
    SAFE_NETWORK_KBPS,
    STABLE_STDDEV_MBPS,
    UPLOAD_LIMIT_MBPS,
    UPLOAD_PEAK_MBPS,
    UPLOAD_WARN_MBPS,
)


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
            "consecutive_saturation_count": 0,
            "sustained_upload_saturation": False,
            "burst_upload_saturation": False,
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
    peak_index = samples.index(max_upload)
    tail_window = samples[-3:] if len(samples) >= 3 else samples
    tail_avg = sum(tail_window) / len(tail_window) if tail_window else 0.0

    above_warn_count = sum(1 for x in samples if x > UPLOAD_WARN_MBPS)
    above_peak_count = sum(1 for x in samples if x > UPLOAD_PEAK_MBPS)
    saturation_threshold = max(UPLOAD_PEAK_MBPS, UPLOAD_LIMIT_MBPS * 0.9)
    consecutive_saturation_count = 0
    max_consecutive_saturation = 0
    for sample in samples:
        if sample >= saturation_threshold:
            consecutive_saturation_count += 1
            max_consecutive_saturation = max(max_consecutive_saturation, consecutive_saturation_count)
        else:
            consecutive_saturation_count = 0

    sustained_upload_saturation = max_consecutive_saturation >= 3
    burst_upload_saturation = max_upload >= saturation_threshold and not sustained_upload_saturation
    sustained_upload_high = sustained_upload_saturation or above_warn_count >= max(4, (len(samples) * 2) // 3)
    brief_upload_spike = max_upload > UPLOAD_WARN_MBPS and not sustained_upload_high
    early_peak_window = max(2, len(samples) // 4)
    early_peak = peak_index <= early_peak_window
    settled_after_spike = (
        tail_avg < UPLOAD_WARN_MBPS
        and tail_avg <= max_upload * 0.72
        and avg_upload <= max_upload * 0.82
    )
    # Treat an early burst that settles quickly as normal buffer-fill behavior,
    # not the same thing as sustained WAN pressure.
    startup_spike_candidate = (
        (burst_upload_saturation or brief_upload_spike)
        and early_peak
        and settled_after_spike
        and max_consecutive_saturation <= 2
        and above_warn_count <= max(3, len(samples) // 3)
    )

    return {
        "samples": samples,
        "avg_upload_mbps": round(avg_upload, 2),
        "max_upload_mbps": round(max_upload, 2),
        "tail_avg_upload_mbps": round(tail_avg, 2),
        "peak_sample_index": peak_index,
        "above_warn_count": above_warn_count,
        "above_peak_count": above_peak_count,
        "consecutive_saturation_count": max_consecutive_saturation,
        "sustained_upload_saturation": sustained_upload_saturation,
        "burst_upload_saturation": burst_upload_saturation,
        "sustained_upload_high": sustained_upload_high,
        "brief_upload_spike": brief_upload_spike,
        "startup_spike_candidate": startup_spike_candidate,
        "upload_std_dev": round(std_dev, 2),
        "upload_is_stable": std_dev < STABLE_STDDEV_MBPS,
        "upload_is_bursty": std_dev > BURSTY_STDDEV_MBPS,
    }


def derive_facts(state: dict) -> dict:
    facts = {}

    plex = state["plex"]
    system = state["system"]
    history = state.get("history", {})
    recent_upload = history.get("recent_upload", {})

    facts["has_sessions"] = plex["active_sessions"] > 0
    facts["has_transcodes"] = plex["transcodes"] > 0

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
    facts["sustained_upload_saturation"] = recent_upload.get("sustained_upload_saturation", False)
    facts["burst_upload_saturation"] = recent_upload.get("burst_upload_saturation", False)
    facts["sustained_upload_high"] = recent_upload.get("sustained_upload_high", False)
    facts["brief_upload_spike"] = recent_upload.get("brief_upload_spike", False)
    facts["startup_spike_candidate"] = recent_upload.get("startup_spike_candidate", False)
    facts["upload_std_dev"] = recent_upload.get("upload_std_dev", 0)
    facts["upload_is_stable"] = recent_upload.get("upload_is_stable", False)
    facts["upload_is_bursty"] = recent_upload.get("upload_is_bursty", False)

    facts["non_plex_upload_mbps"] = max(total_upload - plex_upload, 0)
    facts["non_plex_upload_present"] = facts["non_plex_upload_mbps"] > NON_PLEX_UPLOAD_WARN_MBPS

    facts["plex_vs_total_ratio"] = plex_upload / total_upload if total_upload > 0 else 0
    facts["upload_mostly_plex"] = facts["plex_vs_total_ratio"] > 0.7

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

        session_facts.append(
            {
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
                "is_buffering_signal": tautulli_state == "buffering",
                "is_buffering": tautulli_state == "buffering",
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
                "is_healthy_playing_session": (
                    tautulli_state == "playing"
                    and decision == "directplay"
                    and tautulli_state != "buffering"
                    and tautulli_state != "paused"
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
            }
        )

    facts["session_facts"] = session_facts

    facts["buffering_signal_sessions"] = [
        sf["title"] for sf in session_facts if sf.get("is_buffering_signal")
    ]
    facts["buffering_sessions"] = [
        sf["title"] for sf in session_facts if sf.get("is_buffering")
    ]
    facts["buffering_session_titles"] = list(facts["buffering_sessions"])
    facts["buffering_risk_sessions"] = [
        sf["title"] for sf in session_facts if sf.get("buffering_risk")
    ]

    facts["buffering_signal_detected"] = len(facts["buffering_signal_sessions"]) > 0
    facts["buffering_detected"] = len(facts["buffering_sessions"]) > 0
    facts["buffering_risk_detected"] = len(facts["buffering_risk_sessions"]) > 0

    facts["paused_sessions"] = [sf["title"] for sf in session_facts if sf.get("is_paused")]
    facts["playing_sessions"] = [sf["title"] for sf in session_facts if sf.get("is_playing")]
    facts["healthy_session_titles"] = [
        sf["title"] for sf in session_facts if sf.get("is_healthy_playing_session")
    ]

    facts["buffering_session_count"] = len(facts["buffering_sessions"])
    facts["healthy_playing_session_count"] = len(facts["healthy_session_titles"])
    facts["paused_session_count"] = len(facts["paused_sessions"])

    facts["has_mixed_session_health"] = (
        facts["buffering_session_count"] > 0
        and facts["healthy_playing_session_count"] > 0
    )
    facts["single_session_buffering_while_others_healthy"] = (
        facts["buffering_session_count"] == 1
        and facts["healthy_playing_session_count"] > 0
    )

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

    buffering_session_facts = [sf for sf in session_facts if sf.get("is_buffering")]
    healthy_session_facts = [sf for sf in session_facts if sf.get("is_healthy_playing_session")]
    buffering_titles = {sf.get("title") for sf in buffering_session_facts if sf.get("title")}
    healthy_titles = {sf.get("title") for sf in healthy_session_facts if sf.get("title")}

    facts["affected_session_client_names"] = [
        sf.get("client_name") for sf in buffering_session_facts if sf.get("client_name")
    ]
    facts["affected_session_player_names"] = [
        sf.get("tautulli_player_name") for sf in buffering_session_facts if sf.get("tautulli_player_name")
    ]
    facts["buffering_sessions_delivery_issue_count"] = sum(
        1 for sf in buffering_session_facts if sf.get("delivery_below_expected")
    )
    facts["buffering_sessions_client_trait_risk_count"] = sum(
        1 for sf in buffering_session_facts if sf.get("client_trait_risk")
    )
    facts["buffering_sessions_file_trait_risk_count"] = sum(
        1 for sf in buffering_session_facts if sf.get("file_trait_risk") or sf.get("is_image_subtitle")
    )
    facts["buffering_sessions_path_sensitivity_count"] = sum(
        1 for sf in buffering_session_facts if sf.get("path_sensitivity_pattern") or sf.get("network_path_risk")
    )
    facts["buffering_sessions_compatibility_count"] = sum(
        1 for sf in buffering_session_facts if sf.get("compatibility_pattern")
    )
    facts["healthy_sessions_count"] = len(healthy_session_facts)

    facts["buffering_sessions_have_delivery_issue"] = (
        facts["buffering_sessions_delivery_issue_count"] > 0
    )
    facts["buffering_sessions_have_client_trait_risk"] = (
        facts["buffering_sessions_client_trait_risk_count"] > 0
    )
    facts["buffering_sessions_have_file_trait_risk"] = (
        facts["buffering_sessions_file_trait_risk_count"] > 0
    )
    facts["buffering_sessions_have_path_sensitivity"] = (
        facts["buffering_sessions_path_sensitivity_count"] > 0
    )
    facts["buffering_sessions_have_compatibility_pattern"] = (
        facts["buffering_sessions_compatibility_count"] > 0
    )
    facts["same_content_playing_elsewhere_successfully"] = bool(
        buffering_titles and healthy_titles and buffering_titles.intersection(healthy_titles)
    )
    facts["same_content_healthy_titles"] = sorted(buffering_titles.intersection(healthy_titles))

    facts["session_specific_issue_likely"] = (
        facts["single_session_buffering_while_others_healthy"]
        or (
            facts["has_mixed_session_health"]
            and not facts.get("sustained_upload_high")
        )
        or facts["same_content_playing_elsewhere_successfully"]
    )
    facts["system_wide_issue_likely"] = (
        facts["buffering_session_count"] > 1
        and facts.get("sustained_upload_high")
        and facts.get("upload_is_stable")
        and facts.get("upload_mostly_plex")
    )

    facts["startup_spike_expected"] = (
        facts.get("startup_spike_candidate", False)
        and facts.get("has_sessions", False)
        and not facts.get("buffering_detected", False)
        and not facts.get("sustained_upload_high", False)
        and not facts.get("sustained_upload_saturation", False)
        and plex.get("transcodes", 0) == 0
        and system.get("host_cpu_percent", 0) < 60
        and system.get("host_ram_percent", 0) < 85
        and system.get("iowait_percent", 0) < 8
    )
    facts["startup_buffer_fill"] = facts["startup_spike_expected"]

    return facts
