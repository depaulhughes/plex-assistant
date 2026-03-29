from config import (
    CPU_WARN_PERCENT,
    HIGH_BITRATE_KBPS,
    IOWAIT_WARN_PERCENT,
    NON_PLEX_UPLOAD_WARN_MBPS,
    RAM_WARN_PERCENT,
    UPLOAD_WARN_MBPS,
)


def _system_is_healthy(system: dict) -> bool:
    return (
        system.get("host_cpu_percent", 0) < CPU_WARN_PERCENT
        and system.get("host_ram_percent", 0) < RAM_WARN_PERCENT
        and system.get("iowait_percent", 0) < IOWAIT_WARN_PERCENT
    )


def _strict_upload_saturation_evidence(facts: dict, system_healthy: bool, transcodes_active: bool) -> bool:
    return (
        facts.get("buffering_detected")
        and facts.get("sustained_upload_high")
        and facts.get("upload_is_stable")
        and not facts.get("upload_is_bursty")
        and facts.get("upload_mostly_plex")
        and facts.get("remaining_upload_headroom_mbps", 0) < 2
        and facts.get("recent_upload_avg_mbps", 0) > UPLOAD_WARN_MBPS
        and not facts.get("has_mixed_session_health")
        and not facts.get("single_session_buffering_while_others_healthy")
        and not (
            system_healthy
            and not transcodes_active
            and facts.get("healthy_playing_session_count", 0) > 0
        )
    )


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
    system_healthy = _system_is_healthy(system)
    mixed_session_health = facts.get("has_mixed_session_health", False)
    single_session_buffering_while_others_healthy = facts.get(
        "single_session_buffering_while_others_healthy", False
    )
    strong_delivery_mismatch = (
        facts.get("buffering_session_count", 0) > 0
        and facts.get("buffering_sessions_delivery_issue_count", 0) == facts.get("buffering_session_count", 0)
    )
    compatibility_evidence = (
        facts.get("buffering_sessions_have_compatibility_pattern")
        and facts.get("buffering_sessions_have_file_trait_risk")
        and facts.get("buffering_sessions_have_client_trait_risk")
    )
    same_content_playing_elsewhere_successfully = facts.get(
        "same_content_playing_elsewhere_successfully", False
    )
    path_sensitivity_evidence = (
        (
            facts.get("buffering_sessions_have_path_sensitivity")
            or facts.get("upload_is_bursty")
            or facts.get("buffering_sessions_have_delivery_issue")
        )
        and facts.get("buffering_sessions_have_client_trait_risk")
        and (
            mixed_session_health
            or single_session_buffering_while_others_healthy
            or facts.get("session_specific_issue_likely")
        )
    )
    strict_upload_saturation = _strict_upload_saturation_evidence(
        facts,
        system_healthy=system_healthy,
        transcodes_active=transcodes_active,
    )
    upload_contradiction_guard = (
        single_session_buffering_while_others_healthy
        and not transcodes_active
        and system_healthy
        and facts.get("upload_is_bursty")
    )

    if not transcodes_active:
        ruled_out.append("transcoding")

    if system.get("host_cpu_percent", 0) < CPU_WARN_PERCENT:
        ruled_out.append("cpu")

    if system.get("host_ram_percent", 0) < RAM_WARN_PERCENT:
        ruled_out.append("ram")

    if system.get("iowait_percent", 0) < IOWAIT_WARN_PERCENT:
        ruled_out.append("disk_io")

    if (
        facts.get("remaining_upload_headroom_mbps", 0) >= 5
        and not facts.get("sustained_upload_high")
    ):
        ruled_out.append("upload_saturation")

    if facts.get("upload_is_bursty"):
        ruled_out.append("stable_upload_saturation")

    if upload_contradiction_guard:
        ruled_out.append("upload_saturation")

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

    if mixed_session_health:
        risk_factors.append("mixed_session_health")

    if single_session_buffering_while_others_healthy:
        risk_factors.append("single_session_buffering_while_others_healthy")

    if same_content_playing_elsewhere_successfully:
        risk_factors.append("same_content_playing_elsewhere_successfully")

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

    if not buffering_confirmed:
        most_likely_cause = "none_detected"
        confidence = "low"

    if transcodes_active:
        most_likely_cause = "transcoding"
        confidence = "high"

    elif strict_upload_saturation:
        most_likely_cause = "upload_saturation"
        confidence = "high"

    elif (
        buffering_confirmed
        and not transcodes_active
        and system_healthy
        and mixed_session_health
        and path_sensitivity_evidence
        and not strict_upload_saturation
    ):
        most_likely_cause = "client_network_path_sensitivity"
        confidence = "medium"

    elif (
        buffering_confirmed
        and not transcodes_active
        and system_healthy
        and compatibility_evidence
        and not same_content_playing_elsewhere_successfully
        and not strict_upload_saturation
    ):
        most_likely_cause = "client_file_compatibility_issue"
        confidence = "medium"

    elif (
        buffering_confirmed
        and not transcodes_active
        and system_healthy
        and path_sensitivity_evidence
        and not strict_upload_saturation
    ):
        most_likely_cause = "client_network_path_sensitivity"
        confidence = "medium"

    elif (
        buffering_confirmed
        and strong_delivery_mismatch
        and not transcodes_active
        and not mixed_session_health
    ):
        most_likely_cause = "network_throughput_issue"
        confidence = "high"

    elif (
        buffering_confirmed
        and not transcodes_active
        and system_healthy
    ):
        most_likely_cause = "client_or_network"
        confidence = "medium"

    return {
        "buffering_confirmed": buffering_confirmed,
        "buffering_signal_detected": buffering_signal_detected,
        "buffering_sessions": facts.get("buffering_sessions", []),
        "buffering_signal_sessions": facts.get("buffering_signal_sessions", []),
        "buffering_risk_detected": facts.get("buffering_risk_detected", False),
        "buffering_risk_sessions": facts.get("buffering_risk_sessions", []),
        "most_likely_cause": most_likely_cause,
        "confidence": confidence,
        "system_wide_issue_likely": facts.get("system_wide_issue_likely", False),
        "session_specific_issue_likely": facts.get("session_specific_issue_likely", False),
        "ruled_out": sorted(set(ruled_out)),
        "risk_factors": sorted(set(risk_factors)),
    }


def classify_issue_metadata(state: dict, structured_diagnosis: dict) -> dict:
    diagnosis = structured_diagnosis.get("most_likely_cause", "unknown")
    confidence = structured_diagnosis.get("confidence", "low")
    facts = state.get("facts", {})
    plex = state.get("plex", {})

    scope = "unknown"
    severity = "info"
    diagnosis_family = "unknown"

    if diagnosis == "none_detected":
        scope = "unknown"
        severity = "info"
        diagnosis_family = "healthy_or_risk_only"
    elif diagnosis in {"client_network_path_sensitivity", "client_file_compatibility_issue"}:
        scope = "client_specific"
        severity = "warning"
        diagnosis_family = "client_specific"
    elif diagnosis == "network_throughput_issue":
        scope = "system_wide" if facts.get("buffering_session_count", 0) > 1 else "session_specific"
        severity = "critical" if facts.get("buffering_session_count", 0) > 1 else "warning"
        diagnosis_family = "delivery"
    elif diagnosis == "upload_saturation":
        scope = "system_wide"
        severity = "critical"
        diagnosis_family = "capacity"
    elif diagnosis == "transcoding":
        scope = "system_wide" if plex.get("transcodes", 0) > 1 else "session_specific"
        severity = "critical" if plex.get("transcodes", 0) > 1 else "warning"
        diagnosis_family = "processing"
    elif diagnosis == "client_or_network":
        scope = "session_specific" if facts.get("session_specific_issue_likely") else "unknown"
        severity = "warning" if structured_diagnosis.get("buffering_confirmed") else "info"
        diagnosis_family = "client_or_network"

    if structured_diagnosis.get("system_wide_issue_likely") and diagnosis != "none_detected":
        scope = "service_wide" if severity == "critical" else "system_wide"

    if facts.get("buffering_session_count", 0) == 0 and not structured_diagnosis.get("buffering_confirmed"):
        severity = "info"

    return {
        "scope": scope,
        "severity": severity,
        "confidence": confidence,
        "diagnosis_family": diagnosis_family,
    }
